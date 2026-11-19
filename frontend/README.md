# Sticker Studio Frontend — Next.js on Vercel

A modern, production-grade **Next.js 14 (App Router)** web application for **Telegram to WhatsApp Sticker Converter**, built with React 18, TypeScript, Tailwind CSS, and Lucide Icons.

Designed for native deployment on [Vercel](https://vercel.com) connecting securely to a serverless Python backend on [Modal.com](https://modal.com).

---

## 🚀 One-Click Deploy to Vercel

1. Push this repository to GitHub / GitLab.
2. Go to your [Vercel Dashboard](https://vercel.com/new) and click **Import**.
3. Set **Root Directory** to `frontend`.
4. Vercel automatically detects **Next.js** framework!
5. (Optional) Under **Environment Variables**, add:
   - `NEXT_PUBLIC_MODAL_API_URL`: Your Modal web endpoint (e.g. `https://<workspace>--telegram-sticker-converter-fastapi-app.modal.run`)
6. Click **Deploy**!

> **Note**: Even if you don't configure `NEXT_PUBLIC_MODAL_API_URL` during initial deployment, you can configure your Modal backend URL directly inside the web UI at any time! It is persisted in browser local storage and verified with live latency checks.

---

## 💻 Local Development

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

---

## 🏗️ Next.js App Architecture

```
frontend/
├── app/
│   ├── layout.tsx         # Root layout with dark mode metadata & viewport
│   ├── page.tsx           # Main application view (Tabs, Fetch, Grid, Results)
│   └── globals.css        # Tailwind CSS & custom animations
├── components/
│   ├── Header.tsx         # Navbar with live Modal health indicator & saves badge
│   ├── FetchSection.tsx   # Pack URL & BotFather token inputs with live activity log
│   ├── StickerGrid.tsx    # Responsive sticker selection grid with animated badges
│   ├── ResultsSection.tsx # WhatsApp .wastickers packs with 1-click downloads & import guide
│   ├── FullystExplorer.tsx# Discover public packs without requiring a Bot token
│   ├── SettingsModal.tsx  # Dynamic Modal endpoint configuration with latency ping
│   ├── PreviewModal.tsx   # High-resolution animated GIF / WebM looping preview
│   └── CollectionModal.tsx# Mix favorite stickers from multiple packs into custom WhatsApp packs
├── lib/
│   ├── api.ts             # Strongly typed API client for Modal.com backend
│   └── types.ts           # TypeScript interfaces (Sticker, Job, Pack, Fullyst)
├── tailwind.config.ts     # Custom dark theme (mint & slate palette)
├── tsconfig.json          # TypeScript path aliases (@/*)
└── next.config.mjs        # Next.js optimization settings
```

---

## ⚙️ Environment Variables

| Variable | Description | Default |
|---|---|---|
| `NEXT_PUBLIC_MODAL_API_URL` | Public HTTPS endpoint of your deployed Modal backend | `localStorage` / `http://127.0.0.1:8000` |
