'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Check, Eye, Layers, Sparkles } from 'lucide-react';
import { Sticker } from '../lib/types';
import { api } from '../lib/api';

interface StickerGridProps {
  stickers: Sticker[];
  jobId: string;
  packTitle?: string;
  onPreview: (sticker: Sticker) => void;
  onConvertSelected: (selectedIndices: number[]) => void;
  onConvertFull: () => void;
  onSaveFavorite?: (sticker: Sticker) => void;
  converting: boolean;
  /** Feature 2: Badge indicating the import source (e.g. 'Fullyst') */
  source?: string;
  /** N stickers that failed to download (Fullyst imports) */
  failedCount?: number;
  /** Re-import the pack to retry failed stickers (Fullyst only) */
  onRetryFailed?: () => void;
  /** Feature 3: When true, shows an adult-content warning badge */
  isAdult?: boolean;
  /** Whether 18+ content should be shown unblurred */
  showAdultPreviews?: boolean;
  /** Inline toggle for adult previews */
  onToggleAdultPreviews?: (val: boolean) => void;
  /** Feature 4: Progress info shown as a slim bar while converting */
  conversionProgress?: { done: number; total: number };
  /** Embedded mode inside modal - avoids double-panel border/padding */
  embedded?: boolean;
}

export function StickerGrid({
  stickers,
  jobId,
  packTitle,
  onPreview,
  onConvertSelected,
  onConvertFull,
  onSaveFavorite,
  converting,
  source,
  failedCount = 0,
  onRetryFailed,
  isAdult = false,
  showAdultPreviews = false,
  onToggleAdultPreviews,
  conversionProgress,
  embedded = false,
}: StickerGridProps) {
  const [selectedIndices, setSelectedIndices] = useState<Set<number>>(new Set());
  const [lastClickedIndex, setLastClickedIndex] = useState<number | null>(null);
  const [filter, setFilter] = useState<'all' | 'animated' | 'static'>('all');
  const [savedIndices, setSavedIndices] = useState<Set<number>>(new Set());
  const [focusedIndex, setFocusedIndex] = useState<number | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  // Press & hold (480 ms) a thumbnail to preview it — mirrors webapp.py's attachHoldPreview
  const longPressTimerRef = useRef<number | null>(null);
  const holdTriggeredRef = useRef(false);

  const clearLongPress = () => {
    if (longPressTimerRef.current !== null) {
      window.clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }
  };

  const startLongPress = (sticker: Sticker, e: React.PointerEvent) => {
    // Ignore non-primary mouse buttons (e.g. right-click — handled by onContextMenu)
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    clearLongPress();
    holdTriggeredRef.current = false;
    longPressTimerRef.current = window.setTimeout(() => {
      longPressTimerRef.current = null;
      holdTriggeredRef.current = true;
      onPreview(sticker);
    }, 480);
  };

  useEffect(() => clearLongPress, []);

  // CRITICAL FIX FOR "hover auto select stickers":
  // Only select all stickers when a NEW jobId is loaded, NOT on every 700ms polling tick!
  // webapp.py starts with an empty selection and keeps it across polls.
  // Reset once per jobId, when the first sticker payload arrives.
  const selectionInitializedForRef = useRef<string | null>(null);

  useEffect(() => {
    if (!jobId) return;
    if (selectionInitializedForRef.current === jobId) return;
    if (stickers.length === 0) return; // wait for the first sticker payload
    selectionInitializedForRef.current = jobId;
    setSelectedIndices(new Set());
    setLastClickedIndex(null);
    setFocusedIndex(stickers[0]?.index ?? null);
  }, [jobId, stickers]);

  // (Arrow-key navigation helpers are defined after `filtered` below.)

  useEffect(() => {
    if (focusedIndex === null) return;
    const el = gridRef.current?.querySelector<HTMLElement>(`[data-sticker-index="${focusedIndex}"]`);
    el?.focus();
    // Only on focus index change, not on every render
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusedIndex]);

  const filtered = stickers.filter((s) => {
    if (filter === 'animated') return s.kind === 'Animated';
    if (filter === 'static') return s.kind === 'Static';
    return true;
  });

  // Arrow keys move a roving focus across the grid, Space toggles selection
  // (same navigation model as webapp.py: arrows move, Space selects).
  const filteredIndexes = filtered.map((s) => s.index);

  // Estimate rendered column count for arrow-key navigation by measuring the
  // grid itself (fluid auto-fill tracks: minmax(104px|130px, auto-fill)).
  const estimateColumns = () => {
    const width = gridRef.current?.clientWidth
      ?? (typeof window !== 'undefined' ? window.innerWidth : 800);
    const min = typeof window !== 'undefined' && window.innerWidth >= 640 ? 130 : 104;
    const gap = 12;
    return Math.max(1, Math.floor((width + gap) / (min + gap)));
  };

  const handleGridKeyDown = (e: React.KeyboardEvent) => {
    if (filteredIndexes.length === 0) return;
    const target = e.target as HTMLElement;
    if (target.closest('button, input, a, select, textarea')) return;

    const current = focusedIndex ?? filteredIndexes[0];
    const pos = Math.max(0, filteredIndexes.indexOf(current));
    const cols = estimateColumns();
    let nextPos: number | null = null;
    switch (e.key) {
      case 'ArrowRight': nextPos = Math.min(pos + 1, filteredIndexes.length - 1); break;
      case 'ArrowLeft': nextPos = Math.max(pos - 1, 0); break;
      case 'ArrowDown': nextPos = Math.min(pos + cols, filteredIndexes.length - 1); break;
      case 'ArrowUp': nextPos = Math.max(pos - cols, 0); break;
      case 'Home': nextPos = 0; break;
      case 'End': nextPos = filteredIndexes.length - 1; break;
      case ' ':
      case 'Enter': {
        e.preventDefault();
        toggleSelect(current, e.shiftKey);
        return;
      }
      default: return;
    }
    e.preventDefault();
    setFocusedIndex(filteredIndexes[nextPos]);
  };

  const toggleSelect = useCallback(
    (index: number, shiftKey: boolean = false) => {
      setSelectedIndices((prev) => {
        const next = new Set(prev);
        if (shiftKey && lastClickedIndex !== null) {
          const allIndices = filtered.map((s) => s.index);
          const start = allIndices.indexOf(lastClickedIndex);
          const end = allIndices.indexOf(index);
          if (start !== -1 && end !== -1) {
            const [min, max] = [Math.min(start, end), Math.max(start, end)];
            for (let i = min; i <= max; i++) {
              next.add(allIndices[i]);
            }
            return next;
          }
        }
        if (next.has(index)) next.delete(index);
        else next.add(index);
        return next;
      });
      setLastClickedIndex(index);
    },
    [lastClickedIndex, filtered]
  );

  const handleSelectAll = () => {
    setSelectedIndices(new Set(filtered.map((s) => s.index)));
  };

  const handleDeselectAll = () => {
    setSelectedIndices((prev) => {
      const next = new Set(prev);
      filtered.forEach((s) => next.delete(s.index));
      return next;
    });
  };

  const handleInvert = () => {
    setSelectedIndices((prev) => {
      const next = new Set(prev);
      filtered.forEach((s) => {
        if (next.has(s.index)) next.delete(s.index);
        else next.add(s.index);
      });
      return next;
    });
  };

  const animatedCount = stickers.filter((s) => s.kind === 'Animated').length;
  const staticCount = stickers.filter((s) => s.kind === 'Static').length;

  const selectedAnimated = stickers.filter(
    (s) => selectedIndices.has(s.index) && s.kind === 'Animated'
  ).length;
  const selectedStatic = stickers.filter(
    (s) => selectedIndices.has(s.index) && s.kind === 'Static'
  ).length;
  const canCreatePack = Math.max(selectedAnimated, selectedStatic) >= 3;

  const handleSave = (sticker: Sticker) => {
    setSavedIndices((prev) => new Set(prev).add(sticker.index));
    onSaveFavorite?.(sticker);
  };

  const Container = embedded ? 'div' : 'section';
  const containerClass = embedded
    ? 'space-y-4 animate-in fade-in duration-200'
    : 'p-4 sm:p-6 rounded-2xl bg-panel border border-line shadow-xl space-y-4 animate-in fade-in duration-200';

  return (
    <Container className={containerClass}>
      {/* Title & Subtitle — hidden when embedded inside the pack dialog (the dialog header already shows the title/18+ controls) */}
      {!embedded && (
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h3 className="font-extrabold text-base text-white flex items-center gap-2 flex-wrap">
            <Layers className="w-5 h-5 text-mint" />
            <span>{packTitle || 'Sticker Selection'}</span>
            {source === 'Fullyst' && (
              <span className="px-2 py-0.5 rounded-full bg-blue-500/20 border border-blue-400/40 text-blue-300 text-[10px] font-bold">
                📦 Fullyst Import
              </span>
            )}
            {isAdult && (
              <span className="px-2 py-0.5 rounded-full bg-red-500/20 border border-red-500/40 text-red-300 text-[10px] font-bold">
                🔞 18+ Content
              </span>
            )}
          </h3>
          <p className="text-xs text-muted flex items-center gap-2 flex-wrap mt-0.5">
            <span>{stickers.length} stickers loaded. WhatsApp allows up to 30 stickers per pack.</span>
            <span className="text-[11px] text-dim">• Click to toggle selection, Shift-click for range</span>
          </p>
        </div>

        {/* Inline Adult Toggle if 18+ pack */}
        {isAdult && onToggleAdultPreviews && (
          <label className="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-amber-500/15 border border-amber-500/30 text-amber-200 text-xs font-bold cursor-pointer select-none transition hover:bg-amber-500/20">
            <input
              type="checkbox"
              checked={showAdultPreviews}
              onChange={(e) => onToggleAdultPreviews(e.target.checked)}
              className="accent-amber-400 w-4 h-4 rounded cursor-pointer"
            />
            <span>{showAdultPreviews ? '18+ Revealed' : 'Reveal 18+'}</span>
          </label>
        )}
      </div>
      )}

      {/* Toolbar */}
      <div className="p-2.5 sm:p-3 rounded-xl bg-[#101618] border border-line flex items-center justify-between flex-wrap gap-2.5">
        <div className="flex items-center gap-2 flex-wrap">
          {source === 'Fullyst' && onRetryFailed && failedCount > 0 && (
            <button
              type="button"
              onClick={onRetryFailed}
              disabled={converting}
              className="px-2.5 py-1 rounded-lg bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/40 text-amber-300 text-xs font-bold transition disabled:opacity-40"
              title="Re-import this pack to retry the failed stickers"
            >
              Retry {failedCount} skipped
            </button>
          )}
          <button
            type="button"
            onClick={handleSelectAll}
            className="px-2.5 py-1 rounded-lg bg-panel hover:bg-panel-elevated border border-line text-xs font-semibold text-muted hover:text-white transition"
          >
            Select All
          </button>
          <button
            type="button"
            onClick={handleDeselectAll}
            className="px-2.5 py-1 rounded-lg bg-panel hover:bg-panel-elevated border border-line text-xs font-semibold text-muted hover:text-white transition"
          >
            Clear
          </button>
          <button
            type="button"
            onClick={handleInvert}
            className="px-2.5 py-1 rounded-lg bg-panel hover:bg-panel-elevated border border-line text-xs font-semibold text-muted hover:text-white transition"
          >
            Invert
          </button>
        </div>

        {/* Filter Chips */}
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setFilter('all')}
            className={`px-3 py-1 rounded-full text-xs font-semibold border transition ${
              filter === 'all'
                ? 'bg-mint/20 border-mint text-mint'
                : 'bg-panel border-line text-muted hover:text-white'
            }`}
          >
            All ({stickers.length})
          </button>
          {animatedCount > 0 && (
            <button
              type="button"
              onClick={() => setFilter('animated')}
              className={`px-3 py-1 rounded-full text-xs font-semibold border transition ${
                filter === 'animated'
                  ? 'bg-mint/20 border-mint text-mint'
                  : 'bg-panel border-line text-muted hover:text-white'
              }`}
            >
              Animated ({animatedCount})
            </button>
          )}
          {staticCount > 0 && (
            <button
              type="button"
              onClick={() => setFilter('static')}
              className={`px-3 py-1 rounded-full text-xs font-semibold border transition ${
                filter === 'static'
                  ? 'bg-mint/20 border-mint text-mint'
                  : 'bg-panel border-line text-muted hover:text-white'
              }`}
            >
              Static ({staticCount})
            </button>
          )}
        </div>
      </div>

      {/* Grid — fluid auto-fill tracks like webapp.py (auto-fill, minmax), never fixed column counts */}
      <div
        className="grid grid-cols-[repeat(auto-fill,minmax(104px,1fr))] sm:grid-cols-[repeat(auto-fill,minmax(130px,1fr))] gap-2.5 sm:gap-3"
        role="group"
        aria-label="Sticker picker. Use arrow keys to move, Space to select, Shift-click for a range."
        ref={gridRef}
        onKeyDown={handleGridKeyDown}
      >
        {filtered.map((sticker, cardPosition) => {
          const isSelected = selectedIndices.has(sticker.index);
          const isAnimated = sticker.kind === 'Animated';
          const thumbUrl = api.getThumbUrl(jobId, sticker.index);
          const previewUrl = api.getPreviewUrl(jobId, sticker.index);
          // Prefer the inline poster from the job payload (no network round-trip,
          // immune to backend restarts); falling back to the /thumb URL.
          const thumbSrc = sticker.thumbData || thumbUrl;
          // For animated stickers, request the animated GIF from Modal backend; fallback to static poster
          const initialSrc = isAnimated ? previewUrl : thumbSrc;
          const shouldBlur = Boolean(sticker.isAdult || isAdult) && !showAdultPreviews;
          const isSaved = savedIndices.has(sticker.index);
          const isFocused = focusedIndex === sticker.index || (focusedIndex === null && cardPosition === 0);

          return (
            <div
              key={sticker.index}
              data-sticker-index={sticker.index}
              tabIndex={isFocused ? 0 : -1}
              onFocus={() => setFocusedIndex(sticker.index)}
              role="checkbox"
              aria-checked={isSelected}
              aria-label={`Sticker ${sticker.sourceIndex}, ${sticker.kind}${isSelected ? ', selected' : ''}`}
              onClick={(e) => {
                // Ignore clicks originating from inside buttons
                const target = e.target as HTMLElement;
                if (target.closest('button')) return;
                // Swallow the click that follows a long-press preview
                if (holdTriggeredRef.current) {
                  holdTriggeredRef.current = false;
                  return;
                }
                toggleSelect(sticker.index, e.shiftKey);
              }}
              className={`relative p-2 rounded-xl border flex flex-col items-center gap-1.5 cursor-pointer transition select-none group focus:outline-none focus:ring-2 focus:ring-mint ${
                isSelected
                  ? 'bg-mint/10 border-mint shadow-[0_0_0_1px_#38d69d]'
                  : 'bg-[#101618] border-line hover:border-line-strong hover:bg-panel-elevated'
              }`}
            >
              {/* Checkbox indicator */}
              <div
                className={`absolute top-2 left-2 w-4 h-4 rounded flex items-center justify-center border transition ${
                  isSelected ? 'bg-mint border-mint text-black' : 'border-dim bg-transparent'
                }`}
              >
                {isSelected && <Check className="w-3 h-3 stroke-[3]" />}
              </div>

              {/* Thumbnail with checkerboard — press & hold to preview (webapp.py
                  attachHoldPreview); a "View" pill appears on hover (desktop). */}
              <div
                className="relative w-full aspect-square rounded-lg sticker-checkerboard flex items-center justify-center p-1.5 mt-5 overflow-hidden select-none touch-pan-y"
                onPointerDown={(e) => startLongPress(sticker, e)}
                onPointerUp={clearLongPress}
                onPointerLeave={clearLongPress}
                onPointerCancel={clearLongPress}
                onContextMenu={(e) => {
                  e.preventDefault();
                  clearLongPress();
                  onPreview(sticker);
                }}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={initialSrc}
                  alt={`Sticker ${sticker.sourceIndex}`}
                  onError={(e) => {
                    // If animated GIF preview failed, fall back to the static poster
                    if (e.currentTarget.src !== thumbSrc) {
                      e.currentTarget.src = thumbSrc;
                    }
                  }}
                  className={`max-w-full max-h-full object-contain transition duration-200 ${
                    shouldBlur ? 'adult-blurred' : ''
                  }`}
                  loading="lazy"
                  draggable={false}
                />
                {/* Hover quick-view (desktop). Only the pill is hit-testable — the rest of the
                    thumbnail stays click-through so tap-to-select keeps working; invisible until
                    group hover / keyboard focus. On touch, press & hold the thumbnail. */}
                <button
                  type="button"
                  aria-label={`View sticker ${sticker.sourceIndex}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    onPreview(sticker);
                  }}
                  className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-10 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-black/75 text-[11px] font-bold text-white whitespace-nowrap pointer-events-none opacity-0 hover:bg-black/90 group-hover:pointer-events-auto group-hover:opacity-100 focus-visible:pointer-events-auto focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mint transition"
                >
                  <Eye className="w-3.5 h-3.5" /> View
                </button>
              </div>

              {/* Info — webapp.py .sticker-info: "N. emoji" left, KIND right */}
              <div className="w-full flex items-center justify-between gap-1 px-0.5 text-[11px]">
                <span className="font-bold text-[#d7e2de] truncate">
                  {sticker.sourceIndex}. {sticker.emoji || '📎'}
                </span>
                <span className="text-[9px] uppercase tracking-wider text-mint font-extrabold whitespace-nowrap">
                  {sticker.kind}
                </span>
              </div>

              {/* Format label — webapp.py .sticker-emoji */}
              <div className="w-full px-0.5 -mt-1 text-[10px] text-muted whitespace-nowrap overflow-hidden text-ellipsis">
                {sticker.formatLabel || sticker.kind}
              </div>

              {/* Save for later — webapp.py .save-sticker-btn */}
              {onSaveFavorite && (
                <button
                  type="button"
                  disabled={isSaved}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleSave(sticker);
                  }}
                  title={isSaved ? 'Saved to My Favorites' : 'Save to My Favorites'}
                  className="w-full mt-0.5 rounded-lg border border-[#3d5e50] bg-[#1d2c26] px-2 py-1.5 text-[10px] font-bold text-[#bff1d9] hover:bg-[#273b32] transition disabled:opacity-80 disabled:cursor-default"
                >
                  {isSaved ? 'Saved ✓' : 'Save for later'}
                </button>
              )}
            </div>
          );
        })}
      </div>

      {/* Floating Action Bar */}
      <div className="sticky bottom-3 p-3 sm:p-4 rounded-2xl bg-panel/95 backdrop-blur-md border border-mint/40 shadow-2xl flex flex-col gap-2.5 z-30">
        {converting && conversionProgress && conversionProgress.total > 0 && (
          <div className="w-full space-y-1">
            <div className="flex items-center justify-between text-[11px] text-muted">
              <span>Converting stickers on Modal cloud…</span>
              <span>
                {conversionProgress.done} / {conversionProgress.total} (
                {Math.round((conversionProgress.done / conversionProgress.total) * 100)}%)
              </span>
            </div>
            <div className="w-full h-1.5 rounded-full bg-line overflow-hidden">
              <div
                className="h-full bg-mint rounded-full transition-all duration-300"
                style={{
                  width: `${Math.round((conversionProgress.done / conversionProgress.total) * 100)}%`,
                }}
              />
            </div>
          </div>
        )}

        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <span className="text-xs sm:text-sm font-extrabold text-white">
              {selectedIndices.size} sticker{selectedIndices.size === 1 ? '' : 's'} selected
            </span>
            <p className="text-[11px] text-muted">
              {selectedAnimated} animated · {selectedStatic} static — WhatsApp needs ≥3 of one type per pack.
            </p>
          </div>

          <div className="flex items-center gap-2.5">
            <button
              type="button"
              onClick={() => onConvertSelected(Array.from(selectedIndices).sort((a, b) => a - b))}
              disabled={converting || !canCreatePack}
              className="inline-flex items-center gap-1.5 px-4 sm:px-5 py-2 rounded-xl bg-mint hover:bg-mint-hover text-black font-extrabold text-xs shadow-md shadow-mint/20 transition disabled:opacity-40"
            >
              <Sparkles className="w-4 h-4" />
              <span>Create Selected Pack</span>
            </button>

            <button
              type="button"
              onClick={onConvertFull}
              disabled={converting}
              className="px-3.5 sm:px-4 py-2 rounded-xl bg-panel-elevated hover:bg-[#253337] border border-line hover:border-line-strong text-xs font-bold text-white transition disabled:opacity-40"
            >
              Convert Full Pack
            </button>
          </div>
        </div>
      </div>
    </Container>
  );
}
