'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Header } from '../components/Header';
import { FetchSection } from '../components/FetchSection';
import { StickerGrid } from '../components/StickerGrid';
import { ResultsSection } from '../components/ResultsSection';
import { FullystExplorer } from '../components/FullystExplorer';
import { SettingsModal } from '../components/SettingsModal';
import { PreviewModal } from '../components/PreviewModal';
import { CollectionModal } from '../components/CollectionModal';
import { PackModal } from '../components/PackModal';
import { api, checkBackendHealth, getSavedToken, setSavedToken, getAppSettings, setAppSettings } from '../lib/api';
import { CollectionGroup, CollectionItem, PackFile, Sticker } from '../lib/types';
import { AlertTriangle, Globe, Send } from 'lucide-react';

export default function Home() {
  // Navigation — Catalog Explorer is now the default landing tab
  const [activeTab, setActiveTab] = useState<'telegram' | 'fullyst'>('fullyst');

  // Backend Status
  const [backendStatus, setBackendStatus] = useState<{
    status: 'checking' | 'connected' | 'disconnected' | 'unconfigured';
    latency?: number;
  }>({ status: 'checking' });

  // App Settings (adult content blur and catalog filtering)
  const [appSettings, setAppSettingsState] = useState<{
    showAdultPreviews: boolean;
    hideAdultContent: boolean;
  }>({
    showAdultPreviews: false,
    hideAdultContent: false,
  });

  // Modals
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isCollectionOpen, setIsCollectionOpen] = useState(false);
  const [previewSticker, setPreviewSticker] = useState<Sticker | null>(null);

  // Pack Modal (Full pack dialog view — opens over catalog, like webapp.py #packModal)
  const [packModalOpen, setPackModalOpen] = useState(false);
  const [packModalTitle, setPackModalTitle] = useState('');
  const [packModalAdult, setPackModalAdult] = useState(false);

  // Toast
  const [toast, setToast] = useState<{ show: boolean; message: string; type: 'info' | 'success' | 'error' | 'warn' }>({
    show: false,
    message: '',
    type: 'info',
  });

  // Fetch Form
  const [packLink, setPackLink] = useState('');
  const [botToken, setBotToken] = useState('');
  const [rememberToken, setRememberToken] = useState(true);
  const [fetching, setFetching] = useState(false);
  const [converting, setConverting] = useState(false);

  // Job & Results State
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);
  const [jobState, setJobState] = useState<{
    phase: string;
    message: string;
    done: number;
    total: number;
    isFailed?: boolean;
    activityLog?: any[];
    failedCount?: number;
    source?: string;
    sourceSlug?: string;
    isAdult?: boolean;
  }>({
    phase: 'Ready',
    message: 'Ready to fetch your sticker pack.',
    done: 0,
    total: 0,
  });

  const [stickers, setStickers] = useState<Sticker[]>([]);
  const [packTitle, setPackTitle] = useState('');
  const [packs, setPacks] = useState<PackFile[]>([]);

  // Collection
  const [collectionItems, setCollectionItems] = useState<CollectionItem[]>([]);
  const [collectionGroups, setCollectionGroups] = useState<CollectionGroup[]>([]);

  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);

  const showToast = useCallback((message: string, type: 'info' | 'success' | 'error' | 'warn' = 'info') => {
    setToast({ show: true, message, type });
    setTimeout(() => {
      setToast((prev) => ({ ...prev, show: false }));
    }, 3500);
  }, []);

  const refreshHealth = async () => {
    setBackendStatus({ status: 'checking' });
    const health = await checkBackendHealth();
    if (health.ok) {
      setBackendStatus({ status: 'connected', latency: health.latency });
    } else if (health.error === 'No backend URL configured.') {
      setBackendStatus({ status: 'unconfigured' });
    } else {
      setBackendStatus({ status: 'disconnected' });
    }
  };

  const refreshCollection = async () => {
    try {
      const data = await api.getCollection();
      setCollectionItems(data.items || []);
      setCollectionGroups(data.groups || []);
    } catch {
      // ignore
    }
  };

  // Reload settings from localStorage
  const refreshSettings = useCallback(() => {
    setAppSettingsState(getAppSettings());
  }, []);

  // Stable close handlers so modal effects don't re-run on every poll tick
  const closePackModal = useCallback(() => setPackModalOpen(false), []);
  const closeSettingsModal = useCallback(() => setIsSettingsOpen(false), []);
  const closeCollectionModal = useCallback(() => setIsCollectionOpen(false), []);
  const closePreviewModal = useCallback(() => setPreviewSticker(null), []);

  // Centralized Escape handling with the same priority order as webapp.py:
  // sticker preview → settings → saved collection → pack modal
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (previewSticker) {
        e.preventDefault();
        setPreviewSticker(null);
      } else if (isSettingsOpen) {
        e.preventDefault();
        setIsSettingsOpen(false);
      } else if (isCollectionOpen) {
        e.preventDefault();
        setIsCollectionOpen(false);
      } else if (packModalOpen) {
        e.preventDefault();
        setPackModalOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [previewSticker, isSettingsOpen, isCollectionOpen, packModalOpen]);

  // Cleanup polling interval on unmount
  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    setBotToken(getSavedToken());
    setAppSettingsState(getAppSettings());
    refreshHealth();
    refreshCollection();

    // Resume latest job on page reload
    (async () => {
      try {
        const latest = await api.getLatest();
        if (latest.id) {
          const job = await api.getJob(latest.id);
          setCurrentJobId(job.id);
          if (job.title) setPackTitle(job.title);
          if (job.stickers && job.stickers.length > 0) setStickers(job.stickers);

          if (job.status === 'ready') {
            setJobState({
              phase: job.phase || job.status,
              message: job.message || '',
              done: job.done || 0,
              total: job.total || 0,
              isFailed: false,
              activityLog: job.activityLog || [],
              failedCount: job.failedCount,
              source: job.source,
              sourceSlug: job.sourceSlug,
              isAdult: job.isAdult,
            });
            startPolling(job.id);
          } else if (job.status === 'done') {
            setJobState({
              phase: job.phase || 'Done',
              message: job.message || '',
              done: job.done || 0,
              total: job.total || 0,
              isFailed: false,
              activityLog: job.activityLog || [],
              failedCount: job.failedCount,
              source: job.source,
              sourceSlug: job.sourceSlug,
              isAdult: job.isAdult,
            });
            if (job.packs && job.packs.length > 0) setPacks(job.packs);
          } else if (job.status === 'failed' || job.status === 'running' || job.status === 'queued') {
            startPolling(job.id);
          }
        }
      } catch {
        // Silently ignore
      }
    })();
  }, []);

  // Polling loop
  const startPolling = (jobId: string) => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }

    const intervalId = setInterval(async () => {
      try {
        const job = await api.getJob(jobId);
        setJobState({
          phase: job.phase || job.status,
          message: job.message || '',
          done: job.done || 0,
          total: job.total || 0,
          isFailed: job.status === 'failed',
          activityLog: job.activityLog || [],
          failedCount: job.failedCount,
          source: job.source,
          sourceSlug: job.sourceSlug,
          isAdult: job.isAdult,
        });

        if (job.title) setPackTitle(job.title);
        if (job.isAdult) setPackModalAdult(true);

        // STABLE STICKERS UPDATE: Avoid re-creating stickers array if length & content unchanged
        if (job.stickers && job.stickers.length > 0) {
          setStickers((prev) => {
            if (prev.length === job.stickers!.length) {
              const changed = job.stickers!.some((s, idx) => {
                const p = prev[idx];
                return !p || p.previewReady !== s.previewReady || p.kind !== s.kind;
              });
              if (!changed) return prev;
            }
            return job.stickers!;
          });
        }

        // Clear fetching spinner once the pack is "ready" or done
        if (job.status === 'ready' || job.status === 'done' || (job.stickers && job.stickers.length > 0)) {
          setFetching(false);
        }

        if (job.status === 'done' && job.packs && job.packs.length > 0) {
          setPacks(job.packs);
          setConverting(false);
          clearInterval(intervalId);
          pollIntervalRef.current = null;
        } else if (job.status === 'failed') {
          setFetching(false);
          setConverting(false);
          showToast(job.error || 'Operation failed', 'error');
          clearInterval(intervalId);
          pollIntervalRef.current = null;
        }
      } catch (err: any) {
        clearInterval(intervalId);
        pollIntervalRef.current = null;
        setFetching(false);
        setConverting(false);
        showToast(err.message, 'error');
      }
    }, 700);

    pollIntervalRef.current = intervalId;
  };

  const handleFetch = async () => {
    if (!packLink.trim()) {
      showToast('Please enter a Telegram pack link.', 'error');
      return;
    }
    setSavedToken(botToken, rememberToken);
    setFetching(true);
    setStickers([]);
    setPacks([]);
    setJobState({
      phase: 'Connecting',
      message: 'Contacting Telegram API…',
      done: 0,
      total: 0,
    });

    try {
      const res = await api.startFetch(packLink.trim(), botToken.trim());
      setCurrentJobId(res.id);
      startPolling(res.id);
    } catch (err: any) {
      setFetching(false);
      if (err.status === 409) {
        showToast('A job is already running. Please wait for it to finish.', 'warn');
      } else {
        showToast(err.message, 'error');
      }
    }
  };

  const handleConvertSelected = async (selectedIndices: number[]) => {
    if (!currentJobId) return;
    setConverting(true);
    setPacks([]);
    setJobState((prev) => ({
      ...prev,
      phase: 'Starting',
      message: `Converting ${selectedIndices.length} stickers on Modal cloud…`,
    }));

    try {
      await api.startConvert(currentJobId, 'selected', selectedIndices);
      startPolling(currentJobId);
    } catch (err: any) {
      setConverting(false);
      if (err.status === 409) {
        showToast('A job is already running. Please wait for it to finish.', 'warn');
      } else {
        showToast(err.message, 'error');
      }
    }
  };

  const handleConvertFull = async () => {
    if (!currentJobId) return;
    setConverting(true);
    setPacks([]);
    setJobState((prev) => ({
      ...prev,
      phase: 'Starting',
      message: 'Converting full pack on Modal cloud…',
    }));

    try {
      await api.startConvert(currentJobId, 'full');
      startPolling(currentJobId);
    } catch (err: any) {
      setConverting(false);
      if (err.status === 409) {
        showToast('A job is already running. Please wait for it to finish.', 'warn');
      } else {
        showToast(err.message, 'error');
      }
    }
  };

  // Immediate toggle for adult previews — updates both state and localStorage
  const handleToggleAdultPreviews = useCallback((show: boolean) => {
    setAppSettingsState((prev) => {
      const updated = { ...prev, showAdultPreviews: show };
      setAppSettings(updated);
      return updated;
    });
  }, []);

  // ── Fullyst Catalog → View Pack ──
  // Opens PackModal dialog OVER the catalog (does NOT navigate away, matching webapp.py)
  const handleImportFullyst = async (slug: string, title?: string, isAdult?: boolean) => {
    setPackModalTitle(title || slug);
    setPackModalAdult(Boolean(isAdult));
    setPackModalOpen(true);
    setFetching(true);
    setStickers([]);
    setPacks([]);
    setJobState({
      phase: 'Starting',
      message: `Importing ${title || slug} from Fullyst catalog…`,
      done: 0,
      total: 0,
      isAdult: Boolean(isAdult),
      source: 'Fullyst',
      sourceSlug: slug,
    });

    try {
      const res = await api.startFullystFetch(slug, Boolean(isAdult));
      setCurrentJobId(res.id);
      startPolling(res.id);
    } catch (err: any) {
      setFetching(false);
      // Surface the failure inside the open pack modal (matching webapp.py setPackModalStatus)
      setJobState({
        phase: 'Could not start',
        message: err.message || 'The pack could not be started.',
        done: 0,
        total: 0,
        isFailed: true,
        source: 'Fullyst',
        sourceSlug: slug,
        isAdult: Boolean(isAdult),
      });
      if (err.status === 409) {
        showToast('A job is already running. Please wait for it to finish.', 'warn');
      } else {
        showToast(err.message, 'error');
      }
    }
  };

  const handleSaveFavorite = async (sticker: Sticker) => {
    if (!currentJobId) return;
    try {
      await api.saveToCollection(currentJobId, sticker.index);
      showToast('Saved to My Favorites!', 'success');
      refreshCollection();
    } catch (err: any) {
      showToast(err.message, 'error');
    }
  };

  const handleReset = () => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
    setFetching(false);
    setConverting(false);
    setStickers([]);
    setPacks([]);
    setCurrentJobId(null);
    setPackModalOpen(false);
    setPackModalAdult(false);
    setPackModalTitle('');
    setJobState({
      phase: 'Ready',
      message: 'Ready to fetch your sticker pack.',
      done: 0,
      total: 0,
    });
  };

  const handleConvertCollection = async (selectedIds: string[], title: string) => {
    setActiveTab('telegram');
    setConverting(true);
    setPacks([]);
    setStickers([]);
    setJobState({
      phase: 'Starting',
      message: `Building WhatsApp pack "${title}" from favorites…`,
      done: 0,
      total: selectedIds.length,
    });

    try {
      const res = await api.convertCollection(selectedIds, title);
      setCurrentJobId(res.id);
      startPolling(res.id);
    } catch (err: any) {
      setConverting(false);
      showToast(err.message, 'error');
    }
  };

  const handleSettingsSaved = useCallback(() => {
    refreshHealth();
    refreshSettings();
  }, [refreshSettings]);

  return (
    <main className="max-w-6xl mx-auto px-4 py-8 md:py-12">
      {/* Header */}
      <Header
        backendStatus={backendStatus}
        collectionCount={collectionItems.length}
        onOpenSettings={() => setIsSettingsOpen(true)}
        onOpenCollection={() => setIsCollectionOpen(true)}
      />

      {/* Hero */}
      <section className="mb-7">
        <span className="inline-flex items-center gap-1.5 text-[11px] font-extrabold uppercase tracking-widest text-mint mb-2">
          ✨ Next.js Frontend • Modal.com Engine
        </span>
        <h2 className="text-3xl sm:text-4xl font-extrabold tracking-tight text-white mb-2">
          Convert Telegram Stickers to WhatsApp
        </h2>
        <p className="text-muted text-sm max-w-2xl">
          Transform any Telegram sticker pack (Static WebP, Animated TGS, or Video WebM) into ready-to-import WhatsApp <code>.wastickers</code> packs in seconds.
        </p>
      </section>

      {/* Backend Unconfigured Banner */}
      {backendStatus.status === 'unconfigured' && (
        <div className="mb-6 p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3 text-xs text-amber-200">
            <AlertTriangle className="w-5 h-5 text-amber-400 flex-shrink-0" />
            <span>Connect your Modal.com backend to fetch &amp; convert sticker packs. Catalog browsing works without it.</span>
          </div>
          <button
            onClick={() => setIsSettingsOpen(true)}
            className="px-3.5 py-1.5 rounded-lg bg-amber-500 hover:bg-amber-400 text-black text-xs font-bold transition"
          >
            Configure Modal URL
          </button>
        </div>
      )}

      {/* Tab Switcher — Catalog Explorer is first (default) */}
      <div className="flex items-center gap-2 p-1 rounded-xl bg-panel border border-line w-fit mb-6">
        <button
          onClick={() => setActiveTab('fullyst')}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition ${
            activeTab === 'fullyst'
              ? 'bg-panel-elevated text-white shadow-sm'
              : 'text-muted hover:text-white'
          }`}
        >
          <Globe className="w-3.5 h-3.5 text-accent-telegram" />
          <span>Catalog Explorer</span>
        </button>

        <button
          onClick={() => setActiveTab('telegram')}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition ${
            activeTab === 'telegram'
              ? 'bg-panel-elevated text-white shadow-sm'
              : 'text-muted hover:text-white'
          }`}
        >
          <Send className="w-3.5 h-3.5 text-mint" />
          <span>Telegram Pack</span>
        </button>
      </div>

      {/* Tab 1 (default): Fullyst Catalog Explorer */}
      {activeTab === 'fullyst' && (
        <section className="p-6 rounded-2xl bg-panel border border-line shadow-xl">
          <FullystExplorer
            onImportPack={handleImportFullyst}
            onToast={showToast}
            showAdultPreviews={appSettings.showAdultPreviews}
            hideAdultContent={appSettings.hideAdultContent}
            onToggleAdultPreviews={handleToggleAdultPreviews}
          />
        </section>
      )}

      {/* Tab 2: Telegram Pack Fetch */}
      {activeTab === 'telegram' && (
        <div className="space-y-6">
          <FetchSection
            packLink={packLink}
            setPackLink={setPackLink}
            botToken={botToken}
            setBotToken={setBotToken}
            rememberToken={rememberToken}
            setRememberToken={setRememberToken}
            onFetch={handleFetch}
            onReset={handleReset}
            onOpenFullyst={() => setActiveTab('fullyst')}
            loading={fetching}
            jobState={jobState}
            showAdultPreviews={appSettings.showAdultPreviews}
            onToggleAdultPreviews={handleToggleAdultPreviews}
          />

          {/* Results: If conversion completed */}
          {packs.length > 0 && currentJobId && (
            <ResultsSection packs={packs} jobId={currentJobId} />
          )}

          {/* Sticker Grid Selection: If stickers ready */}
          {stickers.length > 0 && currentJobId && (
            <StickerGrid
              stickers={stickers}
              jobId={currentJobId}
              packTitle={packTitle}
              onPreview={(s) => setPreviewSticker(s)}
              onSaveFavorite={handleSaveFavorite}
              onConvertSelected={handleConvertSelected}
              onConvertFull={handleConvertFull}
              converting={converting}
              source={jobState.source}
              isAdult={jobState.isAdult}
              showAdultPreviews={appSettings.showAdultPreviews}
              onToggleAdultPreviews={handleToggleAdultPreviews}
              conversionProgress={{ done: jobState.done, total: jobState.total }}
            />
          )}
        </div>
      )}

      {/* ── Modals ── */}

      {/* Pack Modal Dialog — opens OVER catalog like webapp.py #packModal */}
      <PackModal
        isOpen={packModalOpen}
        onClose={closePackModal}
        title={packTitle || packModalTitle}
        isAdult={packModalAdult || Boolean(jobState.isAdult)}
        showAdultPreviews={appSettings.showAdultPreviews}
        onToggleAdultPreviews={handleToggleAdultPreviews}
        loading={fetching}
        jobState={jobState}
        jobId={currentJobId}
        stickers={stickers}
        packs={packs}
        converting={converting}
        onPreview={(s) => setPreviewSticker(s)}
        onSaveFavorite={handleSaveFavorite}
        onConvertSelected={handleConvertSelected}
        onConvertFull={handleConvertFull}
      />

      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={closeSettingsModal}
        onSaved={handleSettingsSaved}
        onToast={showToast}
      />

      <PreviewModal
        sticker={previewSticker}
        jobId={currentJobId}
        isOpen={!!previewSticker}
        isAdult={previewSticker?.isAdult || jobState.isAdult}
        showAdultPreviews={appSettings.showAdultPreviews}
        onClose={closePreviewModal}
        onSaveFavorite={handleSaveFavorite}
        onToggleAdultPreviews={handleToggleAdultPreviews}
      />

      <CollectionModal
        isOpen={isCollectionOpen}
        onClose={closeCollectionModal}
        items={collectionItems}
        groups={collectionGroups}
        onRefresh={refreshCollection}
        onConvert={handleConvertCollection}
        onToast={showToast}
      />

      {/* Toast */}
      {toast.show && (
        <div
          className={`fixed bottom-6 right-6 z-[1400] px-4 py-2.5 rounded-xl border text-xs font-semibold shadow-2xl flex items-center gap-2 animate-in fade-in slide-in-from-bottom-2 ${
            toast.type === 'error'
              ? 'bg-red-950 border-red-500/40 text-red-200'
              : toast.type === 'success'
              ? 'bg-emerald-950 border-emerald-500/40 text-emerald-200'
              : toast.type === 'warn'
              ? 'bg-amber-950 border-amber-500/40 text-amber-200'
              : 'bg-panel-elevated border-line-strong text-white'
          }`}
        >
          <span>{toast.message}</span>
        </div>
      )}
    </main>
  );
}
