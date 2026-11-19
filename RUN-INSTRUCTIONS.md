# Sticker Studio — Windows Quick Start

This is a small browser interface for the Telegram-to-WhatsApp sticker converter. It runs as a
local Python program on your PC; the browser page and conversion engine are not hosted online.

## 1. Extract and install once

1. Extract `telegram-sticker-converter.zip` into a folder you can find again.
2. Open that folder in File Explorer. Click the address bar, type `powershell`, and press Enter.
3. In the PowerShell window, run:

```powershell
python install.py
```

This installs the required Python packages. It uses pip and prebuilt wheels; you do not need Node,
Redis, Docker, WSL, a compiler, or administrator rights.

If `python` is not recognized but the Python launcher is installed, use:

```powershell
py install.py
```

## 2. Start the local app

In the same folder, run:

```powershell
python webapp.py
```

The app opens `http://127.0.0.1:8765` in your browser. If it does not open automatically, paste
that address into the browser yourself. Keep the PowerShell window open while converting; press
`Ctrl+C` in it when you are done.

## 3. Fetch, select, and create

1. Paste a public Telegram sticker-pack link such as `https://t.me/addstickers/YourPack`.
2. Paste your Telegram Bot token. Create one with **@BotFather** if needed. Optionally check
   **Remember token on this PC**; Windows encrypts it for your account. **Forget saved token** removes it.
3. Click **Fetch sticker pack**. The live activity log expands with progress while thumbnails load.
4. Select the stickers you want and choose **Create selected pack**, or choose **Convert full pack**.
   Quality and CPU workers are automatic.
5. Preview the created pack in the browser and download the `.wastickers` file(s).

The token stays with the local Python app; it is not sent to Arena or another website. The optional
saved copy is encrypted with Windows DPAPI and never returned to the browser.

## 4. Move the pack to WhatsApp

Transfer the downloaded `.wastickers` file to your Android phone, open it with **Sticker Maker**
(or another compatible sticker host app), then use that app's **Add to WhatsApp** action. Import
one file at a time. The converter separates static and animated stickers and limits each pack to
30. Groups with fewer than three stickers are skipped because WhatsApp requires at least three.

## Where files go

- `webapp-runs/` — only the finished `.wastickers` file(s), grouped by run.
- `webapp-cache/` — private working cache; it speeds up repeat conversions and can be deleted to reclaim space.

These folders are created next to `webapp.py`. You can delete `webapp-runs/` when you no longer
need the downloads. Deleting `webapp-cache/` frees more space, but the next run must download and
convert that pack again.

## Quick fixes

| Problem | What to do |
|---|---|
| `python` is not recognized | Use `py webapp.py`, or install Python with **Add Python to PATH** enabled. |
| Page does not open | Check that the PowerShell window is still running; try `http://127.0.0.1:8765`. |
| Port 8765 is already in use | Start with `python webapp.py --port 8766`, then open `http://127.0.0.1:8766`. |
| “Missing required package” appears | Stop the app, run `python install.py` from the extracted folder, then restart it. |
| Telegram rejects the token | Check it with @BotFather; revoke and replace it if it was shared publicly. |
| No pack was created | WhatsApp needs at least 3 stickers of one type to form a pack. |

The command-line converter is still available as `convert.py`; its advanced options are documented
in `README.md`. For the simplest everyday use, start `webapp.py`.
