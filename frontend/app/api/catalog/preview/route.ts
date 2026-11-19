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

    const assetRes = await fetch(assetUrl, {
      headers: { 'User-Agent': 'Mozilla/5.0 StickerStudio/1.0', Accept: 'image/webp,video/webm,*/*' },
      cache: 'no-store',
    });

    if (!assetRes.ok) {
      return NextResponse.json({ error: `Asset fetch failed HTTP ${assetRes.status}` }, { status: 502 });
    }

    const contentType = assetRes.headers.get('content-type') || (isVideo ? 'video/webm' : 'image/webp');
    const blob = await assetRes.arrayBuffer();

    return new NextResponse(blob, {
      headers: {
        'Content-Type': contentType,
        'Cache-Control': 'public, max-age=3600, s-maxage=3600',
      },
    });
  } catch (err: any) {
    return NextResponse.json({ error: err.message || 'Preview fetch failed' }, { status: 502 });
  }
}
