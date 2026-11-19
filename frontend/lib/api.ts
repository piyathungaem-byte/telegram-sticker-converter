// Typed API Client for Telegram Sticker Converter (Next.js / Modal.com)
// Catalog search/browse runs via Next.js Route Handlers (no Modal needed).
// Heavy work (fetch, convert, collection) goes to Modal.com backend.
import { CollectionGroup, CollectionItem, FullystPack, JobState } from './types';

const STORAGE_KEY_API_URL = 'modal_api_url';
const STORAGE_KEY_TOKEN = 'tg_bot_token';
const STORAGE_KEY_CLIENT_ID = 'tsc_client_id';

/**
 * Per-browser identity. The Modal backend scopes jobs and the Fullyst
 * visit-history per browser via this id (X-Client-Id header, or the
 * ?client_id= query param for media URLs <img> tags can't decorate).
 * Generated lazily and persisted in localStorage.
 */
export function getClientId(): string {
  if (typeof window === 'undefined') return '';
  try {
    let id = window.localStorage.getItem(STORAGE_KEY_CLIENT_ID);
    if (!id || !/^[A-Za-z0-9-]{1,64}$/.test(id)) {
      id = (crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`).replace(/[^A-Za-z0-9-]/g, '');
      window.localStorage.setItem(STORAGE_KEY_CLIENT_ID, id);
    }
    return id;
  } catch {
    return '';
  }
}

/** Append the browser client id as a query param (for media <img>/<a> URLs). */
function withClientId(url: string): string {
  const cid = getClientId();
  if (!cid) return url;
  return url + (url.includes('?') ? '&' : '?') + `client_id=${encodeURIComponent(cid)}`;
}
export const DEFAULT_MODAL_URL = 'https://lahirulakshan129--telegram-sticker-converter-fastapi-app.us-east.modal.run';

export const STORAGE_KEY_SHOW_ADULT = 'show_adult_previews';
export const STORAGE_KEY_HIDE_ADULT = 'hide_adult_content';

export function getAppSettings(): { showAdultPreviews: boolean; hideAdultContent: boolean } {
  if (typeof window === 'undefined') {
    return { showAdultPreviews: false, hideAdultContent: false };
  }
  return {
    showAdultPreviews: localStorage.getItem(STORAGE_KEY_SHOW_ADULT) === 'true',
    hideAdultContent: localStorage.getItem(STORAGE_KEY_HIDE_ADULT) === 'true',
  };
}

export function setAppSettings(settings: { showAdultPreviews: boolean; hideAdultContent: boolean }): void {
  if (typeof window === 'undefined') return;
  localStorage.setItem(STORAGE_KEY_SHOW_ADULT, String(settings.showAdultPreviews));
  localStorage.setItem(STORAGE_KEY_HIDE_ADULT, String(settings.hideAdultContent));
}

export function getBackendUrl(): string {
  if (typeof window !== 'undefined') {
    const custom = localStorage.getItem(STORAGE_KEY_API_URL);
    if (custom && custom.trim()) {
      return custom.trim().replace(/\/+$/, '');
    }
  }

  const envUrl = process.env.NEXT_PUBLIC_MODAL_API_URL;
  if (envUrl && envUrl.trim()) {
    return envUrl.trim().replace(/\/+$/, '');
  }

  return DEFAULT_MODAL_URL;
}

export function setBackendUrl(url: string): string {
  const clean = (url || '').trim().replace(/\/+$/, '');
  if (typeof window !== 'undefined') {
    if (clean) {
      localStorage.setItem(STORAGE_KEY_API_URL, clean);
    } else {
      localStorage.removeItem(STORAGE_KEY_API_URL);
    }
  }
  return clean;
}

export function getSavedToken(): string {
  if (typeof window === 'undefined') return '';
  return localStorage.getItem(STORAGE_KEY_TOKEN) || '';
}

export function setSavedToken(token: string, remember: boolean = true): void {
  if (typeof window === 'undefined') return;
  if (remember && token && token.trim()) {
    localStorage.setItem(STORAGE_KEY_TOKEN, token.trim());
  } else if (!remember) {
    localStorage.removeItem(STORAGE_KEY_TOKEN);
  }
}

export function clearSavedToken(): void {
  if (typeof window === 'undefined') return;
  localStorage.removeItem(STORAGE_KEY_TOKEN);
}

export function resolveUrl(path: string): string {
  if (!path) return '';
  if (path.startsWith('http://') || path.startsWith('https://') || path.startsWith('blob:') || path.startsWith('data:')) {
    return path;
  }
  const base = getBackendUrl();
  const normalizedPath = '/' + path.replace(/^\/+/, '');
  return base ? `${base}${normalizedPath}` : normalizedPath;
}

export async function checkBackendHealth(urlToTest?: string): Promise<{
  ok: boolean;
  latency?: number;
  status?: number;
  data?: any;
  error?: string;
}> {
  const base = (urlToTest || getBackendUrl()).replace(/\/+$/, '');
  if (!base) {
    return { ok: false, error: 'No backend URL configured.' };
  }
  const start = performance.now();
  try {
    const res = await fetch(`${base}/api/config`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    const latency = Math.round(performance.now() - start);
    if (!res.ok) {
      return { ok: false, status: res.status, latency, error: `HTTP ${res.status}` };
    }
    const data = await res.json();
    return { ok: true, latency, data };
  } catch (err: any) {
    const latency = Math.round(performance.now() - start);
    return { ok: false, latency, error: err.message || 'Network connection failed' };
  }
}

export async function apiFetch<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const url = resolveUrl(path);
  const cid = getClientId();
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(cid ? { 'X-Client-Id': cid } : {}),
    ...((options.headers as Record<string, string>) || {}),
  };

  // Set Content-Type for all non-FormData bodies (including pre-stringified JSON strings)
  if (options.body && !(options.body instanceof FormData)) {
    if (!headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }
    if (typeof options.body === 'object') {
      options.body = JSON.stringify(options.body);
    }
  }

  let response: Response;
  try {
    response = await fetch(url, { ...options, headers });
  } catch (err: any) {
    throw new Error(
      `Cannot connect to Modal backend (${url}). Check that your backend is deployed and CORS is active. (${err.message})`
    );
  }

  const contentType = response.headers.get('content-type') || '';
  let data: any;
  if (contentType.includes('application/json')) {
    try {
      data = await response.json();
    } catch {
      data = null;
    }
  } else {
    data = await response.text();
  }

  if (!response.ok) {
    const msg = data && data.detail ? data.detail : data && data.error ? data.error : `Request failed (${response.status})`;
    const error: any = new Error(msg);
    error.status = response.status;
    error.data = data;
    throw error;
  }

  return data as T;
}

export const api = {
  resolveUrl: (path: string) => resolveUrl(path),
  getConfig: () => apiFetch<{ status: string; platform: string; tokenConfigured: boolean }>('/api/config'),
  getLatest: () => apiFetch<{ id: string | null }>('/api/latest'),

  startFetch: (packLink: string, token: string) =>
    apiFetch<{ id: string }>('/api/fetch', {
      method: 'POST',
      body: JSON.stringify({ pack_link: packLink, token }),
    }),

  startConvert: (jobId: string, mode: 'full' | 'selected', selected: number[] = []) =>
    apiFetch<{ id: string }>('/api/convert', {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId, mode, selected }),
    }),

  getJob: (jobId: string) => apiFetch<JobState>(`/api/job/${encodeURIComponent(jobId)}`),

  getThumbUrl: (jobId: string, index: number) => withClientId(resolveUrl(`/api/job/${encodeURIComponent(jobId)}/thumb/${index}`)),
  getPreviewUrl: (jobId: string, index: number) => withClientId(resolveUrl(`/api/job/${encodeURIComponent(jobId)}/preview/${index}`)),
  getOutputUrl: (jobId: string, packIndex: number, stickerIndex: number) =>
    withClientId(resolveUrl(`/api/job/${encodeURIComponent(jobId)}/output/${packIndex}/${stickerIndex}`)),
  getDownloadUrl: (jobId: string, index: number) => withClientId(resolveUrl(`/download/${encodeURIComponent(jobId)}/${index}`)),

  // ── Fullyst Catalog Explorer (served by Next.js Route Handlers on Vercel) ──
  // These do NOT go through Modal.com — they run server-side on Vercel itself.
  searchCatalog: (query: string, page: number = 1) => {
    const params = new URLSearchParams({ page: String(page) });
    if (query) params.set('q', query);
    return fetch(`/api/catalog/search?${params.toString()}`, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    }).then((r) => {
      if (!r.ok) throw new Error(`Catalog search failed (HTTP ${r.status})`);
      return r.json() as Promise<{ packs: FullystPack[]; page: number; pageCount: number; hasMore: boolean }>;
    });
  },
  /** URL served by Vercel (/api/catalog/preview?slug=…), not Modal */
  getCatalogPreviewUrl: (slug: string) =>
    `/api/catalog/preview?slug=${encodeURIComponent(slug)}`,
  /** Real animated GIF rendered by Modal backend */
  getFullystAnimatedPreviewUrl: (slug: string) =>
    resolveUrl(`/api/fullyst/animated-preview?slug=${encodeURIComponent(slug)}`),

  // ── Fullyst Pack Import — heavy download+convert, stays on Modal backend ──
  startFullystFetch: (slug: string, isAdult: boolean = false) =>
    apiFetch<{ id: string }>('/api/fullyst/fetch', {
      method: 'POST',
      body: JSON.stringify({ slug, is_adult: isAdult }),
    }),

  markFullystPageVisited: (page: number) =>
    apiFetch('/api/fullyst/visited-pages', {
      method: 'POST',
      body: JSON.stringify({ page }),
    }),

  // Collection ("My Saves")
  getCollection: () =>
    apiFetch<{ items: CollectionItem[]; count: number; groups: CollectionGroup[] }>('/api/collection'),
  saveToCollection: (jobId: string, index: number) =>
    apiFetch('/api/collection/save', {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId, index }),
    }),
  removeFromCollection: (id: string) =>
    apiFetch('/api/collection/remove', {
      method: 'POST',
      body: JSON.stringify({ id }),
    }),
  createCollectionGroup: (name: string) =>
    apiFetch('/api/collection/groups/create', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),
  moveCollectionStickers: (ids: string[], groupId: string) =>
    apiFetch('/api/collection/groups/move', {
      method: 'POST',
      body: JSON.stringify({ ids, groupId }),
    }),
  deleteCollectionGroup: (groupId: string) =>
    apiFetch('/api/collection/groups/delete', {
      method: 'POST',
      body: JSON.stringify({ groupId }),
    }),
  undoCollectionAction: (undoToken: string) =>
    apiFetch('/api/collection/undo', {
      method: 'POST',
      body: JSON.stringify({ undoToken }),
    }),
  convertCollection: (selected: string[], title: string) =>
    apiFetch<{ id: string }>('/api/collection/convert', {
      method: 'POST',
      body: JSON.stringify({ selected, title }),
    }),
  getCollectionThumbUrl: (itemId: string) => resolveUrl(`/api/collection/thumb/${encodeURIComponent(itemId)}`),
  getCollectionPreviewUrl: (itemId: string) => resolveUrl(`/api/collection/preview/${encodeURIComponent(itemId)}`),
};
