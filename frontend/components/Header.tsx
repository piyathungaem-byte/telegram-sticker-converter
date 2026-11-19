'use client';

import React from 'react';
import { Sparkles, FolderHeart, Settings, Wifi, WifiOff, Loader2 } from 'lucide-react';

interface HeaderProps {
  backendStatus: {
    status: 'checking' | 'connected' | 'disconnected' | 'unconfigured';
    latency?: number;
  };
  collectionCount: number;
  onOpenSettings: () => void;
  onOpenCollection: () => void;
}

export function Header({
  backendStatus,
  collectionCount,
  onOpenSettings,
  onOpenCollection,
}: HeaderProps) {
  return (
    <header className="flex items-center justify-between pb-6 mb-8 border-b border-line flex-wrap gap-4">
      {/* Brand */}
      <div className="flex items-center gap-3">
        <div className="w-11 h-11 rounded-2xl bg-gradient-to-br from-mint to-emerald-600 text-bg flex items-center justify-center font-extrabold shadow-lg shadow-mint/20">
          <Sparkles className="w-6 h-6 text-black" />
        </div>
        <div>
          <h1 className="text-lg font-extrabold tracking-tight text-white flex items-center gap-2">
            Sticker Studio
          </h1>
          <p className="text-xs text-muted font-medium">Telegram → WhatsApp Converter</p>
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2.5">
        {/* Backend Status Pill */}
        <button
          onClick={onOpenSettings}
          className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-panel-elevated hover:bg-[#253337] border border-line hover:border-line-strong transition text-xs font-semibold text-muted hover:text-white"
          title="Click to configure Modal backend URL"
        >
          {backendStatus.status === 'checking' && (
            <>
              <Loader2 className="w-3.5 h-3.5 text-warn animate-spin" />
              <span>Modal: Checking…</span>
            </>
          )}
          {backendStatus.status === 'connected' && (
            <>
              <span className="w-2 h-2 rounded-full bg-mint shadow-[0_0_8px_#38d69d]" />
              <span className="text-white">Modal: Online ({Math.round(backendStatus.latency ?? 0)}ms)</span>
            </>
          )}
          {backendStatus.status === 'disconnected' && (
            <>
              <WifiOff className="w-3.5 h-3.5 text-bad" />
              <span className="text-bad">Modal: Offline</span>
            </>
          )}
          {backendStatus.status === 'unconfigured' && (
            <>
              <span className="w-2 h-2 rounded-full bg-warn" />
              <span className="text-warn">Modal: Not Configured</span>
            </>
          )}
        </button>

        {/* My Saves Shortcut */}
        <button
          onClick={onOpenCollection}
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-panel hover:bg-panel-elevated border border-line hover:border-line-strong text-xs font-semibold text-muted hover:text-white transition"
        >
          <FolderHeart className="w-3.5 h-3.5 text-mint" />
          <span>My Saves</span>
          <span className="px-1.5 py-0.2 rounded-full bg-mint/20 text-mint text-[10px] font-bold">
            {collectionCount}
          </span>
        </button>

        {/* Settings Button */}
        <button
          onClick={onOpenSettings}
          className="p-2 rounded-lg bg-panel hover:bg-panel-elevated border border-line hover:border-line-strong text-muted hover:text-white transition"
          title="Backend Settings"
        >
          <Settings className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
}
