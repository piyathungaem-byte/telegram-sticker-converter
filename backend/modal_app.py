#!/usr/bin/env python3
"""Modal.com Backend & FastAPI Server for Telegram to WhatsApp Sticker Converter.

Deploy to Modal.com with:
    modal deploy modal_app.py

Or test locally with:
    modal serve modal_app.py
    # or
    python modal_app.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent))
if Path("/root").is_dir():
    sys.path.insert(0, "/root")

import convert

import modal
from fastapi import FastAPI, Header, HTTPException, Request as FastApiRequest, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

# ---------------------------------------------------------------------------
# Modal App & Container Configuration
# ---------------------------------------------------------------------------

app = modal.App("telegram-sticker-converter")

# Persistent storage volume across invocations
volume = modal.Volume.from_name("sticker-converter-data", create_if_missing=True)

# Container image with Debian slim, ffmpeg, and Python packages
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install(
        "fastapi[standard]>=0.110.0",
        "uvicorn>=0.28.0",
        "Pillow>=10.0.0",
        "rlottie-python>=1.3.8",
        "requests>=2.31.0",
        "imageio-ffmpeg>=0.4.9",
    )
    .add_local_file(Path(__file__).parent / "convert.py", "/root/convert.py")
)

# ---------------------------------------------------------------------------
# Storage & Directories
# ---------------------------------------------------------------------------

def _get_data_dir() -> Path:
    if Path("/data").is_dir() or os.environ.get("MODAL_ENVIRONMENT"):
        return Path("/data")
    local = Path(__file__).resolve().parent / "modal_data"
    local.mkdir(parents=True, exist_ok=True)
    return local

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = _get_data_dir()
RUNS_DIR = DATA_DIR / "webapp-runs"
CACHE_DIR = DATA_DIR / "webapp-cache"
LEGACY_COLLECTION_DIR = APP_DIR / "webapp-collection"
COLLECTION_DIR = DATA_DIR / "MySaves"
COLLECTION_ASSETS_DIR = COLLECTION_DIR / "assets"
COLLECTION_THUMBS_DIR = COLLECTION_DIR / "thumbs"
COLLECTION_PREVIEWS_DIR = COLLECTION_DIR / "previews"
COLLECTION_UNDO_DIR = COLLECTION_DIR / ".undo"
COLLECTION_INDEX = COLLECTION_DIR / "collection.json"
PREVIEW_CACHE_DIR = DATA_DIR / "preview-cache"
THUMBNAIL_CACHE_DIR = PREVIEW_CACHE_DIR / "thumbnails"
FULLYST_VISITED_PAGES_FILE = DATA_DIR / "fullyst-visited-pages.json"

for d in (DATA_DIR, RUNS_DIR, CACHE_DIR, COLLECTION_DIR, COLLECTION_ASSETS_DIR,
          COLLECTION_THUMBS_DIR, COLLECTION_PREVIEWS_DIR, COLLECTION_UNDO_DIR,
          PREVIEW_CACHE_DIR, THUMBNAIL_CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)

COLLECTION_UNDO_TTL_SECONDS = 10 * 60
MAX_COLLECTION_UNDO_ACTIONS = 32
MAX_SAVED_STICKERS = 1000
MAX_SAVED_GROUPS = 100
MAX_SAVED_GROUP_NAME = 40
UNGROUPED_GROUP_ID = "ungrouped"
COLLECTION_GROUP_ID_RE = re.compile(r"[0-9a-f]{32}")
COLLECTION_FORMATS = {"webp", "webm", "tgs", "png", "jpg", "jpeg"}

THUMBNAIL_CACHE_VERSION = 1
THUMBNAIL_CACHE_MAX_BYTES = 128 * 1024 * 1024
THUMBNAIL_CACHE_MAX_ITEM_BYTES = 4 * 1024 * 1024
MAX_PREVIEW_RENDER_WORKERS = 4
PREVIEW_RENDER_WORKERS = min(MAX_PREVIEW_RENDER_WORKERS, max(1, os.cpu_count() or 1))
PREVIEW_HTTP_CACHE_CONTROL = "public, max-age=3600"
MAX_BODY = 64 * 1024
MAX_JOBS = 50
DEFAULT_WEBM_METHOD = 4
FULLYST_BASE = "https://fullyst.com"
FULLYST_STICKER_HOST = "stickers.fullyst.com"
FULLYST_PAGE_SIZE = 10
FULLYST_MAX_PACK_STICKERS = 500
FULLYST_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
FULLYST_RETRY_ATTEMPTS = 3
FULLYST_RETRY_DELAYS = (0.7, 1.5)
FULLYST_MAX_CATALOG_PAGE = 1_000_000

STATE_LOCK = threading.RLock()
COLLECTION_LOCK = threading.RLock()
COLLECTION_MIGRATION_LOCK = threading.Lock()
FULLYST_VISITED_PAGES_LOCK = threading.RLock()
_COLLECTION_MIGRATED = False
ANIM_PREVIEW_LOCK = threading.Lock()
THUMBNAIL_RENDER_SEMAPHORE = threading.BoundedSemaphore(PREVIEW_RENDER_WORKERS)
_THUMBNAIL_CACHE_MAINTENANCE_LOCK = threading.Lock()
_THUMBNAIL_CACHE_LAST_MAINTENANCE = 0.0
FULLYST_PREVIEW_CACHE_LOCK = threading.Lock()
FULLYST_PREVIEW_CACHE: dict[str, bytes] = {}
FULLYST_PREVIEW_CACHE_MAX = 48
JOBS: dict[str, dict] = {}
COLLECTION_UNDO_ACTIONS: dict[str, dict] = {}
# Per-browser job pointers: the hosted frontend generates a random client id
# (localStorage) and sends it as the X-Client-Id header (or ?client_id= for
# media URLs, which <img> tags cannot decorate with headers). Anonymous
# callers share the "shared" bucket so pre-existing behaviour is preserved.
ACTIVE_JOB_IDS: dict[str, str | None] = {}
LATEST_JOB_IDS: dict[str, str | None] = {}
CLIENT_ID_RE = re.compile(r"[A-Za-z0-9-]{1,64}")
CLIENT_BUCKET_SHARED = "shared"
# Back-stop capacity guard: never let more than this many jobs run at once,
# regardless of how many browsers are connected (2-CPU container).
MAX_ACTIVE_JOBS_TOTAL = 4
FULLYST_VISITED_PAGES_DIR = DATA_DIR / "fullyst-visited-pages"
DEMO_MODE = False



def _json_bytes(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _copy_public_state(job: dict) -> dict:
    """Return only fields needed by the browser; never expose credentials or file paths."""
    keys = ("id", "status", "phase", "message", "done", "total", "packs", "error",
            "title", "source", "sourceSlug", "failedCount", "isAdult",
            "rememberWarning", "activityLog")
    result = {k: job.get(k) for k in keys if k in job}
    if job.get("status") == "ready" or (
            job.get("status") == "running" and job.get("phase") == "Preparing previews"
            and "stickers" in job):
        result["stickers"] = job.get("stickers", [])
    return result


def _job_stickers_for_picker(job_id: str, descriptors: list[dict],
                             thumbnails: list[bytes | None], *, preparing: bool = False) -> list[dict]:
    """Public picker metadata; during preparation, each thumbnail becomes available independently."""
    stickers = []
    for index, desc in enumerate(descriptors):
        sticker = ({k: desc[k] for k in ("index", "sourceIndex", "formatLabel", "kind", "emoji")}
                   | {"thumbUrl": f"/api/job/{job_id}/thumb/{desc['index']}",
                      "largePreviewUrl": f"/api/job/{job_id}/preview/{desc['index']}"})
        if desc["kind"] == "Animated":
            sticker["previewUrl"] = f"/api/job/{job_id}/preview/{desc['index']}"
        if preparing:
            sticker["previewReady"] = False
            sticker["previewAvailable"] = False
        else:
            thumbnail = thumbnails[index] if index < len(thumbnails) else None
            sticker["previewReady"] = True
            sticker["previewAvailable"] = bool(thumbnail)
        stickers.append(sticker)
    return stickers


def _append_activity_log(job: dict, phase: str, message: str) -> None:
    message = str(message or "").strip()
    phase = str(phase or "Activity").strip()
    if not message:
        return
    entries = job.setdefault("activityLog", [])
    if entries and entries[-1].get("phase") == phase and entries[-1].get("message") == message:
        return
    seq = int(job.get("_activitySeq", 0)) + 1
    job["_activitySeq"] = seq
    entries.append({"seq": seq, "time": datetime.now().strftime("%H:%M:%S"),
                    "phase": phase[:60], "message": message[:240]})
    if len(entries) > 600:
        del entries[:-600]


def _update(job_id: str, **fields) -> None:
    with STATE_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            old_phase = job.get("phase")
            old_message = job.get("message")
            old_status = job.get("status")
            job.update(fields)
            phase = job.get("phase") or "Activity"
            message = job.get("message") or ""
            if message and (phase != old_phase or message != old_message
                            or job.get("status") != old_status):
                _append_activity_log(job, phase, message)


def _cache_root(set_name: str) -> Path:
    key = hashlib.sha256(set_name.casefold().encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / key


def _cache_folder(set_name: str) -> Path:
    return _cache_root(set_name) / "raw"




def _remember_supported() -> bool:
    return False


def _token_path() -> Path | None:
    return None


def _load_saved_token() -> str:
    return os.environ.get("TG_BOT_TOKEN", "")


def _save_token(token: str) -> None:
    pass


def _clear_saved_token() -> None:
    pass


def _make_thumbnail(item: dict) -> bytes | None:
    """Make a small, local PNG preview from the first frame of any Telegram sticker type."""
    from io import BytesIO
    from PIL import Image

    blob = item["bytes"]
    fmt = item["format"]
    image = None
    resample = getattr(Image, "Resampling", Image).LANCZOS
    with tempfile.TemporaryDirectory(prefix="sticker_thumb_") as folder:
        folder = Path(folder)
        if fmt == "tgs":
            from rlottie_python import LottieAnimation
            source = folder / "sticker.tgs"
            source.write_bytes(blob)
            anim = LottieAnimation.from_tgs(str(source))
            width, height = anim.lottie_animation_get_size()
            scale = 160 / max(width, height, 1)
            image = anim.render_pillow_frame(frame_num=0,
                                             width=max(1, round(width * scale)),
                                             height=max(1, round(height * scale))).convert("RGBA")
        elif fmt == "webm":
            source, frame = folder / "sticker.webm", folder / "frame.png"
            source.write_bytes(blob)
            vf = ("scale=160:160:force_original_aspect_ratio=decrease,"
                  "pad=160:160:(ow-iw)/2:(oh-ih)/2:color=0x00000000,format=rgba")
            result = convert.run([convert.need_ffmpeg(), "-hide_banner", "-nostdin",
                                  "-loglevel", "error", "-y", "-threads", "1",
                                  "-i", str(source), "-vf", vf, "-frames:v", "1", str(frame)],
                                 timeout=30)
            if result.returncode != 0 or not frame.exists():
                return None
            image = Image.open(frame).convert("RGBA")
        else:
            image = Image.open(BytesIO(blob))
            try:
                image.seek(0)
            except Exception:
                pass
            image = image.convert("RGBA")

        image.thumbnail((160, 160), resample)
        canvas = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
        canvas.alpha_composite(image, ((160 - image.width) // 2, (160 - image.height) // 2))
        output = BytesIO()
        canvas.save(output, "PNG", optimize=True)
        return output.getvalue()


def _thumbnail_cache_path(item: dict) -> Path | None:
    """Build a versioned, content-addressed key for the static poster thumbnail only."""
    blob = item.get("bytes")
    fmt = str(item.get("format") or "").lower()
    if not isinstance(blob, bytes) or not blob or not fmt:
        return None
    key = hashlib.sha256(
        f"sticker-studio-thumbnail-v{THUMBNAIL_CACHE_VERSION}:{fmt}:".encode("utf-8")
        + blob
    ).hexdigest()
    return THUMBNAIL_CACHE_DIR / key[:2] / f"{key}.png"


def _read_cached_thumbnail(path: Path) -> bytes | None:
    png_signature = b"\x89PNG\r\n\x1a\n"
    try:
        if path.stat().st_size > THUMBNAIL_CACHE_MAX_ITEM_BYTES:
            path.unlink()
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if not data.startswith(png_signature):
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return data


def _write_cached_thumbnail(path: Path, data: bytes) -> None:
    png_signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(png_signature) or len(data) > THUMBNAIL_CACHE_MAX_ITEM_BYTES:
        return
    temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_bytes(data)
        os.replace(temp_path, path)
    except OSError:
        # The cache is an optimization; a read-only or full disk must not break fetching.
        pass
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def _prune_thumbnail_cache(*, force: bool = False) -> None:
    """Keep the local poster cache bounded, pruning oldest entries only when needed."""
    global _THUMBNAIL_CACHE_LAST_MAINTENANCE
    now = time.monotonic()
    with _THUMBNAIL_CACHE_MAINTENANCE_LOCK:
        if not force and now - _THUMBNAIL_CACHE_LAST_MAINTENANCE < 60:
            return
        _THUMBNAIL_CACHE_LAST_MAINTENANCE = now
        try:
            files = []
            for path in THUMBNAIL_CACHE_DIR.rglob("*.png"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                files.append((stat.st_mtime_ns, stat.st_size, path))
        except OSError:
            return
        total_bytes = sum(size for _mtime, size, _path in files)
        if total_bytes <= THUMBNAIL_CACHE_MAX_BYTES:
            return
        for _mtime, size, path in sorted(files):
            if total_bytes <= THUMBNAIL_CACHE_MAX_BYTES:
                break
            try:
                path.unlink()
                total_bytes -= size
            except OSError:
                pass


def _get_or_create_thumbnail(item: dict) -> tuple[bytes | None, bool]:
    """Return a cached first-frame PNG when possible; never substitute for an animated preview."""
    path = _thumbnail_cache_path(item)
    if path:
        cached = _read_cached_thumbnail(path)
        if cached:
            return cached, True
    with THUMBNAIL_RENDER_SEMAPHORE:
        # Another request may have filled the same content key while this one waited.
        if path:
            cached = _read_cached_thumbnail(path)
            if cached:
                return cached, True
        thumbnail = _make_thumbnail(item)
        if path and thumbnail:
            _write_cached_thumbnail(path, thumbnail)
    if path and thumbnail:
        _prune_thumbnail_cache()
    return thumbnail, False


def _prepare_thumbnails(items: list[dict], progress_callback=None) -> tuple[list[bytes | None], int]:
    """Render poster thumbnails concurrently with a small CPU-aware worker limit."""
    if not items:
        return [], 0
    from concurrent.futures import ThreadPoolExecutor, as_completed

    thumbnails: list[bytes | None] = [None] * len(items)
    cache_hits = 0
    completed = 0
    workers = max(1, min(PREVIEW_RENDER_WORKERS, len(items)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sticker-thumbnails") as pool:
        futures = {pool.submit(_get_or_create_thumbnail, item): index
                   for index, item in enumerate(items)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                thumbnail, from_cache = future.result()
            except Exception:                                           # noqa: BLE001
                thumbnail, from_cache = None, False
            thumbnails[index] = thumbnail
            completed += 1
            cache_hits += int(from_cache)
            if progress_callback:
                progress_callback({"index": index, "thumbnail": thumbnail,
                                   "done": completed, "total": len(items),
                                   "cacheHits": cache_hits})
    _prune_thumbnail_cache(force=True)
    return thumbnails, cache_hits


def _make_animated_preview(item: dict) -> bytes | None:
    """Render a lightweight looping animated GIF preview for TGS/WebM/animated-WebP stickers."""
    from io import BytesIO
    from PIL import Image

    fmt = item.get("format")
    if fmt not in ("tgs", "webm", "webp"):
        return None
    frames = []
    frame_duration_ms = 125
    resample = getattr(Image, "Resampling", Image).LANCZOS
    with tempfile.TemporaryDirectory(prefix="sticker_anim_preview_") as folder:
        folder = Path(folder)
        if fmt == "tgs":
            from rlottie_python import LottieAnimation
            source = folder / "sticker.tgs"
            source.write_bytes(item["bytes"])
            anim = LottieAnimation.from_tgs(str(source))
            width, height = anim.lottie_animation_get_size()
            source_fps = float(anim.lottie_animation_get_framerate() or 30)
            total_frames = int(anim.lottie_animation_get_totalframe())
            if total_frames < 2 or source_fps <= 0:
                return None
            duration_s = min(3.0, total_frames / source_fps)
            frame_count = min(total_frames, 24, max(2, round(duration_s * 8)))
            frame_duration_ms = max(20, round(duration_s * 1000 / frame_count))
            scale = 160 / max(width, height, 1)
            rw, rh = max(1, round(width * scale)), max(1, round(height * scale))
            for i in range(frame_count):
                frame_num = min(total_frames - 1,
                                round(i * duration_s * source_fps / frame_count))
                image = anim.render_pillow_frame(frame_num=frame_num, width=rw, height=rh).convert("RGBA")
                image.thumbnail((160, 160), resample)
                canvas = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
                canvas.alpha_composite(image, ((160 - image.width) // 2,
                                               (160 - image.height) // 2))
                frames.append(canvas)
        elif fmt == "webm":
            source = folder / "sticker.webm"
            source.write_bytes(item["bytes"])
            pattern = folder / "frame_%03d.png"
            vf = ("fps=8,scale=160:160:force_original_aspect_ratio=decrease,"
                  "pad=160:160:(ow-iw)/2:(oh-ih)/2:color=0x00000000,format=rgba")
            result = convert.run([convert.need_ffmpeg(), "-hide_banner", "-nostdin",
                                  "-loglevel", "error", "-y", "-threads", "1",
                                  "-i", str(source), "-t", "3", "-vf", vf,
                                  "-frames:v", "24", str(pattern)], timeout=30)
            if result.returncode != 0:
                return None
            for path in sorted(folder.glob("frame_*.png")):
                with Image.open(path) as source_frame:
                    frames.append(source_frame.convert("RGBA").copy())
            if len(frames) < 2:
                return None
        else:
            with Image.open(BytesIO(item["bytes"])) as source:
                total_frames = int(getattr(source, "n_frames", 1))
                if source.format != "WEBP" or total_frames < 2:
                    return None
                durations = []
                for i in range(total_frames):
                    source.seek(i)
                    durations.append(max(20, int(source.info.get("duration", 125) or 125)))
                total_ms = sum(durations)
                sample_ms = min(3000, total_ms)
                frame_count = min(total_frames, 24, max(2, round(sample_ms / 1000 * 8)))
                frame_duration_ms = max(20, round(sample_ms / frame_count))
                source_count = max(frame_count, round(total_frames * sample_ms / max(total_ms, 1)))
                for i in range(frame_count):
                    frame_index = min(total_frames - 1, round(i * source_count / frame_count))
                    source.seek(frame_index)
                    image = source.convert("RGBA").copy()
                    image.thumbnail((160, 160), resample)
                    canvas = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
                    canvas.alpha_composite(image, ((160 - image.width) // 2,
                                                   (160 - image.height) // 2))
                    frames.append(canvas)

    if len(frames) < 2:
        return None
    # GIF is used only for UI previews: it animates in every common browser, including
    # browsers that display an animated WebP as a still image. Keep a shared palette
    # across frames to avoid color flicker, reserving index 255 for transparency.
    sheet = Image.new("RGB", (160, 160 * len(frames)))
    for i, frame in enumerate(frames):
        sheet.paste(frame.convert("RGB"), (0, 160 * i))
    quantize = getattr(Image, "Quantize", Image)
    dither = getattr(Image, "Dither", Image)
    palette_source = sheet.quantize(colors=255,
                                   method=getattr(quantize, "MEDIANCUT", 0),
                                   dither=getattr(dither, "NONE", 0))
    palette = (palette_source.getpalette() or [])[:768]
    palette += [0] * max(0, 768 - len(palette))
    palette[765:768] = [0, 0, 0]
    palette_source.putpalette(palette)
    gif_frames = []
    for frame in frames:
        indexed = frame.convert("RGB").quantize(
            palette=palette_source, dither=getattr(dither, "NONE", 0))
        indexed.putpalette(palette)
        transparent = frame.getchannel("A").point(lambda alpha: 255 if alpha < 40 else 0)
        indexed.paste(255, mask=transparent)
        gif_frames.append(indexed)
    output = BytesIO()
    gif_frames[0].save(output, "GIF", save_all=True, append_images=gif_frames[1:],
                       duration=frame_duration_ms, loop=0, disposal=2,
                       transparency=255, optimize=False)
    return output.getvalue()


_ADULT_TEXT_RE = re.compile(
    r"(?<![a-z0-9])(?:18\s*(?:\+|plus)|nsfw|not\s+safe\s+for\s+work|adult(?:s|[-_ ]?only)?|"
    r"mature|explicit|porn(?:o|hub|ography)?|hentai|rule\s*34|r\s*34|ecchi|xxx|sex|sexy|sexual|sexting|"
    r"nudes?|naked|erotic|lewd|bdsm|fetish|only\s*fans|milf|boobs?|breasts?|tits?|nipples?|"
    r"pussy|vagina|penis|dicks?|cocks?|cum|orgasm|blowjobs?|handjobs?|anal|genitals?)"
    r"(?![a-z0-9])", re.IGNORECASE)


def _fullyst_looks_adult(*values: str) -> bool:
    text = " ".join(re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value or ""))
                    for value in values).casefold()
    text = re.sub(r"[_./-]+", " ", text)
    if _ADULT_TEXT_RE.search(text):
        return True
    # Catch common obfuscations in catalog titles/slugs without an external classifier.
    leetspeak = text.translate(str.maketrans({"0": "o", "1": "i", "3": "e",
                                               "4": "a", "5": "s", "7": "t"}))
    return bool(_ADULT_TEXT_RE.search(leetspeak))


def _fullyst_adult_marker(attrs: dict) -> bool:
    classes = str(attrs.get("class") or "")
    if _fullyst_looks_adult(classes):
        return True
    positive_values = {"1", "true", "yes", "adult", "nsfw", "explicit", "18+"}
    text_attributes = {"aria-label", "title", "alt", "data-title", "data-tags",
                       "data-category", "data-rating", "data-content-rating",
                       "data-description", "data-label"}
    for key, value in attrs.items():
        key = str(key).casefold()
        value = str(value or "").strip()
        if key in {"data-nsfw", "data-adult", "data-sensitive", "data-explicit"} \
                and value.casefold() in positive_values:
            return True
        if key in text_attributes and _fullyst_looks_adult(value):
            return True
    return False


class _FullystCatalogParser(HTMLParser):
    """Extract the small result cards from Fullyst's public catalog HTML."""
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
             "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.packs: list[dict] = []
        self.current: dict | None = None
        self.stack: list[str] = []
        self.capture = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())
        if self.current is None:
            if tag == "div" and "sticker-set" in classes:
                self.current = {"titleParts": [], "kindParts": [], "textParts": [], "href": "", "sampleUrl": "",
                                "adultMarked": _fullyst_adult_marker(attrs)}
                self.stack = [tag]
            return
        if tag not in self._VOID:
            self.stack.append(tag)
        if _fullyst_adult_marker(attrs):
            self.current["adultMarked"] = True
        if tag == "h2":
            self.capture = "title"
        elif tag == "span" and "type-label" in classes:
            self.capture = "kind"
        elif tag == "a" and "btn-stickers" in classes:
            self.current["href"] = attrs.get("href", "")
        elif tag == "img" and not self.current["sampleUrl"]:
            src = attrs.get("src", "")
            parsed = urlparse(src)
            if parsed.scheme == "https" and parsed.hostname == FULLYST_STICKER_HOST:
                self.current["sampleUrl"] = src

    def handle_data(self, data):
        if self.current is None:
            return
        if data.strip():
            self.current["textParts"].append(data)
        if self.capture == "title":
            self.current["titleParts"].append(data)
        elif self.capture == "kind":
            self.current["kindParts"].append(data)

    def handle_endtag(self, tag):
        if self.current is None:
            return
        if tag == "h2" and self.capture == "title":
            self.capture = ""
        elif tag == "span" and self.capture == "kind":
            self.capture = ""
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i] == tag:
                del self.stack[i:]
                break
        if not self.stack:
            href = self.current.get("href", "")
            slug = urlparse(href).path.rstrip("/").split("/")[-1]
            title = " ".join("".join(self.current["titleParts"]).split())
            kind = " ".join("".join(self.current["kindParts"]).split())
            if title and re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", slug):
                card_text = " ".join(self.current.get("textParts", []))
                is_adult = bool(self.current.get("adultMarked") or
                                _fullyst_looks_adult(title, slug, kind, card_text))
                is_animated = bool(re.search(r"\b(?:animated|animation|video)\b", kind,
                                             flags=re.IGNORECASE))
                self.packs.append({"title": title, "kind": kind or "Sticker pack",
                                   "slug": slug, "sampleUrl": self.current["sampleUrl"],
                                   "isAdult": is_adult, "isAnimated": is_animated})
            self.current = None
            self.stack = []
            self.capture = ""


def _fullyst_read(request: Request, limit: int = FULLYST_MAX_RESPONSE_BYTES) -> bytes:
    try:
        with urlopen(request, timeout=25) as response:
            data = response.read(limit + 1)
            if len(data) > limit:
                raise RuntimeError("Fullyst response exceeded the local size limit.")
            return data
    except Exception as exc:                                            # noqa: BLE001
        raise RuntimeError(f"Could not reach Fullyst: {exc}") from exc


def _fullyst_retryable(exc: Exception) -> bool:
    cause = exc.__cause__
    if isinstance(cause, HTTPError):
        return cause.code in {408, 425, 429, 500, 502, 503, 504}
    if isinstance(cause, URLError):
        return True
    if isinstance(cause, (TimeoutError, ConnectionError, OSError)):
        return True
    return isinstance(exc, (TimeoutError, ConnectionError, OSError))


def _fullyst_error_text(exc: Exception) -> str:
    cause = exc.__cause__
    if isinstance(cause, HTTPError):
        return f"HTTP {cause.code} {cause.reason}"
    if isinstance(cause, URLError):
        return f"Network error: {cause.reason}"
    return str(exc).replace("Could not reach Fullyst: ", "")[:180]


def _fullyst_read_retry(request: Request, limit: int = FULLYST_MAX_RESPONSE_BYTES,
                        retry_callback=None) -> bytes:
    for attempt in range(1, FULLYST_RETRY_ATTEMPTS + 1):
        try:
            return _fullyst_read(request, limit=limit)
        except Exception as exc:                                      # noqa: BLE001
            if attempt >= FULLYST_RETRY_ATTEMPTS or not _fullyst_retryable(exc):
                raise
            if retry_callback:
                retry_callback(attempt, _fullyst_error_text(exc))
            time.sleep(FULLYST_RETRY_DELAYS[attempt - 1])
    raise RuntimeError("Fullyst request exhausted its retry attempts.")


def _fullyst_search(query: str, page: int) -> dict:
    query = query.strip()
    if len(query) > 100:
        raise ValueError("Search text is too long (maximum 100 characters).")
    if not 1 <= page <= 10000:
        raise ValueError("Catalog page is out of range.")
    params = {}
    if page > 1:
        params["page"] = str(page)
    if query:
        params["search"] = query
    url = f"{FULLYST_BASE}/en/stickers"
    if params:
        url += "?" + urlencode(params)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 StickerStudio/1.0",
                                   "Accept": "text/html,application/xhtml+xml"})
    page_html = _fullyst_read_retry(request).decode("utf-8", "replace")
    parser = _FullystCatalogParser()
    parser.feed(page_html)
    packs = parser.packs[:10]
    pager = re.search(r'<div\b[^>]*class=["\'][^"\']*\bpagination\b[^"\']*["\'][^>]*>(.*?)</div>',
                      page_html, flags=re.IGNORECASE | re.DOTALL)
    page_numbers = [int(value) for value in re.findall(r"page=(\d+)", pager.group(1),
                                                       flags=re.IGNORECASE)] if pager else []
    page_count = max(page_numbers) if page_numbers else 1
    page = min(page, page_count)
    return {"packs": packs, "page": page, "pageCount": page_count, "query": query,
            "hasMore": bool(packs) and page < page_count}


def _visited_pages_file(client_id: str | None) -> Path:
    return FULLYST_VISITED_PAGES_DIR / f"{client_id}.json" if client_id else FULLYST_VISITED_PAGES_FILE


def _read_fullyst_visited_pages(client_id: str | None = None) -> set[int]:
    visited_file = _visited_pages_file(client_id)
    if not visited_file.is_file():
        return set()
    try:
        payload = json.loads(visited_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("The Fullyst page-visit history could not be read.") from exc
    pages = payload.get("pages") if isinstance(payload, dict) else None
    if not isinstance(pages, list):
        raise RuntimeError("The Fullyst page-visit history has an invalid format.")
    return {page for page in pages
            if type(page) is int and 1 <= page <= FULLYST_MAX_CATALOG_PAGE}


def _get_fullyst_visited_pages(client_id: str | None = None) -> list[int]:
    with FULLYST_VISITED_PAGES_LOCK:
        return sorted(_read_fullyst_visited_pages(client_id))


def _mark_fullyst_page_visited(page: int, client_id: str | None = None) -> dict:
    if type(page) is not int or not 1 <= page <= FULLYST_MAX_CATALOG_PAGE:
        raise ValueError("Enter a valid Fullyst catalog page number.")
    visited_file = _visited_pages_file(client_id)
    with FULLYST_VISITED_PAGES_LOCK:
        pages = _read_fullyst_visited_pages(client_id)
        already_visited = page in pages
        if not already_visited:
            pages.add(page)
            visited_file.parent.mkdir(parents=True, exist_ok=True)
            temp_path = visited_file.with_name(
                visited_file.name + f".{uuid.uuid4().hex}.tmp")
            try:
                temp_path.write_text(json.dumps({"version": 1, "pages": sorted(pages)},
                                                separators=(",", ":")), encoding="utf-8")
                os.replace(temp_path, visited_file)
            except OSError as exc:
                raise RuntimeError("Could not save Fullyst page history in this PC's data folder.") from exc
            finally:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass
        return {"alreadyVisited": already_visited, "count": len(pages)}


def _fullyst_fetch_batch(slug: str, offset: int, limit: int = FULLYST_PAGE_SIZE) -> tuple[list[dict], dict]:
    request_body = urlencode({"set": slug, "stickers": "00" if offset == 0 else str(offset),
                              "limit": str(limit)}).encode("ascii")
    request = Request(f"{FULLYST_BASE}/receiver/stickers", data=request_body,
                      headers={"User-Agent": "Mozilla/5.0 StickerStudio/1.0",
                               "Content-Type": "application/x-www-form-urlencoded",
                               "X-Requested-With": "XMLHttpRequest",
                               "Referer": f"{FULLYST_BASE}/en/stickers/{quote(slug, safe='')}"})
    try:
        response = json.loads(_fullyst_read_retry(request).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Fullyst returned an unreadable sticker list.") from exc
    if not isinstance(response, list) or not response or not isinstance(response[0], dict):
        raise RuntimeError("Fullyst returned an unexpected sticker-list response.")
    rows = response[0].get("stickers")
    set_info = response[0].get("set")
    if not isinstance(rows, list) or not isinstance(set_info, dict):
        raise RuntimeError("Fullyst did not return pack metadata for this link.")
    return rows, set_info


def _download_fullyst_asset(set_uuid: str, file_uid: str, fmt: str,
                            retry_callback=None) -> bytes:
    ext = "webm" if fmt == "webm" else "webp"
    url = f"https://{FULLYST_STICKER_HOST}/{quote(set_uuid, safe='-')}/full/{quote(file_uid, safe='_-')}.{ext}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 StickerStudio/1.0",
                                   "Accept": "image/webp,video/webm,*/*"})
    return _fullyst_read_retry(request, limit=20 * 1024 * 1024,
                               retry_callback=retry_callback)


def _fullyst_asset_signature_ok(blob: bytes, fmt: str) -> bool:
    if fmt == "webm":
        return len(blob) >= 4 and blob[:4] == b"\x1a\x45\xdf\xa3"
    return len(blob) >= 12 and blob[:4] == b"RIFF" and blob[8:12] == b"WEBP"


def _fullyst_animated_catalog_preview(slug: str) -> bytes:
    """Render one sticker as a GIF for an animated search-result card, caching briefly in RAM."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", slug):
        raise ValueError("This Fullyst pack link is not valid.")
    with FULLYST_PREVIEW_CACHE_LOCK:
        cached = FULLYST_PREVIEW_CACHE.get(slug)
    if cached:
        return cached
    rows, set_info = _fullyst_fetch_batch(slug, 0, limit=1)
    if not rows:
        raise RuntimeError("Fullyst returned no sticker preview for this pack.")
    row = rows[0]
    is_video = str(row.get("video", "0")).strip().lower() in {"1", "true", "yes"}
    is_animated = str(row.get("animated", "0")).strip().lower() in {"1", "true", "yes"}
    if not is_video and not is_animated:
        raise RuntimeError("Fullyst did not return an animated preview for this pack.")
    pack_uuid = str(set_info.get("uuid") or "")
    file_uid = str(row.get("file_uid") or "")
    if not re.fullmatch(r"[A-Fa-f0-9-]{36}", pack_uuid) \
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", file_uid):
        raise RuntimeError("Fullyst returned invalid animated-preview metadata.")
    fmt = "webm" if is_video else "webp"
    blob = _download_fullyst_asset(pack_uuid, file_uid, fmt)
    if not _fullyst_asset_signature_ok(blob, fmt):
        raise RuntimeError(f"Fullyst returned an invalid {fmt.upper()} preview.")
    item = {"title": str(set_info.get("title") or set_info.get("name") or slug),
            "name": f"01.{fmt}", "format": fmt,
            "emoji": str(row.get("emoji") or "📎"),
            "file_unique_id": file_uid, "bytes": blob}
    preview = _make_animated_preview(item)
    if not preview:
        raise RuntimeError("The animated sticker preview could not be rendered.")
    with FULLYST_PREVIEW_CACHE_LOCK:
        FULLYST_PREVIEW_CACHE[slug] = preview
        while len(FULLYST_PREVIEW_CACHE) > FULLYST_PREVIEW_CACHE_MAX:
            FULLYST_PREVIEW_CACHE.pop(next(iter(FULLYST_PREVIEW_CACHE)))
    return preview


def _fullyst_pack_items(slug: str, progress_callback=None) -> tuple[str, str, list[dict], list[dict]]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", slug):
        raise ValueError("This Fullyst pack link is not valid.")
    records: list[dict] = []
    set_info = {}
    offset = 0
    while offset < FULLYST_MAX_PACK_STICKERS:
        rows, meta = _fullyst_fetch_batch(slug, offset)
        if not set_info:
            set_info = meta
        if not rows:
            break
        records.extend(rows)
        if len(records) > FULLYST_MAX_PACK_STICKERS:
            raise RuntimeError(f"This pack is larger than the {FULLYST_MAX_PACK_STICKERS}-sticker import limit.")
        if len(rows) < FULLYST_PAGE_SIZE:
            break
        offset += FULLYST_PAGE_SIZE
    if not records:
        raise RuntimeError("Fullyst returned no stickers for this pack.")
    set_uuid = str(set_info.get("uuid") or "")
    if not re.fullmatch(r"[A-Fa-f0-9-]{36}", set_uuid):
        raise RuntimeError("Fullyst returned an invalid pack identifier.")
    title = str(set_info.get("title") or set_info.get("name") or slug).strip()[:140]
    cache_name = f"fullyst_{set_uuid}_{slug}"
    raw_dir = _cache_folder(cache_name)
    raw_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict | None] = [None] * len(records)
    failures: list[dict] = []
    done = 0
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def download_one(index: int, row: dict) -> tuple[int, dict | None, dict | None]:
        source_index = index + 1
        file_uid = str(row.get("file_uid") or "")
        is_video = str(row.get("video", "0")) == "1"
        is_animated = str(row.get("animated", "0")) == "1"
        fmt = "webm" if is_video else "webp"
        try:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", file_uid):
                raise RuntimeError("Fullyst returned an invalid file id.")
            cache_path = raw_dir / f"{source_index:03d}_{file_uid}.{fmt}"
            blob = b""
            cache_hit = False
            if cache_path.is_file():
                try:
                    cached = cache_path.read_bytes()
                    if _fullyst_asset_signature_ok(cached, fmt):
                        blob = cached
                        cache_hit = True
                    else:
                        cache_path.unlink()
                except OSError:
                    pass
            if not blob:
                def retry_notice(attempt: int, reason: str) -> None:
                    if progress_callback:
                        progress_callback({"type": "retry", "index": source_index,
                                           "total": len(records), "attempt": attempt,
                                           "maxAttempts": FULLYST_RETRY_ATTEMPTS,
                                           "error": reason})

                blob = _download_fullyst_asset(set_uuid, file_uid, fmt,
                                                retry_callback=retry_notice)
                if not _fullyst_asset_signature_ok(blob, fmt):
                    raise RuntimeError(f"Fullyst returned an invalid {fmt.upper()} file.")
                temp_path = cache_path.with_name(cache_path.name + f".{uuid.uuid4().hex}.tmp")
                try:
                    temp_path.write_bytes(blob)
                    temp_path.replace(cache_path)
                finally:
                    try:
                        temp_path.unlink()
                    except FileNotFoundError:
                        pass
            item = {"title": title, "name": f"{source_index:02d}.{fmt}",
                    "format": fmt, "emoji": str(row.get("emoji") or "📎"),
                    "file_unique_id": file_uid, "sourceIndex": source_index,
                    "bytes": blob, "fromCache": cache_hit,
                    "kind": "Animated" if is_video or is_animated else "Static"}
            return index, item, None
        except Exception as exc:                                      # noqa: BLE001
            failure = {"sourceIndex": source_index, "fileUniqueId": file_uid,
                       "format": fmt, "error": _fullyst_error_text(exc)}
            return index, None, failure

    workers = min(6, os.cpu_count() or 1, len(records))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(download_one, i, row) for i, row in enumerate(records)]
        for future in as_completed(futures):
            index, item, failure = future.result()
            done += 1
            if item is not None:
                items[index] = item
                event = {"type": "complete", "done": done, "total": len(records),
                         "index": index + 1, "format": item["format"],
                         "fromCache": item["fromCache"]}
            else:
                failures.append(failure or {"sourceIndex": index + 1,
                                             "error": "Unknown download failure."})
                event = {"type": "failed", "done": done, "total": len(records),
                         "index": index + 1, "error": failures[-1]["error"]}
            if progress_callback:
                progress_callback(event)
    failures.sort(key=lambda failure: failure["sourceIndex"])
    return cache_name, title, [item for item in items if item is not None], failures


def _migrate_legacy_collection() -> None:
    """Copy an older app-folder collection into stable per-user storage once."""
    global _COLLECTION_MIGRATED
    if _COLLECTION_MIGRATED:
        return
    with COLLECTION_MIGRATION_LOCK:
        if _COLLECTION_MIGRATED:
            return
        legacy_index = LEGACY_COLLECTION_DIR / "collection.json"
        if COLLECTION_INDEX.exists() or not legacy_index.is_file():
            _COLLECTION_MIGRATED = True
            return
        try:
            COLLECTION_DIR.mkdir(parents=True, exist_ok=True)
            for folder in ("assets", "thumbs", "previews"):
                source_dir = LEGACY_COLLECTION_DIR / folder
                if not source_dir.is_dir():
                    continue
                target_dir = COLLECTION_DIR / folder
                target_dir.mkdir(parents=True, exist_ok=True)
                for source_file in source_dir.iterdir():
                    if source_file.is_file():
                        shutil.copy2(source_file, target_dir / source_file.name)
            temp_index = COLLECTION_INDEX.with_name(COLLECTION_INDEX.name + ".migrate.tmp")
            try:
                shutil.copy2(legacy_index, temp_index)
                os.replace(temp_index, COLLECTION_INDEX)
            finally:
                try:
                    temp_index.unlink()
                except FileNotFoundError:
                    pass
            _COLLECTION_MIGRATED = True
        except OSError as exc:
            raise RuntimeError(
                "Could not move your existing My saves into this PC's Sticker Studio data folder. "
                "The original collection is still in the old app folder; close the app and check disk access."
            ) from exc


def _default_collection_groups() -> list[dict]:
    return [{"id": UNGROUPED_GROUP_ID, "name": "Ungrouped"}]


def _normalize_collection_groups(raw_groups) -> list[dict]:
    groups = _default_collection_groups()
    if not isinstance(raw_groups, list):
        return groups
    used_ids = {UNGROUPED_GROUP_ID}
    used_names = {"ungrouped"}
    for raw in raw_groups:
        if not isinstance(raw, dict):
            continue
        group_id = str(raw.get("id") or "")
        name = " ".join(str(raw.get("name") or "").split())
        if (group_id == UNGROUPED_GROUP_ID or not COLLECTION_GROUP_ID_RE.fullmatch(group_id)
                or not name or len(name) > MAX_SAVED_GROUP_NAME):
            continue
        name_key = name.casefold()
        if group_id in used_ids or name_key in used_names:
            continue
        groups.append({"id": group_id, "name": name})
        used_ids.add(group_id)
        used_names.add(name_key)
        if len(groups) >= MAX_SAVED_GROUPS + 1:
            break
    return groups


def _collection_payload_read() -> tuple[list[dict], list[dict]]:
    with COLLECTION_LOCK:
        _migrate_legacy_collection()
        if not COLLECTION_INDEX.exists():
            return [], _default_collection_groups()
        try:
            payload = json.loads(COLLECTION_INDEX.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("The saved-sticker collection file could not be read.") from exc
        if isinstance(payload, dict):
            entries = payload.get("items")
            groups = _normalize_collection_groups(payload.get("groups", []))
        else:
            entries = payload
            groups = _default_collection_groups()
        if not isinstance(entries, list):
            raise RuntimeError("The saved-sticker collection file has an invalid format.")
        valid = []
        group_ids = {group["id"] for group in groups}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item_id = str(entry.get("id", ""))
            fmt = str(entry.get("format", "")).lower()
            if re.fullmatch(r"[0-9a-f]{32}", item_id) and fmt in COLLECTION_FORMATS:
                normalized = dict(entry)
                group_id = str(normalized.get("groupId") or UNGROUPED_GROUP_ID)
                normalized["groupId"] = group_id if group_id in group_ids else UNGROUPED_GROUP_ID
                valid.append(normalized)
        return valid, groups


def _collection_read() -> list[dict]:
    return _collection_payload_read()[0]


def _collection_write(entries: list[dict], groups: list[dict] | None = None) -> None:
    if groups is None:
        _, groups = _collection_payload_read()
    groups = _normalize_collection_groups(groups)
    group_ids = {group["id"] for group in groups}
    normalized_entries = []
    for entry in entries:
        normalized = dict(entry)
        group_id = str(normalized.get("groupId") or UNGROUPED_GROUP_ID)
        normalized["groupId"] = group_id if group_id in group_ids else UNGROUPED_GROUP_ID
        normalized_entries.append(normalized)
    COLLECTION_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = COLLECTION_INDEX.with_name(COLLECTION_INDEX.name + ".tmp")
    try:
        temp_path.write_text(json.dumps({"version": 2, "groups": groups,
                                         "items": normalized_entries},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, COLLECTION_INDEX)
    except OSError as exc:
        raise RuntimeError("Could not update the saved-sticker collection on this PC.") from exc
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _collection_find(entries: list[dict], item_id: str) -> dict | None:
    return next((entry for entry in entries if entry.get("id") == item_id), None)


def _collection_asset_path(entry: dict) -> Path:
    item_id = str(entry.get("id", ""))
    fmt = str(entry.get("format", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{32}", item_id) or fmt not in COLLECTION_FORMATS:
        raise RuntimeError("The saved sticker is invalid.")
    return COLLECTION_ASSETS_DIR / f"{item_id}.{fmt}"


def _collection_thumb_path(item_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", str(item_id)):
        raise ValueError("The saved sticker ID is invalid.")
    return COLLECTION_THUMBS_DIR / f"{item_id}.png"


def _collection_public_item(entry: dict) -> dict:
    item_id = str(entry.get("id", ""))
    try:
        available = _collection_asset_path(entry).is_file()
    except RuntimeError:
        available = False
    result = {
        "id": item_id, "name": str(entry.get("name") or "Saved sticker"),
        "sourceTitle": str(entry.get("sourceTitle") or "Sticker pack"),
        "source": str(entry.get("source") or "Telegram"),
        "sourceSlug": str(entry.get("sourceSlug") or ""),
        "format": str(entry.get("format") or "webp"),
        "formatLabel": str(entry.get("formatLabel") or str(entry.get("format", "webp")).upper()),
        "kind": str(entry.get("kind") or "Static"),
        "emoji": str(entry.get("emoji") or "📎"),
        "groupId": str(entry.get("groupId") or UNGROUPED_GROUP_ID),
        "isAdult": bool(entry.get("isAdult")), "addedAt": str(entry.get("addedAt") or ""),
        "available": available, "thumbUrl": f"/api/collection/thumb/{item_id}",
        "largePreviewUrl": f"/api/collection/preview/{item_id}",
    }
    if result["kind"] == "Animated":
        result["previewUrl"] = result["largePreviewUrl"]
    return result


def _collection_public_data() -> tuple[list[dict], list[dict]]:
    with COLLECTION_LOCK:
        _cleanup_collection_undo_actions_locked()
        entries, groups = _collection_payload_read()
    group_names = {group["id"]: group["name"] for group in groups}
    items = []
    for entry in reversed(entries):
        item = _collection_public_item(entry)
        group_id = item["groupId"]
        if group_id not in group_names:
            group_id = UNGROUPED_GROUP_ID
        item["groupId"] = group_id
        item["groupName"] = group_names[group_id]
        items.append(item)
    return items, groups


def _collection_public_items() -> list[dict]:
    return _collection_public_data()[0]


def _create_collection_group(name: str) -> tuple[dict, list[dict]]:
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("Enter a name for the new group.")
    if len(name) > MAX_SAVED_GROUP_NAME:
        raise ValueError(f"Group names can be up to {MAX_SAVED_GROUP_NAME} characters.")
    if name.casefold() == "ungrouped":
        raise ValueError("Ungrouped is reserved for stickers without a folder.")
    with COLLECTION_LOCK:
        entries, groups = _collection_payload_read()
        if any(group["name"].casefold() == name.casefold() for group in groups):
            raise ValueError("A saved-sticker group with that name already exists.")
        if len(groups) - 1 >= MAX_SAVED_GROUPS:
            raise ValueError(f"You can create up to {MAX_SAVED_GROUPS} saved-sticker groups.")
        group = {"id": uuid.uuid4().hex, "name": name}
        groups.append(group)
        _collection_write(entries, groups)
        return group, groups


def _discard_collection_undo_action_locked(token: str, action: dict) -> None:
    if action.get("kind") == "remove":
        shutil.rmtree(COLLECTION_UNDO_DIR / token, ignore_errors=True)


def _cleanup_collection_undo_actions_locked(now: float | None = None) -> None:
    now = time.time() if now is None else now
    for token, action in list(COLLECTION_UNDO_ACTIONS.items()):
        if float(action.get("expiresAt", 0)) <= now:
            COLLECTION_UNDO_ACTIONS.pop(token, None)
            _discard_collection_undo_action_locked(token, action)
    active_tokens = set(COLLECTION_UNDO_ACTIONS)
    try:
        children = list(COLLECTION_UNDO_DIR.iterdir())
    except OSError:
        return
    for child in children:
        if (re.fullmatch(r"[0-9a-f]{32}", child.name) and child.name not in active_tokens
                and child.is_dir() and not child.is_symlink()):
            shutil.rmtree(child, ignore_errors=True)


def _register_collection_undo_action_locked(action: dict) -> str:
    _cleanup_collection_undo_actions_locked()
    token = uuid.uuid4().hex
    saved = dict(action)
    saved["expiresAt"] = time.time() + COLLECTION_UNDO_TTL_SECONDS
    COLLECTION_UNDO_ACTIONS[token] = saved
    while len(COLLECTION_UNDO_ACTIONS) > MAX_COLLECTION_UNDO_ACTIONS:
        oldest_token = next(iter(COLLECTION_UNDO_ACTIONS))
        oldest_action = COLLECTION_UNDO_ACTIONS.pop(oldest_token)
        _discard_collection_undo_action_locked(oldest_token, oldest_action)
    return token


def _collection_sticker_file_paths(entry: dict) -> list[tuple[str, Path]]:
    item_id = str(entry.get("id", ""))
    return [
        ("assets", _collection_asset_path(entry)),
        ("thumbs", _collection_thumb_path(item_id)),
        ("previews", COLLECTION_PREVIEWS_DIR / f"{item_id}.gif"),
    ]


def _collection_path_for_undo_file(relative_path: str) -> Path:
    relative = str(relative_path).replace("\\", "/")
    folder, separator, filename = relative.partition("/")
    directories = {
        "assets": COLLECTION_ASSETS_DIR,
        "thumbs": COLLECTION_THUMBS_DIR,
        "previews": COLLECTION_PREVIEWS_DIR,
    }
    if (not separator or folder not in directories or not filename or "/" in filename
            or filename in (".", "..")):
        raise RuntimeError("The saved-sticker undo data is invalid.")
    return directories[folder] / filename


def _move_collection_files_to_undo(entry: dict, token: str) -> list[str]:
    moved: list[str] = []
    undo_dir = COLLECTION_UNDO_DIR / token
    try:
        for folder, source in _collection_sticker_file_paths(entry):
            if not source.is_file():
                continue
            relative = f"{folder}/{source.name}"
            staged = undo_dir / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, staged)
            moved.append(relative)
    except OSError as exc:
        for relative in reversed(moved):
            try:
                staged = undo_dir / relative
                original = _collection_path_for_undo_file(relative)
                original.parent.mkdir(parents=True, exist_ok=True)
                if staged.exists():
                    os.replace(staged, original)
            except OSError:
                pass
        shutil.rmtree(undo_dir, ignore_errors=True)
        raise RuntimeError("Could not safely remove the saved sticker on this PC.") from exc
    return moved


def _restore_collection_files_from_undo(token: str, relative_paths: list[str]) -> list[tuple[Path, Path]]:
    undo_dir = COLLECTION_UNDO_DIR / token
    pairs: list[tuple[Path, Path]] = []
    for relative in relative_paths:
        staged = undo_dir / relative
        original = _collection_path_for_undo_file(relative)
        if staged.exists():
            if original.exists():
                raise RuntimeError("A saved-sticker file already exists, so this removal cannot be undone safely.")
            pairs.append((staged, original))
    restored: list[tuple[Path, Path]] = []
    try:
        for staged, original in pairs:
            original.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, original)
            restored.append((staged, original))
    except OSError as exc:
        _move_restored_collection_files_back(restored)
        raise RuntimeError("Could not restore the saved sticker files on this PC.") from exc
    return restored


def _move_restored_collection_files_back(restored: list[tuple[Path, Path]]) -> None:
    for staged, original in reversed(restored):
        try:
            staged.parent.mkdir(parents=True, exist_ok=True)
            if original.exists():
                os.replace(original, staged)
        except OSError:
            pass


def _delete_collection_sticker_files(entry: dict) -> None:
    for _folder, path in _collection_sticker_file_paths(entry):
        try:
            path.unlink()
        except OSError:
            pass


def _move_collection_stickers(item_ids: list[str], group_id: str, *, record_undo: bool = False) -> int | tuple[int, str | None]:
    if not isinstance(item_ids, list) or not item_ids or len(item_ids) > MAX_SAVED_STICKERS:
        raise ValueError("Select saved stickers to move.")
    if any(not isinstance(item_id, str) or not re.fullmatch(r"[0-9a-f]{32}", item_id)
           for item_id in item_ids):
        raise ValueError("The saved-sticker selection is invalid.")
    if len(set(item_ids)) != len(item_ids):
        raise ValueError("The saved-sticker selection contains duplicates.")
    group_id = str(group_id or "")
    with COLLECTION_LOCK:
        entries, groups = _collection_payload_read()
        if group_id != UNGROUPED_GROUP_ID and not COLLECTION_GROUP_ID_RE.fullmatch(group_id):
            raise ValueError("Choose a valid saved-sticker group.")
        if not any(group["id"] == group_id for group in groups):
            raise ValueError("That saved-sticker group no longer exists. Refresh My saves.")
        selected = set(item_ids)
        entry_ids = {entry["id"] for entry in entries}
        if not selected.issubset(entry_ids):
            raise ValueError("One or more stickers are no longer saved. Refresh My saves.")
        moved = 0
        previous_groups = {}
        for entry in entries:
            if entry["id"] in selected and entry.get("groupId") != group_id:
                previous_groups[entry["id"]] = entry.get("groupId") or UNGROUPED_GROUP_ID
                entry["groupId"] = group_id
                moved += 1
        undo_token = None
        if moved:
            _collection_write(entries, groups)
            if record_undo:
                undo_token = _register_collection_undo_action_locked({
                    "kind": "move", "toGroup": group_id, "previousGroups": previous_groups,
                })
        return (moved, undo_token) if record_undo else moved


def _undo_collection_action(token: str) -> dict:
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("The undo action is invalid.")
    with COLLECTION_LOCK:
        _cleanup_collection_undo_actions_locked()
        action = COLLECTION_UNDO_ACTIONS.get(token)
        if action is None:
            raise ValueError("This undo action has expired or was already used.")
        kind = action.get("kind")
        entries, groups = _collection_payload_read()
        if kind == "move":
            previous_groups = action.get("previousGroups")
            if not isinstance(previous_groups, dict) or not previous_groups:
                raise RuntimeError("This group move cannot be undone safely.")
            by_id = {entry["id"]: entry for entry in entries}
            if any(item_id not in by_id for item_id in previous_groups):
                raise RuntimeError("One or more stickers have since been removed; the move cannot be undone safely.")
            to_group = action.get("toGroup")
            if any(by_id[item_id].get("groupId") != to_group for item_id in previous_groups):
                raise RuntimeError("These stickers have changed groups since the move; undo would overwrite a newer change.")
            group_ids = {group["id"] for group in groups}
            fallback_count = 0
            for item_id, previous_group in previous_groups.items():
                destination = previous_group if previous_group in group_ids else UNGROUPED_GROUP_ID
                if destination != previous_group:
                    fallback_count += 1
                by_id[item_id]["groupId"] = destination
            _collection_write(entries, groups)
            COLLECTION_UNDO_ACTIONS.pop(token, None)
            return {"ok": True, "kind": "move", "restored": len(previous_groups),
                    "fallbackUngrouped": fallback_count, "count": len(entries)}
        if kind == "remove":
            entry = action.get("entry")
            if not isinstance(entry, dict) or _collection_find(entries, entry.get("id", "")):
                raise RuntimeError("This sticker is already in My saves or its undo data is invalid.")
            entry = dict(entry)
            group_ids = {group["id"] for group in groups}
            if entry.get("groupId") not in group_ids:
                entry["groupId"] = UNGROUPED_GROUP_ID
            files = action.get("files") if isinstance(action.get("files"), list) else []
            restored_files = _restore_collection_files_from_undo(token, files)
            try:
                _collection_write(entries + [entry], groups)
            except Exception:
                _move_restored_collection_files_back(restored_files)
                raise
            COLLECTION_UNDO_ACTIONS.pop(token, None)
            shutil.rmtree(COLLECTION_UNDO_DIR / token, ignore_errors=True)
            return {"ok": True, "kind": "remove", "id": entry["id"],
                    "groupId": entry["groupId"], "count": len(entries) + 1}
        raise RuntimeError("This undo action is not supported.")


def _delete_collection_group(group_id: str) -> tuple[str, int, list[dict]]:
    group_id = str(group_id or "")
    if group_id == UNGROUPED_GROUP_ID:
        raise ValueError("The Ungrouped group cannot be deleted.")
    if not COLLECTION_GROUP_ID_RE.fullmatch(group_id):
        raise ValueError("Choose a valid saved-sticker group.")
    with COLLECTION_LOCK:
        entries, groups = _collection_payload_read()
        group = next((group for group in groups if group["id"] == group_id), None)
        if group is None:
            raise ValueError("That saved-sticker group no longer exists. Refresh My saves.")
        moved = 0
        for entry in entries:
            if entry.get("groupId") == group_id:
                entry["groupId"] = UNGROUPED_GROUP_ID
                moved += 1
        groups = [item for item in groups if item["id"] != group_id]
        _collection_write(entries, groups)
        return group["name"], moved, groups


def _save_collection_sticker(job_id: str, index: int, client_id: str | None = None) -> dict:
    with STATE_LOCK:
        job = JOBS.get(job_id)
        if not _job_access_allowed(job, client_id):
            raise ValueError("Fetch a sticker pack before saving stickers.")
        if job.get("status") != "ready":
            raise ValueError("Fetch a sticker pack before saving stickers.")
        descriptor = next((item for item in job.get("sourceItems", [])
                           if item.get("index") == index), None)
        if descriptor is None:
            raise ValueError("That sticker is not in the current pack.")
        set_name = str(job.get("setName", ""))
        title = str(job.get("title", set_name))
        source = str(job.get("source") or "Telegram")
        source_slug = str(job.get("sourceSlug") or "")
        is_adult = bool(job.get("isAdult"))
        descriptor = dict(descriptor)
    try:
        item = _raw_item(set_name, descriptor, title)
    except (OSError, RuntimeError, KeyError, ValueError) as exc:
        raise RuntimeError("Could not read this sticker from the local pack cache. Fetch the pack again and retry.") from exc
    raw = item.get("bytes")
    if not isinstance(raw, bytes) or not raw:
        raise RuntimeError("The sticker file is empty and could not be saved.")
    fmt = str(descriptor.get("format") or item.get("format") or "").lower()
    if fmt not in COLLECTION_FORMATS:
        raise ValueError("This sticker format cannot be saved to the collection.")
    content_hash = hashlib.sha256(raw).hexdigest()
    try:
        thumb, _ = _get_or_create_thumbnail(item)
    except Exception:                                                 # noqa: BLE001
        thumb = None
    with COLLECTION_LOCK:
        entries = _collection_read()
        duplicate = next((entry for entry in entries
                          if entry.get("contentHash") == content_hash
                          and entry.get("format") == fmt), None)
        if duplicate:
            if is_adult and not duplicate.get("isAdult"):
                duplicate["isAdult"] = True
                _collection_write(entries)
            return {"item": _collection_public_item(duplicate),
                    "alreadySaved": True, "count": len(entries)}
        if len(entries) >= MAX_SAVED_STICKERS:
            raise ValueError(f"The collection is full ({MAX_SAVED_STICKERS} stickers). Remove some saved stickers first.")
        item_id = uuid.uuid4().hex
        entry = {
            "id": item_id, "name": str(descriptor.get("name") or f"{index + 1:02d}.{fmt}"),
            "sourceTitle": title, "source": source, "sourceSlug": source_slug,
            "format": fmt, "formatLabel": str(descriptor.get("formatLabel") or fmt.upper()),
            "kind": str(descriptor.get("kind") or "Static"),
            "emoji": str(descriptor.get("emoji") or "📎"), "isAdult": is_adult,
            "groupId": UNGROUPED_GROUP_ID,
            "contentHash": content_hash, "addedAt": datetime.now().isoformat(timespec="seconds"),
        }
        COLLECTION_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        COLLECTION_THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        asset_path = _collection_asset_path(entry)
        thumb_path = _collection_thumb_path(item_id)
        try:
            asset_path.write_bytes(raw)
            if thumb:
                thumb_path.write_bytes(thumb)
            entries.append(entry)
            _collection_write(entries)
        except Exception as exc:                                      # noqa: BLE001
            for path in (asset_path, thumb_path):
                try:
                    path.unlink()
                except OSError:
                    pass
            raise RuntimeError("Could not write the saved sticker to this PC.") from exc
        return {"item": _collection_public_item(entry),
                "alreadySaved": False, "count": len(entries)}


def _remove_collection_sticker(item_id: str, *, record_undo: bool = False) -> int | tuple[int, str]:
    if not re.fullmatch(r"[0-9a-f]{32}", str(item_id)):
        raise ValueError("The saved-sticker ID is invalid.")
    with COLLECTION_LOCK:
        entries, groups = _collection_payload_read()
        entry = _collection_find(entries, item_id)
        if entry is None:
            raise ValueError("That sticker is not in the collection.")
        with STATE_LOCK:
            for active in JOBS.values():
                if active.get("status") in ("queued", "running") and active.get("source") == "Collection":
                    if any(d.get("collectionId") == item_id for d in active.get("sourceItems", [])):
                        raise RuntimeError("This saved sticker is being used to create a pack. Try removing it after conversion finishes.")
        updated = [item for item in entries if item.get("id") != item_id]
        if not record_undo:
            _collection_write(updated, groups)
            _delete_collection_sticker_files(entry)
            return len(updated)

        undo_token = _register_collection_undo_action_locked({
            "kind": "remove", "entry": dict(entry), "files": [],
        })
        index_removed = False
        files = []
        try:
            _collection_write(updated, groups)
            index_removed = True
            files = _move_collection_files_to_undo(entry, undo_token)
            COLLECTION_UNDO_ACTIONS[undo_token]["files"] = files
        except Exception:
            if index_removed:
                try:
                    _collection_write(entries, groups)
                except Exception:
                    pass
            if files:
                try:
                    _restore_collection_files_from_undo(undo_token, files)
                except Exception:
                    pass
            COLLECTION_UNDO_ACTIONS.pop(undo_token, None)
            shutil.rmtree(COLLECTION_UNDO_DIR / undo_token, ignore_errors=True)
            raise
        return len(updated), undo_token


def _start_collection_conversion(selected_ids: list[str], title: str, client_id: str | None = None) -> str:
    if not selected_ids or len(selected_ids) > MAX_SAVED_STICKERS:
        raise ValueError("Select saved stickers first.")
    if len(set(selected_ids)) != len(selected_ids):
        raise ValueError("The saved-sticker selection contains duplicates.")
    if not title or len(title) > 60:
        raise ValueError("Enter a pack name between 1 and 60 characters.")
    if any(not re.fullmatch(r"[0-9a-f]{32}", str(item_id)) for item_id in selected_ids):
        raise ValueError("The saved-sticker selection is invalid.")
    with COLLECTION_LOCK:
        entries = _collection_read()
        entry_map = {entry["id"]: entry for entry in entries}
        if any(item_id not in entry_map for item_id in selected_ids):
            raise ValueError("One or more selected stickers are no longer saved. Refresh the collection.")
        selected_entries = [entry_map[item_id] for item_id in selected_ids]
        descriptors = []
        for index, entry in enumerate(selected_entries):
            if not _collection_asset_path(entry).is_file():
                raise ValueError(f"Saved sticker {entry.get('name') or index + 1} is missing. Remove it from the collection and save it again.")
            descriptors.append({
                "index": index, "sourceIndex": index + 1,
                "format": entry["format"], "formatLabel": entry.get("formatLabel") or entry["format"].upper(),
                "kind": entry.get("kind") or "Static", "emoji": entry.get("emoji") or "📎",
                "fileUniqueId": entry["id"], "name": entry.get("name") or f"{index + 1:02d}.{entry['format']}",
                "collectionId": entry["id"], "isAdult": bool(entry.get("isAdult")),
            })
        static_count = sum(1 for item in descriptors if item["kind"] != "Animated")
        animated_count = len(descriptors) - static_count
        if static_count and animated_count:
            raise ValueError("Choose only static or only animated saved stickers. WhatsApp cannot mix types, so one create action makes one pack.")
        if max(static_count, animated_count) < 3:
            raise ValueError("Select at least 3 saved stickers of one type to create a WhatsApp pack.")
        with STATE_LOCK:
            if _client_busy(client_id):
                raise RuntimeError("A job is already running for this browser. Wait for it to finish first.")
            if _active_job_count() >= MAX_ACTIVE_JOBS_TOTAL:
                raise RuntimeError("The converter is busy with other packs. Please try again in a moment.")
            _trim_jobs()
            job_id = _new_job_id()
            set_name = f"saved_collection_{job_id}"
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            job_dir = RUNS_DIR / f"{stamp}_{convert._slug(title)}_{job_id}"
            job_dir.mkdir(parents=True, exist_ok=True)
            JOBS[job_id] = {
                "id": job_id, "status": "queued", "phase": "Starting",
                "message": f"Creating {len(descriptors)} saved stickers…", "done": 0,
                "total": len(descriptors), "packs": [], "packFiles": [],
                "previewEntries": [], "source": "Collection", "sourceSlug": "",
                "clientId": _client_bucket(client_id),
                "sourceItems": descriptors, "setName": set_name, "title": title,
                "isAdult": any(item.get("isAdult") for item in selected_entries),
                "activityLog": [], "_activitySeq": 0, "outputDir": str(job_dir),
                "created": time.time(),
            }
            _append_activity_log(JOBS[job_id], "Queued", "Preparing your saved stickers…")
            _set_current_job(client_id, job_id)
    threading.Thread(target=_run_conversion, args=(job_id, [item["index"] for item in descriptors], "selected"),
                     name=f"saved-sticker-convert-{job_id}", daemon=True).start()
    return job_id



# ---------------------------------------------------------------------------
# Per-browser identity helpers (X-Client-Id)
# ---------------------------------------------------------------------------

def _client_id(value):
    """Validate a client-supplied id; returns None for missing/invalid values."""
    if not value:
        return None
    value = str(value).strip()
    return value if CLIENT_ID_RE.fullmatch(value) else None


def _client_bucket(client_id):
    return client_id or CLIENT_BUCKET_SHARED


def _job_access_allowed(job, client_id):
    """Jobs created before per-browser scoping (no clientId) stay accessible to everyone."""
    if not job:
        return False
    owner = job.get("clientId")
    if not owner:
        return True
    return owner == _client_bucket(client_id)


def _require_job_access(job, client_id):
    if not _job_access_allowed(job, client_id):
        raise HTTPException(status_code=404, detail="This app session has expired. Fetch the pack again.")
    return job


def _active_job_id(client_id):
    return ACTIVE_JOB_IDS.get(_client_bucket(client_id))


def _latest_job_id(client_id):
    return LATEST_JOB_IDS.get(_client_bucket(client_id))


def _set_current_job(client_id, job_id):
    bucket = _client_bucket(client_id)
    ACTIVE_JOB_IDS[bucket] = job_id
    LATEST_JOB_IDS[bucket] = job_id


def _clear_active_job(job_id):
    """Clear the pointer in every bucket that references this job (usually one)."""
    for key, value in list(ACTIVE_JOB_IDS.items()):
        if value == job_id:
            ACTIVE_JOB_IDS[key] = None


def _client_busy(client_id):
    current = _active_job_id(client_id)
    if not current:
        return False
    job = JOBS.get(current)
    return bool(job) and job.get("status") in ("queued", "running")


def _active_job_count():
    return sum(1 for job in JOBS.values() if job.get("status") in ("queued", "running"))


def _new_job_id() -> str:
    return uuid.uuid4().hex[:12]


def _trim_jobs() -> None:
    if len(JOBS) < MAX_JOBS:
        return
    old = [key for key, value in JOBS.items()
           if value.get("status") not in ("queued", "running")]
    for key in old[:max(0, len(JOBS) - MAX_JOBS + 1)]:
        JOBS.pop(key, None)


def _run_fetch(job_id: str, set_name: str, token: str, remember: bool) -> None:
    jobs = min(os.cpu_count() or 1, 8)
    try:
        _update(job_id, status="running", phase="Connecting", message="Contacting Telegram…",
                done=0, total=0)

        def download_progress(event: dict) -> None:
            _update(job_id, phase="Downloading",
                    message=f"Downloading stickers · {event['done']} of {event['total']}",
                    done=event["done"], total=event["total"])

        missing = convert.preflight(fetching=True, suffixes={".tgs", ".webm"})
        if missing:
            packages = ", ".join(pkg for pkg, _why in missing)
            raise RuntimeError(f"Missing required package(s): {packages}. Run python install.py, then restart the app.")
        items = convert.fetch_pack(set_name, token, cache_dir=_cache_folder(set_name),
                                   verbose=False, jobs=jobs, progress_callback=download_progress)
        if not items:
            raise RuntimeError("Telegram returned an empty sticker set.")
        title = (items[0].get("title") or set_name).strip() or set_name
        descriptors = []
        labels = {"tgs": "Animated TGS", "webm": "Video WebM", "webp": "Static WebP"}
        for i, item in enumerate(items):
            fmt = item["format"]
            descriptors.append({
                "index": i,
                "sourceIndex": i + 1,
                "format": fmt,
                "formatLabel": labels.get(fmt, fmt.upper()),
                "kind": "Animated" if fmt in ("tgs", "webm") else "Static",
                "emoji": item.get("emoji") or "📎",
                "fileUniqueId": str(item.get("file_unique_id") or ""),
                "name": str(item.get("name") or f"{i + 1:02d}.{fmt}"),
            })

        thumbnails: list[bytes | None] = [None] * len(items)
        public_stickers = _job_stickers_for_picker(
            job_id, descriptors, thumbnails, preparing=True)
        with STATE_LOCK:
            job = JOBS.get(job_id)
            if job is not None:
                job.update(sourceItems=descriptors, thumbnails=thumbnails,
                           stickers=public_stickers, setName=set_name, title=title,
                           source="Telegram")
        _update(job_id, phase="Preparing previews",
                message=f"Preparing sticker previews · 0 of {len(items)}",
                done=0, total=len(items))

        def thumbnail_progress(event: dict) -> None:
            index = event["index"]
            thumbnail = event["thumbnail"]
            with STATE_LOCK:
                job = JOBS.get(job_id)
                if job is not None:
                    job_thumbnails = job.get("thumbnails", [])
                    job_stickers = job.get("stickers", [])
                    if index < len(job_thumbnails):
                        job_thumbnails[index] = thumbnail
                    if index < len(job_stickers):
                        job_stickers[index]["previewReady"] = True
                        job_stickers[index]["previewAvailable"] = bool(thumbnail)
            reused = f" · {event['cacheHits']} cached" if event["cacheHits"] else ""
            _update(job_id, phase="Preparing previews",
                    message=(f"Preparing sticker previews · {event['done']} of "
                             f"{event['total']}{reused}"),
                    done=event["done"], total=event["total"])

        thumbnails, thumbnail_cache_hits = _prepare_thumbnails(items, thumbnail_progress)
        public_stickers = _job_stickers_for_picker(job_id, descriptors, thumbnails)
        remember_warning = ""
        if remember:
            try:
                _save_token(token)
            except Exception as exc:                                # noqa: BLE001
                remember_warning = f"The pack was fetched, but the token could not be saved securely: {exc}"

        with STATE_LOCK:
            job = JOBS.get(job_id)
            if job is not None:
                job.update(sourceItems=descriptors, thumbnails=thumbnails,
                           stickers=public_stickers, setName=set_name, title=title,
                           thumbnailCacheHits=thumbnail_cache_hits,
                           rememberWarning=remember_warning)
        msg = f"{len(items)} stickers ready. Select some or convert the full pack."
        if thumbnail_cache_hits:
            msg += f" Reused {thumbnail_cache_hits} cached poster thumbnails."
        if remember_warning:
            msg += " Token preference was not fully applied."
        _update(job_id, status="ready", phase="Choose stickers", message=msg,
                done=len(items), total=len(items), title=title, stickers=public_stickers)
    except Exception as exc:                                          # noqa: BLE001
        safe = str(exc)
        if token:
            safe = safe.replace(token, "[redacted]")
        _update(job_id, status="failed", phase="Stopped", message="Could not fetch the pack.",
                error=safe[:900])
    finally:
        token = ""
        with STATE_LOCK:
            _clear_active_job(job_id)


def _run_fullyst_fetch(job_id: str, slug: str) -> None:
    try:
        _update(job_id, status="running", phase="Connecting to Fullyst",
                message="Loading pack details from Fullyst…", done=0, total=0)

        def download_progress(event: dict) -> None:
            event_type = event.get("type")
            index, total = event.get("index", 0), event.get("total", 0)
            if event_type == "retry":
                _update(job_id, phase="Retrying Fullyst download",
                        message=(f"Sticker {index} of {total}: temporary error · "
                                 f"retry {event['attempt']} of {max(1, event['maxAttempts'] - 1)} "
                                 f"({event.get('error', 'network error')})"))
            elif event_type == "failed":
                _update(job_id, phase="Sticker download skipped",
                        message=f"Sticker {index} of {total} unavailable: {event.get('error', 'download failed')}. Continuing.",
                        done=event["done"], total=total)
            else:
                origin = "from local cache" if event.get("fromCache") else "downloaded"
                _update(job_id, phase="Downloading from Fullyst",
                        message=f"Sticker {index} of {total} {origin} · {event['done']} of {total} complete.",
                        done=event["done"], total=total)

        set_name, title, items, failures = _fullyst_pack_items(
            slug, progress_callback=download_progress)
        source_count = len(items) + len(failures)
        if not items:
            if failures:
                first = failures[0]
                raise RuntimeError(
                    f"None of the {source_count} stickers could be downloaded. "
                    f"First failure: sticker {first['sourceIndex']} · {first['error']}")
            raise RuntimeError("Fullyst returned an empty sticker set.")
        descriptors = []
        labels = {"webm": "Video WebM", "webp": "Static WebP"}
        for i, item in enumerate(items):
            source_index = int(item.get("sourceIndex", i + 1))
            fmt = item["format"]
            kind = item["kind"]
            label = "Animated WebP" if fmt == "webp" and kind == "Animated" else labels.get(fmt, fmt.upper())
            descriptors.append({
                "index": i, "sourceIndex": source_index, "format": fmt,
                "formatLabel": label, "kind": kind, "emoji": item.get("emoji") or "📎",
                "fileUniqueId": str(item.get("file_unique_id") or ""),
                "name": str(item.get("name") or f"{source_index:02d}.{fmt}"),
            })

        thumbnails: list[bytes | None] = [None] * len(items)
        public_stickers = _job_stickers_for_picker(
            job_id, descriptors, thumbnails, preparing=True)
        with STATE_LOCK:
            job = JOBS.get(job_id)
            if job is not None:
                job.update(sourceItems=descriptors, thumbnails=thumbnails,
                           stickers=public_stickers, setName=set_name, title=title,
                           source="Fullyst", sourceSlug=slug,
                           failedItems=failures, failedCount=len(failures),
                           sourceItemCount=source_count)
        _update(job_id, phase="Preparing previews",
                message=f"Preparing sticker previews · 0 of {len(items)}",
                done=0, total=len(items))

        def thumbnail_progress(event: dict) -> None:
            index = event["index"]
            thumbnail = event["thumbnail"]
            with STATE_LOCK:
                job = JOBS.get(job_id)
                if job is not None:
                    job_thumbnails = job.get("thumbnails", [])
                    job_stickers = job.get("stickers", [])
                    if index < len(job_thumbnails):
                        job_thumbnails[index] = thumbnail
                    if index < len(job_stickers):
                        job_stickers[index]["previewReady"] = True
                        job_stickers[index]["previewAvailable"] = bool(thumbnail)
            reused = f" · {event['cacheHits']} cached" if event["cacheHits"] else ""
            _update(job_id, phase="Preparing previews",
                    message=(f"Preparing sticker previews · {event['done']} of "
                             f"{event['total']}{reused}"),
                    done=event["done"], total=event["total"])

        thumbnails, thumbnail_cache_hits = _prepare_thumbnails(items, thumbnail_progress)
        public_stickers = _job_stickers_for_picker(job_id, descriptors, thumbnails)

        with STATE_LOCK:
            job = JOBS.get(job_id)
            if job is not None:
                is_adult = bool(job.get("isAdult") or _fullyst_looks_adult(slug, title))
                job.update(sourceItems=descriptors, thumbnails=thumbnails,
                           stickers=public_stickers, setName=set_name, title=title,
                           source="Fullyst", sourceSlug=slug,
                           thumbnailCacheHits=thumbnail_cache_hits,
                           isAdult=is_adult, failedItems=failures,
                           failedCount=len(failures), sourceItemCount=source_count)
        message = f"{len(items)} stickers ready from Fullyst. Select some or convert the full pack."
        if failures:
            message = (f"{len(items)} of {source_count} stickers ready from Fullyst; "
                       f"{len(failures)} skipped. See the live activity log for details. "
                       "Fetch this pack again later to retry skipped stickers; successful downloads are reused from cache.")
        if thumbnail_cache_hits:
            message += f" Reused {thumbnail_cache_hits} cached poster thumbnails."
        _update(job_id, status="ready", phase="Choose stickers", message=message,
                done=source_count, total=source_count, title=title, stickers=public_stickers)
    except Exception as exc:                                          # noqa: BLE001
        _update(job_id, status="failed", phase="Stopped", message="Could not fetch the Fullyst pack.",
                error=str(exc)[:900])
    finally:
        with STATE_LOCK:
            _clear_active_job(job_id)


def _raw_item(set_name: str, descriptor: dict, title: str) -> dict:
    collection_id = descriptor.get("collectionId")
    if collection_id:
        with COLLECTION_LOCK:
            saved = _collection_find(_collection_read(), str(collection_id))
        if saved is None:
            raise RuntimeError("A saved sticker is no longer in the collection.")
        path = _collection_asset_path(saved)
        unique_id = str(descriptor.get("fileUniqueId") or collection_id)
        if not path.is_file():
            raise RuntimeError("A saved sticker file is missing from this PC.")
        return {"title": title, "name": descriptor.get("name") or saved.get("name"),
                "format": descriptor.get("format") or saved["format"],
                "emoji": descriptor.get("emoji") or saved.get("emoji") or "📎",
                "file_unique_id": unique_id, "bytes": path.read_bytes()}
    raw_dir = _cache_folder(set_name)
    unique_id = descriptor["fileUniqueId"]
    if not unique_id or "/" in unique_id or "\\" in unique_id:
        raise RuntimeError("A cached Telegram sticker file is missing. Fetch the pack again.")
    path = raw_dir / f"{descriptor['sourceIndex']:03d}_{unique_id}.{descriptor['format']}"
    if not path.is_file():
        raise RuntimeError("A cached Telegram sticker file is missing. Fetch the pack again.")
    return {"title": title, "name": descriptor["name"], "format": descriptor["format"],
            "emoji": descriptor["emoji"], "file_unique_id": unique_id,
            "bytes": path.read_bytes()}


def _run_conversion(job_id: str, selected_indexes: list[int], mode: str) -> None:
    started = time.perf_counter()
    jobs = min(os.cpu_count() or 1, 8)
    job_dir: Path | None = None
    cache_root: Path | None = None
    source = ""
    try:
        with STATE_LOCK:
            job = JOBS[job_id]
            set_name = job["setName"]
            title = job["title"]
            source = job.get("source", "")
            descriptors = list(job["sourceItems"])
            job_dir = Path(job["outputDir"])
        selected = [d for d in descriptors if d["index"] in set(selected_indexes)]
        items = [_raw_item(set_name, descriptor, title) for descriptor in selected]
        if not items:
            raise RuntimeError("No stickers were selected.")
        _update(job_id, status="running", phase="Converting",
                message=f"Preparing {len(items)} stickers…", done=0, total=len(items))
        cache_root = _cache_root(set_name)
        work = cache_root / "work"
        report = convert.convert_pack(
            items, title, work, publisher="wa-sticker-bridge", verbose=False,
            static_format="webp", jobs=jobs, webm_method=DEFAULT_WEBM_METHOD,
            process_start_method="spawn",
            progress_callback=lambda event: _update(
                job_id, phase="Converting",
                message=f"Converting stickers · {event['done']} of {event['total']}",
                done=event["done"], total=event["total"]))

        packs, pack_files, preview_entries = [], [], []
        for pack_index, pack in enumerate(report["pack"].get("wastickers", [])):
            source_path = Path(pack["file"])
            output_path = job_dir / source_path.name
            shutil.copy2(source_path, output_path)
            entries = [str(entry["file"]) for entry in pack.get("entries", [])]
            preview_entries.append(entries)
            packs.append({
                "name": pack.get("name") or output_path.stem,
                "fileName": output_path.name,
                "kind": pack["kind"],
                "count": pack["count"],
                "sizeLabel": f"{output_path.stat().st_size / (1024 * 1024):.1f} MB",
                "downloadUrl": f"/download/{job_id}/{pack_index}",
                "previewUrls": [f"/api/job/{job_id}/output/{pack_index}/{i}"
                                for i in range(len(entries))],
            })
            pack_files.append({"name": output_path.name, "path": str(output_path)})

        valid = all(v.get("pass") for v in report["validation"])
        lint_ok = all(p.get("lint", {}).get("ok")
                      for p in report["pack"].get("wastickers", []))
        loose_count = len(report["pack"].get("loose", []))
        # Keep only importable archives in the visible run folder; source and converted
        # Telegram/Fullyst caches stay available for safe matching-source repeat conversions.
        # A saved-collection build has its inputs in the durable collection, so its unique
        # per-job cache can be discarded after the output has been copied.
        if source == "Collection":
            shutil.rmtree(cache_root, ignore_errors=True)
        else:
            for child in cache_root.iterdir():
                if child.name in {"raw", "work"}:
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try:
                        child.unlink()
                    except OSError:
                        pass

        if not packs:
            if job_dir is not None:
                shutil.rmtree(job_dir, ignore_errors=True)
            message = "No WhatsApp pack could be made. Each pack needs at least 3 stickers of the same type."
        elif not valid or not lint_ok:
            message = "A pack was created, but validation found a problem. Please try again."
        else:
            message = f"{len(packs)} WhatsApp pack(s) ready."
            if loose_count:
                message += f" {loose_count} sticker(s) were left out because they could not form a pack of 3."
        _update(job_id, status="done", phase="Complete", message=message,
                done=len(items), total=len(items), packs=packs, packFiles=pack_files,
                previewEntries=preview_entries,
                elapsedSeconds=round(time.perf_counter() - started, 1))
    except Exception as exc:                                          # noqa: BLE001
        if source == "Collection" and cache_root is not None:
            shutil.rmtree(cache_root, ignore_errors=True)
        if job_dir is not None:
            shutil.rmtree(job_dir, ignore_errors=True)
        _update(job_id, status="failed", phase="Stopped", message="Could not create the pack.",
                error=str(exc)[:900])
    finally:
        with STATE_LOCK:
            _clear_active_job(job_id)



# ---------------------------------------------------------------------------
# FastAPI Application & Endpoints
# ---------------------------------------------------------------------------

web_app = FastAPI(
    title="Telegram Sticker Converter API",
    description="Backend API running on Modal.com to convert Telegram stickers to WhatsApp .wastickers packs",
    version="2.0.0",
)

# Vercel deployments call this API cross-origin from the browser.
# Note: allow_origins=["*"] cannot be combined with allow_credentials=True
# (Starlette rejects it, and browsers refuse credentialed wildcard requests
# anyway). This API serves public sticker tools and uses no cookies, so
# credentials stay disabled. Optionally restrict origins via the
# MODAL_ALLOWED_ORIGINS env var (comma-separated).
_allowed_origins = [o.strip() for o in os.environ.get("MODAL_ALLOWED_ORIGINS", "*").split(",") if o.strip()] or ["*"]
web_app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@web_app.get("/")
def api_root():
    return {
        "name": "Telegram to WhatsApp Sticker Converter API",
        "status": "online",
        "platform": "modal.com",
        "version": "2.0.0",
        "docs": "/docs",
        "endpoints": {
            "config": "/api/config",
            "latest": "/api/latest",
            "fetch": "/api/fetch",
            "convert": "/api/convert",
            "job": "/api/job/{job_id}",
            "job_thumb": "/api/job/{job_id}/thumb/{index}",
            "job_preview": "/api/job/{job_id}/preview/{index}",
            "job_output": "/api/job/{job_id}/output/{pack_index}/{sticker_index}",
            "download": "/download/{job_id}/{index}",
            "fullyst_search": "/api/fullyst/search?q={query}&page={page}",
            "fullyst_fetch": "/api/fullyst/fetch",
            "fullyst_preview": "/api/fullyst/animated-preview?slug={slug}",
            "collection": "/api/collection"
        }
    }


@web_app.get("/api/config")
def api_config():
    return {
        "status": "online",
        "platform": "modal.com",
        "demo": False,
        "tokenConfigured": bool(os.environ.get("TG_BOT_TOKEN")),
        "tokenSaved": False,
        "rememberSupported": False,
    }


@web_app.get("/api/latest")
def api_latest(x_client_id: str | None = Header(default=None)):
    cid = _client_id(x_client_id)
    with STATE_LOCK:
        return {"id": _latest_job_id(cid)}


@web_app.post("/api/token/forget")
def api_token_forget():
    return {"ok": True}


@web_app.post("/api/fetch", status_code=202)
async def api_fetch(request: FastApiRequest, x_client_id: str | None = Header(default=None)):
    cid = _client_id(x_client_id)
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("Payload must be a JSON object.")
        link = str(data.get("pack_link", "")).strip()
        if not link:
            raise ValueError("Enter a Telegram sticker pack link or name.")
        set_name = convert.parse_set_name(link)
        token = str(data.get("token", "")).strip() or os.environ.get("TG_BOT_TOKEN", "")
        if not token:
            raise ValueError("Enter your Telegram Bot token from @BotFather.")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    with STATE_LOCK:
        if _client_busy(cid):
            raise HTTPException(status_code=409, detail="A conversion or fetch job is already running for this browser. Please wait.")
        if _active_job_count() >= MAX_ACTIVE_JOBS_TOTAL:
            raise HTTPException(status_code=409, detail="The converter is busy with other packs. Please try again in a moment.")
        _trim_jobs()
        job_id = _new_job_id()
        JOBS[job_id] = {
            "id": job_id, "status": "queued", "phase": "Starting",
            "message": "Preparing to fetch stickers…", "done": 0,
            "total": 0, "packs": [], "packFiles": [],
            "activityLog": [], "_activitySeq": 0,
            "outputDir": None, "created": time.time(),
            "clientId": _client_bucket(cid),
        }
        _append_activity_log(JOBS[job_id], "Queued", "Preparing to fetch stickers…")
        _set_current_job(cid, job_id)

    threading.Thread(target=_run_fetch, args=(job_id, set_name, token, False),
                     name=f"sticker-fetch-{job_id}", daemon=True).start()
    return {"id": job_id}


@web_app.post("/api/convert", status_code=202)
async def api_convert(request: FastApiRequest, x_client_id: str | None = Header(default=None)):
    cid = _client_id(x_client_id)
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("Payload must be a JSON object.")
        job_id = str(data.get("job_id", "")).strip()
        mode = str(data.get("mode", ""))
        selected = data.get("selected", [])
        if mode not in ("selected", "full"):
            raise ValueError("Choose selected stickers or the full pack.")
        if not isinstance(selected, list) or any(type(x) is not int for x in selected):
            raise ValueError("The sticker selection is invalid.")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    with STATE_LOCK:
        if _client_busy(cid):
            raise HTTPException(status_code=409, detail="A job is already running for this browser. Wait for it to finish first.")
        job = JOBS.get(job_id)
        _require_job_access(job, cid)
        if job.get("status") != "ready":
            raise HTTPException(status_code=409, detail="Fetch a sticker pack before converting it.")
        descriptors = list(job.get("sourceItems", []))
        available = {item["index"] for item in descriptors}
        if mode == "full":
            selected = sorted(available)
        else:
            if not selected or len(set(selected)) != len(selected) or any(index not in available for index in selected):
                raise HTTPException(status_code=400, detail="Select stickers from the preview first.")
            selected = sorted(selected)
            kinds = {kind: sum(1 for item in descriptors if item["index"] in selected and item["kind"] == kind)
                     for kind in ("Animated", "Static")}
            if max(kinds.values(), default=0) < 3:
                raise HTTPException(status_code=400, detail="Select at least 3 animated or 3 static stickers to create a WhatsApp pack.")
        
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        job_dir = RUNS_DIR / f"{stamp}_{convert._slug(job.get('title') or job['setName'])}_{job_id}"
        job_dir.mkdir(parents=True, exist_ok=True)
        job.update(status="queued", phase="Starting", message="Preparing your pack…",
                   done=0, total=len(selected), packs=[], packFiles=[], previewEntries=[],
                   outputDir=str(job_dir))
        _append_activity_log(job, "Starting", "Preparing your pack…")
        _set_current_job(cid, job_id)

    threading.Thread(target=_run_conversion, args=(job_id, selected, mode),
                     name=f"sticker-convert-{job_id}", daemon=True).start()
    return {"id": job_id}


@web_app.get("/api/job/{job_id}")
def api_job_status(job_id: str, x_client_id: str | None = Header(default=None)):
    cid = _client_id(x_client_id)
    with STATE_LOCK:
        job = JOBS.get(job_id)
        if job and _job_access_allowed(job, cid):
            return _copy_public_state(job)
    raise HTTPException(status_code=404, detail="This app session has expired. Fetch the pack again.")


@web_app.get("/api/job/{job_id}/thumb/{index}")
def api_job_thumbnail(job_id: str, index: int, x_client_id: str | None = Header(default=None), client_id: str | None = None):
    cid = _client_id(x_client_id) or _client_id(client_id)
    with STATE_LOCK:
        job = JOBS.get(job_id) if _job_access_allowed(JOBS.get(job_id), cid) else None
        thumbs = job.get("thumbnails", []) if job else []
        thumb = thumbs[index] if index < len(thumbs) else None
    if not thumb:
        raise HTTPException(status_code=404, detail="Preview not available.")
    return Response(content=thumb, media_type="image/png", headers={"Cache-Control": PREVIEW_HTTP_CACHE_CONTROL})


@web_app.get("/api/job/{job_id}/preview/{index}")
def api_job_large_preview(job_id: str, index: int, x_client_id: str | None = Header(default=None), client_id: str | None = None):
    cid = _client_id(x_client_id) or _client_id(client_id)
    if not _job_access_allowed(JOBS.get(job_id), cid):
        raise HTTPException(status_code=404, detail="Preview not found.")
    with STATE_LOCK:
        job = JOBS.get(job_id)
        descriptors = job.get("sourceItems", []) if job else []
        if index >= len(descriptors):
            raise HTTPException(status_code=404, detail="Preview not found.")
        descriptor = descriptors[index]
        cached = job.get("animatedPreviews", {}).get(index)
        fallback = job.get("thumbnails", [])[index] if index < len(job.get("thumbnails", [])) else None
        set_name, title = job.get("setName", ""), job.get("title", "")

    if cached:
        return Response(content=cached, media_type="image/gif", headers={"Cache-Control": PREVIEW_HTTP_CACHE_CONTROL})

    if descriptor.get("kind") != "Animated":
        try:
            item = _raw_item(set_name, descriptor, title)
            media_type = {"webp": "image/webp", "png": "image/png",
                          "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
                              str(descriptor.get("format", "")).lower(), "image/webp")
            return Response(content=item["bytes"], media_type=media_type, headers={"Cache-Control": PREVIEW_HTTP_CACHE_CONTROL})
        except Exception:
            if fallback:
                return Response(content=fallback, media_type="image/png", headers={"Cache-Control": PREVIEW_HTTP_CACHE_CONTROL})
            raise HTTPException(status_code=404, detail="Preview not available.")

    try:
        with ANIM_PREVIEW_LOCK:
            with STATE_LOCK:
                job = JOBS.get(job_id)
                cached = job.get("animatedPreviews", {}).get(index) if job else None
                descriptors = job.get("sourceItems", []) if job else []
                descriptor = descriptors[index] if index < len(descriptors) else None
                fallback = job.get("thumbnails", [])[index] if job and index < len(job.get("thumbnails", [])) else None
                set_name, title = (job.get("setName", ""), job.get("title", "")) if job else ("", "")
            if not cached and descriptor:
                cached = _make_animated_preview(_raw_item(set_name, descriptor, title))
                if cached:
                    with STATE_LOCK:
                        job = JOBS.get(job_id)
                        if job is not None:
                            job.setdefault("animatedPreviews", {})[index] = cached
    except Exception:
        cached = None

    if cached:
        return Response(content=cached, media_type="image/gif", headers={"Cache-Control": PREVIEW_HTTP_CACHE_CONTROL})
    elif fallback:
        return Response(content=fallback, media_type="image/png", headers={"Cache-Control": PREVIEW_HTTP_CACHE_CONTROL})
    raise HTTPException(status_code=404, detail="Preview not available.")


@web_app.get("/api/job/{job_id}/output/{pack_index}/{sticker_index}")
def api_job_output_sticker(job_id: str, pack_index: int, sticker_index: int, x_client_id: str | None = Header(default=None), client_id: str | None = None):
    cid = _client_id(x_client_id) or _client_id(client_id)
    if not _job_access_allowed(JOBS.get(job_id), cid):
        raise HTTPException(status_code=404, detail="Preview not found.")
    with STATE_LOCK:
        job = JOBS.get(job_id)
        files = job.get("packFiles", []) if job and job.get("status") == "done" else []
        entries = job.get("previewEntries", []) if job else []
        if pack_index >= len(files) or pack_index >= len(entries) or sticker_index >= len(entries[pack_index]):
            raise HTTPException(status_code=404, detail="Preview not found.")
        archive_path = Path(files[pack_index]["path"])
        entry_name = entries[pack_index][sticker_index]
    try:
        with ZipFile(archive_path) as archive:
            data = archive.read(entry_name)
    except Exception:
        raise HTTPException(status_code=404, detail="Preview not available.")
    return Response(content=data, media_type="image/webp", headers={"Cache-Control": PREVIEW_HTTP_CACHE_CONTROL})


@web_app.get("/download/{job_id}/{index}")
@web_app.get("/api/download/{job_id}/{index}")
def api_download_pack(job_id: str, index: int, x_client_id: str | None = Header(default=None), client_id: str | None = None):
    cid = _client_id(x_client_id) or _client_id(client_id)
    if not _job_access_allowed(JOBS.get(job_id), cid):
        raise HTTPException(status_code=404, detail="File not found.")
    with STATE_LOCK:
        job = JOBS.get(job_id)
        files = job.get("packFiles", []) if job and job.get("status") == "done" else []
        if index >= len(files):
            raise HTTPException(status_code=404, detail="File not found.")
        file_path = Path(files[index]["path"])
        filename = files[index]["name"]
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="The pack is no longer available.")
    data = file_path.read_bytes()
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": disposition})


# ---------------------------------------------------------------------------
# Fullyst Catalog Endpoints
# ---------------------------------------------------------------------------

@web_app.get("/api/fullyst/search")
def api_fullyst_search(q: str = "", page: int = 1):
    try:
        return _fullyst_search(q, page)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:500])


@web_app.get("/api/fullyst/visited-pages")
def api_fullyst_visited_pages_get(x_client_id: str | None = Header(default=None)):
    cid = _client_id(x_client_id)
    try:
        pages = _get_fullyst_visited_pages(cid)
        return {"pages": pages, "count": len(pages)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@web_app.post("/api/fullyst/visited-pages")
async def api_fullyst_visited_pages_post(request: FastApiRequest, x_client_id: str | None = Header(default=None)):
    cid = _client_id(x_client_id)
    try:
        data = await request.json()
        page = data.get("page")
        if type(page) is not int:
            raise ValueError("Enter a valid Fullyst catalog page number.")
        return _mark_fullyst_page_visited(page, cid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@web_app.get("/api/fullyst/animated-preview")
def api_fullyst_animated_preview(slug: str):
    slug = str(slug or "").strip()
    try:
        preview = _fullyst_animated_catalog_preview(slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:400])
    return Response(content=preview, media_type="image/gif", headers={"Cache-Control": PREVIEW_HTTP_CACHE_CONTROL})


@web_app.post("/api/fullyst/fetch", status_code=202)
async def api_fullyst_fetch(request: FastApiRequest, x_client_id: str | None = Header(default=None)):
    cid = _client_id(x_client_id)
    try:
        data = await request.json()
        slug = str(data.get("slug", "")).strip()
        if "://" in slug:
            parsed = urlparse(slug)
            if parsed.scheme != "https" or parsed.hostname != "fullyst.com":
                raise ValueError("Use a Fullyst pack page URL.")
            slug = parsed.path.rstrip("/").split("/")[-1]
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", slug):
            raise ValueError("Select a valid pack from the Fullyst results.")
        adult_hint = data.get("is_adult") is True or _fullyst_looks_adult(slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    with STATE_LOCK:
        if _client_busy(cid):
            raise HTTPException(status_code=409, detail="A job is already running for this browser. Wait for it to finish first.")
        if _active_job_count() >= MAX_ACTIVE_JOBS_TOTAL:
            raise HTTPException(status_code=409, detail="The converter is busy with other packs. Please try again in a moment.")
        _trim_jobs()
        job_id = _new_job_id()
        JOBS[job_id] = {
            "id": job_id, "status": "queued", "phase": "Starting",
            "message": "Preparing Fullyst import…", "done": 0,
            "total": 0, "packs": [], "packFiles": [], "source": "Fullyst",
            "sourceSlug": slug, "failedCount": 0, "isAdult": adult_hint,
            "activityLog": [], "_activitySeq": 0,
            "outputDir": None, "created": time.time(),
            "clientId": _client_bucket(cid),
        }
        _append_activity_log(JOBS[job_id], "Queued", "Preparing Fullyst import…")
        _set_current_job(cid, job_id)

    threading.Thread(target=_run_fullyst_fetch, args=(job_id, slug),
                     name=f"fullyst-fetch-{job_id}", daemon=True).start()
    return {"id": job_id}


# ---------------------------------------------------------------------------
# Saved Collection ("My Saves") Endpoints
# ---------------------------------------------------------------------------

@web_app.get("/api/collection")
def api_collection_get():
    try:
        items, groups = _collection_public_data()
        return {"items": items, "count": len(items), "groups": groups}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@web_app.post("/api/collection/save")
async def api_collection_save(request: FastApiRequest, x_client_id: str | None = Header(default=None)):
    cid = _client_id(x_client_id)
    try:
        data = await request.json()
        job_id = str(data.get("job_id", "")).strip()
        index = data.get("index")
        if type(index) is not int:
            raise ValueError("Choose a sticker from the current pack.")
        return _save_collection_sticker(job_id, index, cid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@web_app.post("/api/collection/remove")
async def api_collection_remove(request: FastApiRequest):
    try:
        data = await request.json()
        item_id = str(data.get("id", ""))
        count, undo_token = _remove_collection_sticker(item_id, record_undo=True)
        return {"ok": True, "count": count, "undoToken": undo_token}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@web_app.post("/api/collection/undo")
async def api_collection_undo(request: FastApiRequest):
    try:
        data = await request.json()
        token = str(data.get("undoToken", ""))
        return _undo_collection_action(token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@web_app.post("/api/collection/groups/create")
async def api_collection_group_create(request: FastApiRequest):
    try:
        data = await request.json()
        name = data.get("name")
        if not isinstance(name, str):
            raise ValueError("Enter a name for the new group.")
        group, groups = _create_collection_group(name)
        return {"ok": True, "group": group, "groups": groups}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@web_app.post("/api/collection/groups/move")
async def api_collection_group_move(request: FastApiRequest):
    try:
        data = await request.json()
        item_ids = data.get("ids", [])
        group_id = data.get("groupId", UNGROUPED_GROUP_ID)
        if not isinstance(group_id, str):
            raise ValueError("Choose a valid saved-sticker group.")
        moved, undo_token = _move_collection_stickers(item_ids, group_id, record_undo=True)
        return {"ok": True, "moved": moved, "undoToken": undo_token}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@web_app.post("/api/collection/groups/delete")
async def api_collection_group_delete(request: FastApiRequest):
    try:
        data = await request.json()
        group_id = data.get("groupId", "")
        if not isinstance(group_id, str):
            raise ValueError("Choose a valid saved-sticker group.")
        name, moved, groups = _delete_collection_group(group_id)
        return {"ok": True, "name": name, "moved": moved, "groups": groups}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@web_app.post("/api/collection/convert", status_code=202)
async def api_collection_convert(request: FastApiRequest, x_client_id: str | None = Header(default=None)):
    cid = _client_id(x_client_id)
    try:
        data = await request.json()
        selected = data.get("selected", [])
        if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
            raise ValueError("The saved-sticker selection is invalid.")
        title = str(data.get("title", "")).strip()
        job_id = _start_collection_conversion(selected, title, client_id=cid)
        return {"id": job_id}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@web_app.get("/api/collection/thumb/{item_id}")
def api_collection_thumb(item_id: str):
    try:
        thumb_path = _collection_thumb_path(item_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Saved sticker preview not found.")
    if not thumb_path.is_file():
        raise HTTPException(status_code=404, detail="Saved sticker preview not available.")
    return Response(content=thumb_path.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": PREVIEW_HTTP_CACHE_CONTROL})


@web_app.get("/api/collection/preview/{item_id}")
def api_collection_preview(item_id: str):
    try:
        with COLLECTION_LOCK:
            entry = _collection_find(_collection_read(), item_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Saved sticker not found.")
        asset_path = _collection_asset_path(entry)
    except Exception:
        raise HTTPException(status_code=404, detail="Saved sticker not found.")
    if not asset_path.is_file():
        raise HTTPException(status_code=404, detail="Saved sticker file is missing.")

    if entry.get("kind") == "Animated":
        preview_path = COLLECTION_PREVIEWS_DIR / f"{item_id}.gif"
        preview = None
        try:
            with ANIM_PREVIEW_LOCK:
                if preview_path.is_file():
                    preview = preview_path.read_bytes()
                else:
                    raw_item = {
                        "title": entry.get("sourceTitle", "Saved stickers"),
                        "name": entry.get("name", "saved-sticker"),
                        "format": entry.get("format", "webp"),
                        "emoji": entry.get("emoji", "📎"),
                        "bytes": asset_path.read_bytes()
                    }
                    preview = _make_animated_preview(raw_item)
                    if preview:
                        COLLECTION_PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
                        temp_preview = preview_path.with_suffix(".gif.tmp")
                        temp_preview.write_bytes(preview)
                        os.replace(temp_preview, preview_path)
        except Exception:
            preview = None
        if preview:
            return Response(content=preview, media_type="image/gif", headers={"Cache-Control": PREVIEW_HTTP_CACHE_CONTROL})
        thumb_path = _collection_thumb_path(item_id)
        if thumb_path.is_file():
            return Response(content=thumb_path.read_bytes(), media_type="image/png", headers={"Cache-Control": PREVIEW_HTTP_CACHE_CONTROL})
        raise HTTPException(status_code=404, detail="Saved sticker preview is unavailable.")

    media_type = {"webp": "image/webp", "png": "image/png",
                  "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
                      str(entry.get("format", "")).lower(), "image/webp")
    return Response(content=asset_path.read_bytes(), media_type=media_type, headers={"Cache-Control": PREVIEW_HTTP_CACHE_CONTROL})


# ---------------------------------------------------------------------------
# Modal Deployment ASGI Hook
# ---------------------------------------------------------------------------

@app.function(
    image=image,
    volumes={"/data": volume},
    timeout=900,
    cpu=2.0,
    memory=2048,
)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def fastapi_app():
    return web_app


# ---------------------------------------------------------------------------
# Local standalone runner (optional)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser(description="Sticker Studio API Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    print(f"Starting Sticker Studio API server on http://{args.host}:{args.port}")
    uvicorn.run(web_app, host=args.host, port=args.port)
