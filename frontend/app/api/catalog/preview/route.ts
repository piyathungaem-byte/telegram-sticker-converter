/**
 * Next.js Route Handler: GET /api/catalog/preview?slug=
 *
 * Proxies the first sticker thumbnail from stickers.fullyst.com so the
 * browser never hits a cross-origin image block.
 * Runs on Vercel — no Modal.com needed.
 */
import { NextRequest, NextResponse } from 'next/server';

const FULLYST_BASE = 'https://fullyst.com';
const FULLYST_STICKER_HOST = 'stickers.fullyst.com';
const MAX_ASSET_BYTES = 8 * 1024 * 1024; // 8 MB — mirrors the Python backend cap
const FETCH_TIMEOUT_MS = 15_000;

/** Fetch with an abort timeout; rejects if the body exceeds maxBytes while streaming. */
async function fetchCapped(
  url: string,
  init: RequestInit,
  maxBytes: number
): Promise<{ ok: boolean; status: number; contentType: string; bytes: ArrayBuffer } | { error: string; status: number }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(url, { ...init, signal: controller.signal });
    const contentLength = Number(res.headers.get('content-length') || '0');
    if (contentLength && contentLength > maxBytes) {
      return { error: 'Asset exceeds size cap', status: 502 };
    }
    if (!res.body) {
      const buf = await res.arrayBuffer();
      if (buf.byteLength > maxBytes) return { error: 'Asset exceeds size cap', status: 502 };
      return { ok: res.ok, status: res.status, contentType: res.headers.get('content-type') || '', bytes: buf };
    }
    const reader = res.body.getReader();
    const chunks: Uint8Array[] = [];
    let total = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel();
        return { error: 'Asset exceeds size cap', status: 502 };
      }
      chunks.push(value);
    }
    const merged = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      merged.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return { ok: res.ok, status: res.status, contentType: res.headers.get('content-type') || '', bytes: merged.buffer };
  } finally {
    clearTimeout(timer);
  }
}

export async function GET(req: NextRequest) {
  const slug = (req.nextUrl.searchParams.get('slug') || '').trim();

  if (!slug || !/^[A-Za-z0-9_.-]{1,160}$/.test(slug)) {
    return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
  }

  // Ask Fullyst's receiver endpoint for the first sticker of the pack
  const body = new URLSearchParams({ set: slug, stickers: '00', limit: '1' });
  try {
    const receiverRes = await fetch(`${FULLYST_BASE}/receiver/stickers`, {
      method: 'POST',
      headers: {
        'User-Agent': 'Mozilla/5.0 StickerStudio/1.0',
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-Requested-With': 'XMLHttpRequest',
        Referer: `${FULLYST_BASE}/en/stickers/${encodeURIComponent(slug)}`,
      },
      body: body.toString(),
      next: { revalidate: 300 },
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });

    if (!receiverRes.ok) {
      return NextResponse.json({ error: `Fullyst receiver HTTP ${receiverRes.status}` }, { status: 502 });
    }

    const data = await receiverRes.json();
    if (!Array.isArray(data) || !data[0]) {
      return NextResponse.json({ error: 'No sticker data returned' }, { status: 404 });
    }

    const setInfo = data[0]?.set as Record<string, any>;
    const rows = data[0]?.stickers as any[];
    if (!rows?.length || !setInfo?.uuid) {
      return NextResponse.json({ error: 'Invalid pack data' }, { status: 404 });
    }

    const row = rows[0];
    const packUuid = String(setInfo.uuid || '');
    const fileUid = String(row.file_uid || '');
    const isVideo = ['1', 'true', 'yes'].includes(String(row.video || '0').toLowerCase());
    const isAnimated = ['1', 'true', 'yes'].includes(String(row.animated || '0').toLowerCase());

    if (!/^[A-Fa-f0-9-]{36}$/.test(packUuid) || !/^[A-Za-z0-9_-]{1,150}$/.test(fileUid)) {
      return NextResponse.json({ error: 'Invalid sticker metadata' }, { status: 502 });
    }

    const ext = isVideo ? 'webm' : 'webp';
    const assetUrl = `https://${FULLYST_STICKER_HOST}/${packUuid}/full/${fileUid}.${ext}`;

    const capped = await fetchCapped(
      assetUrl,
      {
        headers: { 'User-Agent': 'Mozilla/5.0 StickerStudio/1.0', Accept: 'image/webp,video/webm,*/*' },
        cache: 'no-store',
      },
      MAX_ASSET_BYTES
    );

    if ('error' in capped) {
      return NextResponse.json({ error: capped.error }, { status: capped.status });
    }
    if (!capped.ok) {
      return NextResponse.json({ error: `Asset fetch failed HTTP ${capped.status}` }, { status: 502 });
    }

    const contentType = capped.contentType || (isVideo ? 'video/webm' : 'image/webp');

    return new NextResponse(capped.bytes, {
      headers: {
        'Content-Type': contentType,
        'Cache-Control': 'public, max-age=3600, s-maxage=3600',
      },
    });
  } catch (err: any) {
    return NextResponse.json({ error: err.message || 'Preview fetch failed' }, { status: 502 });
  }
}
