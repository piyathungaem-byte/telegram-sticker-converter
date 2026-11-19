'use client';

import React, { useState, useEffect } from 'react';
import { X, Server, Zap, CheckCircle2, AlertCircle, Trash2, Eye, ShieldAlert, Sliders } from 'lucide-react';
import {
  getBackendUrl,
  setBackendUrl,
  checkBackendHealth,
  clearSavedToken,
  getAppSettings,
  setAppSettings,
} from '../lib/api';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
  onToast: (msg: string, type: 'info' | 'success' | 'error') => void;
}

export function SettingsModal({ isOpen, onClose, onSaved, onToast }: SettingsModalProps) {
  const [url, setUrl] = useState('');
  const [showAdult, setShowAdult] = useState(false);
  const [hideAdult, setHideAdult] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    tested: boolean;
    ok: boolean;
    latency?: number;
    error?: string;
  } | null>(null);

  useEffect(() => {
    if (isOpen) {
      setUrl(getBackendUrl());
      const settings = getAppSettings();
      setShowAdult(settings.showAdultPreviews);
      setHideAdult(settings.hideAdultContent);
      setTestResult(null);
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.removeProperty('overflow');
      };
    }
  }, [isOpen]);

  if (!isOpen) return null;

  // Immediate toggle for Show Adult Previews (matching webapp.py:992)
  const handleToggleShowAdult = (checked: boolean) => {
    setShowAdult(checked);
    const updated = { showAdultPreviews: checked, hideAdultContent: hideAdult && !checked };
    if (checked && hideAdult) setHideAdult(false);
    setAppSettings(updated);
    onSaved();
    onToast(
      checked ? 'Adult previews unblurred (18+ revealed)' : 'Adult previews blurred (18+ safe mode)',
      'info'
    );
  };

  // Immediate toggle for Hide Adult Content (matching webapp.py:998)
  const handleToggleHideAdult = (checked: boolean) => {
    setHideAdult(checked);
    const updatedShowAdult = checked ? false : showAdult;
    if (checked) setShowAdult(false);
    const updated = { showAdultPreviews: updatedShowAdult, hideAdultContent: checked };
    setAppSettings(updated);
    onSaved();
    onToast(
      checked ? 'Adult packs hidden from catalog' : 'Adult packs visible in catalog',
      'info'
    );
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    const res = await checkBackendHealth(url);
    setTesting(false);
    setTestResult({
      tested: true,
      ok: res.ok,
      latency: res.latency,
      error: res.error,
    });
  };

  const handleSaveBackendUrl = () => {
    setBackendUrl(url);
    onToast('Backend URL saved successfully!', 'success');
    onSaved();
  };

  const handleClearToken = () => {
    clearSavedToken();
    onToast('Saved Bot Token cleared from browser memory.', 'info');
  };

  return (
    <div
      className="fixed inset-0 z-[1300] bg-black/80 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-panel border border-line-strong rounded-2xl max-w-lg w-full max-h-[92vh] overflow-y-auto shadow-2xl animate-in fade-in zoom-in-95 duration-150"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-line sticky top-0 bg-panel z-10">
          <div className="flex items-center gap-2.5">
            <Sliders className="w-5 h-5 text-mint" />
            <div>
              <h3 className="font-bold text-base text-white">Settings</h3>
              <p className="text-[11px] text-muted">Control adult pack visibility, previews &amp; cloud backend.</p>
            </div>
          </div>
          <button onClick={onClose} className="p-1 rounded-lg text-muted hover:text-white hover:bg-panel-elevated">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="p-6 space-y-6">
          {/* Adult Content & 18+ Controls (from webapp.py lines 86-88) */}
          <div className="space-y-3">
            <h4 className="text-xs font-bold text-white uppercase tracking-wider text-muted">
              Content Visibility &amp; Safety
            </h4>

            {/* Toggle 1: Show Adult Previews */}
            <label className="flex items-start justify-between gap-4 p-3.5 rounded-xl bg-[#101618] border border-line hover:border-line-strong cursor-pointer transition select-none">
              <div className="space-y-0.5">
                <span className="text-xs font-bold text-white flex items-center gap-1.5">
                  <Eye className="w-3.5 h-3.5 text-mint" />
                  <span>Show adult previews (18+)</span>
                </span>
                <p className="text-[11px] text-muted leading-relaxed">
                  Reveal adult previews instantly instead of keeping them blurred.
                </p>
              </div>
              <input
                type="checkbox"
                checked={showAdult}
                disabled={hideAdult}
                onChange={(e) => handleToggleShowAdult(e.target.checked)}
                className="accent-mint w-4 h-4 rounded mt-0.5 cursor-pointer flex-shrink-0 disabled:opacity-40"
              />
            </label>

            {/* Toggle 2: Hide Adult Content */}
            <label className="flex items-start justify-between gap-4 p-3.5 rounded-xl bg-[#101618] border border-line hover:border-line-strong cursor-pointer transition select-none">
              <div className="space-y-0.5">
                <span className="text-xs font-bold text-white flex items-center gap-1.5">
                  <ShieldAlert className="w-3.5 h-3.5 text-amber-400" />
                  <span>Hide adult content</span>
                </span>
                <p className="text-[11px] text-muted leading-relaxed">
                  Hide adult-marked Fullyst catalog results and previews. Unmarked previews remain visible; adult labeling is best-effort.
                </p>
              </div>
              <input
                type="checkbox"
                checked={hideAdult}
                onChange={(e) => handleToggleHideAdult(e.target.checked)}
                className="accent-mint w-4 h-4 rounded mt-0.5 cursor-pointer flex-shrink-0"
              />
            </label>
            <p className="text-[10px] text-dim leading-relaxed px-1">
              Adult pack labeling is best-effort. Settings apply immediately; they affect on-screen previews and catalog visibility, but do not change sticker conversion.
            </p>
          </div>

          <hr className="border-line" />

          {/* Modal.com Backend Settings */}
          <div className="space-y-3">
            <h4 className="text-xs font-bold text-white uppercase tracking-wider text-muted flex items-center gap-1.5">
              <Server className="w-3.5 h-3.5 text-mint" />
              <span>Modal.com Cloud Backend</span>
            </h4>

            <div>
              <label className="block text-xs font-semibold text-muted mb-1.5">
                Backend Endpoint URL
              </label>
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://lahirulakshan129--telegram-sticker-converter-fastapi-app.us-east.modal.run"
                className="w-full bg-[#101618] border border-line hover:border-line-strong focus:border-mint focus:ring-1 focus:ring-mint rounded-xl px-3.5 py-2.5 text-xs text-white placeholder-dim outline-none transition font-mono"
              />
              <p className="text-[11px] text-muted mt-1.5">
                The public HTTPS endpoint URL of your deployed Modal container.
              </p>
            </div>

            {/* Test & Save buttons */}
            <div className="flex items-center gap-3">
              <button
                onClick={handleTest}
                disabled={testing || !url.trim()}
                className="inline-flex items-center gap-2 px-3.5 py-2 rounded-xl bg-panel-elevated hover:bg-[#253337] border border-line hover:border-line-strong text-xs font-semibold text-white disabled:opacity-40 transition"
              >
                <Zap className="w-3.5 h-3.5 text-mint" />
                <span>{testing ? 'Pinging…' : 'Test Connection'}</span>
              </button>

              <button
                onClick={handleSaveBackendUrl}
                className="px-5 py-2 rounded-xl bg-mint hover:bg-mint-hover text-black font-extrabold text-xs shadow-md shadow-mint/20 transition ml-auto"
              >
                Save Backend URL
              </button>
            </div>

            {/* Test Result Box */}
            {testResult && testResult.tested && (
              <div
                className={`p-3.5 rounded-xl border text-xs ${
                  testResult.ok
                    ? 'bg-mint/10 border-mint/30 text-mint'
                    : 'bg-red-500/10 border-red-500/30 text-red-300'
                }`}
              >
                <div className="flex items-center gap-2 font-bold mb-1">
                  {testResult.ok ? (
                    <>
                      <CheckCircle2 className="w-4 h-4 text-mint" />
                      <span>Connected to Modal Cloud! ({testResult.latency} ms)</span>
                    </>
                  ) : (
                    <>
                      <AlertCircle className="w-4 h-4 text-red-400" />
                      <span>Connection Failed</span>
                    </>
                  )}
                </div>
                <p className="text-[11px] opacity-80">
                  {testResult.ok
                    ? 'Your Modal backend is healthy and responding to requests.'
                    : `Error: ${testResult.error || 'Network unreachable'}. Make sure your Modal app is deployed.`}
                </p>
              </div>
            )}
          </div>

          <hr className="border-line" />

          {/* Bot Token Storage */}
          <div>
            <h4 className="text-xs font-semibold text-white mb-1">Saved Bot Token</h4>
            <p className="text-xs text-muted mb-3">
              Clear the cached Telegram Bot Token saved in this browser&apos;s localStorage.
            </p>
            <button
              onClick={handleClearToken}
              className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 hover:bg-red-500/20 text-xs font-semibold transition"
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span>Clear Saved Bot Token</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
