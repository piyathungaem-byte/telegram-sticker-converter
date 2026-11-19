'use client';

import React, { useEffect, useRef } from 'react';
import { X, Loader2, AlertCircle, Terminal, ExternalLink, AlertTriangle } from 'lucide-react';
import { StickerGrid } from './StickerGrid';
import { ResultsSection } from './ResultsSection';
import { Sticker, PackFile, JobActivity } from '../lib/types';

interface PackModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  isAdult?: boolean;
  showAdultPreviews?: boolean;
  onToggleAdultPreviews?: (val: boolean) => void;
  loading: boolean;
  jobState: {
    phase: string;
    message: string;
    done: number;
    total: number;
    isFailed?: boolean;
    activityLog?: JobActivity[];
    failedCount?: number;
    source?: string;
    sourceSlug?: string;
    isAdult?: boolean;
  };
  jobId: string | null;
  stickers: Sticker[];
  packs: PackFile[];
  converting: boolean;
  onPreview: (sticker: Sticker) => void;
  onSaveFavorite?: (sticker: Sticker) => void;
  onConvertSelected: (selectedIndices: number[]) => void;
  onConvertFull: () => void;
  onRetryFailed?: () => void;
}

export function PackModal({
  isOpen,
  onClose,
  title,
  isAdult = false,
  showAdultPreviews = false,
  onToggleAdultPreviews,
  loading,
  jobState,
  jobId,
  stickers,
  packs,
  converting,
  onPreview,
  onSaveFavorite,
  onConvertSelected,
  onConvertFull,
  onRetryFailed,
}: PackModalProps) {
  const logContainerRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  // Lock body scroll, focus the close button, and restore focus on close
  // (Escape is handled centrally in page.tsx so it closes the topmost modal first)
  useEffect(() => {
    if (!isOpen) return;
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    document.body.style.overflow = 'hidden';
    const timer = setTimeout(() => {
      if (closeButtonRef.current) closeButtonRef.current.focus();
    }, 0);
    return () => {
      clearTimeout(timer);
      document.body.style.removeProperty('overflow');
      if (previousFocusRef.current && document.contains(previousFocusRef.current)) {
        previousFocusRef.current.focus();
      }
    };
  }, [isOpen]);

  // Auto-scroll activity log
  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [jobState.activityLog?.length]);

  if (!isOpen) return null;

  const pct =
    jobState.total > 0
      ? Math.min(100, Math.round((jobState.done / jobState.total) * 100))
      : 0;

  const isRunning = loading || converting;
  const showSkeleton = isRunning && stickers.length === 0;
  const showStatusBar = isRunning || jobState.isFailed;

  // Build subtitle text matching webapp.py updatePackModalStatus
  const computedSubtitle = (() => {
    if (jobState.isFailed) return jobState.message || 'The pack could not be loaded.';
    const sourceLabel = jobState.source === 'Fullyst' ? 'Fullyst' : 'Telegram';
    if (isRunning) {
      return `${sourceLabel} pack${jobState.sourceSlug ? ` · ${jobState.sourceSlug}` : ''}`;
    }
    if (packs.length > 0) return jobState.message || 'Pack ready to download.';
    if (stickers.length > 0) {
      const failedNote = jobState.failedCount ? ` · ${jobState.failedCount} skipped` : '';
      const slugNote = jobState.sourceSlug ? ` · ${jobState.sourceSlug}` : '';
      return `${stickers.length} stickers ready${failedNote}${slugNote}`;
    }
    return 'Loading the selected sticker pack…';
  })();

  return (
    /* pack-modal — matches webapp.py .pack-modal exactly: fixed fullscreen overlay, z-1000, grid center */
    <div className="pack-modal" role="dialog" aria-modal="true" aria-labelledby="packModalTitle">
      {/* Backdrop — click to close */}
      <div className="pack-modal-backdrop" onClick={onClose} />

      {/* pack-dialog — the centered dialog box */}
      <section className="pack-dialog">
        {/* ═══ Header (pack-dialog-head) ═══ */}
        <header className="pack-dialog-head">
          <div className="min-w-0 flex-1">
            <h2 id="packModalTitle" className="pack-dialog-title">
              {title || 'View pack'}
              {isAdult && (
                <span className="ml-2 inline-flex items-center px-2 py-0.5 rounded-full bg-red-500/20 text-red-300 border border-red-500/40 text-[10px] font-extrabold align-middle">
                  🔞 18+
                </span>
              )}
            </h2>
            <p className="pack-dialog-subtitle">{computedSubtitle}</p>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            {/* Inline Adult Toggle if 18+ pack */}
            {isAdult && onToggleAdultPreviews && (
              <label className="adult-toggle-label-modal">
                <input
                  type="checkbox"
                  checked={showAdultPreviews}
                  onChange={(e) => onToggleAdultPreviews(e.target.checked)}
                  className="accent-amber-400 w-4 h-4 rounded cursor-pointer"
                />
                <span>{showAdultPreviews ? '18+ Revealed' : 'Reveal 18+'}</span>
              </label>
            )}

            <button
              type="button"
              onClick={onClose}
              ref={closeButtonRef}
              className="pack-dialog-close-btn"
              aria-label="Close pack viewer"
            >
              Close
            </button>
          </div>
        </header>

        {/* ═══ Status Bar (pack-modal-status) — shown while loading/converting/failed ═══ */}
        {showStatusBar && (
          <div className="pack-modal-status">
            <div className="pack-modal-status-head">
              <div>
                <strong className="flex items-center gap-1.5 text-xs text-white">
                  {jobState.isFailed ? (
                    <AlertCircle className="w-3.5 h-3.5 text-red-400" />
                  ) : (
                    <Loader2 className="w-3.5 h-3.5 text-mint animate-spin" />
                  )}
                  <span>{jobState.phase || 'Loading pack…'}</span>
                </strong>
                <p className="text-[11px] text-muted mt-0.5">
                  {jobState.message || 'Getting pack details and sticker previews.'}
                </p>
              </div>
              {jobState.total > 0 && (
                <span className="text-xs text-muted font-mono whitespace-nowrap">
                  {jobState.done} / {jobState.total}
                </span>
              )}
            </div>

            {/* Progress bar */}
            {jobState.total > 0 && !jobState.isFailed && (
              <div className="mt-2.5">
                <div className="progress-track">
                  <div
                    className="progress-fill"
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            )}

            {/* Shimmer Skeleton Grid — matches webapp.py .skeleton-grid */}
            {showSkeleton && (
              <div className="skeleton-grid">
                {Array.from({ length: 8 }).map((_, i) => (
                  <div key={i} className="skeleton-card">
                    <div className="skeleton-sticker" />
                    <div className="skeleton-line" />
                    <div className="skeleton-line short" />
                  </div>
                ))}
              </div>
            )}

            {/* Failed Items Warning */}
            {jobState.failedCount && jobState.failedCount > 0 && !jobState.isFailed && (
              <div className="flex items-center gap-2 mt-2 p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-[11px]">
                <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
                <span>
                  {jobState.failedCount} sticker{jobState.failedCount > 1 ? 's' : ''} failed to download.
                </span>
              </div>
            )}
          </div>
        )}

        {/* ═══ Activity Log inside modal (matching webapp.py activityPanel move) ═══
            Kept visible after the job finishes too — webapp.py always shows the log
            panel; it stays open while running, collapsed once done. */}
        {(isRunning || (jobState.activityLog?.length ?? 0) > 0) && jobState.activityLog && (
          <div className="pack-modal-activity">
            <details
              open
              className="live-log-modal"
            >
              <summary className="live-log-summary">
                <span className="flex items-center gap-1.5">
                  <Terminal className="w-3.5 h-3.5 text-mint" />
                  <span>Live activity log</span>
                </span>
                <span className="log-count-label">
                  {jobState.activityLog.length
                    ? `${jobState.activityLog.length} event${jobState.activityLog.length === 1 ? '' : 's'}`
                    : 'Waiting for events'}
                </span>
              </summary>
              <div
                ref={logContainerRef}
                className="log-entries-modal"
              >
                {jobState.activityLog.length > 0 ? (
                  jobState.activityLog.map((entry, idx) => (
                    <div key={idx} className="log-entry">
                      <span className="log-time">{entry.time}</span>
                      <span className="log-phase">{entry.phase}</span>
                      <span className="log-message">{entry.message}</span>
                    </div>
                  ))
                ) : (
                  <div className="log-empty">Fetch or convert a pack to see progress events.</div>
                )}
              </div>
            </details>
          </div>
        )}

        {/* ═══ Content area (pack-dialog-content) ═══ */}
        <div className="pack-dialog-content">
          {/* Error state */}
          {jobState.isFailed && (
            <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-red-300 text-xs flex items-center gap-3">
              <AlertCircle className="w-5 h-5 flex-shrink-0 text-red-400" />
              <div>
                <strong>Could not load the pack</strong>
                <p className="text-[11px] opacity-80 mt-0.5">{jobState.message}</p>
              </div>
            </div>
          )}

          {/* Results: If conversion completed */}
          {packs.length > 0 && jobId && (
            <ResultsSection packs={packs} jobId={jobId} />
          )}

          {/* Sticker Grid Selection — rendered FLAT (embedded=true, no outer section wrapper) */}
          {stickers.length > 0 && jobId && (
            <StickerGrid
              stickers={stickers}
              jobId={jobId}
              packTitle={title}
              onPreview={onPreview}
              onSaveFavorite={onSaveFavorite}
              onConvertSelected={onConvertSelected}
              onConvertFull={onConvertFull}
              converting={converting}
              source={jobState.source}
              failedCount={jobState.failedCount ?? 0}
              onRetryFailed={onRetryFailed}
              isAdult={isAdult}
              showAdultPreviews={showAdultPreviews}
              onToggleAdultPreviews={onToggleAdultPreviews}
              conversionProgress={{ done: jobState.done, total: jobState.total }}
              embedded={true}
            />
          )}

          {/* Source Link */}
          {jobState.sourceSlug && (
            <div className="pt-3">
              <a
                href={
                  jobState.source === 'Fullyst'
                    ? `https://fullyst.com/en/stickers/${encodeURIComponent(jobState.sourceSlug)}`
                    : `https://t.me/addstickers/${encodeURIComponent(jobState.sourceSlug)}`
                }
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-[11px] text-muted hover:text-mint transition"
              >
                <ExternalLink className="w-3 h-3 text-mint" />
                <span>
                  View on {jobState.source === 'Fullyst' ? 'Fullyst' : 'Telegram'} ({jobState.sourceSlug})
                </span>
              </a>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
