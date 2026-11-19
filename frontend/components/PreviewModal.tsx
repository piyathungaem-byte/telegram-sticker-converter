'use client';

import React, { useState, useEffect } from 'react';
import { X, Star, Loader2, Eye, EyeOff } from 'lucide-react';
import { Sticker } from '../lib/types';
import { api } from '../lib/api';

interface PreviewModalProps {
  sticker: Sticker | null;
  jobId: string | null;
  isOpen: boolean;
  isAdult?: boolean;
  showAdultPreviews?: boolean;
  onClose: () => void;
  onSaveFavorite: (sticker: Sticker) => void;
  onToggleAdultPreviews?: (revealed: boolean) => void;
}

export function PreviewModal({
  sticker,
  jobId,
  isOpen,
  isAdult = false,
  showAdultPreviews = false,
  onClose,
  onSaveFavorite,
  onToggleAdultPreviews,
}: PreviewModalProps) {
  const [imgLoaded, setImgLoaded] = useState(false);
  const [revealed, setRevealed] = useState(showAdultPreviews);

  useEffect(() => {
    setImgLoaded(false);
    setRevealed(showAdultPreviews);
  }, [sticker, showAdultPreviews]);

  if (!isOpen || !sticker || !jobId) return null;

  const previewUrl = api.getPreviewUrl(jobId, sticker.index);
  const thumbUrl = api.getThumbUrl(jobId, sticker.index);
  const isBlurred = isAdult && !revealed;

  const toggleReveal = () => {
    const nextVal = !revealed;
    setRevealed(nextVal);
    onToggleAdultPreviews?.(nextVal);
  };

  return (
    <div
      className="fixed inset-0 z-[1000] bg-black/85 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-panel border border-line-strong rounded-2xl max-w-sm w-full overflow-hidden shadow-2xl animate-in fade-in zoom-in-95 duration-150 text-center"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-line">
          <div className="flex items-center gap-2">
            <h3 className="font-bold text-sm text-white">
              Sticker #{sticker.sourceIndex} ({sticker.emoji || '📎'})
            </h3>
            {isAdult && (
              <span className="px-2 py-0.5 rounded-full bg-red-500/20 text-red-300 text-[10px] font-extrabold border border-red-500/30">
                18+
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-muted hover:text-white hover:bg-panel-elevated transition"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Preview Stage Box */}
        <div className="p-6">
          <div className="relative w-60 h-60 mx-auto mb-4 rounded-2xl sticker-checkerboard border border-line flex items-center justify-center p-3 overflow-hidden shadow-inner">
            {!imgLoaded && (
              <div className="absolute inset-0 animate-shimmer flex items-center justify-center z-10">
                <Loader2 className="w-6 h-6 text-mint animate-spin opacity-50" />
              </div>
            )}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={previewUrl}
              alt={`Sticker ${sticker.sourceIndex}`}
              onLoad={() => setImgLoaded(true)}
              onError={(e) => {
                if (e.currentTarget.src !== thumbUrl) {
                  e.currentTarget.src = thumbUrl;
                }
              }}
              className={`max-w-full max-h-full object-contain transition duration-200 ${
                imgLoaded ? 'opacity-100' : 'opacity-0'
              } ${isBlurred ? 'adult-blurred cursor-pointer' : ''}`}
              onClick={() => {
                if (isAdult) toggleReveal();
              }}
            />

            {/* 18+ Reveal Badge Overlay */}
            {isAdult && (
              <button
                type="button"
                onClick={toggleReveal}
                className="absolute bottom-2 right-2 px-2.5 py-1 rounded-lg bg-black/80 hover:bg-black text-[10px] font-bold text-amber-200 border border-amber-500/30 flex items-center gap-1 z-20 backdrop-blur-sm transition"
              >
                {revealed ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
                <span>{revealed ? 'Blur 18+' : 'Reveal 18+'}</span>
              </button>
            )}
          </div>

          <div className="text-xs text-muted mb-5 flex items-center justify-center gap-2">
            <span className="font-bold text-white uppercase text-[10px] px-2 py-0.5 rounded bg-panel-elevated border border-line">
              {sticker.formatLabel || sticker.kind}
            </span>
            <span>•</span>
            <span>Emoji: {sticker.emoji || '📎'}</span>
            {sticker.kind === 'Animated' && (
              <>
                <span>•</span>
                <span className="text-mint font-semibold">Animated GIF</span>
              </>
            )}
          </div>

          <button
            onClick={() => onSaveFavorite(sticker)}
            className="w-full inline-flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl bg-panel-elevated hover:bg-[#233134] border border-line hover:border-line-strong text-xs font-bold text-white transition"
          >
            <Star className="w-3.5 h-3.5 text-mint" />
            <span>Save to My Favorites</span>
          </button>
        </div>
      </div>
    </div>
  );
}
