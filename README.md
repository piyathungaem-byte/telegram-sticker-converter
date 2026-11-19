# Telegram → WhatsApp Sticker Converter

Convert a public Telegram sticker pack into one or more WhatsApp-importable `.wastickers` files.
The **local browser app** is the easy way to use it; the original command-line converter remains
available for advanced runs.

The app and conversion run on your own Windows PC. The browser UI is embedded in `webapp.py`, so
there is no Node.js, web framework, Redis, Docker, or hosted service to configure.

## Quick start — local browser app

### 1. Install once

Extract the ZIP, open the extracted folder in PowerShell, and run:

```powershell
python install.py
```

The installer uses pip and prebuilt wheels. Python 3.9+ is supported; Python 3.11+ (64-bit) is
recommended. If the `python` command is unavailable, try `py install.py`.

To keep the packages in a virtual environment instead:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe install.py
```

### 2. Start Sticker Studio

```powershell
python webapp.py
```

Your browser opens `http://127.0.0.1:8765`. If it does not open automatically, visit that address.
Keep the PowerShell window open while converting; press `Ctrl+C` in it when you are done. For a
virtual environment, launch with `.\.venv\Scripts\python.exe webapp.py` instead.

### 3. Fetch, select, and create

1. Paste a public Telegram sticker-pack link, for example `https://t.me/addstickers/YourPack`.
2. Paste a Bot token from **@BotFather**. To reuse it on this PC, check **Remember token on this
   PC** before fetching. Windows encrypts the saved token for your account; use **Forget saved token**
   to remove it. Leave the box unchecked for one-time use.
3. Click **Fetch sticker pack**. The app shows a thumbnail for each fetched sticker and expands a live activity log while work is running.
4. Tick the stickers you want and click **Create selected pack**, or click **Convert full pack**.
   Quality and CPU worker count are automatic. Static and animated stickers are kept in separate
   WhatsApp packs; a selected group needs at least 3 of one type.
5. Preview the created pack in the browser, then download its `.wastickers` file(s).

The token is used only by the local Python app to call Telegram. It is never sent to Arena or another
website. On Windows, the optional saved copy is encrypted with DPAPI and is never returned to the
browser. Remember-token storage is disabled on non-Windows systems.

## Output and WhatsApp import

The app creates these folders beside `webapp.py`:

- `webapp-runs/` — only the finished `.wastickers` download(s), grouped by conversion run.
- `webapp-cache/` — private working cache that speeds up repeat conversions. Deleting it is safe,
  but the next run must download and convert that pack again.

Transfer the downloaded `.wastickers` file to your Android phone. Open it with **Sticker Maker**
(or another compatible sticker-host app), then choose that app's **Add to WhatsApp** action. Import
one file at a time.

The converter separates static and animated stickers and splits packs at 30 stickers. It creates
only importable packs; a group with fewer than 3 stickers is skipped because WhatsApp won't import it.

## Optional: command-line converter

The local app is recommended for everyday use. For the CLI, set your token in the current PowerShell
window and run `convert.py`:

```powershell
$env:TG_BOT_TOKEN = "paste-your-token-here"
python convert.py --pack "https://t.me/addstickers/YourPack" --out .\out
```

Useful options:

```powershell
python convert.py --pack "https://t.me/addstickers/YourPack" --out .\out --jobs 8
python convert.py --pack "https://t.me/addstickers/YourPack" --out .\out --webm-method 2
python convert.py --from-dir .\my_stickers --out .\out
python convert.py --repackage .\out
python convert.py --self-test
```

`--webm-method 2` can encode some video stickers faster, but may reduce smoothness on difficult
stickers; method 4 remains the default. Re-running the same CLI conversion resumes only when the
source bytes and conversion settings match. Changing the Telegram pack never reuses the prior
pack's converted stickers, even when the same output folder is used.

## Requirements and troubleshooting

- Windows 10/11 and Python 3.11+ (64-bit recommended).
- Install dependencies with `python install.py`; no system ffmpeg, compiler, or administrator
  rights are required. `imageio-ffmpeg` supplies the ffmpeg binary.
- `python` is not recognized: use `py` or reinstall Python with **Add Python to PATH** enabled.
- Missing packages: stop the app, run `python install.py` from the extracted folder, and restart.
- Port 8765 is busy: run `python webapp.py --port 8766` and open `http://127.0.0.1:8766`.
- Telegram rejects the token: check it with @BotFather. Revoke and replace it if it was shared.
- No pack was created: each static or animated group needs at least three stickers.

## WhatsApp format checks

The converter validates 512×512 WebP output, transparency, file size and animation constraints.
Static stickers must be at most 100 KB; animated stickers at most 500 KB. WhatsApp sticker packs
hold 3–30 stickers and may not mix static with animated stickers. The `.wastickers` file is a ZIP
container intended to be imported through a compatible sticker-host app, not directly through
WhatsApp's chat screen.

## Files

| File/folder | Purpose |
|---|---|
| `webapp.py` | Local browser UI and standard-library HTTP server |
| `RUN-INSTRUCTIONS.md` | Short Windows-first start, import, and troubleshooting guide |
| `convert.py` | Conversion engine and optional command-line interface |
| `install.py` | pip-only installer and dependency check |
| `requirements.txt` | Python package requirements |
| `assets/`, `local_pack/` | Bundled inputs for the end-to-end self-test and local-file example |
| `diagnostic/` | Test `.wastickers` files for import troubleshooting |
| `make_diagnostics.py`, `verify_diagnostics.py` | Rebuild and verify the diagnostic packs |
| `frontend/` + `backend/` | Hosted deployment: Next.js 14 app (Vercel) and Modal.com FastAPI conversion engine — see `DEPLOYMENT.md` |

### Hosted version

A browser-hosted variant lives in `frontend/` (Next.js 14 on Vercel) + `backend/` (FastAPI on Modal.com). It tracks the Fullyst sticker catalog as the main experience. Deployment steps and the per-browser session model/limits are documented in [`DEPLOYMENT.md`](DEPLOYMENT.md).
