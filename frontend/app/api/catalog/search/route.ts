/**
 * Next.js Route Handler: GET /api/catalog/search?q=&page=
 *
 * Server-side proxy that scrapes the Fullyst public HTML catalog so the
 * browser is never blocked by Fullyst's CORS policy.
 * This runs on Vercel's Edge / Node runtime — no Modal.com needed.
 */
import { NextRequest, NextResponse } from 'next/server';

const FULLYST_BASE = 'https://fullyst.com';
const PAGE_SIZE = 10;
const MAX_HTML_BYTES = 4 * 1024 * 1024;
const FETCH_TIMEOUT_MS = 15_000;

// ── Adult-content keyword filter ──────────────────────────────────────────────
const ADULT_PATTERN =
  /\b(nude|naked|porn|sex|xxx|nsfw|adult|erotic|hentai|loli|shota|fetish|bdsm|onlyfans)\b/i;

function looksAdult(...values: string[]): boolean {
  const text = values
    .map((v) => (v || '').replace(/([a-z])([A-Z])/g, '$1 $2'))
    .join(' ')
    .toLowerCase()
    .replace(/[_./-]+/g, ' ');
  if (ADULT_PATTERN.test(text)) return true;
  // leet-speak normalisation
  const leet = text.replace(/0/g, 'o').replace(/1/g, 'i').replace(/3/g, 'e')
    .replace(/4/g, 'a').replace(/5/g, 's').replace(/7/g, 't');
  return ADULT_PATTERN.test(leet);
}

// ── HTML catalog parser ───────────────────────────────────────────────────────
interface RawCard {
  titleParts: string[];
  kindParts: string[];
  textParts: string[];
  href: string;
  sampleUrl: string;
  adultMarked: boolean;
}

interface ParsedPack {
  title: string;
  kind: string;
  slug: string;
  sampleUrl: string;
  isAdult: boolean;
  isAnimated: boolean;
  count?: number;
}

/**
 * Minimal recursive-descent HTML card extractor.
 * Looks for <div class="...sticker-set..."> cards, pulls out title, kind, href, img.
 */
function parseFullystHtml(html: string): { packs: ParsedPack[]; pageCount: number } {
  const packs: ParsedPack[] = [];

  // Split on sticker-set div openings; sections[0] is preamble, the rest are card interiors
  const sections = html.split(/<div\b[^>]*class="[^"]*\bsticker-set\b[^"]*"[^>]*>/i);

  for (let i = 1; i < sections.length; i++) {
    const chunk = sections[i];

    // title: first <h2>…</h2>
    const titleMatch = chunk.match(/<h2[^>]*>([\s\S]*?)<\/h2>/i);
    const title = titleMatch
      ? titleMatch[1].replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/&#\d+;/g, '').trim()
      : '';
    if (!title) continue;

    // kind: <span class="...type-label...">…</span>
    const kindMatch = chunk.match(/<span[^>]*\btype-label\b[^>]*>([\s\S]*?)<\/span>/i);
    const kind = kindMatch
      ? kindMatch[1].replace(/<[^>]+>/g, '').trim()
      : 'Sticker pack';

    // href: <a class="...btn-stickers..." href="...">
    const hrefMatch = chunk.match(/<a\b[^>]*\bbtn-stickers\b[^>]*href="([^"]+)"/i);
    const href = hrefMatch ? hrefMatch[1] : '';
    // slug = last path segment
    const slug = (href.replace(/\/$/, '').split('/').pop() || '').trim();
    if (!slug || !/^[A-Za-z0-9_.-]{1,160}$/.test(slug)) continue;

    // count: look for a number near "stickers"
    const countMatch = chunk.match(/(\d+)\s*sticker/i);
    const count = countMatch ? parseInt(countMatch[1], 10) : undefined;

    // first img from stickers.fullyst.com
    const imgMatch = chunk.match(/src="(https:\/\/stickers\.fullyst\.com[^"]+)"/i);
    const sampleUrl = imgMatch ? imgMatch[1] : '';

    const plainText = chunk.replace(/<[^>]+>/g, ' ');
    const isAdult = looksAdult(title, slug, kind, plainText);
    const isAnimated = /\b(animated|animation|video)\b/i.test(kind);

    packs.push({ title, kind, slug, sampleUrl, isAdult, isAnimated, count });
  }

  // Pagination: find max page= from pagination div
  const pagerMatch = html.match(
    /<div\b[^>]*\bpagination\b[^>]*>([\s\S]*?)<\/div>/i
  );
  let pageCount = 1;
  if (pagerMatch) {
    const re = /page=(\d+)/gi;
    const nums: number[] = [];
    let m: RegExpExecArray | null;
    while ((m = re.exec(pagerMatch[1])) !== null) {
      nums.push(parseInt(m[1], 10));
    }
    if (nums.length) pageCount = Math.max(...nums);
  }

  return { packs: packs.slice(0, PAGE_SIZE), pageCount };
}

// ── Route Handler ─────────────────────────────────────────────────────────────
export async function GET(req: NextRequest) {
  const { searchParams } = req.nextUrl;
  const rawQ = (searchParams.get('q') || '').trim().slice(0, 100);
  const page = Math.min(Math.max(parseInt(searchParams.get('page') || '1', 10), 1), 10000);

  const params: Record<string, string> = {};
  if (page > 1) params['page'] = String(page);
  if (rawQ) params['search'] = rawQ;

  const url =
    `${FULLYST_BASE}/en/stickers` + (Object.keys(params).length ? '?' + new URLSearchParams(params).toString() : '');

  try {
    const res = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 StickerStudio/1.0',
        Accept: 'text/html,application/xhtml+xml',
      },
      next: { revalidate: 60 }, // cache for 60 s on Vercel edge
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });

    if (!res.ok) {
      return NextResponse.json(
        { error: `Fullyst returned HTTP ${res.status}` },
        { status: 502 }
      );
    }

    const contentLength = Number(res.headers.get('content-length') || '0');
    if (contentLength && contentLength > MAX_HTML_BYTES) {
      return NextResponse.json({ error: 'Catalog page too large' }, { status: 502 });
    }
    const html = await res.text();
    if (html.length > MAX_HTML_BYTES) {
      return NextResponse.json({ error: 'Catalog page too large' }, { status: 502 });
    }
    const { packs, pageCount } = parseFullystHtml(html);
    const clampedPage = Math.min(page, pageCount);

    return NextResponse.json(
      {
        packs,
        page: clampedPage,
        pageCount,
        query: rawQ,
        hasMore: packs.length > 0 && clampedPage < pageCount,
      },
      {
        headers: { 'Cache-Control': 'public, s-maxage=60, stale-while-revalidate=120' },
      }
    );
  } catch (err: any) {
    return NextResponse.json({ error: err.message || 'Catalog fetch failed' }, { status: 502 });
  }
}
