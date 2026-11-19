'use client';

import React, { useState, useEffect } from 'react';
import { X, FolderHeart, Plus, Trash2, CheckCircle2 } from 'lucide-react';
import { CollectionGroup, CollectionItem } from '../lib/types';
import { api } from '../lib/api';

interface CollectionModalProps {
  isOpen: boolean;
  onClose: () => void;
  items: CollectionItem[];
  groups: CollectionGroup[];
  onRefresh: () => void;
  onConvert: (selectedIds: string[], packTitle: string) => void;
  onToast: (msg: string, type: 'info' | 'success' | 'error') => void;
}

export function CollectionModal({
  isOpen,
  onClose,
  items,
  groups,
  onRefresh,
  onConvert,
  onToast,
}: CollectionModalProps) {
  const [activeGroup, setActiveGroup] = useState<string>('all');
  const [savedFilter, setSavedFilter] = useState<'all' | 'static' | 'animated'>('all');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [packTitle, setPackTitle] = useState('My Favorites Mix');
  const [newGroupName, setNewGroupName] = useState('');
  // Undo Delete/Move: stores { message, undoToken } when remove/move succeeds
  const [undoBanner, setUndoBanner] = useState<{ message: string; undoToken: string } | null>(null);
  // Move to Group: tracks which group the user wants to move selected stickers into
  const [moveTargetGroupId, setMoveTargetGroupId] = useState<string>('');
  // Brief inline success message shown after a successful move
  const [moveSuccessMsg, setMoveSuccessMsg] = useState<string>('');

  // Reset selections every time the modal is opened/closed so stale picks don't carry over
  useEffect(() => {
    if (isOpen) {
      setSelectedIds(new Set());
      setActiveGroup('all');
      setSavedFilter('all');
      setUndoBanner(null);
      setMoveSuccessMsg('');
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.removeProperty('overflow');
      };
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const filtered = items.filter((item) => {
    if (activeGroup !== 'all' && (item.groupId || 'ungrouped') !== activeGroup) return false;
    if (savedFilter === 'animated') return item.kind === 'Animated';
    if (savedFilter === 'static') return item.kind !== 'Animated';
    return true;
  });

  const staticCount = items.filter((i) => i.kind !== 'Animated').length;
  const animatedCount = items.length - staticCount;

  // Type-aware selection stats — WhatsApp cannot mix types in one pack
  const selectedItems = items.filter((i) => selectedIds.has(i.id));
  const selectedAnimated = selectedItems.filter((i) => i.kind === 'Animated').length;
  const selectedStatic = selectedItems.length - selectedAnimated;
  const hasMixedSelection = selectedAnimated > 0 && selectedStatic > 0;
  const canCreatePack = Math.max(selectedAnimated, selectedStatic) >= 3;

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleCreateGroup = async () => {
    if (!newGroupName.trim()) return;
    try {
      await api.createCollectionGroup(newGroupName.trim());
      setNewGroupName('');
      onToast(`Group "${newGroupName}" created!`, 'success');
      onRefresh();
    } catch (err: any) {
      onToast(err.message, 'error');
    }
  };

  const handleRemove = async (id: string) => {
    try {
      const result = await api.removeFromCollection(id);
      setSelectedIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      // Undo Delete: show inline banner with undo token from backend
      if (result?.undoToken) {
        setUndoBanner({ undoToken: result.undoToken, message: 'Sticker removed.' });
      } else {
        onToast('Sticker removed from favorites.', 'info');
      }
      onRefresh();
    } catch (err: any) {
      onToast(err.message, 'error');
    }
  };

  // Undo: call backend undo endpoint, then refresh
  const handleUndo = async () => {
    if (!undoBanner) return;
    try {
      await api.undoCollectionAction(undoBanner.undoToken);
      setUndoBanner(null);
      onToast('Undone!', 'success');
      onRefresh();
    } catch (err: any) {
      onToast(err.message, 'error');
    }
  };

  // Move Stickers to Group
  const handleMoveToGroup = async () => {
    if (selectedIds.size === 0 || !moveTargetGroupId) return;
    try {
      const result = await api.moveCollectionStickers(Array.from(selectedIds), moveTargetGroupId);
      const targetGroup = groups.find((g) => g.id === moveTargetGroupId);
      setSelectedIds(new Set());
      setMoveSuccessMsg(`Moved to "${targetGroup?.name ?? 'group'}" ✓`);
      setTimeout(() => setMoveSuccessMsg(''), 3000);
      // Backend records the move as undoable — offer the same undo banner as deletes
      if (result?.undoToken) {
        setUndoBanner({ undoToken: result.undoToken, message: 'Stickers moved.' });
      }
      onRefresh();
    } catch (err: any) {
      onToast(err.message, 'error');
    }
  };

  // Feature 3 — Delete Group
  const handleDeleteGroup = async (groupId: string) => {
    try {
      await api.deleteCollectionGroup(groupId);
      onToast('Group deleted.', 'info');
      if (activeGroup === groupId) setActiveGroup('all');
      onRefresh();
    } catch (err: any) {
      onToast(err.message, 'error');
    }
  };

  const handleCreatePack = () => {
    if (selectedIds.size < 3 || hasMixedSelection || !canCreatePack) return;
    onConvert(Array.from(selectedIds), packTitle.trim() || 'My Favorites Mix');
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-[1100] bg-black/80 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-panel border border-line-strong rounded-2xl max-w-2xl w-full max-h-[90vh] flex flex-col overflow-hidden shadow-2xl animate-in fade-in zoom-in-95 duration-150"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-line">
          <div className="flex items-center gap-2.5">
            <FolderHeart className="w-5 h-5 text-mint" />
            <h3 className="font-bold text-base text-white">My Saved Stickers</h3>
          </div>
          <button onClick={onClose} className="p-1 rounded-lg text-muted hover:text-white hover:bg-panel-elevated">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 overflow-y-auto space-y-5 flex-1">
          {/* New Group & Subtitle */}
          <div className="flex items-center justify-between flex-wrap gap-3">
            <p className="text-xs text-muted">Mix favorite stickers from multiple packs into custom WhatsApp packs.</p>
            <div className="flex items-center gap-2">
              <input
                type="text"
                placeholder="New group name…"
                value={newGroupName}
                onChange={(e) => setNewGroupName(e.target.value)}
                className="bg-[#101618] border border-line rounded-lg px-2.5 py-1 text-xs text-white placeholder-dim outline-none w-36"
              />
              <button
                onClick={handleCreateGroup}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-panel-elevated hover:bg-[#233134] border border-line text-xs font-semibold text-white"
              >
                <Plus className="w-3.5 h-3.5 text-mint" />
                <span>Group</span>
              </button>
            </div>
          </div>

          {/* Group Chips */}
          <div className="flex items-center gap-1.5 flex-wrap">
            <button
              onClick={() => setActiveGroup('all')}
              className={`px-3 py-1 rounded-full text-xs font-semibold border transition ${
                activeGroup === 'all'
                  ? 'bg-mint/20 border-mint text-mint'
                  : 'bg-panel-elevated border-line text-muted hover:text-white'
              }`}
            >
              All ({items.length})
            </button>
            {groups.map((g) => {
              const count = items.filter((i) => (i.groupId || 'ungrouped') === g.id).length;
              return (
                // Feature 3 — Delete Group: chip is a flex row; × button calls handleDeleteGroup
                <span
                  key={g.id}
                  className={`inline-flex items-center gap-0.5 rounded-full border text-xs font-semibold transition ${
                    activeGroup === g.id
                      ? 'bg-mint/20 border-mint text-mint'
                      : 'bg-panel-elevated border-line text-muted hover:text-white'
                  }`}
                >
                  <button
                    onClick={() => setActiveGroup(g.id)}
                    className="pl-3 pr-1 py-1"
                  >
                    {g.name} ({count})
                  </button>
                  {g.id !== 'ungrouped' && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDeleteGroup(g.id);
                      }}
                      title={`Delete group "${g.name}"`}
                      className="pr-2 py-1 hover:text-bad"
                      aria-label={`Delete group "${g.name}"`}
                    >
                      ×
                    </button>
                  )}
                </span>
              );
            })}
          </div>

          {/* Undo: inline dismissible banner (deletes and moves) */}
          {undoBanner && (
            <div className="flex items-center justify-between gap-3 px-3 py-2 rounded-lg bg-[#1a2a24] border border-mint/40 text-xs text-mint">
              <span>{undoBanner.message}</span>
              <div className="flex items-center gap-2">
                <button
                  onClick={handleUndo}
                  className="font-bold underline hover:text-white"
                >
                  Undo
                </button>
                <button
                  onClick={() => setUndoBanner(null)}
                  className="p-0.5 hover:text-white"
                  title="Dismiss"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}

          {/* Type filter tabs — WhatsApp packs can't mix types */}
          <div className="flex items-center gap-1.5 flex-wrap" role="group" aria-label="Filter saved stickers by type">
            {(
              [
                { id: 'all', label: 'All', count: items.length },
                { id: 'static', label: 'Static', count: staticCount },
                { id: 'animated', label: 'Animated', count: animatedCount },
              ] as const
            ).map((tab) => (
              <button
                key={tab.id}
                onClick={() => setSavedFilter(tab.id)}
                aria-pressed={savedFilter === tab.id}
                className={`px-3 py-1 rounded-full text-xs font-semibold border transition ${
                  savedFilter === tab.id
                    ? 'bg-mint/20 border-mint text-mint'
                    : 'bg-panel-elevated border-line text-muted hover:text-white'
                }`}
              >
                {tab.label} ({tab.count})
              </button>
            ))}
          </div>

          {/* Stickers Grid */}
          {filtered.length === 0 ? (
            <div className="p-12 text-center border border-dashed border-line rounded-xl text-muted text-xs">
              {savedFilter === 'all' && activeGroup === 'all'
                ? 'No stickers saved yet. Click "Save to My Favorites" on any sticker preview to save them here.'
                : `No ${savedFilter === 'all' ? '' : savedFilter + ' '}stickers match this filter yet.`}
            </div>
          ) : (
            <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 gap-3 max-h-72 overflow-y-auto pr-1">
              {filtered.map((item) => {
                const isSelected = selectedIds.has(item.id);
                const thumbUrl = api.getCollectionThumbUrl(item.id);
                return (
                  <div
                    key={item.id}
                    onClick={() => toggleSelect(item.id)}
                    className={`relative p-2.5 rounded-xl border flex flex-col items-center gap-2 cursor-pointer transition select-none ${
                      isSelected
                        ? 'bg-mint/10 border-mint shadow-[0_0_0_1px_#38d69d]'
                        : 'bg-[#101618] border-line hover:border-line-strong'
                    }`}
                  >
                    <div className="w-16 h-16 rounded-lg bg-[#0a0e10] flex items-center justify-center p-1">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={thumbUrl} alt="Saved" className="max-w-full max-h-full object-contain" />
                    </div>

                    <div className="w-full flex items-center justify-between text-[10px]">
                      <span className="font-semibold text-muted">{item.kind}</span>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleRemove(item.id);
                        }}
                        className="p-1 rounded text-muted hover:text-bad"
                        title="Remove from favorites"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-line bg-[#101618] flex flex-col gap-3">
          {/* Feature 2 — Move Stickers to Group: shown when stickers are selected and groups exist */}
          {selectedIds.size > 0 && groups.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs text-muted">Move selected to group:</span>
              <select
                value={moveTargetGroupId}
                onChange={(e) => setMoveTargetGroupId(e.target.value)}
                className="bg-[#101618] border border-line rounded-lg px-2 py-1 text-xs text-white outline-none"
              >
                <option value="">— pick group —</option>
                {groups.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
              </select>
              <button
                onClick={handleMoveToGroup}
                disabled={!moveTargetGroupId}
                className="px-3 py-1 rounded-lg bg-panel-elevated border border-line text-xs font-semibold text-white hover:bg-[#233134] disabled:opacity-40 transition"
              >
                Move
              </button>
              {moveSuccessMsg && (
                <span className="text-xs font-semibold text-mint">{moveSuccessMsg}</span>
              )}
            </div>
          )}

          <div className="flex items-center justify-between flex-wrap gap-3">
            <input
              type="text"
              value={packTitle}
              onChange={(e) => setPackTitle(e.target.value)}
              placeholder="Custom Pack Name"
              className="bg-panel border border-line rounded-xl px-3 py-1.5 text-xs text-white outline-none w-52"
            />

            <button
              onClick={handleCreatePack}
              disabled={selectedIds.size < 3 || !canCreatePack || hasMixedSelection}
              title={
                hasMixedSelection
                  ? 'WhatsApp cannot mix static and animated stickers in one pack. Pick only one type.'
                  : !canCreatePack
                    ? `Need at least 3 ${selectedAnimated > 0 ? 'animated' : 'static'} stickers.`
                    : 'Build a WhatsApp pack from the selected favorites'
              }
              className="inline-flex items-center gap-2 px-5 py-2 rounded-xl bg-mint hover:bg-mint-hover text-black font-bold text-xs disabled:opacity-40 transition"
            >
              <CheckCircle2 className="w-4 h-4" />
              <span>Create Pack from Selected ({selectedIds.size})</span>
            </button>
          </div>

          {/* Type-mix guard hint */}
          {selectedIds.size >= 3 && hasMixedSelection && (
            <p className="text-[11px] text-warn font-semibold">
              Mixed selection — WhatsApp packs need all-static or all-animated stickers. You have {selectedStatic} static and {selectedAnimated} animated selected.
            </p>
          )}
          {selectedIds.size >= 1 && !hasMixedSelection && !canCreatePack && (
            <p className="text-[11px] text-muted">
              {selectedAnimated > 0
                ? `Select ${3 - selectedAnimated} more animated sticker${3 - selectedAnimated === 1 ? '' : 's'} to create a pack.`
                : `Select ${3 - selectedStatic} more static sticker${3 - selectedStatic === 1 ? '' : 's'} to create a pack.`}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
