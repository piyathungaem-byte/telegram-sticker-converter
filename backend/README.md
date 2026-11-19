# Modal.com Backend — Telegram to WhatsApp Sticker Converter

This folder contains the serverless Python backend running on [Modal.com](https://modal.com). It runs with high performance, supports automatic scaling, installs `ffmpeg` and native libraries automatically, and persists jobs, cache, and converted `.wastickers` files in a Modal Volume.

---

## 🚀 Quick Start: Deploy to Modal.com

### 1. Install & Authenticate Modal CLI

If you haven't installed `modal` yet:

```bash
pip install modal
```

Authenticate with your Modal account (opens browser to sign in):

```bash
modal setup
```

### 2. Deploy the Backend App

From this directory (`backend/`) or the project root:

```bash
modal deploy modal_app.py
```

Modal will build the container image (installing ffmpeg, Pillow, rlottie-python, etc.), create the persistent volume `sticker-converter-data`, and output your public endpoint URL:

```text
✓ Created objects.
├── 🔨 Created mount /root/convert.py
└── 🌐 Created web endpoint -> https://<your-modal-workspace>--telegram-sticker-converter-fastapi-app.modal.run
```

**Copy that URL!** You will paste it into the frontend on Vercel (or configure it in `VITE_MODAL_API_URL`).

---

## 🧪 Local Testing & Development

### Option A: Modal Interactive Dev Server (Live Reload on Cloud)

```bash
modal serve modal_app.py
```

This starts a live-sync development session with Modal's cloud GPU/CPUs. Changes to `modal_app.py` update instantly.

### Option B: Run Locally with Uvicorn (FastAPI)

```bash
pip install -r requirements.txt
python modal_app.py --port 8000
```

The API will be available at `http://127.0.0.1:8000`. You can test endpoints and explore the interactive Swagger documentation at `http://127.0.0.1:8000/docs`.

---

## ⚙️ Environment Variables (Optional)

You can optionally configure default environment variables on Modal (via [Modal Secrets](https://modal.com/secrets)):

- `TG_BOT_TOKEN`: (Optional) Default Telegram Bot Token from `@BotFather`. If set, users don't need to enter a token in the frontend. If omitted, users can provide their token directly in the frontend UI.

To deploy with a Modal Secret:
1. Create a secret named `telegram-secrets` on the Modal dashboard with key `TG_BOT_TOKEN`.
2. Add `secrets=[modal.Secret.from_name("telegram-secrets")]` to `@app.function` in `modal_app.py`.

---

## 📡 API Endpoints Summary

- `GET /` — API health check & overview
- `GET /api/config` — Backend status, capabilities
- `POST /api/fetch` — Fetch Telegram sticker pack (`{ pack_link, token }`)
- `GET /api/job/{job_id}` — Poll job status, progress, and sticker list
- `GET /api/job/{job_id}/thumb/{index}` — Sticker thumbnail (PNG)
- `GET /api/job/{job_id}/preview/{index}` — Animated preview (GIF / WebP)
- `POST /api/convert` — Convert pack to WhatsApp (`{ job_id, mode, selected }`)
- `GET /api/job/{job_id}/output/{pack_index}/{sticker_index}` — Converted sticker preview
- `GET /download/{job_id}/{index}` — Download `.wastickers` file
- `GET /api/fullyst/search?q={q}&page={page}` — Search Fullyst sticker catalog
- `POST /api/fullyst/fetch` — Fetch pack directly from Fullyst catalog
- `GET /api/collection` — List user's saved stickers and groups
- `POST /api/collection/save` — Save sticker to favorites
- `POST /api/collection/convert` — Build WhatsApp pack from saved collection
