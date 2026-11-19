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
├── modal_app.py                  # Root deployable file
├── convert.py                    # Core converter script
├── DEPLOYMENT.md                 # Architecture & deployment guide
└── README.md                     # Main documentation
```
