'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Search, Globe, Eye, ChevronLeft, ChevronRight, Loader2, ShieldAlert } from 'lucide-react';
import { FullystPack } from '../lib/types';
import { api } from '../lib/api';

interface FullystExplorerProps {
  onImportPack: (slug: string, title?: string, isAdult?: boolean) => void;
  onToast: (msg: string, type: 'info' | 'success' | 'error') => void;
  showAdultPreviews?: boolean;
  hideAdultContent?: boolean;
  onToggleAdultPreviews?: (val: boolean) => void;
}

export function FullystExplorer({
  onImportPack,
  onToast,
  showAdultPreviews = false,
  hideAdultContent = false,
  onToggleAdultPreviews,
}: FullystExplorerProps) {
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [jumpPage, setJumpPage] = useState('');
  const [loading, setLoading] = useState(false);
  const [packs, setPacks] = useState<FullystPack[]>([]);
  const [totalPages, setTotalPages] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [visitedPages, setVisitedPages] = useState<Set<number>>(new Set());

  // Stable ref so fetchCatalog always reads the latest query value
  const queryRef = useRef(query);
  useEffect(() => {
    queryRef.current = query;
  }, [query]);

  const handleJump = (e: React.FormEvent) => {
    e.preventDefault();
    const p = parseInt(jumpPage, 10);
    if (!isNaN(p) && p >= 1 && p <= totalPages) {
      fetchCatalog(p);
      setJumpPage('');
    }
  };

  const fetchCatalog = useCallback(
    async (p = 1, q?: string) => {
      const searchQuery = q !== undefined ? q : queryRef.current;
      setLoading(true);
      try {
        const data = await api.searchCatalog(searchQuery, p);
        setPacks(data.packs || []);
        setPage(data.page || 1);
        setTotalPages(data.pageCount || 1);
        setHasMore(data.hasMore || false);

        try {
          await api.markFullystPageVisited(p);
        } catch {
          // ignore
        }
        setVisitedPages((prev) => new Set(prev).add(p));
      } catch (err: any) {
        onToast(err.message, 'error');
      } finally {
        setLoading(false);
      }
    },
    [onToast]
  );

  useEffect(() => {
    fetchCatalog(1, '');
    (async () => {
      try {
        const { apiFetch } = await import('../lib/api');
        const result = await apiFetch<{ pages: number[] }>('/api/fullyst/visited-pages');
        if (Array.isArray(result?.pages)) {
          setVisitedPages(new Set(result.pages));
        }
      } catch {
        // ignore
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    fetchCatalog(1, query);
  };

  // Filter packs based on Hide Adult Content setting (from webapp.py:714)
  const visiblePacks = hideAdultContent
    ? packs.filter((p) => !p.isAdult)
    : packs;

  return (
    <div className="space-y-5">
      {/* Search Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h3 className="text-base font-bold text-white flex items-center gap-2 flex-wrap">
            <Globe className="w-5 h-5 text-mint" />
            <span>Browse Fullyst Catalog</span>
            {hideAdultContent && (
              <span className="px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 text-[10px] font-bold border border-amber-500/30 flex items-center gap-1">
                <ShieldAlert className="w-3 h-3" />
                <span>18+ Filtered</span>
              </span>
            )}
            {onToggleAdultPreviews && !hideAdultContent && (
              <label className="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-[#101618] border border-line hover:border-line-strong text-muted text-[11px] font-semibold cursor-pointer select-none transition">
                <input
                  type="checkbox"
                  checked={showAdultPreviews}
                  onChange={(e) => onToggleAdultPreviews(e.target.checked)}
                  className="accent-mint w-3.5 h-3.5 rounded"
                />
                <span className={showAdultPreviews ? 'text-amber-300 font-bold' : ''}>
                  {showAdultPreviews ? '18+ Revealed' : 'Reveal 18+'}
                </span>
              </label>
            )}
          </h3>
          <p className="text-xs text-muted">
            Search public Telegram sticker packs. Files download directly to your pack without needing a Bot token.
          </p>
        </div>

        {/* Search input */}
        <form onSubmit={handleSearch} className="flex items-center gap-2 w-full sm:w-auto">
          <div className="relative flex-1 sm:w-64">
            <Search className="w-4 h-4 text-muted absolute left-3 top-2.5" />
            <input
              type="text"
              placeholder="Search packs, e.g. cat, pepe, anime…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="w-full bg-[#101618] border border-line rounded-xl pl-9 pr-3 py-2 text-xs text-white placeholder-dim outline-none focus:border-mint"
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="px-4 py-2 rounded-xl bg-mint hover:bg-mint-hover text-black font-extrabold text-xs transition disabled:opacity-50"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Search'}
          </button>
        </form>
      </div>

      {/* Grid of Packs */}
      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3.5">
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="p-3.5 rounded-xl bg-panel border border-line flex items-center gap-3.5"
            >
              <div className="w-16 h-16 rounded-xl animate-shimmer flex-shrink-0" />
              <div className="min-w-0 flex-1 space-y-2">
                <div className="h-3 w-3/4 rounded animate-shimmer" />
                <div className="h-2.5 w-1/2 rounded animate-shimmer" />
                <div className="h-7 w-24 rounded-lg animate-shimmer mt-2" />
              </div>
            </div>
          ))}
        </div>
      ) : visiblePacks.length === 0 ? (
        <div className="py-16 text-center border border-dashed border-line rounded-xl text-muted text-xs">
          {hideAdultContent && packs.length > 0
            ? 'All matching packs on this page were hidden by your "Hide adult content" setting.'
            : 'No sticker packs found. Try searching for something else!'}
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3.5">
          {visiblePacks.map((pack) => {
            const isAdult = !!pack.isAdult;
            const isBlurred = isAdult && !showAdultPreviews;

            return (
              <PackCard
                key={pack.slug}
                pack={pack}
                isAdult={isAdult}
                isBlurred={isBlurred}
                showAdultPreviews={showAdultPreviews}
                onImport={onImportPack}
              />
            );
          })}
        </div>
      )}

      {/* Pagination */}
      {!loading && visiblePacks.length > 0 && (
        <div className="flex items-center justify-between flex-wrap gap-3 pt-3 border-t border-line/40">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => fetchCatalog(page - 1)}
              disabled={page <= 1}
              className="p-1.5 rounded-lg bg-panel border border-line hover:border-line-strong text-muted hover:text-white disabled:opacity-30"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="text-xs text-muted">
              Page {page}
              {visitedPages.has(page) && (
                <span className="text-mint ml-1 font-bold" title="Page visited">
                  ✓
                </span>
              )}
              &nbsp;of {totalPages}
            </span>
            <button
              type="button"
              onClick={() => fetchCatalog(page + 1)}
              disabled={!hasMore}
              className="p-1.5 rounded-lg bg-panel border border-line hover:border-line-strong text-muted hover:text-white disabled:opacity-30"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>

          <form onSubmit={handleJump} className="flex items-center gap-1.5 text-xs text-muted">
            <label htmlFor="jumpInput" className="text-[11px]">
              Go to page:
            </label>
            <input
              id="jumpInput"
              type="number"
              min={1}
              max={totalPages}
              value={jumpPage}
              onChange={(e) => setJumpPage(e.target.value)}
              placeholder="#"
              className="w-12 bg-[#101618] border border-line rounded-lg px-2 py-1 text-xs text-white text-center outline-none focus:border-mint"
            />
            <button
              type="submit"
              disabled={!jumpPage}
              className="px-2.5 py-1 rounded-lg bg-panel-elevated hover:bg-[#253337] border border-line text-xs font-semibold text-white transition disabled:opacity-30"
            >
              Go
            </button>
          </form>
        </div>
      )}
    </div>
  );
}

/**
 * Individual pack card matching webapp.py:716-734:
 * Tries animated GIF preview from Modal backend for animated packs;
 * falls back to static catalog preview route or sampleUrl.
 */
function PackCard({
  pack,
  isAdult,
  isBlurred,
  showAdultPreviews,
  onImport,
}: {
  pack: FullystPack;
  isAdult: boolean;
  isBlurred: boolean;
  showAdultPreviews: boolean;
  onImport: (slug: string, title?: string, isAdult?: boolean) => void;
}) {
  const isAnimatedPack =
    pack.isAnimated === true || /animated|video/i.test(String(pack.kind || ''));
  const animatedUrl = isAnimatedPack
    ? api.getFullystAnimatedPreviewUrl(pack.slug)
    : '';
  const staticUrl = api.getCatalogPreviewUrl(pack.slug);

  const [currentSrc, setCurrentSrc] = useState(animatedUrl || staticUrl);
  const [fallbackAttempted, setFallbackAttempted] = useState(false);
  const [allFailed, setAllFailed] = useState(false);

  useEffect(() => {
    setCurrentSrc(animatedUrl || staticUrl);
    setFallbackAttempted(false);
    setAllFailed(false);
  }, [pack.slug, animatedUrl, staticUrl]);

  const handleError = () => {
    // If animated URL failed, fall back to static WebP proxy
    if (!fallbackAttempted && currentSrc !== staticUrl) {
      setFallbackAttempted(true);
      setCurrentSrc(staticUrl);
    } else if (pack.sampleUrl && currentSrc !== pack.sampleUrl) {
      setCurrentSrc(pack.sampleUrl);
    } else {
      setAllFailed(true);
    }
  };

  return (
    <div className="p-3.5 rounded-2xl bg-panel border border-line hover:border-line-strong flex items-center gap-3.5 transition group">
      {/* Thumbnail with checkerboard & adult blur */}
      <div className="w-16 h-16 rounded-xl sticker-checkerboard flex-shrink-0 flex items-center justify-center p-1 overflow-hidden border border-line/60 shadow-inner">
        {allFailed ? (
          <div className="w-full h-full flex items-center justify-center text-muted text-base">
            {isAnimatedPack ? '🎬' : '🖼️'}
          </div>
        ) : (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img
            src={currentSrc}
            onError={handleError}
            alt={pack.title || 'Sticker sample'}
            className={`max-w-full max-h-full object-contain transition duration-200 ${
              isBlurred ? 'adult-blurred' : ''
            }`}
            loading="lazy"
            draggable={false}
          />
        )}
      </div>

      <div className="min-w-0 flex-1 flex flex-col justify-between gap-1">
        <div>
          <h4
            className="text-xs font-bold text-white truncate group-hover:text-mint transition"
            title={pack.title || pack.slug}
          >
            {pack.title || pack.slug}
          </h4>
          <p className="text-[11px] text-muted truncate">
            {pack.count ? `${pack.count} stickers` : pack.kind || 'Sticker pack'}
            {isAnimatedPack && ' • Animated'}
          </p>
        </div>

        <div className="flex items-center gap-2 mt-1 flex-wrap">
          <button
            type="button"
            onClick={() => onImport(pack.slug, pack.title, isAdult)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-panel-elevated hover:bg-mint hover:text-black border border-line text-xs font-bold text-white transition shadow-sm"
          >
            <Eye className="w-3.5 h-3.5" />
            <span>View pack</span>
          </button>

          {isAdult && (
            <span
              className={`px-2 py-0.5 rounded-full text-[9px] font-extrabold border ${
                showAdultPreviews
                  ? 'bg-amber-500/20 text-amber-300 border-amber-500/30'
                  : 'bg-red-500/20 text-red-300 border-red-500/30'
              }`}
            >
              {showAdultPreviews ? '18+ preview' : '18+ • blurred'}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
