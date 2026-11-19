#!/usr/bin/env python3
"""Build a diagnostic matrix of .wastickers variants, 3 stickers each.

Android host apps are less standardized than the iOS one, and containers observed in
the wild disagree on naming (canonical `cover.png` + zero-padded `01.webp` vs legacy
`0.png` icon + single-digit `1.webp`). Extra files are normally ignored by importers,
so a hybrid is also offered. Each pack is 3 stickers = below WhatsApp's useful size but
valid, and small enough to test in seconds.

Download the whole folder, then try each file: whichever imports tells us the convention.
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from convert import Sticker, _png_quantize, kb  # noqa: E402
from PIL import Image  # noqa: E402

OUT = Path(__file__).parent / "diagnostic"
STATIC_SRC = [Path(__file__).parent / f"poshlikoti_out/work/{i:02d}.webp" for i in (1, 2, 4)]
ANIM_SRC = [Path(__file__).parent / f"mrrobot_out/work/{i:02d}.webp" for i in (1, 3, 5)]


def cover_from(src: Path, px: int = 96) -> bytes:
    im = Image.open(src).convert("RGBA")
    s = px / max(im.width, im.height)
    im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    canvas = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    canvas.paste(im, ((px - im.width) // 2, (px - im.height) // 2))
    buf = io.BytesIO()
    canvas.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def payload(src: Path, ext: str, tmp: Path) -> bytes:
    """Static -> quantized PNG, static-webp -> webp as-is, animated -> webp as-is.

    Importers infer sticker kind from the file extension, so the extension is what
    matters here, not just the bytes.
    """
    if ext == ".png":
        out = tmp / (src.stem + ".png")
        _png_quantize(src, out)
        return out.read_bytes()
    return src.read_bytes()


def build(name: str, srcs: list[Path], kind: str, *, icon: str, pad: int,
          start: int, extras: bool = False, ext: str | None = None) -> Path:
    d = OUT / name
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "_tmp"
    tmp.mkdir(exist_ok=True)
    ext = ext or (".png" if kind == "static" else ".webp")
    title = f"DIAG {name}"
    zpath = d / f"{name}.wastickers"
    entries, files = [], []
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(icon, cover_from(srcs[0]))
        z.writestr("title.txt", title + "\n")
        z.writestr("author.txt", "wa-sticker-bridge\n")
        for n, s in enumerate(srcs, start=start):
            fn = f"{n:0{pad}d}{ext}"
            z.writestr(fn, payload(s, ext, tmp))
            entries.append({"image_file": fn, "emojis": ["📎"], "accessibility_text": ""})
            files.append(fn)
        if extras:
            z.writestr("tray.png", cover_from(srcs[0]))
            z.writestr("contents.json", json.dumps({
                "android_play_store_link": "", "ios_app_store_link": "",
                "sticker_packs": [{
                    "name": title, "identifier": f"diag.{name}", "publisher": "wa-sticker-bridge",
                    "tray_image_file": "tray.png", "image_data_version": "1",
                    "animated_sticker_pack": kind != "static", "stickers": entries}]},
                indent=2))
    return zpath


def main() -> None:
    OUT.mkdir(exist_ok=True)
    built = []
    # axis 1: container naming convention (animated payload, so size caps never interfere)
    built.append(build("A_anim_cover_01", ANIM_SRC, "anim", icon="cover.png", pad=2, start=1))
    built.append(build("B_anim_icon0_1", ANIM_SRC, "anim", icon="0.png", pad=0, start=1))
    built.append(build("C_anim_hybrid", ANIM_SRC, "anim", icon="cover.png", pad=2, start=1,
                       extras=True))
    # axis 2: static sticker file format (canonical naming)
    built.append(build("D_static_png_01", STATIC_SRC, "static", icon="cover.png", pad=2, start=1))
    built.append(build("E_static_webp_01", STATIC_SRC, "static", icon="cover.png", pad=2,
                       start=1, ext=".webp"))
    # axis 3: legacy naming + static png (covers the 2019-style container)
    built.append(build("F_static_png_icon0", STATIC_SRC, "static", icon="0.png", pad=0, start=1))

    print(f"{'file':34s} {'KB':>7s}  {'kind':6s}  entries")
    for p in built:
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
        ignore = {"cover.png", "0.png", "tray.png"}
        payload_exts = {Path(n).suffix for n in names if n not in ignore
                        and not n.endswith(".txt") and n != "contents.json"}
        kind = f"static{','.join(sorted(payload_exts))}"
        print(f"{p.name:34s} {kb(p):7.1f}  {kind:6s}  {names}")


if __name__ == "__main__":
    main()
