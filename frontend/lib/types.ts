export interface Sticker {
  index: number;
  sourceIndex: number;
  formatLabel: string;
  kind: 'Animated' | 'Static';
  emoji: string;
  previewReady?: boolean;
  previewAvailable?: boolean;
  thumbUrl?: string;
  thumbData?: string;
  previewUrl?: string;
  largePreviewUrl?: string;
  isAdult?: boolean;
}

export interface JobActivity {
  seq: number;
  time: string;
  phase: string;
  message: string;
}

export interface PackFile {
  name: string;
  fileName: string;
  kind: string;
  count: number;
  sizeLabel: string;
  downloadUrl: string;
  previewUrls: string[];
}

export interface JobState {
  id: string;
  status: 'queued' | 'running' | 'ready' | 'done' | 'failed';
  phase: string;
  message: string;
  done: number;
  total: number;
  title?: string;
  source?: string;
  sourceSlug?: string;
  failedCount?: number;
  isAdult?: boolean;
  stickers?: Sticker[];
  packs?: PackFile[];
  activityLog?: JobActivity[];
  error?: string;
}

export interface CollectionItem {
  id: string;
  format: string;
  kind: 'Animated' | 'Static';
  emoji: string;
  name: string;
  groupId: string;
  sourceTitle: string;
  isAdult?: boolean;
}

export interface CollectionGroup {
  id: string;
  name: string;
}

export interface FullystPack {
  slug: string;
  title: string;
  count?: number;
  thumb?: string;
  kind?: string;
  sampleUrl?: string;
  isAdult?: boolean;
  isAnimated?: boolean;
}

export interface AppSettings {
  showAdultPreviews: boolean;
  hideAdultContent: boolean;
}
