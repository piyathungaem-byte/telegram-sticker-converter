'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Check, Eye, Layers, Sparkles, Star, EyeOff } from 'lucide-react';
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

  // CRITICAL FIX FOR "hover auto select stickers":
  // Only select all stickers when a NEW jobId is loaded, NOT on every 700ms polling tick!
  const lastJobIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!jobId) return;
    if (jobId !== lastJobIdRef.current) {
      lastJobIdRef.current = jobId;
      setSelectedIndices(new Set(stickers.map((s) => s.index)));
    } else if (stickers.length > 0 && selectedIndices.size === 0 && lastJobIdRef.current === null) {
      lastJobIdRef.current = jobId;
      setSelectedIndices(new Set(stickers.map((s) => s.index)));
    }
  }, [jobId, stickers]);

  const filtered = stickers.filter((s) => {
    if (filter === 'animated') return s.kind === 'Animated';
    if (filter === 'static') return s.kind === 'Static';
    return true;
  });

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
      {/* Title & Subtitle */}
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

      {/* Toolbar */}
      <div className="p-2.5 sm:p-3 rounded-xl bg-[#101618] border border-line flex items-center justify-between flex-wrap gap-2.5">
        <div className="flex items-center gap-2">
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

      {/* Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-2.5 sm:gap-3">
        {filtered.map((sticker) => {
          const isSelected = selectedIndices.has(sticker.index);
          const isAnimated = sticker.kind === 'Animated';
          const thumbUrl = api.getThumbUrl(jobId, sticker.index);
          const previewUrl = api.getPreviewUrl(jobId, sticker.index);
          // For animated stickers, request the animated GIF from Modal backend; fallback to static PNG thumb
          const initialSrc = isAnimated ? previewUrl : thumbUrl;
          const shouldBlur = Boolean(sticker.isAdult || isAdult) && !showAdultPreviews;
          const isSaved = savedIndices.has(sticker.index);

          return (
            <div
              key={sticker.index}
              onClick={(e) => {
                // Ignore clicks originating from inside buttons
                const target = e.target as HTMLElement;
                if (target.closest('button')) return;
                toggleSelect(sticker.index, e.shiftKey);
              }}
              className={`relative p-2 rounded-xl border flex flex-col items-center gap-1.5 cursor-pointer transition select-none group ${
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

              {/* Action buttons (top right): Star / Save and Preview */}
              <div className="absolute top-1.5 right-1.5 flex items-center gap-1 z-10">
                {onSaveFavorite && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleSave(sticker);
                    }}
                    className={`p-1 rounded-md transition ${
                      isSaved
                        ? 'bg-mint/20 text-mint'
                        : 'bg-[#0a0e10]/80 text-muted hover:text-mint hover:bg-panel-elevated'
                    }`}
                    title={isSaved ? 'Saved to Favorites' : 'Save to Favorites'}
                  >
                    <Star className={`w-3.5 h-3.5 ${isSaved ? 'fill-mint' : ''}`} />
                  </button>
                )}
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onPreview(sticker);
                  }}
                  className="p-1 rounded-md bg-[#0a0e10]/80 text-muted hover:text-white hover:bg-panel-elevated transition"
                  title="Enlarge Preview"
                >
                  <Eye className="w-3.5 h-3.5" />
                </button>
              </div>

              {/* Thumbnail with Checkerboard & Animated Preview */}
              <div className="w-20 h-20 rounded-lg sticker-checkerboard flex items-center justify-center p-1.5 mt-5 overflow-hidden">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={initialSrc}
                  alt={`Sticker ${sticker.sourceIndex}`}
                  onError={(e) => {
                    // If animated GIF preview failed, fall back to PNG thumbnail
                    if (isAnimated && e.currentTarget.src !== thumbUrl) {
                      e.currentTarget.src = thumbUrl;
                    }
                  }}
                  className={`max-w-full max-h-full object-contain transition duration-200 ${
                    shouldBlur ? 'adult-blurred' : ''
                  }`}
                  loading="lazy"
                  draggable={false}
                />
              </div>

              {/* Meta */}
              <div className="w-full flex items-center justify-between text-[11px] px-0.5">
                <span
                  className={`px-1.5 py-0.5 rounded text-[9px] font-extrabold uppercase ${
                    sticker.kind === 'Animated' ? 'bg-mint/20 text-mint' : 'bg-line text-muted'
                  }`}
                >
                  {sticker.formatLabel || sticker.kind}
                </span>
                <span className="text-xs">{sticker.emoji || '📎'}</span>
              </div>
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
              WhatsApp requires ≥3 Animated OR ≥3 Static per pack.
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
