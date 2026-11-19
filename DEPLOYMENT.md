# Deployment Architecture: Next.js on Vercel + Python on Modal.com

This project is separated into a **Next.js 14 (App Router) Frontend** hosted on [Vercel](https://vercel.com) and a **Serverless Python Conversion Engine** hosted on [Modal.com](https://modal.com).

```
   ┌────────────────────────────────────────────────────────┐
   │                    User Web Browser                    │
   └───────────────────────────┬────────────────────────────┘
                               │
               ┌───────────────┴───────────────┐
               │                               │
               ▼                               ▼
      ┌──────────────────┐           ┌──────────────────┐
      │  Vercel Frontend │           │ Modal.com API    │
      │ (Next.js 14 App) │           │ (FastAPI Engine) │
      └──────────────────┘           └────────┬─────────┘
                                              │
                                    ┌─────────┴─────────┐
                                    ▼                   ▼
                            ┌──────────────┐     ┌─────────────┐
                            │ Telegram API │     │ Modal Vol   │
                            │   (Bot API)  │     │  (/data)    │
                            └──────────────┘     └─────────────┘
```

---

## 1️⃣ Part 1: Deploy Python Backend to Modal.com

### Step 1: Install Modal CLI & Log In
```bash
pip install modal
modal setup
```

### Step 2: Deploy the Backend
From the root workspace or `backend/` directory:
```bash
modal deploy backend/modal_app.py
```

### Step 3: Copy Your Public Modal URL
When the deployment finishes, Modal prints your web endpoint:
```text
✓ Created objects.
├── 🔨 Created mount /root/convert.py
└── 🌐 Created web endpoint -> https://<workspace>--telegram-sticker-converter-fastapi-app.modal.run
```
Copy this URL.

---

## 2️⃣ Part 2: Deploy Frontend to Vercel

1. Push your repository to GitHub / GitLab.
2. Go to [vercel.com/new](https://vercel.com/new).
3. Import this repository.
4. Set **Root Directory** to `frontend`.
5. Vercel automatically detects **Next.js** framework preset.
6. In **Environment Variables**, add:
   - `NEXT_PUBLIC_MODAL_API_URL` = your copied Modal URL (e.g. `https://<workspace>--telegram-sticker-converter-fastapi-app.modal.run`)
7. Click **Deploy**.

---

## 3️⃣ Dynamic Backend Connection

Even if you deploy without configuring the environment variable initially:
1. Open your Vercel website.
2. Click the **⚙️ Settings** icon or the **Modal Status Pill** in the top bar.
3. Paste your Modal URL and click **Test Connection** to verify with live ping latency.
4. Click **Save Endpoint** (it is saved in your browser's `localStorage` and immediately connects).

---

## 📁 Project Structure

```
telegram-sticker-converter/
├── frontend/                     # Next.js 14 + Tailwind CSS + TypeScript
│   ├── app/
│   │   ├── layout.tsx            # Global layout & metadata
│   │   ├── page.tsx              # Main dashboard view
│   │   └── globals.css           # Styling
│   ├── components/               # Modular UI components
│   │   ├── Header.tsx            # Navbar & live health pill
│   │   ├── FetchSection.tsx      # Telegram pack fetch & activity log
│   │   ├── StickerGrid.tsx       # Sticker selection grid
│   │   ├── ResultsSection.tsx    # .wastickers packs & WhatsApp guide
│   │   ├── FullystExplorer.tsx   # Public pack catalog browser
│   │   ├── SettingsModal.tsx     # Modal URL config & latency ping
│   │   ├── PreviewModal.tsx      # Animated preview modal
│   │   └── CollectionModal.tsx   # Custom favorites pack creator
│   ├── lib/
│   │   ├── api.ts                # Strongly typed Modal API client
│   │   └── types.ts              # TypeScript interfaces
│   ├── tailwind.config.ts        # Tailwind configuration
│   ├── tsconfig.json             # TypeScript configuration
│   ├── next.config.mjs           # Next.js configuration
│   └── package.json              # Next.js 14, React 18, Lucide React
│
├── backend/                      # Modal.com serverless Python backend
│   ├── modal_app.py              # Modal App + FastAPI endpoints
│   ├── convert.py                # Core sticker conversion engine
│   ├── requirements.txt          # Modal container dependencies
│   └── README.md                 # Modal deployment guide
│
├── convert.py                    # Core converter (local CLI + webapp.py engine; keep in sync with backend/convert.py)
├── DEPLOYMENT.md                 # Architecture & deployment guide
└── README.md                     # Main documentation
```

> **Two `convert.py` copies are intentional:** `backend/modal_app.py` bundles its
> sibling `backend/convert.py` into the Modal image
> (`add_local_file(Path(__file__).parent / "convert.py", ...)`), while the root
> `convert.py` powers `webapp.py`, `install.py` and the command-line interface.
> Apply any engine change to **both** copies (they are byte-identical today).
> The backend entry point lives only in `backend/`.


---

## 🔒 Hosted deployment notes & limits (Vercel + Modal)

These are deliberate, documented trade-offs of the serverless deployment — read this before treating the hosted instance as a full production service.

**Per-browser session isolation (jobs)**
The hosted frontend generates a random client id (stored in `localStorage` as `tsc_client_id`) and sends it as the `X-Client-Id` header (or `?client_id=` for media URLs that `<img>` tags cannot decorate). The Modal backend scopes every job, `/api/latest`, the sticker picker, thumbnails/previews/downloads, and the Fullyst page-visit history to that browser. Jobs created before this scoping — or by anonymous callers such as `curl` — live in a shared `"shared"` bucket. A second browser can never read, convert, or download another browser's job (404).

**Global capacity guard**
Even with per-browser isolation, the 2-CPU Modal container allows at most 4 concurrently queued/running jobs across all browsers (`MAX_ACTIVE_JOBS_TOTAL`). Beyond that, clients get `409 The converter is busy` instead of an overloaded instance.

**Jobs are ephemeral**
Job state (stickers, conversion progress, pack archives) lives in per-process RAM on Modal. If the container recycles — timeout, deploy, or scale-to-zero — in-flight and even finished-pack job entries are lost, and the frontend will show the "This app session has expired" message and ask you to fetch again. Finished `.wastickers` files are written under `/data/webapp-runs` (persisted via the `sticker-converter-data` Modal Volume), but the API no longer knows about them after a recycle.

**The favorites collection is shared**
The "My Saves" favorites collection (`/data/MySaves`) and the legacy visit-history file are **shared by every visitor** of a deployment — there is no per-browser separation for saved stickers. Anyone browsing the hosted instance sees the same favorites, groups, and (with the moderation caveat) the same saved stickers. Do not save private or personal stickers to a shared deployment's collection. The hosted instance is intended for public catalog packs.

**CORS**
The API answers credentialed requests for no one: it sets `Access-Control-Allow-Origin: *` with `Allow-Credentials: false` and no cookies are used. Optionally restrict origins with the `MODAL_ALLOWED_ORIGINS` environment variable on the Modal function (comma-separated, e.g. `https://your-app.vercel.app`).

**Out of scope today**
Per-browser favorites collections, durable job history across container recycles, and per-user Fullyst visit history for anonymous callers. See the git history for the session-scoping work.
