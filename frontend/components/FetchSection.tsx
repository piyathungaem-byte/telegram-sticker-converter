'use client';

import React, { useState, useEffect, useRef } from 'react';
import {
  Eye,
  EyeOff,
  Send,
  Loader2,
  Sparkles,
  Terminal,
  RotateCcw,
  ExternalLink,
  AlertTriangle,
  Compass,
} from 'lucide-react';
import { JobActivity } from '../lib/types';

interface FetchSectionProps {
  packLink: string;
  setPackLink: (v: string) => void;
  botToken: string;
  setBotToken: (v: string) => void;
  rememberToken: boolean;
  setRememberToken: (v: boolean) => void;
  onFetch: () => void;
  onReset?: () => void;
  onOpenFullyst?: () => void;
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
  showAdultPreviews?: boolean;
  onToggleAdultPreviews?: (v: boolean) => void;
}

export function FetchSection({
  packLink,
  setPackLink,
  botToken,
  setBotToken,
  rememberToken,
  setRememberToken,
  onFetch,
  onReset,
  onOpenFullyst,
  loading,
  jobState,
  showAdultPreviews = false,
  onToggleAdultPreviews,
}: FetchSectionProps) {
  const [showToken, setShowToken] = useState(false);
  const logContainerRef = useRef<HTMLDivElement>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onFetch();
  };

  const pct =
    jobState.total > 0
      ? Math.min(100, Math.round((jobState.done / jobState.total) * 100))
      : jobState.phase === 'Done'
      ? 100
      : 0;

  // Auto-scroll activity log to bottom as entries arrive
  useEffect(() => {
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [jobState.activityLog?.length]);

  return (
    <section className="p-6 rounded-2xl bg-panel border border-line shadow-xl">
      <form onSubmit={handleSubmit} className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Left Column: Fetch Form (7 cols) */}
        <div className="lg:col-span-7 space-y-4">
          <div>
            <h2 className="text-base font-extrabold text-white mb-2">1 · Fetch Telegram pack</h2>
            <label className="block text-xs font-bold text-white mb-1.5">
              Telegram Sticker Pack Link or Name
            </label>
            <input
              type="text"
              required
              placeholder="https://t.me/addstickers/YourPack or pack_name"
              value={packLink}
              onChange={(e) => setPackLink(e.target.value)}
              className="w-full bg-[#101618] border border-line hover:border-line-strong focus:border-mint focus:ring-1 focus:ring-mint rounded-xl px-3.5 py-2.5 text-sm text-white placeholder-dim outline-none transition"
            />
            <p className="text-[11.5px] text-muted mt-1.5">
              Paste any public Telegram sticker set URL or identifier.
            </p>
          </div>

          <div>
            <label className="block text-xs font-bold text-white mb-1.5">
              Telegram Bot Token (from @BotFather)
            </label>
            <div className="relative">
              <input
                type={showToken ? 'text' : 'password'}
                placeholder="Paste token (or use saved token)"
                value={botToken}
                onChange={(e) => setBotToken(e.target.value)}
                className="w-full bg-[#101618] border border-line hover:border-line-strong focus:border-mint focus:ring-1 focus:ring-mint rounded-xl pl-3.5 pr-10 py-2.5 text-sm text-white placeholder-dim outline-none transition font-mono"
              />
              <button
                type="button"
                onClick={() => setShowToken(!showToken)}
                className="absolute right-3 top-2.5 text-muted hover:text-white"
              >
                {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
            <p className="text-[11.5px] text-muted mt-1.5">
              Needed to download stickers. Get a free token from{' '}
              <a
                href="https://t.me/BotFather"
                target="_blank"
                rel="noopener noreferrer"
                className="text-mint hover:underline font-semibold"
              >
                @BotFather
              </a>
              . Kept safe in your browser.
            </p>
          </div>

          <label className="flex items-center gap-2.5 text-xs text-muted cursor-pointer select-none">
            <input
              type="checkbox"
              checked={rememberToken}
              onChange={(e) => setRememberToken(e.target.checked)}
              className="accent-mint w-4 h-4 rounded"
            />
            <span>Remember token on this browser (saved safely in localStorage)</span>
          </label>

          {/* Action Buttons */}
          <div className="space-y-2.5 pt-1">
            <div className="flex items-center gap-2">
              <button
                type="submit"
                disabled={loading || !packLink.trim()}
                className="flex-1 inline-flex items-center justify-center gap-2 py-3 px-5 rounded-xl bg-mint hover:bg-mint-hover text-black font-extrabold text-sm shadow-md shadow-mint/20 transition disabled:opacity-40"
              >
                {loading ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>Contacting Telegram…</span>
                  </>
                ) : (
                  <>
                    <Send className="w-4 h-4" />
                    <span>Fetch sticker pack</span>
                  </>
                )}
              </button>

              {onReset && (loading || (jobState.phase && jobState.phase !== 'Ready')) && (
                <button
                  type="button"
                  onClick={onReset}
                  className="inline-flex items-center justify-center gap-1.5 py-3 px-3.5 rounded-xl bg-panel-elevated hover:bg-red-500/20 hover:border-red-500/40 border border-line text-muted hover:text-red-300 font-bold text-xs transition"
                  title="Reset current job"
                >
                  <RotateCcw className="w-4 h-4" />
                  <span className="hidden sm:inline">Reset</span>
                </button>
              )}
            </div>

            {/* Secondary Catalog Button matching webapp.py:line 32 */}
            {onOpenFullyst && (
              <button
                type="button"
                onClick={onOpenFullyst}
                className="w-full inline-flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl bg-panel-elevated hover:bg-[#253437] border border-line hover:border-mint/50 text-xs font-bold text-[#c2f1dc] transition shadow-sm"
              >
                <Compass className="w-4 h-4 text-mint" />
                <span>Browse Fullyst catalog · no Bot token</span>
              </button>
            )}
          </div>

          {/* Adult Pack Control if adult pack detected (from webapp.py:line 30) */}
          {jobState.isAdult && onToggleAdultPreviews && (
            <div className="p-3 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-center justify-between gap-3 text-xs">
              <div className="space-y-0.5">
                <span className="font-bold text-amber-200">18+ Content Detected</span>
                <p className="text-[11px] text-amber-300/80">Allow unblurred previews for this sticker pack.</p>
              </div>
              <label className="flex items-center gap-2 cursor-pointer text-amber-200 font-bold select-none">
                <input
                  type="checkbox"
                  checked={showAdultPreviews}
                  onChange={(e) => onToggleAdultPreviews(e.target.checked)}
                  className="accent-amber-400 w-4 h-4 rounded cursor-pointer"
                />
                <span>Reveal</span>
              </label>
            </div>
          )}
        </div>

        {/* Right Column: Live Activity Card (5 cols) matching webapp.py */}
        <div className="lg:col-span-5 p-4 rounded-xl bg-[#101618] border border-line space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="font-bold text-xs text-white flex items-center gap-1.5">
              <Sparkles className="w-3.5 h-3.5 text-mint" />
              <span>Live Activity</span>
            </h3>
            <span
              className={`px-2.5 py-0.5 rounded-full text-[10px] font-extrabold uppercase tracking-wide ${
                jobState.isFailed
                  ? 'bg-red-500/20 text-red-400'
                  : 'bg-mint/20 text-mint'
              }`}
            >
              {jobState.phase || 'READY'}
            </span>
          </div>

          <p className="text-xs text-muted min-h-[36px] line-clamp-2">
            {jobState.message || 'Ready to fetch your sticker pack.'}
          </p>

          {/* Progress Track */}
          <div>
            <div className="h-2 rounded-full bg-[#0a0e10] border border-line overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-mint to-emerald-400 rounded-full transition-all duration-300"
                style={{ width: `${pct}%` }}
              />
            </div>
            <div className="flex items-center justify-between text-[11px] text-muted mt-1.5">
              <span>
                {jobState.done} / {jobState.total}
              </span>
              <span>{pct}%</span>
            </div>
          </div>

          {/* Failed Items Warning Box */}
          {jobState.failedCount && jobState.failedCount > 0 ? (
            <div className="flex items-center gap-2 p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-[11px]">
              <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
              <span>
                {jobState.failedCount} sticker{jobState.failedCount > 1 ? 's' : ''} failed to download from source.
              </span>
            </div>
          ) : null}

          {/* Source Link */}
          {jobState.sourceSlug ? (
            <div className="pt-0.5">
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
          ) : null}

          {/* Real-time Activity Log Drawer matching webapp.py */}
          <details
            open={!!(jobState.activityLog && jobState.activityLog.length > 0)}
            className="mt-3 rounded-lg border border-line bg-[#0a0e10] text-[11px] font-mono overflow-hidden"
          >
            <summary className="p-2 cursor-pointer font-sans font-bold text-muted hover:text-white flex items-center justify-between gap-1.5 select-none">
              <span className="flex items-center gap-1.5">
                <Terminal className="w-3.5 h-3.5 text-mint" />
                <span>Live activity log</span>
              </span>
              <span className="text-[10px] text-muted font-normal">
                {jobState.activityLog?.length
                  ? `${jobState.activityLog.length} event${jobState.activityLog.length === 1 ? '' : 's'}`
                  : 'Waiting for a job'}
              </span>
            </summary>
            <div
              ref={logContainerRef}
              className="p-2 max-h-36 overflow-y-auto border-t border-line space-y-1.5 text-muted scroll-smooth"
            >
              {jobState.activityLog && jobState.activityLog.length > 0 ? (
                jobState.activityLog.map((entry, idx) => (
                  <div key={idx} className="flex items-start gap-2 leading-relaxed text-[11px]">
                    <span className="text-dim flex-shrink-0 font-mono">{entry.time}</span>
                    <span className="text-mint flex-shrink-0 font-bold">{entry.phase}</span>
                    <span className="text-white truncate">{entry.message}</span>
                  </div>
                ))
              ) : (
                <div className="text-dim py-1">Fetch or convert a pack to see progress events.</div>
              )}
            </div>
          </details>
        </div>
      </form>
    </section>
  );
}
