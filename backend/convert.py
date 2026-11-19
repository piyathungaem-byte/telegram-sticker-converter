#!/usr/bin/env python3
# NOTE: keep this file in sync with the other convert.py in this repo.
# backend/modal_app.py bundles the backend/ copy into the Modal image,
# while the root convert.py powers webapp.py, install.py and the CLI.
"""
Telegram -> WhatsApp sticker converter.

Runs in a plain Python environment -- no system packages required:

    pip install pillow rlottie-python requests imageio-ffmpeg
    python convert.py --pack "https://t.me/addstickers/SomePack"

Works the same on Windows, macOS and Linux. If `ffmpeg` and/or `pngquant` happen to be
installed they are used (pngquant gives marginally better small-size quality); otherwise the
bundled ffmpeg from imageio-ffmpeg decodes video, and Pillow handles every encode.

Pipeline:
    TGS  (gzipped Lottie) -> render frames (rlottie)  -> animated WebP (Pillow)   <=500KB
    WEBM (VP9 + alpha)    -> decode frames (ffmpeg)   -> animated WebP (Pillow)   <=500KB
    animated WebP input   -> resize/resample frames    -> animated WebP (Pillow)   <=500KB
    WEBP (static)         -> fit 512x512              -> static WebP (Pillow)     <=100KB
    static sticker PNGs   -> median-cut quantize      -> PNG under the 100KB cap
then split (never mixing static with animated), chunk at 30, and write canonical
`.wastickers` files (flat zip: cover.png + title.txt + author.txt + numbered stickers),
validated by an importer simulation before they are offered.

Fetching uses the Telegram Bot API (getStickerSet -> getFile) and needs one bot token.

Usage:
    python convert.py --self-test                                  # verify the install
    python convert.py --pack "<link or name>" --out ./out           # convert a Telegram pack
    python convert.py --from-dir ./folder --out ./out              # convert local files, no token
    python convert.py --repackage ./out                            # rebuild packs only
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


# --------------------------------------------------------------- console safety
# Windows consoles and redirected output are frequently cp1252/cp437, which cannot
# encode the decorative glyphs used below -- that would raise UnicodeEncodeError
# mid-run. Degrade gracefully instead: never crash on output, and fall back to
# plain ASCII for the three glyphs when the stream cannot render them.
def _configure_console() -> "tuple[str, str, str]":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")   # Python 3.7+: bad chars become '?', never raise
        except Exception:
            pass
    enc = getattr(sys.stdout, "encoding", None) or "ascii"

    def pick(fancy: str, plain: str) -> str:
        try:
            fancy.encode(enc)
            return fancy
        except Exception:
            return plain

    return pick("\u26a0", "(!)"), pick("\u2717", "x"), pick("\u2014", "--")


WARN, CROSS, DASH = _configure_console()

# ---------------------------------------------------------------- platform specs
WA_STICKER_PX      = 512            # WhatsApp: exactly 512x512
WA_STATIC_MAX_KB   = 100            # static sticker cap
WA_ANIM_MAX_KB     = 500            # animated sticker cap
WA_ANIM_MAX_SEC    = 10.0           # animated duration cap
WA_MIN_FRAME_MS    = 8              # min per-frame duration
WA_TRAY_PX         = 96             # tray / picker icon
WA_TRAY_MAX_KB     = 50
WA_PACK_MIN, WA_PACK_MAX = 3, 30    # stickers per pack
# WhatsApp forbids mixing static and animated stickers inside one pack.
# (fps, quality, alpha_quality) — alpha_q is the big lever for vector/animated art,
# because a lossless alpha plane can dwarf the RGB payload on transparent artwork;
# Encoding is Pillow (its WebP writer exposes alpha_quality), measured equal in size to
# cwebp+webpmux on real Telegram frames and faster. No CLI image tools required.
# Ladder measured on a real 3.02s / 75-frame Telegram TGS (see report.json).
ANIM_SIZE_LADDER = [(25, 85, 85), (25, 75, 70), (20, 80, 75), (20, 70, 60),
                    (15, 75, 70), (15, 60, 55), (12, 75, 70), (12, 60, 55)]
STATIC_Q_LADDER  = [90, 80, 70, 60, 50]
ANIM_TARGET_KB   = 470          # aim below the 500KB cap for headroom


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    # Every child in this project is a non-interactive ffmpeg job. Inherit no console
    # stdin: an unexpected ffmpeg prompt must never leave a large pack waiting for Enter.
    kw.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.run(cmd, capture_output=True, **kw)


def kb(p: Path) -> float:
    return p.stat().st_size / 1024.0


# ------------------------------------------------------- environment discovery
# Everything below runs with pip packages only. System binaries are used only if you
# already have them (pngquant gives slightly better small-size quality); nothing is
# required. On Windows this means:  pip install pillow rlottie-python requests imageio-ffmpeg
PNGQUANT = shutil.which("pngquant")
_FFMPEG: str | None = None


def find_ffmpeg() -> str | None:
    """System ffmpeg if present, otherwise the one bundled in imageio-ffmpeg's wheel."""
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    exe = shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:  # noqa: BLE001
            exe = None
    _FFMPEG = exe
    return exe


def need_ffmpeg() -> str:
    exe = find_ffmpeg()
    if not exe:
        raise RuntimeError(
            "No ffmpeg found. Video (WEBM) stickers need it:\n"
            "  pip install imageio-ffmpeg     <- bundles ffmpeg, no system install\n"
            "  (or install ffmpeg yourself and put it on PATH)")
    return exe


def probe_video(path: Path) -> dict:
    """Resolution / fps / duration straight from ffmpeg's banner — no ffprobe needed."""
    r = run([need_ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path)])
    txt = r.stderr.decode(errors="ignore")
    info: dict = {"width": None, "height": None, "fps": None, "duration": None}
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", txt)
    if m:
        info["width"], info["height"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"([\d.]+)\s+fps", txt)
    if m:
        info["fps"] = float(m.group(1))
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", txt)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return info


def extract_video_frames(src: Path, out_dir: Path, px: int = WA_STICKER_PX) -> list[Path]:
    """Decode a video sticker to 512x512 RGBA PNG frames (alpha preserved, letterboxed)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    vf = (f"scale={px}:{px}:force_original_aspect_ratio=decrease,"
          f"pad={px}:{px}:(ow-iw)/2:(oh-ih)/2:color=0x00000000,format=rgba")
    run([need_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
         "-threads", "1", "-filter_threads", "1", "-i", str(src),
         "-fps_mode", "passthrough", "-pix_fmt", "rgba", "-vf", vf, str(out_dir / "%05d.png")])
    return sorted(out_dir.glob("*.png"))


def encode_animated_webp(frames, dur_ms: int, quality: int, alpha_q: int,
                         out_path: Path, method: int = 4) -> bool:
    """Animated (or single-frame) WebP via Pillow — replaces cwebp + img2webp + webpmux.

    Measured against cwebp+webpmux on the same real Telegram frames: equal size
    (541.8 KB vs 545.0 KB at q75/aq70) and faster, with alpha_quality support.
    """
    from PIL import Image
    if not frames:
        return False
    # Accept paths (streamed from disk) or already-decoded PIL images (kept in RAM).
    # Those RAM images are already RGBA: do NOT convert/copy them again on every rung.
    imgs = []
    for frame in frames:
        if isinstance(frame, Image.Image):
            imgs.append(frame if frame.mode == "RGBA" else frame.convert("RGBA"))
        else:
            with Image.open(frame) as opened:
                imgs.append(opened.convert("RGBA"))
    if len(imgs) == 1:
        imgs[0].save(out_path, "WEBP", quality=quality, alpha_quality=alpha_q, method=method)
    else:
        imgs[0].save(out_path, "WEBP", save_all=True, append_images=imgs[1:],
                     duration=max(dur_ms, WA_MIN_FRAME_MS), loop=0,
                     quality=quality, alpha_quality=alpha_q, method=method)
    return out_path.exists()


def webp_info(path: Path) -> dict:
    """Read a WebP container directly — replaces webpinfo (RIFF chunk walk)."""
    d = path.read_bytes()
    if d[:4] != b"RIFF" or d[8:12] != b"WEBP":
        raise ValueError(f"{path.name} is not a WebP file")
    pos = 12
    info = {"canvas": None, "alpha": False, "animated": False, "durations": [], "loop": None}
    while pos + 8 <= len(d):
        cid = d[pos:pos + 4].decode("ascii", "replace")
        size = struct.unpack("<I", d[pos + 4:pos + 8])[0]
        body = d[pos + 8:pos + 8 + size]
        if cid == "VP8X":
            flags = body[0]
            info["animated"] = bool(flags & 0x02)
            info["alpha"] = bool(flags & 0x10)
            info["canvas"] = (int.from_bytes(body[4:7], "little") + 1,
                              int.from_bytes(body[7:10], "little") + 1)
        elif cid == "ANIM" and len(body) >= 6:
            info["loop"] = int.from_bytes(body[4:6], "little")
        elif cid == "ANMF" and len(body) >= 15:
            info["durations"].append(int.from_bytes(body[12:15], "little"))
        elif cid in ("VP8 ", "VP8L") and info["canvas"] is None:
            if cid == "VP8L" and len(body) >= 5:
                # VP8L: 1-byte signature 0x2f, then a 32-bit LE group holding
                # width-1 (14b), height-1 (14b), alpha_is_used (1b), version (3b).
                # The alpha bit is bit 28 -> byte 3 of that group -> body[4] & 0x10.
                b = int.from_bytes(body[1:5], "little")
                info["canvas"] = ((b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1)
                info["alpha"] = info["alpha"] or bool(b & (1 << 28))
            elif len(body) >= 10:
                w, h = struct.unpack("<HH", body[6:10])
                info["canvas"] = (w & 0x3FFF, h & 0x3FFF)
        pos += 8 + size + (size & 1)
    return info


def _rung_plan(cache: list[Path], entry, src_fps: float, duration: float):
    """Which source frames a ladder rung needs, and its frame duration."""
    fps, quality, alpha_q = entry
    fps_eff = max(1, min(int(round(fps)), int(round(src_fps))))
    n_out = max(2, int(math.floor(duration * fps_eff)))
    idx = [min(len(cache) - 1, int(round(i * src_fps / fps_eff))) for i in range(n_out)]
    return fps_eff, quality, alpha_q, n_out, idx, int(round(1000 / fps_eff))


def _encode_rung_worker(payload: dict) -> dict:
    """Encode ONE ladder rung into its own file. Top-level so it can run in a worker
    PROCESS: a single animated-WebP encode is seconds of pure CPU inside Pillow and
    cannot be split, so the way to use more cores on one sticker is to encode several
    rungs of its ladder concurrently."""
    frames = [Path(f) for f in payload["frames"]]
    method = int(payload.get("method", 4))
    ok = encode_animated_webp(frames, payload["dur_ms"], payload["quality"],
                              payload["alpha_q"], Path(payload["dest"]), method=method)
    size = round(kb(Path(payload["dest"])), 1) if ok else None
    return {"rung": payload["rung"], "fps": payload["fps"], "quality": payload["quality"],
            "alpha_quality": payload["alpha_q"], "frames": payload["frames_n"],
            "method": method, "kb": size, "dest": payload["dest"], "speculative": False}


# Stickers rarely exceed ~90 frames; at 1 MB per 512x512 RGBA frame that is small enough
# to hold in RAM, which removes a PNG decode per rung (~0.3 s of the ~2.7 s per encode).
# Bigger stickers stream from disk instead of risking a memory blow-up per worker.
FRAME_CACHE_MAX = int(os.environ.get("STICKER_FRAME_CACHE_FRAMES", "96"))


def _ladder_encode(cache: list[Path], src_fps: float, duration: float, ladder, out_path: Path,
                   label: str, jobs: int = 1, method: int = 4,
                   process_start_method: str | None = None) -> dict:
    """Find the best ladder rung that fits the size target, using extra cores when given.

    Correctness rule (learned the hard way): the ladder is NOT monotonic -- a later rung
    with a lower quality can produce a LARGER file, because the rungs also change frame
    rate. So we never binary-search it. We walk it in order, exactly as before, and

      * jobs > 1 -- encode the next `jobs` rungs of the walk CONCURRENTLY and take the
        first one in ladder order that fits. Identical result to the sequential walk
        (the first fitting rung is still the first fitting rung), one encode-time of
        wall clock instead of up to `jobs`, at the cost of a few speculative encodes.
      * jobs == 1 -- plain sequential walk.
    """
    src_fps = max(1.0, float(src_fps))
    n = len(ladder)
    tmp = Path(tempfile.mkdtemp(prefix="rung_"))
    plans = [_rung_plan(cache, entry, src_fps, duration) for entry in ladder]
    ram = None
    if len(cache) <= FRAME_CACHE_MAX:
        from PIL import Image
        try:
            ram = [Image.open(p).convert("RGBA") for p in cache]
        except Exception:                                          # noqa: BLE001
            ram = None

    def payload(i: int) -> dict:
        fps_eff, quality, alpha_q, frames_n, idx, dur_ms = plans[i]
        return {"rung": i, "fps": fps_eff, "quality": quality, "alpha_q": alpha_q,
                "frames_n": frames_n, "frames": [str(cache[j]) for j in idx],
                "dur_ms": dur_ms, "method": method,
                "dest": str(tmp / f"rung{i:02d}.webp")}

    def encode_here(i: int) -> dict:
        """Encode rung i in this process (reusing in-RAM frames when we have them)."""
        fps_eff, quality, alpha_q, frames_n, idx, dur_ms = plans[i]
        frames = [ram[j] for j in idx] if ram is not None else [cache[j] for j in idx]
        dest = tmp / f"rung{i:02d}.webp"
        ok = encode_animated_webp(frames, dur_ms, quality, alpha_q, dest, method=method)
        size = round(kb(dest), 1) if ok else None
        return {"rung": i, "fps": fps_eff, "quality": quality, "alpha_quality": alpha_q,
                "frames": frames_n, "method": method, "kb": size,
                "dest": str(dest), "speculative": False}

    def fits(a: dict) -> bool:
        return a["kb"] is not None and a["kb"] <= ANIM_TARGET_KB

    attempts: list[dict] = []
    chosen: dict | None = None

    if jobs > 1 and n > 1:
        from concurrent.futures import ProcessPoolExecutor
        width = max(1, min(jobs, n))
        pool_options = {}
        if process_start_method:
            import multiprocessing
            pool_options["mp_context"] = multiprocessing.get_context(process_start_method)
        with ProcessPoolExecutor(max_workers=width, **pool_options) as pool:
            for start in range(0, n, width):
                batch = list(range(start, min(start + width, n)))
                for res in pool.map(_encode_rung_worker, [payload(i) for i in batch]):
                    attempts.append(res)
                # first fitting rung in ladder order == what the sequential walk would pick
                hit = next((a for a in sorted(attempts, key=lambda a: a["rung"]) if fits(a)), None)
                if hit:
                    chosen = hit
                    break
    else:
        for i in range(n):
            a = encode_here(i)
            attempts.append(a)
            if fits(a):
                chosen = a
                break

    attempts.sort(key=lambda a: a["rung"])
    if chosen is None:
        within_cap = [a for a in attempts if a["kb"] is not None and a["kb"] <= WA_ANIM_MAX_KB]
        best = within_cap[0] if within_cap else min(attempts, key=lambda a: a["kb"] or 10**9)
    else:
        best = chosen
        for a in attempts:               # rungs probed after the winner were speculative
            a["speculative"] = a["rung"] > best["rung"]

    if best.get("dest"):
        shutil.copyfile(best["dest"], out_path)
    tries = len(attempts)
    attempts = [{k: v for k, v in a.items() if k != "dest"} for a in attempts]
    shutil.rmtree(tmp, ignore_errors=True)
    chosen_out = {k: v for k, v in best.items() if k != "dest"}

    return {"strategy": label, "src_fps": src_fps, "duration_s": round(duration, 2),
            "attempts": attempts, "chosen": chosen_out, "encodes": tries,
            "within_limit": chosen_out["kb"] is not None
                            and chosen_out["kb"] <= WA_ANIM_MAX_KB}


# ------------------------------------------------------------------ 1. TGS -> WebP
def tgs_to_animated_webp(tgs_bytes: bytes, out_path: Path,
                         fps_pref: int = 25, load_dir: Path | None = None,
                         jobs: int = 1, process_start_method: str | None = None) -> dict:
    """Render a Telegram .tgs (gzipped Lottie) to an animated WebP within WhatsApp caps.

    Frames are rasterised ONCE at the highest frame rate we would use; the size ladder then
    re-samples and re-encodes from that cache, so the expensive render happens one time.
    Pure Python: rlottie-python for rendering, Pillow for encoding.
    """
    import time
    from rlottie_python import LottieAnimation

    t_start = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="tgs_"))
    src = tmp / "sticker.tgs"
    src.write_bytes(tgs_bytes)

    anim = LottieAnimation.from_tgs(str(src))
    w, h = anim.lottie_animation_get_size()
    src_fps = anim.lottie_animation_get_framerate()
    total = anim.lottie_animation_get_totalframe()
    dur_s = total / src_fps if src_fps else 3.0

    # Telegram art is often not square (e.g. 512x448) -> render at native aspect, then
    # centre on a 512x512 transparent canvas (WhatsApp requires exactly 512x512).
    scale = WA_STICKER_PX / max(w, h)
    rw, rh = max(1, round(w * scale)), max(1, round(h * scale))

    top_fps = min(fps_pref, int(round(src_fps)) or fps_pref)
    n_cache = max(2, int(math.floor(dur_s * top_fps)))
    cache = []
    for i in range(n_cache):
        src_frame = min(total - 1, int(round(i * src_fps / top_fps)))
        img = anim.render_pillow_frame(frame_num=src_frame, width=rw, height=rh)
        fp = tmp / f"cache_{i:04d}.png"
        _square_canvas(img).save(fp, "PNG")
        cache.append(fp)
    render_s = time.time() - t_start

    res = _ladder_encode(cache, top_fps, dur_s, ANIM_SIZE_LADDER, out_path, "tgs->webp",
                         jobs=jobs, process_start_method=process_start_method)
    res.update({"src_size": f"{w}x{h}", "src_fps": src_fps, "render_s": round(render_s, 1)})
    return res


def _square_canvas(img, px: int = WA_STICKER_PX):
    from PIL import Image
    canvas = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    canvas.paste(img.convert("RGBA"), ((px - img.width) // 2, (px - img.height) // 2))
    return canvas


# ----------------------------------------------------------------- 2. WEBM -> WebP
# (fps_cap, quality) — hand-ordered best-quality-first. Measured on a real 50-sticker
# Telegram video pack: quality alone (floor q40) left 8/50 over the 500KB cap, and
# EVERY one of them fit as soon as frame rate dropped. Complexity per frame, not
# length, is what blows the byte budget on video stickers.
WEBM_LADDER = [
    (30, 80, 75), (30, 70, 65), (30, 60, 55), (30, 50, 45), (30, 40, 38),
    (25, 70, 65), (25, 60, 55), (25, 50, 45), (25, 40, 38), (25, 30, 30),
    (20, 70, 65), (20, 60, 55), (20, 50, 45), (20, 40, 38), (20, 30, 30), (20, 25, 25),
    (15, 60, 55), (15, 50, 45), (15, 40, 38), (15, 30, 30), (15, 25, 25), (15, 20, 22),
    (12, 40, 38), (12, 30, 30), (12, 25, 25), (12, 20, 22),
    (10, 30, 30), (10, 25, 25), (10, 20, 22), (8, 20, 20),
]


def webm_to_animated_webp(webm_bytes: bytes, out_path: Path, jobs: int = 1,
                           method: int = 4, process_start_method: str | None = None) -> dict:
    """Telegram video sticker (VP9 + alpha, <=3s) -> animated WebP, alpha preserved.

    Pipeline: decode once with ffmpeg (system, or the binary bundled in the
    imageio-ffmpeg wheel) straight to 512x512 RGBA frames, then run the same
    quality x frame-rate ladder as TGS, encoding with Pillow.
    """
    tmp = Path(tempfile.mkdtemp(prefix="webm_"))
    src = tmp / "sticker.webm"
    src.write_bytes(webm_bytes)

    meta = probe_video(src)
    src_fps = float(meta.get("fps") or 30.0)
    duration = meta.get("duration") or None
    cache = extract_video_frames(src, tmp / "frames")
    if not cache:
        return {"strategy": "webm->webp", "attempts": [], "chosen": {"kb": None},
                "within_limit": False, "error": "ffmpeg decoded no frames"}
    if not duration:
        # fall back to what we actually decoded
        duration = len(cache) / src_fps
        src_fps = min(src_fps, max(1.0, len(cache) / max(duration, 0.01)))
    duration = min(float(duration), WA_ANIM_MAX_SEC)

    res = _ladder_encode(cache, src_fps, duration, WEBM_LADDER, out_path, "webm->webp",
                         jobs=jobs, method=method, process_start_method=process_start_method)
    res.update({"src_size": f"{meta.get('width')}x{meta.get('height')}",
                "decoded_frames": len(cache)})
    return res


# --------------------------------------------------------------- 3. static WebP
def static_webp_to_sticker(webp_bytes: bytes, out_path: Path) -> dict:
    from PIL import Image
    tmp = Path(tempfile.mkdtemp(prefix="webp_"))
    src = tmp / "in.webp"
    src.write_bytes(webp_bytes)
    img = Image.open(src).convert("RGBA")
    scale = WA_STICKER_PX / max(img.width, img.height)
    if scale < 1:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    canvas = _square_canvas(img)
    flat = tmp / "canvas.png"
    canvas.save(flat, "PNG")

    attempts = []
    for q in STATIC_Q_LADDER:
        canvas.save(out_path, "WEBP", quality=q, alpha_quality=90, method=4)
        size = round(kb(out_path), 1)
        attempts.append({"quality": q, "kb": size})
        if size <= WA_STATIC_MAX_KB:
            break
    return {"strategy": "webp->static webp", "attempts": attempts, "chosen": attempts[-1],
            "within_limit": attempts[-1]["kb"] is not None and attempts[-1]["kb"] <= WA_STATIC_MAX_KB}


def webp_is_animated(webp_bytes: bytes) -> bool:
    """Return whether a WebP source contains multiple animation frames."""
    from io import BytesIO
    from PIL import Image
    try:
        with Image.open(BytesIO(webp_bytes)) as image:
            return image.format == "WEBP" and bool(getattr(image, "is_animated", False)) \
                and int(getattr(image, "n_frames", 1)) > 1
    except Exception:                                                # noqa: BLE001
        return False


def animated_webp_to_sticker(webp_bytes: bytes, out_path: Path, jobs: int = 1,
                             process_start_method: str | None = None) -> dict:
    """Fit an animated WebP source to WhatsApp's 512px / 500KB animated limits."""
    from io import BytesIO
    from PIL import Image

    temp = Path(tempfile.mkdtemp(prefix="webp_anim_"))
    source = Image.open(BytesIO(webp_bytes))
    try:
        frame_count = int(getattr(source, "n_frames", 1))
        if source.format != "WEBP" or frame_count < 2:
            raise ValueError("source is not an animated WebP")
        durations = []
        for i in range(frame_count):
            source.seek(i)
            durations.append(max(WA_MIN_FRAME_MS, int(source.info.get("duration", 100) or 100)))
        total_ms = sum(durations)
        if total_ms <= 0:
            raise ValueError("animated WebP has no frame duration")
        duration_s = min(total_ms / 1000.0, WA_ANIM_MAX_SEC)
        source_fps = frame_count / max(total_ms / 1000.0, 0.001)
        cache_fps = min(30.0, max(1.0, source_fps))
        cache_count = max(2, min(frame_count, int(math.floor(duration_s * cache_fps))))
        elapsed = []
        cursor = 0
        for duration in durations:
            cursor += duration
            elapsed.append(cursor)
        width, height = source.size
        resampling = getattr(Image, "Resampling", Image)
        scale = WA_STICKER_PX / max(width, height, 1)
        cache = []
        for i in range(cache_count):
            target_ms = i * duration_s * 1000 / cache_count
            frame_index = next((j for j, stop in enumerate(elapsed) if stop > target_ms), frame_count - 1)
            source.seek(frame_index)
            image = source.convert("RGBA")
            if scale < 1 or image.size != (WA_STICKER_PX, WA_STICKER_PX):
                image.thumbnail((WA_STICKER_PX, WA_STICKER_PX), resampling.LANCZOS)
            canvas = _square_canvas(image)
            path = temp / f"frame_{i:04d}.png"
            canvas.save(path, "PNG")
            cache.append(path)
        effective_fps = cache_count / duration_s
        result = _ladder_encode(cache, effective_fps, duration_s, WEBM_LADDER,
                                out_path, "animated-webp->webp", jobs=jobs,
                                process_start_method=process_start_method)
        result.update({"src_size": f"{width}x{height}", "src_frames": frame_count,
                       "source_duration_s": round(total_ms / 1000.0, 2)})
        return result
    finally:
        source.close()
        shutil.rmtree(temp, ignore_errors=True)


# ------------------------------------------------------------------- 4. validator
def validate(path: Path) -> dict:
    """Hard checks = WhatsApp rejects the sticker. Warnings = it will install but may look wrong.

    Uses a direct WebP container read (RIFF chunk walk), so no webpinfo binary is needed.
    """
    info = {"file": path.name, "kb": round(kb(path), 1)}
    w = webp_info(path)
    cw, ch = w.get("canvas") or (0, 0)
    info["animated"] = w["animated"]
    info["alpha"] = w["alpha"]
    info["frames"] = len(w["durations"])
    info["canvas"] = f"{cw} x {ch}"
    info["duration_ms"] = sum(w["durations"])
    cap = WA_ANIM_MAX_KB if info["animated"] else WA_STATIC_MAX_KB
    info["limit_kb"] = cap
    info["loop"] = w.get("loop")
    checks = {
        "canvas_512": (cw, ch) == (WA_STICKER_PX, WA_STICKER_PX),
        "size_ok": info["kb"] <= cap,
        "duration_ok": (not info["animated"]) or info["duration_ms"] / 1000 <= WA_ANIM_MAX_SEC,
        "frames_ok": (not info["animated"]) or info["frames"] >= 1,
    }
    warnings = []
    if not info["alpha"]:
        warnings.append("no alpha channel: the sticker will render as an opaque square in chat")
    if info["animated"] and info["duration_ms"] / 1000 > WA_ANIM_MAX_SEC:
        warnings.append("longer than 10s")
    if info["animated"] and info["loop"] != 0:
        warnings.append("animation may not loop (WhatsApp expects infinite loop)")
    info["checks"] = checks
    info["warnings"] = warnings
    info["pass"] = all(checks.values())
    return info



# Cyrillic -> Latin, so Russian/Ukrainian pack titles produce readable ASCII identifiers
# instead of collapsing to "pack". WhatsApp identifiers only permit a-z A-Z 0-9 _ - . space.
_CYR = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
    "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ё": "e", "і": "i", "ї": "yi", "є": "ye", "ґ": "g",
})


def _slug(text: str, fallback: str = "pack") -> str:
    import unicodedata
    # fold Latin accents (ü -> u) before filtering, then transliterate Cyrillic
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c)).translate(_CYR)
    t = "".join(c if (c.isalnum() and c.isascii()) else "_" for c in t)
    t = re.sub(r"_+", "_", t).strip("_")[:60].strip("_")
    return t or fallback

# ------------------------------------------------------------- 5. pack packaging
# ---------------------------------------------------------------------------
# The .wastickers format is NOT the WhatsApp sample-app assets layout, and it is
# NOT read by WhatsApp itself — a host app (Sticker Maker Studio etc.) reads it and
# then registers the pack with WhatsApp. Canonical structure, verified against the
# maintained reference implementation (laggykiller/sticker-convert) and field reports:
#
#   <pack>.wastickers  = FLAT zip (junk paths), containing
#       cover.png        96x96 PNG, <=50KB          (pack icon / tray)
#       author.txt       publisher
#       title.txt        pack title
#       01.webp  ...     ANIMATED stickers (<=500KB each)
#       01.webp / 01.png  STATIC stickers  (<=100KB each). Written as single-frame .webp by
#                         default: that is WhatsApp's native static sticker format and what
#                         host apps actually count as stickers. Some legacy apps count only
#                         .png -- use --static-format png for those.
#
# One pack per file, max 30, never mixing static with animated. My earlier output
# used contents.json + per-pack folders and omitted title.txt/author.txt — which is
# exactly why importers said "invalid pack".
# ---------------------------------------------------------------------------
@dataclass
class Sticker:
    path: Path
    animated: bool
    emojis: list[str] = field(default_factory=lambda: ["😀"])
    index: int = 0


def _png_quantize(src_webp: Path, out_png: Path, target_kb: int = WA_STATIC_MAX_KB) -> dict:
    """Static sticker -> quantized PNG under the static size cap.

    A plain 512x512 RGBA PNG of sticker art is 300-400 KB against a 100 KB cap, so
    quantization is mandatory. pngquant is used when present (slightly better quality at a
    given size); otherwise Pillow's fast-octree quantizer does the job with pip packages only.
    """
    from PIL import Image
    im = Image.open(src_webp).convert("RGBA")
    attempts = []

    if PNGQUANT:
        tmp = Path(tempfile.mkdtemp(prefix="png_"))
        raw = tmp / "raw.png"
        im.save(raw, "PNG", optimize=True)
        ladder = [("65-90", None), ("50-80", None), ("40-70", None), ("20-50", None),
                  ("10-40", None), (None, 256), (None, 128), (None, 64), (None, 32)]
        for quality, colors in ladder:
            cmd = [PNGQUANT, "--speed", "1", "--force", "--output", str(out_png), str(raw)]
            if quality:
                cmd[1:1] = ["--quality", quality]
            if colors:
                cmd[1:1] = ["--colors", str(colors)]
            r = run(cmd)
            if r.returncode == 0 and out_png.exists():
                size = round(kb(out_png), 1)
                attempts.append({"tool": "pngquant", "quality": quality, "colors": colors, "kb": size})
                if size <= target_kb:
                    break
        if not attempts:
            attempts, PNGQUANT_FALLBACK = [], True
        else:
            best = attempts[-1]
            return {"kb": best["kb"], "chosen": best, "attempts": attempts,
                    "within_limit": best["kb"] <= target_kb}

    for colors in (256, 192, 128, 96, 64, 48, 32, 24, 16, 12, 8):
        im.quantize(colors=colors, method=Image.FASTOCTREE).save(out_png, "PNG", optimize=True)
        size = round(kb(out_png), 1)
        attempts.append({"tool": "pillow", "colors": colors, "kb": size})
        if size <= target_kb:
            break
    best = attempts[-1]
    return {"kb": best["kb"], "chosen": best, "attempts": attempts,
            "within_limit": best["kb"] <= target_kb}


def _make_cover(sticker: Path, out_path: Path) -> dict:
    """96x96 PNG pack icon, <=50KB, taken from a sticker's first frame."""
    from PIL import Image
    img = Image.open(sticker).convert("RGBA")
    scale = WA_TRAY_PX / max(img.width, img.height)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                     Image.LANCZOS)
    tray = _square_canvas(img, WA_TRAY_PX)
    tray.save(out_path, "PNG", optimize=True)
    size = round(kb(out_path), 1)
    if size > WA_TRAY_MAX_KB:          # quantize if a busy icon blows the cap
        _png_quantize(_webp_ify(out_path), out_path, WA_TRAY_MAX_KB)
        size = round(kb(out_path), 1)
    return {"kb": size, "ok": size <= WA_TRAY_MAX_KB, "px": WA_TRAY_PX}


def _webp_ify(png_path: Path) -> Path:
    """pngquant needs an 8-bit source; wrap a PNG in a throwaway webp for reuse."""
    from PIL import Image
    out = png_path.with_suffix(".tmp.webp")
    Image.open(png_path).convert("RGBA").save(out, "WEBP", lossless=False, quality=95)
    return out


def lint_wastickers(path: Path) -> dict:
    """Simulate what a host app checks on import. This is the check I should have
    written before hand-rolling the container format."""
    import tempfile
    problems, notes = [], []
    with zipfile.ZipFile(path) as z:
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        folders = sorted({n.split("/")[0] for n in names if "/" in n})
        if folders:
            problems.append(f"not a flat zip; contains folders {folders[:3]}")
        for req in ("author.txt", "title.txt"):
            if req not in names:
                problems.append(f"missing {req}")
        for txt in ("author.txt", "title.txt"):
            if txt in names and not z.read(txt).decode("utf-8", "replace").strip():
                problems.append(f"{txt} is empty")
        icon = next((n for n in ("cover.png", "0.png", "tray.png") if n in names), None)
        if icon:
            c = z.getinfo(icon)
            if c.file_size > WA_TRAY_MAX_KB * 1024:
                problems.append(f"{icon} {c.file_size/1024:.1f} KB > {WA_TRAY_MAX_KB} KB")
            else:
                notes.append(f"{icon} {c.file_size/1024:.1f} KB")
        else:
            problems.append("no icon (cover.png / 0.png)")
        # payloads are image files; anything else (icons, manifests, metadata) is not a
        # sticker. Extra files are tolerated -- importers ignore what they don't know --
        # so they are reported as notes rather than problems.
        icon_names = {"cover.png", "0.png", "tray.png", "tray_image.png"}
        meta_names = {"author.txt", "title.txt"}
        stickers = [n for n in names
                    if n not in icon_names and n not in meta_names
                    and Path(n).suffix.lower() in (".webp", ".png", ".jpg", ".jpeg")]
        extras = [n for n in names if n not in stickers and n not in icon_names
                  and n not in meta_names]
        if extras:
            notes.append("ignored extra entries: " + ", ".join(sorted(extras)[:5]))
        exts = {Path(n).suffix.lower() for n in stickers}
        if len(exts) > 1:
            problems.append(f"mixed sticker types {sorted(exts)} (a pack must be all-one-kind)")
        if not (WA_PACK_MIN <= len(stickers) <= WA_PACK_MAX):
            problems.append(f"{len(stickers)} stickers (needs {WA_PACK_MIN}-{WA_PACK_MAX})")
        tmp = Path(tempfile.mkdtemp(prefix="lint_"))
        for n in stickers:
            (tmp / Path(n).name).write_bytes(z.read(n))
    for n in stickers:
        f = tmp / Path(n).name
        if f.suffix.lower() == ".webp":
            v = validate(f)
            if not v["pass"]:
                problems.append(f"{Path(n).name}: {[k for k, ok in v['checks'].items() if not ok]}")
        else:
            v = _validate_png(f)
            if v["hard_fail"]:
                problems.append(f"{Path(n).name}: {v['hard_fail']}")
    return {"file": path.name, "ok": not problems, "problems": problems, "notes": notes,
            "stickers": len(stickers), "kb": round(kb(path), 1)}


def _validate_png(path: Path) -> dict:
    from PIL import Image
    im = Image.open(path)
    info = {"canvas": f"{im.width} x {im.height}", "kb": round(kb(path), 1),
            "alpha": im.mode in ("RGBA", "LA") or "transparency" in im.info,
            "hard_fail": None}
    if (im.width, im.height) != (WA_STICKER_PX, WA_STICKER_PX):
        info["hard_fail"] = f"not {WA_STICKER_PX}x{WA_STICKER_PX}"
    elif info["kb"] > WA_STATIC_MAX_KB:
        info["hard_fail"] = f"over {WA_STATIC_MAX_KB} KB"
    return info


def build_packs(stickers: list["Sticker"], title: str, publisher: str, out_dir: Path,
                static_format: str = "webp") -> dict:
    """Emit canonical .wastickers files (one per pack) + an optional app-assets tree."""
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = _slug(title)

    groups: list[tuple[str, list[Sticker]]] = []
    for animated in (True, False):
        pool = [s for s in stickers if s.animated == animated]
        for i in range(0, len(pool), WA_PACK_MAX):
            if pool[i:i + WA_PACK_MAX]:
                groups.append(("Animated" if animated else "Static", pool[i:i + WA_PACK_MAX]))
    valid = [g for g in groups if len(g[1]) >= WA_PACK_MIN]
    submin = [g for g in groups if len(g[1]) < WA_PACK_MIN]

    warnings, loose = [], []
    for kind, chunk in submin:
        ldir = out_dir / "loose"
        ldir.mkdir(parents=True, exist_ok=True)
        for n, st in enumerate(chunk, 1):
            fn = f"{_slug(kind.lower())}_{n:02d}{st.path.suffix}"
            shutil.copy(st.path, ldir / fn)
            loose.append({"file": str(ldir / fn), "kind": kind})
        warnings.append(
            f"{len(chunk)} {kind.lower()} sticker(s) could not form a pack (needs "
            f"{WA_PACK_MIN}-{WA_PACK_MAX}, and static/animated can't be mixed). Their files "
            f"are in loose/ {DASH} they are deliberately kept OUT of every .wastickers so the "
            f"valid packs still import.")

    packs, manifest_packs = [], []
    for idx, (kind, chunk) in enumerate(valid, 1):
        animated = kind == "Animated"
        name = f"{title} {idx}" if len(valid) > 1 else title
        zpath = out_dir / (f"{slug}_{idx}.wastickers" if len(valid) > 1 else f"{slug}.wastickers")
        work = out_dir / f"_{slug}_{idx}_build"
        work.mkdir(parents=True, exist_ok=True)

        cover = _make_cover(chunk[0].path, work / "cover.png")
        entries = []
        for n, st in enumerate(chunk, 1):
            if animated:
                fn = f"{n:02d}.webp"
                shutil.copy(st.path, work / fn)
                entries.append({"file": fn, "index": st.index, "kb": round(kb(st.path), 1)})
            else:
                fn = f"{n:02d}.png" if static_format == "png" else f"{n:02d}.webp"
                if static_format == "png":
                    res = _png_quantize(st.path, work / fn)
                    entries.append({"file": fn, "index": st.index, "kb": res["kb"],
                                    "encode": res["chosen"]})
                else:
                    shutil.copy(st.path, work / fn)
                    entries.append({"file": fn, "index": st.index, "kb": round(kb(st.path), 1)})

        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(work / "cover.png", "cover.png")
            z.writestr("author.txt", publisher + "\n")
            z.writestr("title.txt", name + "\n")
            for e in sorted(entries, key=lambda x: x["file"]):
                z.write(work / e["file"], e["file"])

        lint = lint_wastickers(zpath)
        warnings.extend(f"{zpath.name}: {p}" for p in lint["problems"])
        packs.append({"file": str(zpath), "name": name, "kind": kind, "count": len(chunk),
                      "indices": [e["index"] for e in entries], "cover_kb": cover["kb"],
                      "entries": entries, "lint": lint, "zip_kb": round(kb(zpath), 1)})

        # secondary: WhatsApp sample-app assets layout (only needed if you later
        # publish your own sticker app; harmless to keep alongside)
        adir = out_dir / "app_assets" / f"{idx}"
        adir.mkdir(parents=True, exist_ok=True)
        shutil.copy(work / "cover.png", adir / "tray.png")
        m_entries = []
        for n, st in enumerate(chunk, 1):
            fn = f"{n:02d}.webp"
            shutil.copy(st.path, adir / fn)
            m_entries.append({"image_file": fn, "emojis": (st.emojis or ["📎"])[:3],
                              "accessibility_text": ""})
        manifest_packs.append({
            "name": name, "identifier": f"{slug}.pack{idx}"[:128], "publisher": publisher[:128],
            "tray_image_file": "tray.png", "image_data_version": "1",
            "animated_sticker_pack": animated, "stickers": m_entries})

    if manifest_packs:
        (out_dir / "app_assets" / "contents.json").write_text(
            json.dumps({"android_play_store_link": "", "ios_app_store_link": "",
                        "sticker_packs": manifest_packs}, indent=2, ensure_ascii=False),
            encoding="utf-8")

    return {"wastickers": packs, "loose": loose, "warnings": warnings,
            "app_assets": str(out_dir / "app_assets") if manifest_packs else None}


# ------------------------------------------------------- 6. Telegram pack fetcher
def _bot_session():
    """One pooled HTTPS session with retries: 100+ sequential downloads over fresh TLS
    sockets WILL eventually hit a transient SSL EOF, so this never uses raw requests.get."""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    s = requests.Session()
    retry = Retry(total=5, backoff_factor=0.6,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET"]), raise_on_status=False)
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4))
    s.headers["Connection"] = "keep-alive"
    return s


def _bot_call(session, api: str, method: str, **params):
    r = session.get(f"{api}/{method}", params=params, timeout=30)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"{method} failed: {data.get('description')}")
    return data["result"]


def _fetch_one(index: int, spec: dict, session, api: str, bot_token: str,
               cache_dir: Path | None, set_meta: dict) -> dict:
    """Download one sticker (or read it from the raw/ cache). Safe to run concurrently:
    each sticker writes its own cache file."""
    fmt = "tgs" if spec.get("is_animated") else ("webm" if spec.get("is_video") else "webp")
    cached = (cache_dir / f"{index:03d}_{spec['file_unique_id']}.{fmt}") if cache_dir else None
    if cached and cached.exists():
        blob = cached.read_bytes()
    else:
        f = _bot_call(session, api, "getFile", file_id=spec["file_id"])
        url = f"https://api.telegram.org/file/bot{bot_token}/{f['file_path']}"
        r = session.get(url, timeout=60)
        r.raise_for_status()
        blob = r.content
        if cached:
            cached.write_bytes(blob)
    return {"title": set_meta.get("title"), "name": set_meta.get("name"), "format": fmt,
            "emoji": spec.get("emoji") or "📎", "bytes": blob,
            "tg_size": spec.get("file_size"),
            "w": spec.get("width"), "h": spec.get("height"),
            "file_unique_id": spec.get("file_unique_id")}


def fetch_pack(set_name: str, bot_token: str, cache_dir: Path | None = None,
               verbose: bool = True, jobs: int = 1, progress_callback=None) -> list[dict]:
    """Bot API: getStickerSet -> getFile -> download.

    Downloads are I/O-bound, so `jobs` > 1 uses a thread pool with one session per
    thread (requests.Session is not thread-safe). Order is preserved in the result.
    """
    api = f"https://api.telegram.org/bot{bot_token}"
    session = _bot_session()
    st = _bot_call(session, api, "getStickerSet", name=set_name)
    specs = st["stickers"]
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)

    out: list = [None] * len(specs)

    def record(i: int, item: dict) -> None:
        out[i - 1] = item
        if verbose:
            print(f"  fetched {i}/{len(specs)} ({item['format']}, "
                  f"{len(item['bytes'])/1024:.1f} KB)", flush=True)
        if progress_callback:
            try:
                progress_callback({"phase": "download", "done": sum(x is not None for x in out),
                                   "total": len(specs), "index": i, "format": item["format"]})
            except Exception:                                   # noqa: BLE001
                pass

    workers = max(1, min(int(jobs), len(specs) or 1))
    if workers == 1:
        for i, spec in enumerate(specs, 1):
            record(i, _fetch_one(i, spec, session, api, bot_token, cache_dir, st))
    else:
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed
        tls = threading.local()

        def work(i: int, spec: dict):
            sess = getattr(tls, "session", None)
            if sess is None:
                sess = tls.session = _bot_session()
            return i, _fetch_one(i, spec, sess, api, bot_token, cache_dir, st)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(work, i, spec) for i, spec in enumerate(specs, 1)]
            for fut in as_completed(futures):
                i, item = fut.result()
                record(i, item)
    session.close()
    return out


def _make_tray(sticker: Path, out_path: Path) -> None:
    """96x96 PNG tray icon from the first frame of a sticker, on a transparent canvas."""
    from PIL import Image
    img = Image.open(sticker).convert("RGBA")
    scale = WA_TRAY_PX / max(img.width, img.height)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                     Image.LANCZOS)
    tray = _square_canvas(img, WA_TRAY_PX)
    tray.save(out_path, "PNG", optimize=True)


# ------------------------------------------------------------------- 7. self-test
def self_test() -> int:
    root = Path(__file__).parent
    assets, work, out = root / "assets", root / "work", root / "out"
    work.mkdir(exist_ok=True)
    report = {"stages": [], "stickers": []}

    # ---- TGS (real Telegram animated sticker files)
    for tgs in sorted(assets.glob("*.tgs")):
        dest = work / f"{tgs.stem}_anim.webp"
        r = tgs_to_animated_webp(tgs.read_bytes(), dest)
        report["stages"].append({**r, "source": tgs.name, "output": dest.name})
        report["stickers"].append({"path": dest, "animated": True, "emoji": "🦆"})

    # ---- WEBM (spec-exact synthetic Telegram video sticker: VP9+alpha, 512x512, 30fps, ~3s)
    fake = work / "fake_telegram_video_sticker.webm"
    frames = work / "vidframes"
    frames.mkdir(exist_ok=True)
    if not fake.exists():
        from PIL import Image, ImageDraw
        for i in range(60):
            im = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
            ImageDraw.Draw(im).ellipse((60 + i * 6, 180, 300 + i * 6, 420),
                                       fill=(230, 60, 60, 255), outline=(255, 255, 255, 255), width=8)
            im.save(frames / f"{i:03d}.png")
        run([need_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-framerate", "30",
             "-i", str(frames / "%03d.png"), "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
             "-b:v", "150k", str(fake)])
    r = webm_to_animated_webp(fake.read_bytes(), work / "video_sticker_anim.webp", method=2)
    report["stages"].append({**r, "source": fake.name, "output": "video_sticker_anim.webp"})
    report["stickers"].append({"path": work / "video_sticker_anim.webp", "animated": True, "emoji": "🎬"})

    # ---- animated WebP input (as supplied by some public sticker catalogs)
    webp_source = work / "animated_webp_source.webp"
    webp_converted = work / "animated_webp_sticker.webp"
    webp_result = convert_one({"dest": str(webp_converted), "format": "webp",
                               "bytes": (work / "video_sticker_anim.webp").read_bytes(),
                               "name": webp_source.name, "index": 1,
                               "emojis": ["🔁"], "rung_jobs": 1})
    report["stages"].append({**webp_result["stage"], "source": webp_source.name,
                              "output": webp_converted.name})
    report["stickers"].append({"path": webp_converted, "animated": webp_result["animated"],
                               "emoji": "🔁"})

    # ---- static WebP (from the first rendered frame)
    from PIL import Image, ImageDraw
    src_png = work / "static_src.png"
    sim = Image.new("RGBA", (512, 448), (0, 0, 0, 0))   # note: non-square, like many TG statues
    ImageDraw.Draw(sim).polygon([(256, 40), (472, 408), (40, 408)],
                                fill=(60, 120, 230, 255), outline=(255, 255, 255, 255), width=8)
    sim.save(src_png)
    static_src = work / "static_src.webp"
    Image.open(src_png).save(static_src, "WEBP", quality=95, alpha_quality=100, method=4)
    r = static_webp_to_sticker(static_src.read_bytes(), work / "static_sticker.webp")
    report["stages"].append({**r, "source": static_src.name, "output": "static_sticker.webp"})
    report["stickers"].append({"path": work / "static_sticker.webp", "animated": False, "emoji": "🟥"})

    # ---- resume regression: a different source at the same index must NEVER be reused
    # as the output from the previous source. Then re-run the identical new source and
    # prove that a genuine interrupted-run resume still hits the matching sidecar.
    from io import BytesIO
    variant = Image.open(static_src).convert("RGBA")
    ImageDraw.Draw(variant).ellipse((36, 36, 112, 112), fill=(240, 24, 42, 255))
    variant_buffer = BytesIO()
    variant.save(variant_buffer, "WEBP", quality=95, alpha_quality=100, method=4)
    resume_work = work / "resume_identity" / "work"
    source_a = {"format": "webp", "bytes": static_src.read_bytes(),
                "name": "resume-source-a.webp", "emoji": "📎"}
    source_b = {"format": "webp", "bytes": variant_buffer.getvalue(),
                "name": "resume-source-b.webp", "emoji": "📎"}
    convert_pack([source_a], "Resume A", resume_work, verbose=False, jobs=1)
    switched = convert_pack([source_b], "Resume B", resume_work, verbose=False, jobs=1)
    repeated = convert_pack([source_b], "Resume B", resume_work, verbose=False, jobs=1)
    report["resume_regression"] = {
        "different_source_reconverted": not switched["stages"][0].get("reused", False),
        "same_source_resumed": bool(repeated["stages"][0].get("reused", False)),
        "validation_pass": all(v["pass"] for v in switched["validation"] + repeated["validation"]),
    }
    report["resume_regression"]["pass"] = all(report["resume_regression"].values())

    # ---- validation
    report["validation"] = [validate(s["path"]) for s in report["stickers"]]

    # ---- package into WhatsApp packs (needs >=3 per format, so pad for the demo)
    demo = []
    for s in report["stickers"]:
        demo.append(Sticker(s["path"], s["animated"], [s["emoji"]]))
    for kind in (True, False):
        while sum(1 for d in demo if d.animated is kind) < 3:
            v = next(d for d in demo if d.animated is kind)
            demo.append(Sticker(v.path, kind, [v.emojis[0]]))
    report["pack"] = build_packs(demo, "Telegram Import", "wa-sticker-bridge", out)

    (root / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str),
                                         encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if (all(v["pass"] for v in report["validation"])
                 and report["resume_regression"]["pass"]) else 1


# ------------------------------------------------- 7. pack acquisition + pipeline
def parse_set_name(value: str) -> str:
    """Accept a bare set name or any of the usual link shapes."""
    v = value.strip()
    for pat in (r"addstickers/([A-Za-z0-9_]+)",          # t.me/addstickers/NAME
                r"addstickers\?(?:start|set)=([A-Za-z0-9_]+)",  # tg://addstickers?set=NAME
                r"[?&]set=([A-Za-z0-9_]+)",              # ...?set=NAME
                r"^@?([A-Za-z0-9_]+)$"):                 # bare name or @name
        m = re.search(pat, v)
        if m:
            return m.group(1)
    raise ValueError(f"could not find a sticker-set name in {value!r}")


def load_from_dir(src: Path) -> list[dict]:
    """Offline mode: treat a folder of .tgs/.webm/.webp files as a pack."""
    out = []
    for f in sorted(src.iterdir()):
        ext = f.suffix.lower().lstrip(".")
        if ext in ("tgs", "webm", "webp"):
            out.append({"title": src.name, "name": src.name, "format": ext,
                        "emoji": "📎", "bytes": f.read_bytes(), "tg_size": f.stat().st_size})
    return out


def convert_one(job: dict) -> dict:
    """Convert exactly one sticker. Top-level and self-contained so it can run in a
    worker PROCESS (encoding is CPU-bound in C extensions that hold the GIL, so threads
    would not help -- separate processes do)."""
    dest = Path(job["dest"])
    fmt = job["format"]
    rung_jobs = int(job.get("rung_jobs", 1))
    process_start_method = job.get("process_start_method")
    if fmt == "tgs":
        stage = tgs_to_animated_webp(job["bytes"], dest, jobs=rung_jobs,
                                     process_start_method=process_start_method)
        animated = True
    elif fmt == "webm":
        stage = webm_to_animated_webp(job["bytes"], dest, jobs=rung_jobs,
                                      method=int(job.get("webm_method", 4)),
                                      process_start_method=process_start_method)
        animated = True
    elif fmt == "webp" and webp_is_animated(job["bytes"]):
        stage = animated_webp_to_sticker(job["bytes"], dest, jobs=rung_jobs,
                                         process_start_method=process_start_method)
        animated = True
    else:
        stage = static_webp_to_sticker(job["bytes"], dest)
        animated = False
    stage.update({"index": job["index"], "source": job["name"], "fmt": fmt,
                  "output": str(dest)})
    return {"index": job["index"], "animated": animated, "emojis": job["emojis"],
            "stage": stage}


def _print_progress(res: dict, total: int) -> None:
    stage, c = res["stage"], res["stage"]["chosen"]
    detail = ", ".join(f"{k}={v}" for k, v in c.items() if k != "kb")
    print(f"[{res['index']}/{total}] {stage['fmt']:4s} -> {Path(stage['output']).name:8s} "
          f"{'anim' if res['animated'] else 'static':6s} {c['kb']:7.1f} KB  ({detail}) "
          f"{'OK' if stage['within_limit'] else 'OVER LIMIT'}", flush=True)


# Resume cache schema. Increment whenever conversion behavior changes in a way not
# represented by the settings below. Older work/NN.webp files without matching sidecars
# are deliberately treated as cache misses (safe, though they need one re-conversion).
RESUME_CACHE_SCHEMA = 2


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resume_cache_key(item: dict, static_format: str, webm_method: int = 4) -> str:
    """Identity of the source sticker AND the conversion settings that produced its WebP.

    Index and output filename are intentionally NOT identity: a different pack commonly
    has another sticker at index 1. Hashing the source bytes prevents cross-pack reuse.
    """
    fmt = str(item.get("format", "")).lower()
    raw = item.get("bytes", b"")
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise TypeError(f"sticker {item.get('name', '')!r} has no source bytes for resume check")
    settings = {
        "schema": RESUME_CACHE_SCHEMA,
        "format": fmt,
        "static_format": static_format,
        "canvas_px": WA_STICKER_PX,
        "static_max_kb": WA_STATIC_MAX_KB,
        "static_quality_ladder": STATIC_Q_LADDER,
        "animated_max_kb": WA_ANIM_MAX_KB,
        "animated_target_kb": ANIM_TARGET_KB,
        "animated_max_sec": WA_ANIM_MAX_SEC,
        "tgs_ladder": ANIM_SIZE_LADDER,
        "webm_ladder": WEBM_LADDER,
        "animated_webp_ladder": WEBM_LADDER,
    }
    # Keep method=4's previous signature so valid outputs from the existing build can
    # resume; a non-default speed setting must never reuse method-4 output.
    if fmt == "webm" and webm_method != 4:
        settings["webm_method"] = int(webm_method)
    digest = hashlib.sha256()
    digest.update(json.dumps(settings, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest.update(b"\0")
    digest.update(raw)
    return digest.hexdigest()


def _resume_sidecar(dest: Path) -> Path:
    return dest.with_name(dest.name + ".resume.json")


def _save_resume_record(dest: Path, key: str) -> bool:
    """Atomically record a finished, validated result for safe interrupted-run resume."""
    if not dest.exists():
        return False
    try:
        result = validate(dest)
        if not result.get("pass"):
            return False
        record = {"schema": RESUME_CACHE_SCHEMA, "key": key,
                  "output_sha256": _sha256_file(dest)}
        sidecar = _resume_sidecar(dest)
        tmp = sidecar.with_name(sidecar.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, sidecar)
        return True
    except Exception:                                            # noqa: BLE001
        return False


def convert_pack(items: list[dict], title: str, work: Path, publisher: str = "wa-sticker-bridge",
                 verbose: bool = True, static_format: str = "webp", jobs: int = 1,
                 reconvert: bool = False, webm_method: int = 4,
                 progress_callback=None, process_start_method: str | None = None) -> dict:
    """items (from fetch_pack or load_from_dir) -> validated stickers -> WhatsApp pack.

    `jobs` > 1 converts stickers concurrently in worker processes. A signed per-sticker
    resume sidecar ties each saved output to its exact source bytes and encoding settings;
    a different pack reusing the same work/ directory is never mistaken for a resume.
    Report/validation order remains the original sticker order regardless of worker timing.
    Applications that dispatch jobs from server threads can set `process_start_method="spawn"`
    to avoid forking an already multi-threaded process.
    """
    work.mkdir(parents=True, exist_ok=True)
    jobs = max(1, int(jobs))
    n_items = len(items) or 1
    # Spend the cores where they pay off:
    #   many stickers -> one worker per sticker, each searching its ladder smartly
    #   one/few stickers -> several cores on that sticker, encoding several ladder
    #                       rungs at once (a single encode cannot be split further)
    item_workers = max(1, min(jobs, n_items))
    rung_jobs = min(jobs, 4) if item_workers == 1 else 1
    if verbose and (item_workers > 1 or rung_jobs > 1):
        what = (f"{item_workers} stickers at once" if item_workers > 1
                else f"{rung_jobs} ladder rungs at once per sticker")
        print(f"parallel: {what} (--jobs 1 = fully sequential)", flush=True)
    # A previous run may have been interrupted (Ctrl+C, laptop sleep, closed window).
    # Reuse a finished WebP ONLY when its sidecar proves it came from these exact source
    # bytes and current encoding settings. Index/filename alone is unsafe: a second pack
    # often has a different sticker at 01.webp.
    todo, reused = [], []
    resume_keys: dict[int, str] = {}
    completed_indices: set[int] = set()

    def notify_progress(result: dict) -> None:
        if not progress_callback:
            return
        index = result["index"]
        completed_indices.add(index)
        stage = result["stage"]
        try:
            progress_callback({"phase": "convert", "done": len(completed_indices),
                               "total": len(items), "index": index,
                               "format": stage.get("fmt"),
                               "animated": bool(result.get("animated")),
                               "kb": stage.get("chosen", {}).get("kb"),
                               "reused": bool(stage.get("reused"))})
        except Exception:                                       # noqa: BLE001
            pass

    for n, it in enumerate(items, 1):
        dest = work / f"{n:02d}.webp"
        sidecar = _resume_sidecar(dest)
        key = _resume_cache_key(it, static_format, webm_method)
        resume_keys[n] = key
        cached_validation = None
        if not reconvert and dest.exists() and sidecar.exists():
            try:
                record = json.loads(sidecar.read_text(encoding="utf-8"))
                if (record.get("schema") == RESUME_CACHE_SCHEMA
                        and record.get("key") == key
                        and record.get("output_sha256") == _sha256_file(dest)):
                    candidate = validate(dest)
                    if candidate.get("pass"):
                        cached_validation = candidate
            except Exception:                                  # noqa: BLE001
                cached_validation = None
        if cached_validation is not None:
            v = cached_validation
            reused_result = {
                "index": n, "animated": bool(v["animated"]),
                "emojis": [it.get("emoji") or "📎"],
                "stage": {"strategy": "resumed from matching source (work/)", "attempts": [],
                          "chosen": {"kb": round(kb(dest), 1)},
                          "within_limit": True, "reused": True,
                          "index": n, "source": it.get("name", f"{n:02d}"),
                          "fmt": it["format"], "output": str(dest)}}
            reused.append(reused_result)
            notify_progress(reused_result)
            if verbose:
                print(f"[{n}/{len(items)}] kept   -> {dest.name:8s} "
                      f"{'anim' if v['animated'] else 'static':6s} "
                      f"{kb(dest):7.1f} KB  (source matches; resumed)", flush=True)
            continue

        # Do not let a stale or pre-sidecar output survive if conversion fails. This also
        # makes old builds safe: their output has no signed sidecar, so it is regenerated.
        if dest.exists():
            dest.unlink()
        if sidecar.exists():
            sidecar.unlink()
        todo.append({"index": n, "format": it["format"], "bytes": it["bytes"],
                     "name": it.get("name", f"{n:02d}"),
                     "emojis": [it.get("emoji") or "📎"],
                     "dest": str(dest), "rung_jobs": rung_jobs,
                     "webm_method": webm_method,
                     "process_start_method": process_start_method})
    if reused and verbose:
        print(f"{len(reused)} of {len(items)} sticker(s) match the saved source and settings; "
              "reusing them. Other source files are always re-converted.", flush=True)

    results: list[dict] = reused
    if item_workers > 1 and len(todo) > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        pool_options = {}
        if process_start_method:
            import multiprocessing
            pool_options["mp_context"] = multiprocessing.get_context(process_start_method)
        with ProcessPoolExecutor(max_workers=item_workers, **pool_options) as pool:
            futures = [pool.submit(convert_one, job) for job in todo]
            for fut in as_completed(futures):
                res = fut.result()
                _save_resume_record(Path(res["stage"]["output"]), resume_keys[res["index"]])
                results.append(res)
                notify_progress(res)
                if verbose:
                    _print_progress(res, len(todo))
    else:
        for job in todo:
            res = convert_one(job)
            _save_resume_record(Path(res["stage"]["output"]), resume_keys[res["index"]])
            results.append(res)
            notify_progress(res)
            if verbose:
                _print_progress(res, len(todo))

    results.sort(key=lambda r: r["index"])       # deterministic output, whatever the finish order
    stages = [r["stage"] for r in results]
    stickers = [Sticker(Path(r["stage"]["output"]), r["animated"], r["emojis"],
                        index=r["index"]) for r in results]

    validation = [validate(s.path) for s in stickers]
    pack = build_packs(stickers, title, publisher, work.parent, static_format=static_format)
    return {"title": title, "stages": stages, "validation": validation, "pack": pack,
            "passed": all(v["pass"] for v in validation)}


def write_preview(report: dict, out_path: Path, source_note: str = "",
                  live_count: int = 6) -> Path:
    """Self-contained HTML proof (no network needed).

    A 50-sticker pack inlined as animation would be ~25MB, so the preview shows the
    hardest-to-convert stickers playing live (the interesting evidence) and the rest
    as first-frame thumbnails. The real files are in the .wastickers zip.
    """
    import base64
    from io import BytesIO
    from PIL import Image
    rep = report

    def data(p, mime):
        return f"data:{mime};base64,{base64.b64encode(Path(p).read_bytes()).decode()}"

    def thumb(p):
        im = Image.open(p).convert("RGBA")
        im.thumbnail((160, 160), Image.LANCZOS)
        buf = BytesIO()
        im.save(buf, "PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    def figure(p, label, live=True):
        src = data(p, "image/webp") if live else thumb(p)
        tag = "<figcaption>" + h(label) + ("</figcaption>" if live else " <em>(thumb)</em></figcaption>")
        return f'<figure><img src="{src}" alt="{h(label)}">{tag}</figure>'

    def h(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # pick the stickers that took the most encoder attempts = the hard ones
    hardest = sorted((s for s in rep["stages"] if s.get("attempts")),
                     key=lambda s: -len(s["attempts"]))[:live_count]
    live_idx = {s["index"] for s in hardest}

    val_rows = ""
    for v in rep["validation"]:
        badge = ('<span style="color:#4ade80;font-weight:700">PASS</span>' if v["pass"]
                 else '<span style="color:#f87171;font-weight:700">FAIL</span>')
        warn = "; ".join(v.get("warnings", []))
        val_rows += (f"<tr><td>{h(v['file'])}</td><td>{h(v['canvas'])}</td>"
                     f"<td>{'animated' if v['animated'] else 'static'}</td>"
                     f"<td>{'yes' if v['alpha'] else 'no'}</td><td>{v['kb']:.1f} KB</td>"
                     f"<td>{v['limit_kb']} KB</td><td>{v['duration_ms']} ms</td>"
                     f"<td>{badge}</td><td class='warn'>{h(warn)}</td></tr>")

    stage_rows = ""
    for s in rep["stages"]:
        c = s["chosen"]
        settings = ", ".join(f"{k}={v}" for k, v in c.items() if k != "kb")
        stage_rows += (f"<tr><td>{h(s['source'])}</td><td>{h(s['fmt'])}</td>"
                       f"<td>{h(s['strategy'])}</td><td>{h(settings)}</td>"
                       f"<td>{c['kb']} KB</td></tr>")

    ladder_rows = ""
    for s in rep["stages"]:
        if not s.get("attempts"):
            continue
        cells = "".join(f"<td>{a.get('fps','-')}fps</td><td>{a['kb']:.0f}</td>" for a in s["attempts"])
        ladder_rows += f"<tr><td>{h(s['source'])}</td>{cells}</tr>"

    tries = [len(s["attempts"]) for s in rep["stages"] if s.get("attempts")]
    deg = [s for s in rep["stages"] if len(s.get("attempts", [])) > 1]
    stats = (f"{len(rep['validation'])} stickers &middot; "
             f"{sum(1 for v in rep['validation'] if v['pass'])} pass &middot; "
             f"{len(deg)} needed more than one encode pass &middot; "
             f"deepest ladder {max(tries) if tries else 0} rungs &middot; "
             f"largest output {max((v['kb'] for v in rep['validation']), default=0):.0f} KB")
    packs_html = ""
    pack_info = rep["pack"]
    out_by_index = {s["index"]: Path(s["output"]) for s in rep["stages"] if s.get("output")}
    if pack_info.get("wastickers"):
        for p in pack_info["wastickers"]:
            imgs = ""
            for i in p["indices"]:
                f = out_by_index.get(i)
                if f and f.exists():
                    imgs += figure(f, f.name, live=i in live_idx)
            lint = p.get("lint", {})
            verdict = ("<span style='color:#4ade80;font-weight:700'>LINT OK</span>" if lint.get("ok")
                       else "<span style='color:#f87171;font-weight:700'>LINT FAIL</span>")
            problems = "".join(f"<li>{h(x)}</li>" for x in lint.get("problems", []))
            packs_html += (
                f'<div class="pack"><h3>{h(Path(p["file"]).name)} '
                f'<span class="badge">{h(p["kind"])}, {p["count"]} stickers</span> {verdict}</h3>'
                f'<p class="sub">Flat zip: cover.png ({p["cover_kb"]} KB) + author.txt + title.txt + '
                f'{p["count"]} {"webp" if p["kind"] == "Animated" else "png"} files &middot; {p["zip_kb"]} KB</p>'
                f'{f"<ul class=warnlist>{problems}</ul>" if problems else ""}'
                f'<div class="grid">{imgs}</div></div>')

    doc = f"""<!doctype html><meta charset="utf-8"><title>{h(rep['title'])} — WhatsApp pack</title>
<style>
 body{{margin:0;background:#0b141a;color:#e9edef;font:15px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}}
 .wrap{{max-width:1080px;margin:0 auto;padding:28px 20px 60px}}
 h1{{font-size:25px;margin:0 0 4px}} h2{{font-size:18px;margin:32px 0 10px;color:#8fd6c8}} h3{{font-size:15px;margin:0 0 10px}}
 .sub{{color:#8696a0;margin:0 0 20px;font-size:13.5px}}
 table{{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}}
 th,td{{border:1px solid #23343d;padding:7px 9px;text-align:left;vertical-align:top}}
 th{{background:#16222a;color:#a9c4cf;font-weight:600}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:12px}}
 figure{{margin:0;background:#16222a;border:1px solid #23343d;border-radius:10px;padding:9px;text-align:center}}
 figure img{{width:100%;height:auto;border-radius:8px;background:repeating-conic-gradient(#1f2c34 0% 25%,#26343d 0% 50%) 50%/16px 16px}}
 figcaption{{font-size:11px;color:#8696a0;margin-top:7px}}
 .pack{{background:#111d24;border:1px solid #23343d;border-radius:12px;padding:16px;margin:14px 0}}
 .badge{{display:inline-block;background:#0a7d33;color:#fff;border-radius:20px;padding:2px 9px;font-size:11px;font-weight:700}}
 .warn{{color:#fbbf24}} .warnlist{{color:#fbbf24;font-size:12.5px;margin:0 0 10px;padding-left:18px}}
 code{{background:#16222a;padding:1px 5px;border-radius:4px;font-size:12.5px}}
</style>
<div class="wrap">
<h1>{h(rep['title'])} — converted to WhatsApp format</h1>
<p class="sub">{h(source_note)}Stickers marked live are the real converted files playing in your browser
 (picked as the hardest ones to squeeze under the cap); the rest are first-frame thumbnails —
 all {len(rep['validation'])} files are in the archive under their full size.</p>
{f'<h2>.wastickers packs ({len(pack_info["wastickers"])})</h2>{packs_html}' if packs_html else '<p class="warn">No pack could be built.</p>'}
<p class="sub">{stats}</p>
<h2>Validation against WhatsApp's caps</h2>
<table><tr><th>file</th><th>canvas</th><th>kind</th><th>alpha</th><th>size</th><th>cap</th><th>duration</th><th></th><th>warnings</th></tr>{val_rows}</table>
<h2>Conversion path per sticker</h2>
<table><tr><th>source</th><th>format</th><th>strategy</th><th>settings</th><th>result</th></tr>{stage_rows}</table>
{f'<h2>Encoder ladder (fps / size per attempt)</h2><table><tr><th>source</th><th colspan="{max(2*len(s["attempts"]) for s in rep["stages"] if s.get("attempts"))}">each column pair = fps, resulting KB</th></tr>{ladder_rows}</table>' if ladder_rows else ''}
<p class="sub">Import: share one <code>.wastickers</code> file at a time to a host app (Sticker Maker Studio etc.), then add the pack inside WhatsApp. The <code>app_assets/</code> folder is the WhatsApp sample-app layout — only needed if you publish your own sticker app.</p>
</div>"""
    out_path.write_text(doc, encoding="utf-8")
    return out_path


def repackage(out_dir: Path, static_format: str = "webp") -> int:
    """Re-run only the packaging stage from a previous run (work/*.webp + report.json)."""
    rep = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    stickers = []
    for st in rep["stages"]:
        p = out_dir / "work" / f"{st['index']:02d}.webp"
        if not p.exists():
            print(f"missing {p}; re-run the conversion instead", file=sys.stderr)
            return 2
        stickers.append(Sticker(p, animated=st["fmt"] in ("tgs", "webm"),
                                emojis=["📎"], index=st["index"]))
    rep["pack"] = build_packs(stickers, rep["title"], "wa-sticker-bridge", out_dir,
                              static_format=static_format)
    (out_dir / "report.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str),
                                            encoding="utf-8")
    print("--- .wastickers packs ---")
    for w in rep["pack"].get("warnings", []):
        print(f"  {WARN} {w}")
    for p in rep["pack"].get("wastickers", []):
        flag = "LINT OK  " if p["lint"]["ok"] else "LINT FAIL"
        print(f"{flag} {Path(p['file']).name} ({p['kind'].lower()}, {p['count']} stickers, "
              f"{p['zip_kb']} KB)")
        for prob in p["lint"]["problems"]:
            print(f"    {CROSS} {prob}")
    if static_format == "png" and any(p["kind"] == "Static"
                                      for p in rep["pack"].get("wastickers", [])):
        _static_png_hint(out_dir)

    write_preview(rep, out_dir / "preview.html",
                  source_note=f"Repackaged from {out_dir.name}. ")
    print(f"preview  {out_dir / 'preview.html'}")
    return 0


# --------------------------------------------------- 11. dependency preflight
# Every import this tool needs is lazily executed deep inside the pipeline, which
# means a missing package used to surface as a raw ModuleNotFoundError traceback
# halfway through a conversion (after the whole pack had been downloaded). Check
# everything up front instead and fail with instructions a human can act on.
_PKGS = {
    "PIL":            ("Pillow",          "encodes and decodes every image (required)"),
    "requests":       ("requests",        "fetches the pack from the Telegram Bot API"),
    "rlottie_python": ("rlottie-python",  "renders Telegram .tgs animations"),
    "imageio_ffmpeg": ("imageio-ffmpeg",  "ships the ffmpeg binary used for .webm video stickers"),
}


def preflight(*, fetching: bool, suffixes: set[str] | None = None) -> list[tuple[str, str]]:
    """Return [(pip package, why it is needed)] for anything missing."""
    import importlib
    needed = ["PIL"]
    if fetching:
        needed.append("requests")
    sfx = suffixes or set()
    if fetching or ".tgs" in sfx:
        needed.append("rlottie_python")
    if fetching or (sfx & {".webm", ".mp4", ".mov"}):
        needed.append("imageio_ffmpeg")

    missing: list[tuple[str, str]] = []
    for mod in needed:
        try:
            importlib.import_module(mod)
        except Exception:                                        # noqa: BLE001
            missing.append(_PKGS[mod])
    if any(mod == "imageio_ffmpeg" for mod in needed) and not find_ffmpeg() \
            and _PKGS["imageio_ffmpeg"] not in missing:
        missing.append(("imageio-ffmpeg", "installed, but no ffmpeg binary could be found"))
    return missing


def dep_error(missing: list[tuple[str, str]]) -> int:
    print()
    print("Missing Python package(s) -- nothing was converted yet.")
    print()
    for pkg, why in missing:
        print(f"  - {pkg:<16s} {why}")
    print()
    print("Fix it with ONE of these commands, run from this folder:")
    print()
    print("  python install.py                              (installs + verifies everything)")
    print("  python -m pip install -r requirements.txt      (or the 4 packages by hand)")
    print()
    print("Notes:")
    print("  * the package is called 'Pillow' -- there is no 'pip install pil' or 'pillow'")
    print("  * use 'python -m pip ...', not bare 'pip ...': on Windows 'pip' can belong to a")
    print("    different Python than the one running convert.py, so the packages land in the")
    print("    wrong place and the import still fails")
    print("  * if pip says 'Defaulting to user installation', that is fine -- no admin needed")
    return 2


def _static_png_hint(out_dir: Path) -> None:
    """Warn about the one import failure that is NOT visible in the lint.

    Some sticker apps follow the WhatsApp sample-app spec, where static stickers are
    single-frame .webp too -- those apps read .png entries as pack icons and then show
    the pack with zero stickers ("empty"). The pack is valid either way; only the
    payload extension differs, so this is a 10-second rebuild, not a re-conversion.
    """
    print()
    print("note: you chose .png static stickers (the legacy .wastickers convention). If a")
    print("      sticker app imports one but shows an EMPTY pack or says 'at least 3 stickers")
    print("      required', that app only counts .webp entries. Rebuild in seconds, no")
    print("      re-converting -- the default already does this:")
    print(f'        python convert.py --repackage "{out_dir}"')
    print("      (equivalently: --static-format webp)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Telegram -> WhatsApp sticker converter")
    ap.add_argument("--pack", help="sticker set name or link, e.g. https://t.me/addstickers/FunnyAnimals")
    ap.add_argument("--token", default=os.environ.get("TG_BOT_TOKEN"), help="Bot API token (or env TG_BOT_TOKEN)")
    ap.add_argument("--from-dir", help="offline mode: convert a folder of .tgs/.webm/.webp files")
    ap.add_argument("--title", help="override the WhatsApp pack name")
    ap.add_argument("--publisher", default="wa-sticker-bridge")
    ap.add_argument("--out", default="./out")
    ap.add_argument("--static-format", choices=("png", "webp"), default="webp",
                    help="static stickers inside .wastickers: webp (default, WhatsApp's native "
                         "static format -- what your working packs use) or png (legacy apps)")
    ap.add_argument("--repackage", metavar="OUT_DIR",
                    help="rebuild packs/preview from an existing run's work/ + report.json")
    ap.add_argument("--reconvert", action="store_true",
                    help="re-encode all stickers, even if the exact source/settings match "
                         "a saved resume record (default: resume matching stickers)")
    ap.add_argument("--webm-method", type=int, choices=range(7), default=4, metavar="0-6",
                    help="WebP encoder effort for video stickers: 2 is faster (~1.5x in a hard 90-frame test), "
                         "4 is the quality/size default; 0-6 are Pillow/libwebp settings")
    ap.add_argument("--jobs", type=int, default=0, metavar="N",
                    help="convert N stickers in parallel (default: one per CPU core, "
                         "capped at 8; use 1 for sequential/log-friendly output)")
    ap.add_argument("--self-test", action="store_true",
                    help="bundled regression proof: run the whole pipeline on local samples")
    a = ap.parse_args()
    jobs = a.jobs if a.jobs and a.jobs > 0 else min(os.cpu_count() or 1, 8)

    fetch_mode = bool(a.pack) and not a.from_dir
    suffixes: set[str] = set()
    if a.from_dir and Path(a.from_dir).is_dir():
        suffixes = {f.suffix.lower() for f in Path(a.from_dir).iterdir() if f.is_file()}
    if a.self_test:
        suffixes |= {".tgs", ".webm", ".webp"}
    missing = preflight(fetching=fetch_mode or a.self_test, suffixes=suffixes)
    if missing:
        return dep_error(missing)

    if a.self_test:
        return self_test()
    if a.repackage:
        return repackage(Path(a.repackage), static_format=a.static_format)

    out = Path(a.out).resolve()
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)

    if a.from_dir:
        src = Path(a.from_dir)
        items = load_from_dir(src)
        if not items:
            print(f"no .tgs/.webm/.webp files in {src}", file=sys.stderr)
            return 2
        title = a.title or src.name
        note = f"Source: local folder <code>{h_(src)}</code>. "
    elif a.pack:
        name = parse_set_name(a.pack)
        if not a.token:
            print("This pack needs the Telegram Bot API, which requires a bot token.\n"
                  "  1. Open Telegram, talk to @BotFather, send /newbot, follow the prompts\n"
                  "  2. Then run:  TG_BOT_TOKEN=<token> python3 convert.py --pack "
                  f"{a.pack!r} --out {a.out}\n"
                  "(The token only ever talks to api.telegram.org from your machine.)",
                  file=sys.stderr)
            return 3
        items = fetch_pack(name, a.token, cache_dir=out / "raw", jobs=jobs)
        title = a.title or (items[0].get("title") if items else name) or name
        note = f"Source: Telegram pack <code>{h_(name)}</code> ({len(items)} stickers). "
    else:
        ap.error("give --pack (with --token) or --from-dir")

    if not items:
        print("the pack came back empty", file=sys.stderr)
        return 4

    type_counts = {fmt: sum(1 for it in items if it["format"] == fmt)
                   for fmt in ("webm", "tgs", "webp")}
    labels = {"webm": "animated/video WEBM", "tgs": "animated TGS", "webp": "static WebP"}
    summary = ", ".join(f"{count} {labels[fmt]}" for fmt, count in type_counts.items() if count)
    print(f"{len(items)} sticker(s) from {title!r} ({summary})")
    report = convert_pack(items, title, work, publisher=a.publisher,
                          static_format=a.static_format, jobs=jobs,
                          reconvert=a.reconvert, webm_method=a.webm_method)
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str),
                                     encoding="utf-8")

    print("\n--- validation ---")
    for v in report["validation"]:
        flag = "OK  " if v["pass"] else "FAIL"
        print(f"{flag} {v['file']:8s} {v['canvas']:>9s} {'anim' if v['animated'] else 'static':6s} "
              f"alpha={'y' if v['alpha'] else 'n'} {v['kb']:7.1f}/{v['limit_kb']} KB "
              f"{v['duration_ms']} ms" + (f"  {WARN} " + "; ".join(v["warnings"]) if v["warnings"] else ""))

    print("\n--- .wastickers packs ---")
    for w in report["pack"].get("warnings", []):
        print(f"  {WARN} {w}")
    for p in report["pack"].get("wastickers", []):
        flag = "LINT OK  " if p["lint"]["ok"] else "LINT FAIL"
        print(f"{flag} {Path(p['file']).name}  ({p['kind'].lower()}, {p['count']} stickers, "
              f"{p['zip_kb']} KB, cover {p['cover_kb']} KB)")
        for prob in p["lint"]["problems"]:
            print(f"    {CROSS} {prob}")

    if a.static_format == "png" and any(p["kind"] == "Static"
                                         for p in report["pack"].get("wastickers", [])):
        _static_png_hint(out)

    preview = write_preview(report, out / "preview.html", source_note=note)
    print(f"\npreview  {preview}\nreport   {out / 'report.json'}")
    return 0 if report["passed"] else 1


def h_(x) -> str:
    """Minimal HTML escape for the CLI-side note."""
    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ModuleNotFoundError as e:                              # pragma: no cover
        sys.exit(dep_error([(getattr(e, "name", "?") or "?", "imported at runtime and missing")]))
