#!/usr/bin/env python3
"""
One-step installer + verifier for the Telegram -> WhatsApp sticker converter.

    python install.py              install the 4 pip packages, then verify them
    python install.py --verify     also run the end-to-end self-test (~30 s)
    python install.py --no-install skip pip, only verify what is already there

Pure Python + pip: identical on Windows, macOS and Linux. No admin rights, no
system packages, no shell scripts, nothing to compile.

Replaces the old setup.sh / setup.ps1 -- those needed bash or PowerShell, which
is exactly the "won't work on Windows" problem.
"""
from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIREMENTS = HERE / "requirements.txt"
MIN_PY = (3, 9)

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if os.name == "nt":                      # old cmd.exe: colour codes print as garbage
    GREEN = RED = YELLOW = DIM = RESET = ""


def ok(msg: str) -> None:
    print(f"  {GREEN}OK{RESET}    {msg}")


def bad(msg: str) -> None:
    print(f"  {RED}FAIL{RESET}  {msg}")


def note(msg: str) -> None:
    print(f"  {YELLOW}note{RESET}  {msg}")


def run_pip(packages: list[str], quiet: bool = True) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    if quiet:
        cmd.append("--quiet")
    cmd += packages
    print(f"  {DIM}$ {' '.join(cmd)}{RESET}")
    return subprocess.call(cmd) == 0


def check_python() -> bool:
    v = sys.version_info
    if (v.major, v.minor) < MIN_PY:
        bad(f"Python {v.major}.{v.minor} is too old -- need {MIN_PY[0]}.{MIN_PY[1]} or newer")
        print("       Install it from https://www.python.org/downloads/")
        print('       (Windows: tick "Add python.exe to PATH" in the installer.)')
        return False
    ok(f"Python {v.major}.{v.minor}.{v.micro}  [{platform.system()} {platform.machine()}]")
    return True


def verify(deep: bool) -> bool:
    """Import everything and prove the pieces actually work -- not just importable."""
    good = True

    # --- imports -------------------------------------------------------------
    try:
        import PIL
        from PIL import Image
        ok(f"Pillow {PIL.__version__}")
    except Exception as e:                                        # pragma: no cover
        bad(f"Pillow missing ({e}) -- run: {sys.executable} -m pip install Pillow")
        return False

    try:
        import rlottie_python
        ok("rlottie-python (renders Telegram .tgs animations)")
    except Exception as e:                                        # pragma: no cover
        bad(f"rlottie-python missing ({e}) -- run: "
            f"{sys.executable} -m pip install rlottie-python")
        good = False

    try:
        import requests
        ok(f"requests {requests.__version__} (Telegram Bot API)")
    except Exception as e:                                        # pragma: no cover
        bad(f"requests missing ({e}) -- run: {sys.executable} -m pip install requests")
        good = False

    # --- the one capability that must work: animated WebP with alpha ---------
    try:
        import io
        f1 = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
        f2 = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        buf = io.BytesIO()
        f1.save(buf, "WEBP", save_all=True, append_images=[f2], duration=100, loop=0,
                quality=80, alpha_quality=80)
        buf.seek(0)
        im = Image.open(buf)
        n_frames = getattr(im, "n_frames", 1)
        if getattr(im, "is_animated", False) and n_frames == 2:
            ok("animated WebP write + read (required for every sticker)")
        else:
            bad("this Pillow cannot write animated WebP -- upgrade with: "
                f"{sys.executable} -m pip install -U Pillow")
            good = False
    except Exception as e:
        bad(f"animated WebP encode failed: {e}")
        good = False

    # --- ffmpeg: bundled wheel, or a system install if present --------------
    try:
        sys.path.insert(0, str(HERE))
        import convert                                    # reuses the tool's own resolver
        found = convert.find_ffmpeg()
        if found:
            kind = "bundled (imageio-ffmpeg)" if "site-packages" in str(found) or \
                "dist-packages" in str(found) else "system install"
            ok(f"ffmpeg ready -- {kind}: {found}")
        else:
            bad("no ffmpeg at all -- video stickers (.webm) would fail. Fix: "
                f"{sys.executable} -m pip install imageio-ffmpeg")
            good = False
    except Exception as e:
        bad(f"could not load convert.py ({e}) -- run install.py from its own folder")
        good = False

    # --- optional extras: informational only --------------------------------
    import shutil
    ff = shutil.which("ffmpeg")
    if ff:
        print(f"  {DIM}info{RESET}  system ffmpeg on PATH ({ff}) -- will be preferred")
    pq = shutil.which("pngquant")
    if pq:
        print(f"  {DIM}info{RESET}  pngquant on PATH ({pq}) -- used for static PNGs")
    else:
        print(f"  {DIM}info{RESET}  pngquant not installed -- fine, Pillow's own "
              f"quantizer is used")

    # --- deep: end-to-end ---------------------------------------------------
    if deep:
        print()
        print("== end-to-end self-test (TGS + WEBM + static -> .wastickers) ==")
        rc = subprocess.call([sys.executable, "-u", str(HERE / "convert.py"), "--self-test"],
                             cwd=str(HERE))
        if rc == 0:
            ok("self-test passed")
        else:
            bad(f"self-test exited {rc}")
            good = False
    return good


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-install", action="store_true", help="skip pip, verify only")
    ap.add_argument("--verify", action="store_true", help="also run the end-to-end self-test")
    a = ap.parse_args()

    os.chdir(HERE)                       # works even if double-clicked on Windows
    print("Telegram -> WhatsApp sticker converter - install & verify")
    print("=" * 62)
    print()
    print("== 1. python ==")
    if not check_python():
        return 1

    if not a.no_install:
        print()
        print("== 2. pip packages ==")
        if not REQUIREMENTS.exists():
            bad(f"{REQUIREMENTS} not found -- is convert.py next to install.py?")
            return 1
        if not run_pip(["-r", str(REQUIREMENTS)]):
            bad("pip failed -- check your internet connection / proxy, then re-run")
            return 1

    print()
    print("== 3. verify ==")
    good = verify(deep=a.verify)

    print()
    if good:
        print(f"{GREEN}Everything works.{RESET} Next:")
        print('  python convert.py --self-test')

        print('  python convert.py --pack "https://t.me/addstickers/SomePack" --out ./out')
        return 0
    print(f"{RED}Something is not right -- see the FAIL lines above.{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
