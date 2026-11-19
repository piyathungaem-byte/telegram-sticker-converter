#!/usr/bin/env python3
"""Verify every diagnostic .wastickers with the real importer linter and media checks."""
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from convert import lint_wastickers  # noqa: E402
from PIL import Image  # noqa: E402

META = {"title.txt", "author.txt", "tray.png", "contents.json"}

for f in sorted((Path(__file__).parent / "diagnostic").glob("*.wastickers")):
    lint = lint_wastickers(f)
    z = zipfile.ZipFile(f)
    names = z.namelist()
    icon = "cover.png" if "cover.png" in names else "0.png"
    im = Image.open(io.BytesIO(z.read(icon)))
    payloads = [n for n in names if n not in META and n != icon]
    p0 = Image.open(io.BytesIO(z.read(payloads[0])))
    print(f"{f.name:32s} lint={'OK  ' if lint['ok'] else 'FAIL'} "
          f"icon={im.size[0]}x{im.size[1]} payload={len(payloads)}x{p0.format} "
          f"canvas={p0.size[0]}x{p0.size[1]} frames={getattr(p0, 'n_frames', 1)} "
          f"alpha={'A' in p0.mode or 'transparency' in p0.info} "
          f"| {lint['problems'] or 'no problems'}")
