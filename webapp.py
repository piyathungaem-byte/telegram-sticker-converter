#!/usr/bin/env python3
"""Local browser UI for fetching, selecting, converting, and previewing sticker packs.

Run with `python webapp.py`. Uses only the standard library for the web server; conversion
uses the same local Python engine as convert.py. The Bot token is never sent to a hosted service.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

import convert

def _user_data_dir() -> Path:
    """Stable per-user data path, independent of where a new app copy is extracted."""
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return root / "StickerStudio"
    root = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return root / "sticker-studio"


APP_DIR = Path(__file__).resolve().parent
RUNS_DIR = APP_DIR / "webapp-runs"
CACHE_DIR = APP_DIR / "webapp-cache"
DATA_DIR = _user_data_dir()
LEGACY_COLLECTION_DIR = APP_DIR / "webapp-collection"
COLLECTION_DIR = DATA_DIR / "MySaves"
COLLECTION_ASSETS_DIR = COLLECTION_DIR / "assets"
COLLECTION_THUMBS_DIR = COLLECTION_DIR / "thumbs"
COLLECTION_PREVIEWS_DIR = COLLECTION_DIR / "previews"
COLLECTION_UNDO_DIR = COLLECTION_DIR / ".undo"
COLLECTION_INDEX = COLLECTION_DIR / "collection.json"
COLLECTION_UNDO_TTL_SECONDS = 10 * 60
MAX_COLLECTION_UNDO_ACTIONS = 32
MAX_SAVED_STICKERS = 1000
MAX_SAVED_GROUPS = 100
MAX_SAVED_GROUP_NAME = 40
UNGROUPED_GROUP_ID = "ungrouped"
COLLECTION_GROUP_ID_RE = re.compile(r"[0-9a-f]{32}")
COLLECTION_FORMATS = {"webp", "webm", "tgs", "png", "jpg", "jpeg"}
PREVIEW_CACHE_DIR = DATA_DIR / "preview-cache"
THUMBNAIL_CACHE_DIR = PREVIEW_CACHE_DIR / "thumbnails"
THUMBNAIL_CACHE_VERSION = 1
THUMBNAIL_CACHE_MAX_BYTES = 128 * 1024 * 1024
THUMBNAIL_CACHE_MAX_ITEM_BYTES = 4 * 1024 * 1024
MAX_PREVIEW_RENDER_WORKERS = 3
PREVIEW_RENDER_WORKERS = min(MAX_PREVIEW_RENDER_WORKERS, max(1, os.cpu_count() or 1))
PREVIEW_HTTP_CACHE_CONTROL = "private, max-age=300"
MAX_BODY = 64 * 1024
MAX_JOBS = 20
DEFAULT_WEBM_METHOD = 4
FULLYST_BASE = "https://fullyst.com"
FULLYST_STICKER_HOST = "stickers.fullyst.com"
FULLYST_PAGE_SIZE = 10
FULLYST_MAX_PACK_STICKERS = 500
FULLYST_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
FULLYST_RETRY_ATTEMPTS = 3
FULLYST_RETRY_DELAYS = (0.7, 1.5)
FULLYST_VISITED_PAGES_FILE = DATA_DIR / "fullyst-visited-pages.json"
FULLYST_MAX_CATALOG_PAGE = 1_000_000

STATE_LOCK = threading.RLock()
COLLECTION_LOCK = threading.RLock()
COLLECTION_MIGRATION_LOCK = threading.Lock()
FULLYST_VISITED_PAGES_LOCK = threading.RLock()
_COLLECTION_MIGRATED = False
ANIM_PREVIEW_LOCK = threading.Lock()
THUMBNAIL_RENDER_SEMAPHORE = threading.BoundedSemaphore(PREVIEW_RENDER_WORKERS)
_THUMBNAIL_CACHE_MAINTENANCE_LOCK = threading.Lock()
_THUMBNAIL_CACHE_LAST_MAINTENANCE = 0.0
FULLYST_PREVIEW_CACHE_LOCK = threading.Lock()
FULLYST_PREVIEW_CACHE: dict[str, bytes] = {}
FULLYST_PREVIEW_CACHE_MAX = 24
JOBS: dict[str, dict] = {}
COLLECTION_UNDO_ACTIONS: dict[str, dict] = {}
ACTIVE_JOB_ID: str | None = None
LATEST_JOB_ID: str | None = None
DEMO_MODE = False

PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sticker Studio · Telegram to WhatsApp</title>
<style>
:root{color-scheme:dark;--bg:#101416;--panel:#191f22;--line:#2b3738;--text:#eef4f2;--muted:#9aa9a5;--mint:#9ce8cb;--mint2:#5ac49a;--warn:#f1c879;--bad:#ff9b9b;--radius:18px}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(900px 500px at 85% -5%,#1b3730 0%,transparent 65%),var(--bg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--text)}
button,input{font:inherit}.shell{max-width:1120px;margin:0 auto;padding:28px 20px 60px}.top{display:flex;align-items:center;gap:12px;margin-bottom:32px}.top-actions{display:flex;align-items:center;gap:8px;margin-left:auto}.top-settings{min-height:38px;white-space:nowrap}.saved-count{display:inline-grid;place-items:center;min-width:19px;height:19px;margin-left:4px;padding:0 5px;border-radius:99px;background:#3a5147;color:#c4f3dc;font-size:10px;font-weight:800}.logo{width:42px;height:42px;border-radius:14px;background:var(--mint);color:#10221c;display:grid;place-items:center;font-size:22px;font-weight:800}.brand{font-weight:750}.brand small{display:block;color:var(--muted);font-weight:500;font-size:12px}.hero{max-width:760px;margin-bottom:22px}.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.15em;color:var(--mint);font-weight:750}.hero h1{font-size:clamp(30px,5vw,46px);line-height:1.08;letter-spacing:-.04em;margin:8px 0 10px}.hero p{color:var(--muted);font-size:15px;margin:0}.panel{background:linear-gradient(180deg,#1b2224,#171c1e);border:1px solid var(--line);border-radius:var(--radius);padding:20px;box-shadow:0 18px 50px #0002}.form-layout{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(260px,.8fr);gap:14px;align-items:start}.panel h2{font-size:17px;margin:0 0 13px}.field{margin:0 0 13px}.field label{display:block;color:#d8e2df;font-weight:650;font-size:13px;margin-bottom:6px}.field input{width:100%;border:1px solid #394648;background:#111719;color:var(--text);border-radius:10px;padding:11px 12px;outline:none}.field input:focus{border-color:var(--mint2);box-shadow:0 0 0 3px #5ac49a24}.field input::placeholder{color:#72817e}.secret-row{display:flex;gap:8px}.secret-row input{flex:1;min-width:0}.tiny,.soft-btn{border:1px solid #394648;background:#20282a;color:#dbe7e2;border-radius:9px;padding:0 11px;cursor:pointer}.hint{font-size:12px;color:var(--muted);margin:6px 0 0}.remember-row{display:flex;align-items:center;gap:9px;flex-wrap:wrap;color:#dce6e2;font-size:13px;margin:4px 0 14px}.remember-row input,.select-all input,.pick input{accent-color:var(--mint2)}.remember-note{color:var(--muted);font-size:11px;width:100%;padding-left:23px}.link-btn{border:0;background:none;color:#b7daca;text-decoration:underline;cursor:pointer;font-size:12px;padding:0}.cta{border:0;border-radius:10px;padding:12px 16px;background:var(--mint);color:#11241d;font-weight:800;cursor:pointer;transition:.15s}.cta:hover{background:#b7f4db;transform:translateY(-1px)}.cta:disabled{opacity:.45;cursor:not-allowed;transform:none}.cta.secondary{background:#263730;color:#c2f1dc;border:1px solid #426456}.cta.secondary:hover{background:#30473d}.fetch-btn{width:100%}.privacy{color:var(--muted);font-size:11px;margin:10px 2px 0}.adult-control{margin-top:9px;padding:10px;border:1px solid #5b4b31;background:#26231d;border-radius:10px}.adult-toggle-label{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:700;color:#f1d8a2;cursor:pointer}.adult-toggle-label input{accent-color:var(--warn);width:16px;height:16px}.adult-control p{font-size:10px;color:var(--muted);margin:5px 0 0}.adult-blurred{filter:blur(12px);transform:scale(.94)}.demo-banner{display:none;padding:11px 13px;border-radius:10px;background:#2b281d;border:1px solid #5a4f2f;color:#f4dca0;font-size:13px;margin:0 0 15px}.activity{min-height:205px}.status-top{display:flex;justify-content:space-between;gap:12px;align-items:center}.pill{font-size:10px;padding:4px 8px;border-radius:99px;background:#273430;color:var(--mint);font-weight:800;white-space:nowrap}.pill.fail{background:#3b2525;color:var(--bad)}.message{color:var(--muted);font-size:13px;margin:7px 0 13px;min-height:20px}.progress-track{height:9px;background:#0f1415;border-radius:99px;overflow:hidden;border:1px solid #2b3536}.progress-fill{height:100%;width:0;background:linear-gradient(90deg,#4bb88d,#a6edcf);border-radius:99px;transition:width .3s}.progress-meta{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;margin:7px 0 10px}.errorbox{display:none;padding:10px 12px;background:#321f20;border:1px solid #674041;border-radius:9px;color:#ffc1c1;font-size:12px;margin-top:12px;white-space:pre-wrap}.warnbox{display:none;padding:10px 12px;background:#29261e;border:1px solid #4a422b;border-radius:9px;color:var(--warn);font-size:12px;margin-top:12px}.live-log{margin-top:13px;border:1px solid #2b3738;border-radius:10px;background:#121718;overflow:hidden}.live-log summary{cursor:pointer;padding:9px 11px;color:#d5e3dd;font-size:12px;font-weight:700;display:flex;justify-content:space-between;gap:10px;list-style:none}.live-log summary::-webkit-details-marker{display:none}.live-log summary:after{content:'＋';color:var(--mint);margin-left:auto}.live-log[open] summary:after{content:'−'}.log-count{font-weight:500;color:var(--muted)}.log-entries{max-height:210px;overflow:auto;border-top:1px solid #2b3738;padding:8px 10px;font:11px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}.log-entry{display:grid;grid-template-columns:62px 96px minmax(0,1fr);gap:7px;padding:2px 0;color:#c3d0cb}.log-time{color:#71827d}.log-phase{color:var(--mint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.log-message{overflow-wrap:anywhere}.log-empty{color:var(--muted);font-family:system-ui,sans-serif}.selection-panel,.result-panel,.fullyst-panel{margin-top:18px}.fullyst-panel>p{color:var(--muted);font-size:12px;margin:0 0 12px}.fullyst-search{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.fullyst-search input{flex:1;min-width:150px;border:1px solid #394648;background:#111719;color:var(--text);border-radius:10px;padding:10px 12px;outline:none}.fullyst-search input:focus{border-color:var(--mint2)}.fullyst-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin-top:12px}.fullyst-card{display:grid;grid-template-columns:62px minmax(0,1fr);gap:10px;align-items:center;border:1px solid #334041;background:#121819;border-radius:12px;padding:9px;min-width:0}.fullyst-sample{width:62px;height:62px;object-fit:contain;border-radius:8px;background-color:#202729}.fullyst-card-body{min-width:0;display:flex;flex-direction:column;gap:4px}.fullyst-card h3{font-size:13px;line-height:1.3;margin:0;overflow-wrap:anywhere}.fullyst-card .meta{font-size:10px;color:var(--muted)}.fullyst-card .cta{font-size:11px;padding:7px 9px;margin-top:4px}.adult-badge{align-self:flex-start;font-size:9px;font-weight:800;color:#f1d8a2;border:1px solid #6a5736;background:#332b1e;border-radius:99px;padding:2px 6px}.fullyst-search-status{font-size:12px;color:var(--muted);min-height:18px}.fullyst-visit-status{font-size:10px;color:var(--muted);min-height:16px;margin:6px 0 0;text-align:center}.fullyst-visit-status.repeat{color:var(--warn)}.catalog-pager{display:flex;align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;margin-top:14px}.catalog-page-label{font-size:12px;color:var(--muted);min-width:90px;text-align:center}.catalog-pager .soft-btn{min-height:36px}.catalog-page-jump{display:flex;align-items:center;gap:5px}.catalog-page-jump label{font-size:10px;color:var(--muted)}.catalog-page-jump input{width:58px;min-height:36px;border:1px solid #394648;background:#111719;color:var(--text);border-radius:8px;padding:5px 7px}.preview-hidden-placeholder{display:grid;place-items:center;padding:5px;background:#202729;border:1px dashed #475555;border-radius:8px;color:#9aa9a5;text-align:center;font-size:10px;font-weight:700;line-height:1.25}.sticker-preview-hidden{aspect-ratio:1/1}.pack-preview-hidden{width:100%;aspect-ratio:1/1}.soft-btn:disabled{opacity:.45;cursor:not-allowed}.selection-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap}.selection-head p{color:var(--muted);font-size:12px;margin:4px 0 0}.select-all{font-size:12px;color:#d5e3dd;display:flex;gap:7px;align-items:center}.selection-controls{display:flex;align-items:center;gap:8px}.sticker-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:12px 0}.sticker-filters{display:flex;gap:6px;flex-wrap:wrap}.filter-btn{border:1px solid #394648;background:#20282a;color:#dbe7e2;border-radius:9px;padding:6px 11px;cursor:pointer;font-size:11px}.filter-btn.active{border-color:#426456;background:#263730;color:#c2f1dc}.filter-btn:hover{background:#2a3536}.selection-summary{color:var(--muted);font-size:12px;margin:12px 0}.selection-panel .selection-summary{position:sticky;top:0;z-index:15;padding:9px 12px;margin:8px -8px;background:#182020ed;backdrop-filter:blur(10px);border:1px solid #334041;border-radius:10px;box-shadow:0 6px 16px #0005;line-height:1.45}.sticker-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px}.sticker-card{position:relative;display:flex;flex-direction:column;gap:6px;background:#121819;border:1px solid #334041;border-radius:12px;padding:9px;cursor:pointer;min-width:0;transition:border-color .15s,background .15s}.sticker-card:has(input:checked){border-color:var(--mint2);background:#1b2924}.sticker-card input{position:absolute;top:10px;left:10px;z-index:2;width:17px;height:17px}.sticker-card-label{display:flex;flex-direction:column;gap:6px;cursor:pointer;min-width:0}.save-sticker-btn{border:1px solid #3d5e50;background:#1d2c26;color:#bff1d9;border-radius:8px;padding:6px 8px;font-size:10px;font-weight:750;cursor:pointer}.save-sticker-btn:hover{background:#273b32}.save-sticker-btn:disabled{opacity:.65;cursor:default}.sticker-thumb{width:100%;aspect-ratio:1/1;object-fit:contain;border-radius:8px;background-color:#202729;background-image:linear-gradient(45deg,#293334 25%,transparent 25%),linear-gradient(-45deg,#293334 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#293334 75%),linear-gradient(-45deg,transparent 75%,#293334 75%);background-size:16px 16px;background-position:0 0,0 8px,8px -8px,-8px 0}.sticker-info{display:flex;justify-content:space-between;gap:4px;align-items:center}.sticker-number{font-size:11px;color:#d7e2de;font-weight:700}.sticker-kind{font-size:9px;text-transform:uppercase;letter-spacing:.06em;color:var(--mint);font-weight:800}.sticker-emoji{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:15px}.actions button{flex:1;min-width:200px}.result-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:13px}.result-card{border:1px solid var(--line);background:#141a1c;border-radius:13px;padding:14px;min-width:0}.result-card h3{font-size:14px;margin:0}.result-meta{font-size:11px;color:var(--muted);margin:4px 0 10px}.pack-preview{display:grid;grid-template-columns:repeat(auto-fill,minmax(70px,1fr));gap:6px;margin-top:10px}.pack-preview img{width:100%;aspect-ratio:1/1;object-fit:contain;border-radius:8px;background-color:#202729;background-image:linear-gradient(45deg,#293334 25%,transparent 25%),linear-gradient(-45deg,#293334 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#293334 75%),linear-gradient(-45deg,transparent 75%,#293334 75%);background-size:16px 16px;background-position:0 0,0 8px,8px -8px,-8px 0}.download{display:inline-flex;text-decoration:none;align-items:center;justify-content:center;margin-top:8px;background:#263730;color:#b8f1d8;border:1px solid #3f6354;border-radius:9px;padding:8px 11px;font-size:12px;font-weight:750}.download:hover{background:#30473d}.footnote{color:#778682;font-size:11px;margin:17px 2px 0}.help{margin-top:15px;color:var(--muted);font-size:12px}.help summary{cursor:pointer;color:#d3dfda;font-weight:650}.help p{margin:8px 0}.preview-hint{color:var(--muted);font-size:11px;margin:-4px 0 10px}.hidden{display:none!important}
.pack-modal,.preview-modal{position:fixed;inset:0;display:grid;place-items:center;padding:12px;z-index:1000}.pack-modal.hidden,.preview-modal.hidden{display:none}.pack-modal-backdrop,.preview-modal-backdrop{position:absolute;inset:0;background:#050809dd;backdrop-filter:blur(5px)}.pack-dialog{position:relative;display:flex;flex-direction:column;width:min(1160px,96vw);max-height:94vh;background:#171d1f;border:1px solid #3b4949;border-radius:17px;box-shadow:0 25px 85px #000b;overflow:hidden}.pack-dialog-head{display:flex;align-items:center;justify-content:space-between;gap:15px;padding:14px 18px;border-bottom:1px solid var(--line)}.pack-dialog-head h2{margin:0;font-size:18px}.pack-dialog-head p{margin:3px 0 0;color:var(--muted);font-size:11px}.pack-dialog-content{overflow:auto;padding:14px}.pack-dialog-content .selection-panel,.pack-dialog-content .result-panel{margin-top:0;box-shadow:none}.pack-modal-status{padding:12px 18px 0}.pack-modal-status-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;color:#dce7e2;font-size:12px}.pack-modal-status-head p{color:var(--muted);margin:3px 0 0}.pack-modal-status-head span{color:var(--muted);font-size:11px}.skeleton-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(115px,1fr));gap:10px;margin-top:12px}.skeleton-card{padding:8px;border:1px solid #2d3a3a;background:#111718;border-radius:11px}.skeleton-sticker,.skeleton-line{background:linear-gradient(100deg,#1c2526 20%,#293536 40%,#1c2526 60%);background-size:220% 100%;animation:shimmer 1.35s ease-in-out infinite;border-radius:8px}.skeleton-sticker{aspect-ratio:1;margin-bottom:8px}.skeleton-line{height:8px;margin:6px 0}.skeleton-line.short{width:55%}@keyframes shimmer{to{background-position-x:-220%}}.preview-modal{z-index:1200}.preview-dialog{position:relative;display:flex;flex-direction:column;gap:10px;width:min(760px,94vw);max-height:94vh;overflow:auto;padding:18px;background:#171d1f;border:1px solid #3b4949;border-radius:16px;box-shadow:0 25px 85px #000b}.preview-close{align-self:flex-end}.preview-dialog h2{font-size:16px;margin:0}.preview-stage{display:grid;place-items:center;min-height:260px;max-height:72vh;overflow:hidden;background:#111718;border:1px solid #2c3838;border-radius:12px;padding:12px}.preview-stage img,.preview-stage video{display:block;max-width:100%;max-height:68vh;object-fit:contain;border-radius:8px}.preview-caption{color:var(--muted);font-size:12px;margin:0}
.settings-modal{position:fixed;inset:0;display:grid;place-items:center;padding:14px;z-index:1300}.settings-modal.hidden{display:none}.settings-backdrop{position:absolute;inset:0;background:#050809dd;backdrop-filter:blur(5px)}.settings-dialog{position:relative;width:min(520px,94vw);padding:18px;background:#171d1f;border:1px solid #3b4949;border-radius:16px;box-shadow:0 25px 85px #000b}.settings-dialog-head{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:8px}.settings-dialog-head h2{margin:0;font-size:18px}.settings-dialog-head p{margin:3px 0 0;color:var(--muted);font-size:11px}.setting-row{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 2px;border-top:1px solid var(--line);cursor:pointer}.setting-copy{display:flex;flex-direction:column;gap:3px}.setting-copy strong{font-size:13px}.setting-copy small{color:var(--muted);font-size:11px;line-height:1.45}.setting-row input{flex:0 0 auto;width:18px;height:18px;accent-color:var(--mint2)}.settings-note{color:var(--muted);font-size:10px;margin:8px 0 0;line-height:1.45}
.saved-collection-modal{position:fixed;inset:0;display:grid;place-items:center;padding:12px;z-index:1100}.saved-collection-modal.hidden{display:none}.saved-collection-backdrop{position:absolute;inset:0;background:#050809dd;backdrop-filter:blur(5px)}.saved-collection-dialog{position:relative;display:flex;flex-direction:column;width:min(1120px,96vw);max-height:94vh;overflow:auto;padding:18px;background:#171d1f;border:1px solid #3b4949;border-radius:17px;box-shadow:0 25px 85px #000b}.saved-collection-head{display:flex;align-items:flex-start;justify-content:space-between;gap:15px}.saved-collection-head h2{margin:0;font-size:18px}.saved-collection-head p{margin:4px 0 0;color:var(--muted);font-size:11px}.saved-collection-status{font-size:12px;color:var(--muted);margin:12px 0;min-height:18px}.saved-undo-notice{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin:8px 0 0;padding:8px 10px;border:1px solid #426456;background:#1d2c26;border-radius:10px;color:#c2f1dc;font-size:11px}.saved-undo-notice span{flex:1;min-width:140px}.saved-undo-notice .soft-btn{min-height:32px;border-color:#426456}.error-text{color:var(--bad)}.saved-collection-tools{position:sticky;top:0;z-index:12;margin:4px 0 12px;padding:9px 10px;border:1px solid #334041;border-radius:10px;background:#171f20ed;backdrop-filter:blur(10px);box-shadow:0 6px 16px #0005}.saved-group-tools{display:grid;gap:8px;margin:8px 0 12px;padding:10px;border:1px solid #334041;background:#121819;border-radius:10px}.saved-group-header{display:flex;align-items:center;justify-content:space-between;gap:8px}.saved-group-header strong{font-size:12px}.saved-group-header .soft-btn{min-height:34px}.saved-group-chips{display:flex;gap:6px;overflow-x:auto;padding:1px 0 4px;scrollbar-width:thin}.saved-group-chip-wrap{display:flex;align-items:stretch;flex:0 0 auto;overflow:hidden;border:1px solid #394648;border-radius:99px;background:#20282a}.saved-group-chip-wrap.active{border-color:#5ac49a;background:#263730}.saved-group-chip{border:0;background:transparent;color:#dbe7e2;padding:6px 10px;cursor:pointer;font-size:11px;white-space:nowrap}.saved-group-chip-wrap.active .saved-group-chip{color:#c2f1dc;font-weight:750}.saved-group-more{border:0;border-left:1px solid #394648;background:transparent;color:#c4d1cc;padding:0 8px;cursor:pointer;font-size:16px}.saved-group-more:hover,.saved-group-chip:hover{background:#2c3837}.new-saved-group-form{display:flex;align-items:center;gap:7px;flex-wrap:wrap}.new-saved-group-form input{flex:1;min-width:140px;border:1px solid #394648;background:#0e1415;color:var(--text);border-radius:9px;padding:8px 10px}.new-saved-group-form .soft-btn{min-height:34px}.saved-group-help{color:var(--muted);font-size:10px;margin:0}.saved-group-move-menu{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:8px;padding:8px;border:1px solid #334041;border-radius:9px;background:#121819}.saved-group-move-menu button{min-height:32px;border:1px solid #394648;background:#20282a;color:#dbe7e2;border-radius:8px;padding:5px 9px;cursor:pointer;font-size:11px}.saved-group-move-menu button:hover{border-color:#5ac49a;background:#263730;color:#c2f1dc}.saved-context-menu{position:fixed;z-index:1500;display:grid;gap:3px;min-width:190px;max-width:min(280px,90vw);max-height:70vh;overflow:auto;padding:6px;border:1px solid #3b4949;border-radius:11px;background:#171f20;box-shadow:0 14px 38px #000b}.saved-context-title{padding:6px 8px;color:var(--muted);font-size:10px;font-weight:750;text-transform:uppercase;letter-spacing:.08em}.saved-context-item{width:100%;border:0;border-radius:7px;background:transparent;color:#e4ece8;text-align:left;padding:8px 9px;cursor:pointer;font:inherit;font-size:12px}.saved-context-item:hover{background:#263730;color:#c2f1dc}.saved-context-item.danger{color:#ffb4b4}.saved-context-item.danger:hover{background:#3b2525;color:#ffc1c1}.saved-sticker-group{align-self:flex-start;display:inline-flex;border:1px solid #3d5e50;background:#1d2c26;color:#bff1d9;border-radius:99px;padding:2px 7px;font-size:9px;font-weight:750}.saved-filter-tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:9px}.saved-filter-tab{display:inline-flex;align-items:center;gap:6px;border:1px solid #394648;background:#20282a;color:#dbe7e2;border-radius:9px;padding:6px 11px;cursor:pointer;font-size:11px}.saved-filter-tab.active{border-color:#426456;background:#263730;color:#c2f1dc}.saved-filter-tab:disabled{opacity:.45;cursor:not-allowed}.saved-filter-tab span{font-size:10px;color:var(--muted)}.saved-collection-controls{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:4px 0 0}.saved-selection-summary{color:var(--muted);font-size:11px}.saved-sticker-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(145px,1fr));gap:10px}.saved-sticker-card{position:relative;display:flex;flex-direction:column;gap:7px;min-width:0;padding:9px;border:1px solid #334041;background:#121819;border-radius:12px}.saved-sticker-card:has(input:checked){border-color:var(--mint2);background:#1b2924}.saved-sticker-card .saved-pick{position:absolute;top:11px;left:11px;z-index:2;width:17px;height:17px;accent-color:var(--mint2)}.saved-sticker-main{display:flex;flex-direction:column;gap:6px;min-width:0;cursor:pointer}.saved-sticker-thumb{width:100%;aspect-ratio:1;object-fit:contain;border-radius:8px;background-color:#202729}.saved-sticker-title{font-size:11px;line-height:1.3;overflow-wrap:anywhere}.saved-sticker-meta{font-size:9px;color:var(--muted);line-height:1.4;overflow-wrap:anywhere}.remove-saved{min-height:30px;padding:4px 8px;font-size:10px}.saved-collection-empty{padding:20px;border:1px dashed #3c4a4a;border-radius:12px;text-align:center;color:var(--muted);font-size:12px}.saved-collection-create{margin-top:15px;padding:12px;border:1px solid #334041;background:#121819;border-radius:12px}.saved-collection-create>label{display:block;font-size:12px;font-weight:700;margin-bottom:6px}.saved-create-line{display:flex;gap:9px}.saved-create-line input{flex:1;min-width:0;border:1px solid #394648;background:#0e1415;color:var(--text);border-radius:9px;padding:10px 11px}.saved-create-line .cta{white-space:nowrap;padding:9px 12px;font-size:12px}.saved-collection-create p{color:var(--muted);font-size:10px;margin:7px 0 0}.saved-collection-flow{margin-top:14px}.saved-collection-flow .result-panel{margin-top:12px}
@media(prefers-reduced-motion:reduce){.skeleton-sticker,.skeleton-line{animation:none}}
@media(max-width:760px){.shell{padding:20px 13px 40px}.form-layout{grid-template-columns:1fr}.activity{min-height:170px}.sticker-grid{grid-template-columns:repeat(auto-fill,minmax(105px,1fr))}.panel{padding:16px}.hero{margin-bottom:16px}.top{margin-bottom:24px;flex-wrap:wrap}.top-actions{gap:5px}.top-actions .soft-btn{padding:0 8px;font-size:11px}.saved-collection-dialog{padding:14px}.saved-sticker-grid{grid-template-columns:repeat(auto-fill,minmax(112px,1fr))}.saved-create-line{flex-direction:column}.saved-create-line .cta{width:100%}.saved-collection-flow .result-grid{grid-template-columns:minmax(0,1fr)}}
.preview-loading{background-image:linear-gradient(100deg,#1c2526 20%,#293536 40%,#1c2526 60%)!important;background-size:220% 100%!important;animation:shimmer 1.35s ease-in-out infinite;color:transparent!important}.preview-failed{background-image:none!important;animation:none!important;color:transparent!important}.preview-stage.preview-stage-loading{background-image:linear-gradient(100deg,#1c2526 20%,#293536 40%,#1c2526 60%);background-size:220% 100%;animation:shimmer 1.35s ease-in-out infinite}.preview-stage.preview-stage-loading img{opacity:0}.preview-stage img{transition:opacity .16s ease}@media(prefers-reduced-motion:reduce){.preview-loading,.preview-stage.preview-stage-loading{animation:none;background-position:50% 50%}}
</style>
</head>
<body>
<div class="shell">
<header class="top"><div class="logo">✳</div><div class="brand">Sticker Studio<small>Telegram → WhatsApp · runs locally on your PC</small></div><div class="top-actions"><button id="openSavedCollection" class="soft-btn" type="button" aria-haspopup="dialog" aria-controls="savedCollectionModal">My saves <span id="savedCount" class="saved-count">0</span></button><button id="openSettings" class="soft-btn top-settings" type="button" aria-haspopup="dialog" aria-controls="settingsModal">⚙ Settings</button></div></header>
<section class="hero"><div class="eyebrow">Simple sticker conversion</div><h1>Pick the stickers you want.</h1><p>Fetch a pack, preview its stickers, select a subset or convert everything, then preview and download the WhatsApp pack.</p></section>
<div id="demoBanner" class="demo-banner">View-only preview. Run <b>python webapp.py</b> on your own PC to fetch and convert. Never enter a Bot token here.</div>
<div class="form-layout">
<form id="fetchForm" class="panel">
<h2>1 · Fetch Telegram pack</h2>
<div class="field"><label for="packLink">Telegram sticker-pack link</label><input id="packLink" type="url" placeholder="https://t.me/addstickers/YourPack" required autocomplete="url"></div>
<div class="field"><label for="botToken">Telegram Bot token</label><div class="secret-row"><input id="botToken" type="password" placeholder="Paste token (or use saved token)" autocomplete="off"><button class="tiny" id="toggleToken" type="button">Show</button></div><p class="hint" id="tokenHint">Needed to fetch stickers. It stays on this computer.</p></div>
<div class="remember-row"><label><input id="rememberToken" type="checkbox"> Remember token on this PC</label><button class="link-btn hidden" id="forgetToken" type="button">Forget saved token</button><span id="rememberNote" class="remember-note">When enabled, Windows encrypts it for this user account.</span></div>
<button id="fetchButton" class="cta fetch-btn" type="submit">Fetch sticker pack</button>
<p class="privacy">Your token is sent only from this local app to Telegram. It is never sent to Arena or another website.</p>
<button id="showFullyst" class="cta secondary fetch-btn" type="button" style="margin-top:10px">Browse Fullyst catalog · no Bot token</button>
</form>
<div id="activityHome"><section id="activityPanel" class="panel activity" aria-live="polite">
<div class="status-top"><h2 id="statusTitle">Ready when you are</h2><span id="statusPill" class="pill">READY</span></div>
<p id="statusMessage" class="message">Paste a pack link and token to begin.</p>
<div id="progressWrap" class="hidden"><div class="progress-track"><div id="progressFill" class="progress-fill"></div></div><div class="progress-meta"><span id="progressLabel">Preparing…</span><span id="progressCount"></span></div></div>
<div id="errorBox" class="errorbox"></div><div id="warnBox" class="warnbox"></div>
<details id="liveLogPanel" class="live-log"><summary><span>Live activity log</span><span id="liveLogCount" class="log-count">Waiting for a job</span></summary><div id="liveLogEntries" class="log-entries" role="log" aria-live="polite"><div class="log-empty">Fetch or convert a pack to see progress events.</div></div></details>
</section></div>
</div>
<section id="fullystBrowser" class="panel fullyst-panel hidden">
<div class="selection-head"><div><h2>Browse Fullyst catalog</h2><p>Search public sticker packs. Files are downloaded to this PC; no Bot token is needed for Fullyst. Unfiltered catalog pages you visit are remembered on this PC and repeats are flagged.</p></div><button id="hideFullyst" type="button" class="soft-btn">Close</button></div>
<form id="fullystSearchForm" class="fullyst-search"><input id="fullystQuery" type="search" maxlength="100" placeholder="Search packs, e.g. duck or cherry"><button id="fullystSearchButton" class="cta" type="submit">Search packs</button><button id="backToCatalog" class="soft-btn hidden" type="button">Back to catalog</button></form>
<p id="fullystSearchStatus" class="fullyst-search-status">Loading catalog…</p>
<div id="fullystGrid" class="fullyst-grid"></div>
<div id="fullystPager" class="catalog-pager hidden"><button id="fullystPrev" class="soft-btn" type="button" disabled>← Previous</button><span id="fullystPageLabel" class="catalog-page-label">Page 1</span><button id="fullystNext" class="soft-btn" type="button" disabled>Next →</button><div class="catalog-page-jump"><label for="fullystPageInput">Go to page</label><input id="fullystPageInput" type="number" min="1" value="1" inputmode="numeric" aria-label="Page number"><button id="fullystGoPage" class="soft-btn" type="button">Go</button></div></div>
<p id="fullystVisitStatus" class="fullyst-visit-status" role="status" aria-live="polite">Visited page history loads when you open the catalog.</p>
</section>
<div id="packsHome">
<section id="selectionPanel" class="panel selection-panel hidden">
<div class="selection-head"><div><h2>2 · Choose stickers</h2><p id="selectionTitle">Fetched stickers</p></div><div class="selection-controls"><button id="retryFailed" class="soft-btn hidden" type="button">Retry skipped stickers</button><label class="select-all" title="Select all stickers in this pack"><input id="selectAll" type="checkbox"> Select all</label><button id="clearSelection" class="soft-btn" type="button" disabled>Clear selection</button></div></div>
<p id="selectionSummary" class="selection-summary">0 selected. Select at least 3 of one type to make a pack.</p>
<p class="preview-hint">Previews appear as they finish. Use arrow keys to move, Space to select, or Shift-click to select a range. Hold a sticker only to enlarge it; use Save for later to collect favorites.</p>
<div class="sticker-toolbar"><div class="sticker-filters" role="group" aria-label="Filter stickers"><button class="filter-btn active" data-sticker-filter="all" type="button" aria-pressed="true">All</button><button class="filter-btn" data-sticker-filter="static" type="button" aria-pressed="false">Static</button><button class="filter-btn" data-sticker-filter="animated" type="button" aria-pressed="false">Animated</button></div></div>
<div id="stickerGrid" class="sticker-grid" role="group" aria-label="Sticker picker. Use arrow keys to move, Space to select, and Shift-click for a range."></div>
<div class="actions"><button id="createSelected" class="cta secondary" type="button" disabled>Create selected pack</button><button id="convertAll" class="cta" type="button">Convert full pack</button></div>
</section>
<section id="resultPanel" class="panel result-panel hidden">
<h2>3 · Preview and download</h2>
<p id="resultMessage" class="selection-summary"></p>
<p class="preview-hint">Scroll through the pack previews; hold one only to enlarge it.</p>
<div id="resultGrid" class="result-grid"></div>
</section>
</div>
<div id="packModal" class="pack-modal hidden">
<div id="packModalBackdrop" class="pack-modal-backdrop"></div>
<section class="pack-dialog" role="dialog" aria-modal="true" aria-labelledby="packModalTitle">
<header class="pack-dialog-head"><div><h2 id="packModalTitle">View pack</h2><p id="packModalSubtitle">Loading the selected sticker pack…</p></div><button id="closePackModal" class="soft-btn" type="button" aria-label="Close pack viewer">Close</button></header>
<div id="packModalStatus" class="pack-modal-status hidden" role="status" aria-live="polite">
<div class="pack-modal-status-head"><div><strong id="packModalPhase">Loading pack…</strong><p id="packModalMessage">Getting pack details and previews.</p></div><span id="packModalCount"></span></div>
<div id="packModalProgressWrap" class="hidden"><div class="progress-track"><div id="packModalProgressFill" class="progress-fill"></div></div></div>
<div id="packModalSkeleton" class="skeleton-grid"><div class="skeleton-card"><div class="skeleton-sticker"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div><div class="skeleton-card"><div class="skeleton-sticker"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div><div class="skeleton-card"><div class="skeleton-sticker"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div><div class="skeleton-card"><div class="skeleton-sticker"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div><div class="skeleton-card"><div class="skeleton-sticker"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div><div class="skeleton-card"><div class="skeleton-sticker"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div><div class="skeleton-card"><div class="skeleton-sticker"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div><div class="skeleton-card"><div class="skeleton-sticker"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div></div>
</div>
<div id="packModalContent" class="pack-dialog-content"></div>
</section>
</div>
<div id="stickerPreviewModal" class="preview-modal hidden">
<div id="stickerPreviewBackdrop" class="preview-modal-backdrop"></div>
<section class="preview-dialog" role="dialog" aria-modal="true" aria-labelledby="stickerPreviewTitle"><button id="closeStickerPreview" class="soft-btn preview-close" type="button">Close</button><h2 id="stickerPreviewTitle">Sticker preview</h2><div class="preview-stage"><img id="largeStickerImage" class="hidden" alt="Large sticker preview"><video id="largeStickerVideo" class="hidden" controls playsinline loop></video></div><p id="stickerPreviewCaption" class="preview-caption"></p></section>
</div>
<div id="settingsModal" class="settings-modal hidden">
<div id="settingsBackdrop" class="settings-backdrop"></div>
<section class="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settingsTitle">
<header class="settings-dialog-head"><div><h2 id="settingsTitle">Settings</h2><p>Control adult pack visibility and previews.</p></div><button id="closeSettings" class="soft-btn" type="button" aria-label="Close settings">Close</button></header>
<label class="setting-row"><span class="setting-copy"><strong>Show adult previews (18+)</strong><small>Reveal adult previews instead of keeping them blurred.</small></span><input id="adultToggle" type="checkbox"></label>
<label class="setting-row"><span class="setting-copy"><strong>Hide adult content</strong><small>Hide adult-marked Fullyst results and previews. Unmarked pack previews remain visible; adult labeling is best-effort. Imports remain allowed.</small></span><input id="hideAdultToggle" type="checkbox"></label>
<p class="settings-note">Adult pack labeling is best-effort. These settings only affect catalog visibility and on-screen previews; they do not change conversion.</p>
</section>
</div>
<div id="savedCollectionModal" class="saved-collection-modal hidden">
<div id="savedCollectionBackdrop" class="saved-collection-backdrop"></div>
<section class="saved-collection-dialog" role="dialog" aria-modal="true" aria-labelledby="savedCollectionTitle">
<header class="saved-collection-head"><div><h2 id="savedCollectionTitle">My saved stickers</h2><p>Files stay on this PC. On Windows, find them at <code>%LOCALAPPDATA%\<wbr>StickerStudio\<wbr>MySaves</code>. Keep favorites from different packs here.</p></div><button id="closeSavedCollection" class="soft-btn" type="button" aria-label="Close saved stickers">Close</button></header>
<p id="savedCollectionStatus" class="saved-collection-status" role="status" aria-live="polite">Loading your saved stickers…</p>
<div id="savedCollectionLoading" class="skeleton-grid"><div class="skeleton-card"><div class="skeleton-sticker"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div><div class="skeleton-card"><div class="skeleton-sticker"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div><div class="skeleton-card"><div class="skeleton-sticker"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div><div class="skeleton-card"><div class="skeleton-sticker"></div><div class="skeleton-line"></div><div class="skeleton-line short"></div></div></div>
<div id="savedGroupTools" class="saved-group-tools hidden"><div class="saved-group-header"><strong>Groups</strong><button id="openNewSavedGroup" class="soft-btn" type="button">＋ New group</button></div><div id="savedGroupChips" class="saved-group-chips" role="group" aria-label="Filter saved stickers by group"></div><form id="newSavedGroupForm" class="new-saved-group-form hidden"><input id="newSavedGroupName" type="text" maxlength="40" placeholder="Group name" aria-label="New saved-sticker group name"><button id="createSavedGroup" class="soft-btn" type="button">Create</button><button id="cancelNewSavedGroup" class="soft-btn" type="button">Cancel</button></form><p class="saved-group-help">New saves start in Ungrouped. Click a group to filter; select stickers and choose Move to…, or right-click a sticker/use ⋯ for quick actions.</p></div>
<div id="savedCollectionControls" class="saved-collection-tools hidden"><div class="saved-filter-tabs" role="group" aria-label="Filter saved stickers by type"><button class="saved-filter-tab active" data-saved-filter="all" type="button" aria-pressed="true">All <span id="savedTabCountAll">0</span></button><button class="saved-filter-tab" data-saved-filter="static" type="button" aria-pressed="false">Static <span id="savedTabCountStatic">0</span></button><button class="saved-filter-tab" data-saved-filter="animated" type="button" aria-pressed="false">Animated <span id="savedTabCountAnimated">0</span></button></div><div class="saved-collection-controls"><label class="select-all"><input id="selectAllSaved" type="checkbox"> Select all visible</label><span id="savedSelectionSummary" class="saved-selection-summary">0 selected</span><button id="openSavedMoveMenu" class="soft-btn hidden" type="button" aria-haspopup="menu" aria-controls="savedGroupMoveMenu" aria-expanded="false">Move to…</button><button id="clearSavedSelection" class="soft-btn" type="button" disabled>Clear</button></div><div id="savedGroupMoveMenu" class="saved-group-move-menu hidden" role="menu"></div><div id="savedUndoNotice" class="saved-undo-notice hidden" role="status" aria-live="polite"><span id="savedUndoMessage"></span><button id="undoSavedAction" class="soft-btn" type="button">Undo</button></div></div>
<div id="savedStickerGrid" class="saved-sticker-grid"></div>
<p id="savedCollectionEmpty" class="saved-collection-empty hidden">No stickers saved yet. Fetch a pack and use <b>Save for later</b> on your favorite stickers.</p>
<div id="savedCollectionCreate" class="saved-collection-create hidden"><label for="collectionPackName">Your pack name</label><div class="saved-create-line"><input id="collectionPackName" type="text" maxlength="60" value="My sticker mix" placeholder="Name your WhatsApp pack"><button id="createSavedPack" class="cta" type="button" disabled>Create pack from selected</button></div><p>WhatsApp needs at least 3 stickers of one type per pack. Select only static or only animated stickers; each action creates one pack.</p></div>
<div id="savedCollectionFlow" class="saved-collection-flow hidden"></div>
</section>
<div id="savedContextMenu" class="saved-context-menu hidden" role="menu"></div>
</div>
<details class="help"><summary>Why is a Bot token needed?</summary><p>Telegram serves sticker files through its Bot API, which requires a token. Create one with <b>@BotFather</b>. The token is kept in this local Python app; if you choose “Remember,” it is encrypted with Windows DPAPI for your account.</p></details>
<p class="footnote">Static and animated stickers are kept in separate WhatsApp packs. WhatsApp requires at least 3 stickers of one type per pack.</p>
</div>
<script>
const $=id=>document.getElementById(id);
let demoMode=false, activeJobId=null, pollTimer=null, config={}, activityLogJob=null, activityLogSeq=0, progressiveStickerJobId=null;
let fullystPage=0, fullystPageCount=1, fullystHasMore=false, fullystQuery='', fullystSearching=false, fullystLoaded=false;
let fullystVisitedPages=new Set(), fullystVisitedPageCount=0, fullystVisitHistoryLoaded=false, fullystVisitHistoryError='';
let fullystCurrentPacks=[], showAdultPreviews=false, hideAdultContent=false, fullystCatalogReturnState=null;
let savedCollectionItems=[], savedCollectionSelection=new Set(), savedCollectionBusy=false, savedCollectionFilter='all';
let savedCollectionGroups=[{id:'ungrouped',name:'Ungrouped'}], savedCollectionGroupFilter='all';
let savedCollectionJobStatus='', savedCollectionPreviousFocus=null, savedUndoToken=null, savedUndoBusy=false, savedUndoTimer=null;
let selectedStickerIndexes=new Set(), stickerFilter='all', focusedStickerIndex=null, lastClickedStickerIndex=null;
function error(text){$('errorBox').textContent=text||'';$('errorBox').style.display=text?'block':'none'}
function warning(text){$('warnBox').textContent=text||'';$('warnBox').style.display=text?'block':'none'}
function adultPreviewEnabled(){return showAdultPreviews&&!hideAdultContent}
function applyAdultBlur(img,isAdult){
 if(!isAdult){delete img.dataset.adultPreview;img.classList.remove('adult-blurred');img.removeAttribute('title');return}
 img.dataset.adultPreview='true';img.classList.toggle('adult-blurred',!adultPreviewEnabled());
 img.title=adultPreviewEnabled()?'18+ preview':'18+ preview blurred. Turn on Show adult previews in Settings to reveal.';
}
function refreshAdultBlur(){document.querySelectorAll('img[data-adult-preview="true"]').forEach(img=>applyAdultBlur(img,true))}
function hiddenPreview(className,label){const node=document.createElement('div');node.className=`${className} preview-hidden-placeholder`;node.setAttribute('role','img');node.setAttribute('aria-label',label||'Preview hidden by adult-content filter');node.textContent='Preview hidden';return node}
function startPreviewLoading(image){image.classList.remove('preview-failed');image.classList.add('preview-loading');image.onload=()=>{image.classList.remove('preview-loading');image.onload=null}}
function failPreview(image){image.removeAttribute('src');image.alt='';image.onload=null;image.onerror=null;image.classList.remove('preview-loading');image.classList.add('preview-failed')}
let packModalPreviousFocus=null, previewPreviousFocus=null, settingsPreviousFocus=null, packModalSlug='', packModalAdult=false;
let holdPressTimer=null, holdPressImage=null, holdPressPointerId=null, holdPressActive=false, holdPressTriggered=false, holdSuppressClick=false;
function setPackModalStatus(phase,message,isLoading=true,done=0,total=0){
 $('packModalStatus').classList.remove('hidden');$('packModalPhase').textContent=phase||'Loading pack…';$('packModalMessage').textContent=message||'';
 $('packModalSkeleton').classList.toggle('hidden',!isLoading);
 const hasProgress=Number(total)>0;$('packModalProgressWrap').classList.toggle('hidden',!isLoading||!hasProgress);
 $('packModalProgressFill').style.width=hasProgress?Math.min(100,Math.round(Number(done||0)/Number(total)*100))+'%':'0%';
 $('packModalCount').textContent=hasProgress?`${Number(done||0)} / ${Number(total)}`:'';
}
function updatePackModalStatus(job){
 if($('packModal').classList.contains('hidden'))return;
 const running=job.status==='queued'||job.status==='running';
 if(job.title){$('packModalTitle').textContent=(job.isAdult?`${job.title} · 18+`:job.title);$('packModalSubtitle').textContent=`${job.source==='Fullyst'?'Fullyst':'Telegram'} pack${job.sourceSlug?` · ${job.sourceSlug}`:''}`}
 if(running){setPackModalStatus(job.phase||'Working…',job.message||'Preparing stickers…',true,job.done,job.total);if(job.phase==='Preparing previews'&&Array.isArray(job.stickers))$('packModalSkeleton').classList.add('hidden');return}
 if(job.status==='failed'){setPackModalStatus(job.phase||'Could not fetch pack',job.error||job.message||'The pack could not be loaded.',false,job.done,job.total);return}
 $('packModalStatus').classList.add('hidden');
 if(job.status==='ready')$('packModalSubtitle').textContent=`${job.stickers?.length||0} stickers ready${job.failedCount?` · ${job.failedCount} skipped`:''}${job.sourceSlug?` · ${job.sourceSlug}`:''}`;
 if(job.status==='done')$('packModalSubtitle').textContent=job.message||'Pack ready to download.';
}
function openPackModal(slug,title,isAdult=false){
 packModalPreviousFocus=document.activeElement;packModalSlug=slug||'';packModalAdult=!!isAdult;
 $('packModalTitle').textContent=(title||slug||'View pack')+(isAdult?' · 18+':'');
 $('packModalSubtitle').textContent=`Fullyst sticker pack${slug?` · ${slug}`:''}`;
 $('selectionPanel').classList.add('hidden');$('resultPanel').classList.add('hidden');
 $('packModalContent').replaceChildren();$('packModalContent').append($('activityPanel'),$('selectionPanel'),$('resultPanel'));
 $('packsHome').classList.add('hidden');$('packModal').classList.remove('hidden');document.body.style.overflow='hidden';
 setPackModalStatus('Loading pack…','Getting pack details and previews.',true);
 setTimeout(()=>{if(!$('packModal').classList.contains('hidden'))$('closePackModal').focus()},0);
}
function closePackModal(){
 if($('packModal').classList.contains('hidden'))return;
 if(!$('stickerPreviewModal').classList.contains('hidden'))closeStickerPreview();
 $('activityHome').append($('activityPanel'));$('packsHome').append($('selectionPanel'),$('resultPanel'));
 $('packModalContent').replaceChildren();$('packModal').classList.add('hidden');$('packsHome').classList.remove('hidden');document.body.style.removeProperty('overflow');
 if(packModalPreviousFocus&&document.contains(packModalPreviousFocus))packModalPreviousFocus.focus();
}
function openSettings(){
 settingsPreviousFocus=document.activeElement;$('settingsModal').classList.remove('hidden');document.body.style.overflow='hidden';setTimeout(()=>{if(!$('settingsModal').classList.contains('hidden'))$('closeSettings').focus()},0);
}
function closeSettings(){
 if($('settingsModal').classList.contains('hidden'))return;
 $('settingsModal').classList.add('hidden');document.body.style.removeProperty('overflow');
 if(settingsPreviousFocus&&document.contains(settingsPreviousFocus))settingsPreviousFocus.focus();
}
function openStickerPreview(src,fallback,caption,isAdult=false){
 if(!src)src=fallback;if(!src)return;
 previewPreviousFocus=document.activeElement;
 const image=$('largeStickerImage'),video=$('largeStickerVideo'),stage=document.querySelector('.preview-stage');video.pause();video.classList.add('hidden');image.classList.remove('hidden','preview-failed');image.dataset.previewFallback=fallback&&fallback!==src?fallback:'';image.alt=caption||'Large sticker preview';stage.classList.add('preview-stage-loading');
 image.onload=()=>{stage.classList.remove('preview-stage-loading');image.onload=null};
 image.onerror=()=>{const backup=image.dataset.previewFallback;if(backup&&image.src!==backup){image.dataset.previewFallback='';stage.classList.add('preview-stage-loading');image.src=backup;return}failPreview(image);stage.classList.remove('preview-stage-loading')};
 applyAdultBlur(image,!!isAdult);image.src=src;$('stickerPreviewTitle').textContent=caption||'Sticker preview';$('stickerPreviewCaption').textContent=!isAdult?'':hideAdultContent?'18+ preview · blurred because adult previews are disabled in Settings.':adultPreviewEnabled()?'18+ preview shown unblurred.':'18+ preview · blurred by default; change this in Settings.';
 $('stickerPreviewModal').classList.remove('hidden');setTimeout(()=>{if(!$('stickerPreviewModal').classList.contains('hidden'))$('closeStickerPreview').focus()},0);
}
function closeStickerPreview(){
 if(holdPressActive){clearTimeout(holdPressTimer);holdPressTimer=null;holdPressActive=false;holdPressTriggered=false;holdPressImage=null;holdPressPointerId=null;holdSuppressClick=true;setTimeout(()=>holdSuppressClick=false,250)}
 $('stickerPreviewModal').classList.add('hidden');const image=$('largeStickerImage');image.removeAttribute('src');image.onload=null;image.onerror=null;document.querySelector('.preview-stage').classList.remove('preview-stage-loading');$('largeStickerVideo').pause();$('largeStickerVideo').removeAttribute('src');
 const focus=previewPreviousFocus;previewPreviousFocus=null;if(focus&&document.contains(focus)&&typeof focus.focus==='function')focus.focus();
}
function finishHeldPreview(event){
 if(!holdPressActive)return;
 if(holdPressPointerId!==null&&event&&event.pointerId!==undefined&&event.pointerId!==holdPressPointerId)return;
 clearTimeout(holdPressTimer);holdPressTimer=null;
 const wasPreviewOpen=holdPressTriggered;holdPressActive=false;holdPressTriggered=false;holdPressImage=null;holdPressPointerId=null;
 if(wasPreviewOpen){if(event&&event.cancelable)event.preventDefault();holdSuppressClick=true;setTimeout(()=>holdSuppressClick=false,250);closeStickerPreview()}
}
function attachHoldPreview(image,src,fallback,caption,isAdult=false){
 image.draggable=false;image.style.touchAction='pan-y';image.style.userSelect='none';image.style.webkitUserSelect='none';
 const openWhileHeld=()=>{
  if(!holdPressActive||holdPressImage!==image||holdPressTriggered)return;
  holdPressTriggered=true;
  if(image.setPointerCapture&&holdPressPointerId!==null){try{image.setPointerCapture(holdPressPointerId)}catch(_){}}
  openStickerPreview(src,fallback,caption,isAdult);
 };
 image.addEventListener('pointerdown',event=>{
  if(event.button!==undefined&&event.button!==0)return;
  clearTimeout(holdPressTimer);holdPressImage=image;holdPressPointerId=event.pointerId??null;holdPressActive=true;holdPressTriggered=false;
  holdPressTimer=setTimeout(openWhileHeld,480);
 });
 image.addEventListener('pointerleave',()=>{
  if(holdPressImage!==image||holdPressTriggered)return;
  clearTimeout(holdPressTimer);holdPressTimer=null;holdPressActive=false;holdPressImage=null;holdPressPointerId=null;
 });
 image.addEventListener('dragstart',event=>event.preventDefault());
 image.addEventListener('contextmenu',event=>{event.preventDefault();if(holdPressActive&&holdPressImage===image)openWhileHeld()});
}
function dismissSuppressedHoldClick(event){
 if(!holdSuppressClick)return;
 holdSuppressClick=false;if(event.cancelable)event.preventDefault();event.stopPropagation();if(event.stopImmediatePropagation)event.stopImmediatePropagation();
}
function clearActivityLog(){activityLogJob=null;activityLogSeq=0;$('liveLogEntries').replaceChildren();$('liveLogEntries').innerHTML='<div class="log-empty">Fetch or convert a pack to see progress events.</div>';$('liveLogCount').textContent='Waiting for a job';$('liveLogPanel').open=false}
function renderActivityLog(job){
 if(!Array.isArray(job.activityLog))return;
 const root=$('liveLogEntries');
 if(activityLogJob!==job.id){activityLogJob=job.id;activityLogSeq=0;root.replaceChildren()}
 const entries=job.activityLog;
 if(entries.length&&activityLogSeq&&Number(entries[0].seq)>activityLogSeq+1){root.replaceChildren();activityLogSeq=Number(entries[0].seq)-1}
 let added=false;
 for(const entry of entries){const seq=Number(entry.seq)||0;if(seq<=activityLogSeq)continue;
  const row=document.createElement('div');row.className='log-entry';
  const time=document.createElement('span');time.className='log-time';time.textContent=entry.time||'';
  const phase=document.createElement('span');phase.className='log-phase';phase.textContent=entry.phase||'Activity';
  const message=document.createElement('span');message.className='log-message';message.textContent=entry.message||'';
  row.append(time,phase,message);root.append(row);activityLogSeq=Math.max(activityLogSeq,seq);added=true;
 }
 if(!entries.length&&!root.children.length)root.innerHTML='<div class="log-empty">Waiting for progress events…</div>';
 $('liveLogCount').textContent=`${entries.length} event${entries.length===1?'':'s'}`;
 if(added)$('liveLogPanel').open=true;
 if(added&&$('liveLogPanel').open)root.scrollTop=root.scrollHeight;
}
function pill(text,failed=false){$('statusPill').textContent=text;$('statusPill').className='pill'+(failed?' fail':'')}
function busy(value){$('fetchButton').disabled=value||demoMode;$('fetchButton').textContent=value?'Working…':'Fetch sticker pack';$('convertAll').disabled=value||demoMode;$('createSelected').disabled=value||demoMode||!selectionValid()}
function selectedIndexes(){return [...selectedStickerIndexes].sort((a,b)=>a-b)}
function selectionValid(){
 let animated=0,statics=0;
 for(const sticker of (window.currentStickers||[])){if(!selectedStickerIndexes.has(Number(sticker.index)))continue;if(sticker.kind==='Animated')animated++;else statics++}
 return animated>=3||statics>=3;
}
function updateSelection(){
 const all=window.currentStickers||[],selected=selectedIndexes();let animated=0,statics=0;
 for(const sticker of all){if(!selectedStickerIndexes.has(Number(sticker.index)))continue;if(sticker.kind==='Animated')animated++;else statics++}
 const compatibility=animated&&statics?'Static and animated will be split into separate WhatsApp packs.':animated?'Animated-only pack; WhatsApp cannot mix static stickers.':statics?'Static-only pack; WhatsApp cannot mix animated stickers.':'WhatsApp packs cannot mix static and animated stickers.';
 $('selectionSummary').textContent=`${selected.length} selected · ${animated} animated · ${statics} static. ${compatibility} Need at least 3 of one type.`;
 const preparing=!!window.currentStickerLoading;
 $('createSelected').disabled=preparing||demoMode||!selectionValid();
 $('selectAll').disabled=preparing||demoMode;
 $('selectAll').checked=all.length>0&&selected.length===all.length;
 $('selectAll').indeterminate=selected.length>0&&selected.length<all.length;
 $('clearSelection').disabled=preparing||selected.length===0;
}
function updateStickerFilters(){
 const all=window.currentStickers||[];
 const counts={all:all.length,static:all.filter(s=>s.kind!=='Animated').length,animated:all.filter(s=>s.kind==='Animated').length};
 const labels={all:'All',static:'Static',animated:'Animated'};
 document.querySelectorAll('[data-sticker-filter]').forEach(button=>{
  const key=button.dataset.stickerFilter;button.textContent=`${labels[key]} (${counts[key]||0})`;
  button.classList.toggle('active',key===stickerFilter);button.setAttribute('aria-pressed',key===stickerFilter?'true':'false');
  button.classList.toggle('hidden',key!=='all'&&!counts[key]);
 });
}
function filteredStickers(){
 const all=window.currentStickers||[];
 if(stickerFilter==='static')return all.filter(s=>s.kind!=='Animated');
 if(stickerFilter==='animated')return all.filter(s=>s.kind==='Animated');
 return all;
}
function rangeSelectStickerIndexes(root,visible,anchorIndex,clickedIndex,shouldSelect){
 const indexes=visible.map(sticker=>Number(sticker.index)),anchor=indexes.indexOf(anchorIndex),clicked=indexes.indexOf(clickedIndex);
 if(anchor<0||clicked<0)return false;
 for(let i=Math.min(anchor,clicked);i<=Math.max(anchor,clicked);i++){
  if(shouldSelect)selectedStickerIndexes.add(indexes[i]);else selectedStickerIndexes.delete(indexes[i]);
 }
 root.querySelectorAll('input.pick').forEach(input=>{input.checked=selectedStickerIndexes.has(Number(input.value))});
 updateSelection();return true;
}
function moveStickerPickerFocus(event,current,root){
 const key=event.key;
 if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(key))return;
 const inputs=[...root.querySelectorAll('input.pick:not(:disabled)')],position=inputs.indexOf(current);
 if(position<0||!inputs.length)return;
 const columns=Math.max(1,getComputedStyle(root).gridTemplateColumns.trim().split(/\s+/).filter(Boolean).length);
 let next=position;
 if(key==='Home')next=0;
 else if(key==='End')next=inputs.length-1;
 else if(key==='ArrowLeft'&&position>0&&Math.floor((position-1)/columns)===Math.floor(position/columns))next=position-1;
 else if(key==='ArrowRight'&&position+1<inputs.length&&Math.floor((position+1)/columns)===Math.floor(position/columns))next=position+1;
 else if(key==='ArrowUp'&&position-columns>=0)next=position-columns;
 else if(key==='ArrowDown'&&position+columns<inputs.length)next=position+columns;
 if(next===position)return;
 event.preventDefault();inputs.forEach((input,index)=>{input.tabIndex=index===next?0:-1});
 focusedStickerIndex=Number(inputs[next].value);inputs[next].focus();
}
function renderStickerPage(){
 const visible=filteredStickers(),root=$('stickerGrid');
 const activePick=document.activeElement?.closest?.('#stickerGrid input.pick');
 if(activePick)focusedStickerIndex=Number(activePick.value);
 const focusIndex=visible.some(sticker=>Number(sticker.index)===focusedStickerIndex)?focusedStickerIndex:(visible.length?Number(visible[0].index):null);
 root.replaceChildren();
 for(const s of visible){
   const card=document.createElement('article');card.className='sticker-card';
   const check=document.createElement('input');check.type='checkbox';check.className='pick';check.id=`sticker-pick-${s.index}`;check.value=String(s.index);
   check.checked=selectedStickerIndexes.has(Number(s.index));check.disabled=!!window.currentStickerLoading;check.tabIndex=Number(s.index)===focusIndex?0:-1;
   check.addEventListener('focus',()=>{focusedStickerIndex=Number(s.index);root.querySelectorAll('input.pick').forEach(input=>{input.tabIndex=input===check?0:-1})});
   check.addEventListener('keydown',event=>moveStickerPickerFocus(event,check,root));
   check.addEventListener('click',event=>{const index=Number(s.index);if(event.shiftKey&&lastClickedStickerIndex!==null){if(!rangeSelectStickerIndexes(root,visible,lastClickedStickerIndex,index,check.checked))lastClickedStickerIndex=index;else lastClickedStickerIndex=index}else lastClickedStickerIndex=index});
   check.addEventListener('change',()=>{const index=Number(s.index);if(check.checked)selectedStickerIndexes.add(index);else selectedStickerIndexes.delete(index);updateSelection()});
   const label=document.createElement('label');label.className='sticker-card-label';label.htmlFor=check.id;
   const sourceNumber=Number(s.sourceIndex)||Number(s.index)+1;
   const previewSource=s.previewUrl||s.thumbUrl,hidePreview=!!window.currentPackPreviewsHidden;
   const img=hidePreview?hiddenPreview('sticker-thumb sticker-preview-hidden','Fullyst preview hidden while adult-content hiding is enabled'):document.createElement('img');
   if(!hidePreview){
    const preparing=!!window.currentStickerLoading,previewReady=!preparing||s.previewReady===true;
    img.className='sticker-thumb';img.loading='eager';img.decoding='async';img.alt=`Sticker preview ${sourceNumber}`;img.dataset.previewReady=previewReady?'true':'false';startPreviewLoading(img);applyAdultBlur(img,window.currentPackIsAdult);
    img.onerror=()=>{if(!preparing&&s.previewUrl&&!img.dataset.fallback){img.dataset.fallback='1';startPreviewLoading(img);img.src=s.thumbUrl;return}failPreview(img)};
    if(previewReady){if(!preparing||s.previewAvailable!==false)img.src=preparing?s.thumbUrl:previewSource;else failPreview(img)}
    attachHoldPreview(img,s.largePreviewUrl||previewSource,s.thumbUrl,`Sticker ${sourceNumber} · ${s.emoji||''}`,window.currentPackIsAdult)
   }
   const info=document.createElement('div');info.className='sticker-info';
   const number=document.createElement('span');number.className='sticker-number';number.textContent=`${sourceNumber}. ${s.emoji||'📎'}`;
   const kind=document.createElement('span');kind.className='sticker-kind';kind.textContent=s.kind;
   info.append(number,kind);
   const fmt=document.createElement('div');fmt.className='sticker-emoji';fmt.textContent=s.formatLabel;
   label.append(img,info,fmt);
   const saveButton=document.createElement('button');saveButton.type='button';saveButton.className='save-sticker-btn';
   const isSaved=window.currentSavedJobId===window.currentStickerJobId&&window.currentSavedStickerIndexes?.has(Number(s.index));
   saveButton.textContent=isSaved?'Saved ✓':'Save for later';saveButton.disabled=demoMode||!!isSaved||!!window.currentStickerLoading;
   saveButton.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();saveSticker(window.currentStickerJobId,Number(s.index),saveButton)});
   card.append(check,label,saveButton);root.append(card);
 }
 updateSelection();
}
function updateProgressiveStickerPreviews(job){
 window.currentStickers=Array.isArray(job.stickers)?job.stickers:[];
 for(const sticker of window.currentStickers){
  if(sticker.previewReady!==true)continue;
  const check=$(`sticker-pick-${sticker.index}`),img=check?.closest('.sticker-card')?.querySelector('img.sticker-thumb');
  if(!img||img.dataset.previewReady==='true')continue;
  img.dataset.previewReady='true';
  if(sticker.previewAvailable===false){failPreview(img);continue}
  startPreviewLoading(img);img.onerror=()=>failPreview(img);img.src=sticker.thumbUrl;
 }
 updateStickerFilters();updateSelection();
}
async function saveSticker(jobId,index,button){
 if(!jobId||demoMode)return;
 button.disabled=true;button.textContent='Saving…';
 try{
  const response=await fetch('/api/collection/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:jobId,index})});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not save this sticker');
  if(window.currentSavedJobId!==jobId){window.currentSavedJobId=jobId;window.currentSavedStickerIndexes=new Set()}
  window.currentSavedStickerIndexes.add(Number(index));button.textContent='Saved ✓';button.title=data.alreadySaved?'This sticker is already in My saves.':'Saved on this PC.';updateSavedCount(data.count);
 }catch(e){button.disabled=false;button.textContent='Save for later';error(e.message)}
}
function updateSavedCount(count){$('savedCount').textContent=String(Math.max(0,Number(count)||0))}
async function refreshSavedCount(){try{const response=await fetch('/api/collection',{cache:'no-store'});if(response.ok){const data=await response.json();updateSavedCount(data.count)}}catch(_){}}
function savedCollectionSelectionValid(){
 let statics=0,animated=0;
 for(const item of savedCollectionItems){if(!savedCollectionSelection.has(item.id)||!item.available)continue;if(item.kind==='Animated')animated++;else statics++}
 return (statics>=3&&animated===0)||(animated>=3&&statics===0);
}
function savedCollectionGroupItems(){
 if(savedCollectionGroupFilter==='all')return savedCollectionItems;
 return savedCollectionItems.filter(item=>(item.groupId||'ungrouped')===savedCollectionGroupFilter);
}
function savedCollectionVisibleItems(){
 const grouped=savedCollectionGroupItems();
 if(savedCollectionFilter==='static')return grouped.filter(item=>item.kind!=='Animated');
 if(savedCollectionFilter==='animated')return grouped.filter(item=>item.kind==='Animated');
 return grouped;
}
function selectedAvailableSavedIds(){return savedCollectionItems.filter(item=>savedCollectionSelection.has(item.id)&&item.available).map(item=>item.id)}
function closeSavedContextMenus(){
 $('savedGroupMoveMenu').classList.add('hidden');$('savedGroupMoveMenu').replaceChildren();$('openSavedMoveMenu').setAttribute('aria-expanded','false');
 $('savedContextMenu').classList.add('hidden');$('savedContextMenu').replaceChildren();
}
function positionSavedContextMenu(menu,event){
 menu.classList.remove('hidden');
 const margin=8,left=Math.max(margin,Math.min(event.clientX,window.innerWidth-menu.offsetWidth-margin)),top=Math.max(margin,Math.min(event.clientY,window.innerHeight-menu.offsetHeight-margin));
 menu.style.left=`${left}px`;menu.style.top=`${top}px`;
}
function addSavedContextItem(menu,label,action,danger=false){
 const button=document.createElement('button');button.type='button';button.className='saved-context-item'+(danger?' danger':'');button.textContent=label;button.setAttribute('role','menuitem');
 button.addEventListener('click',()=>{closeSavedContextMenus();action()});menu.append(button);return button;
}
function openSavedGroupContextMenu(event,groupId){
 if(demoMode||savedCollectionBusy)return;
 const group=savedCollectionGroups.find(item=>item.id===groupId);if(!group||groupId==='all')return;
 closeSavedContextMenus();const menu=$('savedContextMenu');
 const title=document.createElement('div');title.className='saved-context-title';title.textContent=group.name;menu.append(title);
 const ids=selectedAvailableSavedIds();
 if(ids.length)addSavedContextItem(menu,`Move ${ids.length} selected here`,()=>moveSavedStickersToGroup(group.id,ids));
 if(group.id!=='ungrouped')addSavedContextItem(menu,'Delete group',()=>deleteSavedGroup(group.id),true);
 if(!menu.querySelector('button')){const empty=document.createElement('div');empty.className='saved-context-title';empty.textContent='No actions yet';menu.append(empty)}
 positionSavedContextMenu(menu,event);menu.querySelector('button:not(:disabled)')?.focus();
}
function openSavedStickerContextMenu(event,itemId){
 if(demoMode||savedCollectionBusy)return;
 const item=savedCollectionItems.find(saved=>saved.id===itemId);if(!item)return;
 const selected=selectedAvailableSavedIds(),ids=savedCollectionSelection.has(itemId)?selected:[itemId];
 closeSavedContextMenus();const menu=$('savedContextMenu');
 const title=document.createElement('div');title.className='saved-context-title';title.textContent=ids.length>1?`Move ${ids.length} selected stickers`:`Move “${item.name||'sticker'}”`;menu.append(title);
 for(const group of savedCollectionGroups)addSavedContextItem(menu,`Move to ${group.name}`,()=>moveSavedStickersToGroup(group.id,ids));
 positionSavedContextMenu(menu,event);menu.querySelector('button:not(:disabled)')?.focus();
}
function toggleSavedGroupMoveMenu(){
 const menu=$('savedGroupMoveMenu');if(!menu.classList.contains('hidden')){closeSavedContextMenus();return}
 const ids=selectedAvailableSavedIds();if(!ids.length)return;
 closeSavedContextMenus();
 const heading=document.createElement('span');heading.className='saved-context-title';heading.textContent='Move selected to';menu.append(heading);
 for(const group of savedCollectionGroups)addSavedContextItem(menu,group.name,()=>moveSavedStickersToGroup(group.id));
 menu.classList.remove('hidden');$('openSavedMoveMenu').setAttribute('aria-expanded','true');
}
function showNewSavedGroupForm(){if(demoMode||savedCollectionBusy)return;$('newSavedGroupForm').classList.remove('hidden');$('newSavedGroupName').focus()}
function cancelNewSavedGroup(){ $('newSavedGroupName').value='';$('newSavedGroupForm').classList.add('hidden');$('openNewSavedGroup').focus() }
function updateSavedCollectionGroupControls(){
 const ids=new Set(savedCollectionGroups.map(group=>group.id));
 if(savedCollectionGroupFilter!=='all'&&!ids.has(savedCollectionGroupFilter))savedCollectionGroupFilter='all';
 const counts=new Map(savedCollectionGroups.map(group=>[group.id,0]));
 for(const item of savedCollectionItems){const id=ids.has(item.groupId)?item.groupId:'ungrouped';counts.set(id,(counts.get(id)||0)+1)}
 const root=$('savedGroupChips');root.replaceChildren();
 const filters=[{id:'all',name:'All',count:savedCollectionItems.length},...savedCollectionGroups.map(group=>({...group,count:counts.get(group.id)||0}))];
 for(const group of filters){
  const wrap=document.createElement('div');wrap.className='saved-group-chip-wrap'+(savedCollectionGroupFilter===group.id?' active':'');
  const filter=document.createElement('button');filter.type='button';filter.className='saved-group-chip';filter.textContent=`${group.name} (${group.count})`;
  filter.setAttribute('aria-pressed',savedCollectionGroupFilter===group.id?'true':'false');filter.title=`Show ${group.name} stickers`;filter.disabled=savedCollectionBusy;
  filter.addEventListener('click',()=>setSavedCollectionGroupFilter(group.id));wrap.append(filter);
  if(group.id!=='all'){
   const more=document.createElement('button');more.type='button';more.className='saved-group-more';more.textContent='⋯';more.title=`Options for ${group.name}`;more.setAttribute('aria-label',`Options for ${group.name}`);more.setAttribute('aria-haspopup','menu');more.disabled=demoMode||savedCollectionBusy;
   more.addEventListener('click',event=>{event.stopPropagation();openSavedGroupContextMenu(event,group.id)});wrap.append(more);
   wrap.addEventListener('contextmenu',event=>{event.preventDefault();openSavedGroupContextMenu(event,group.id)});
  }
  root.append(wrap);
 }
 $('openNewSavedGroup').disabled=demoMode||savedCollectionBusy;
 $('newSavedGroupName').disabled=demoMode||savedCollectionBusy;
 $('createSavedGroup').disabled=demoMode||savedCollectionBusy;
 $('cancelNewSavedGroup').disabled=demoMode||savedCollectionBusy;
}
function updateSavedCollectionFilterTabs(){
 const grouped=savedCollectionGroupItems();
 const counts={all:grouped.length,static:grouped.filter(item=>item.kind!=='Animated').length,animated:grouped.filter(item=>item.kind==='Animated').length};
 if(savedCollectionFilter!=='all'&&!counts[savedCollectionFilter])savedCollectionFilter='all';
 const labels={all:'All',static:'Static',animated:'Animated'};
 for(const filter of ['all','static','animated']){
  const button=document.querySelector(`[data-saved-filter="${filter}"]`);if(!button)continue;
  button.classList.toggle('active',filter===savedCollectionFilter);button.setAttribute('aria-pressed',filter===savedCollectionFilter?'true':'false');
  button.disabled=filter!=='all'&&counts[filter]===0;
  const count=$(`savedTabCount${labels[filter]}`);if(count)count.textContent=String(counts[filter]);
 }
}
function setSavedCollectionFilter(filter){
 if(!['all','static','animated'].includes(filter)||filter===savedCollectionFilter)return;
 const count=filter==='static'?savedCollectionGroupItems().filter(item=>item.kind!=='Animated').length:filter==='animated'?savedCollectionGroupItems().filter(item=>item.kind==='Animated').length:savedCollectionGroupItems().length;
 if(!count)return;
 closeSavedContextMenus();savedCollectionFilter=filter;renderSavedCollection(savedCollectionItems);
}
function setSavedCollectionGroupFilter(groupId){
 if(groupId!=='all'&&!savedCollectionGroups.some(group=>group.id===groupId))return;
 if(groupId===savedCollectionGroupFilter)return;
 closeSavedContextMenus();savedCollectionGroupFilter=groupId;renderSavedCollection(savedCollectionItems);
}
function updateSavedCollectionSelection(){
 let statics=0,animated=0,selected=0;
 for(const item of savedCollectionItems){if(!savedCollectionSelection.has(item.id)||!item.available)continue;selected++;if(item.kind==='Animated')animated++;else statics++}
 const mixed=statics>0&&animated>0;
 updateSavedCollectionFilterTabs();
 const visibleIds=new Set(savedCollectionVisibleItems().map(item=>item.id));
 const hiddenTypeSelected=(savedCollectionFilter==='static'&&animated>0)||(savedCollectionFilter==='animated'&&statics>0);
 const hiddenGroupSelected=savedCollectionItems.some(item=>savedCollectionSelection.has(item.id)&&item.available&&!visibleIds.has(item.id));
 const compatibility=mixed?' · WhatsApp cannot mix static and animated; choose one type per pack':animated?' · animated-only pack':statics?' · static-only pack':' · WhatsApp packs cannot mix static and animated';
 $('savedSelectionSummary').textContent=`${selected} selected · ${statics} static · ${animated} animated${compatibility}${hiddenTypeSelected?' · selection includes other type':''}${hiddenGroupSelected?' · selection includes other groups or filters':''}`;
 const selectable=savedCollectionVisibleItems().filter(item=>item.available),visibleSelected=selectable.filter(item=>savedCollectionSelection.has(item.id)).length;
 $('selectAllSaved').checked=selectable.length>0&&visibleSelected===selectable.length;
 $('selectAllSaved').indeterminate=visibleSelected>0&&!$('selectAllSaved').checked;
 $('clearSavedSelection').disabled=savedCollectionBusy||selected===0;
 $('openSavedMoveMenu').classList.toggle('hidden',selected===0);
 $('openSavedMoveMenu').disabled=demoMode||savedCollectionBusy;
 if(!selected)closeSavedContextMenus();
 $('createSavedPack').disabled=demoMode||savedCollectionBusy||mixed||!savedCollectionSelectionValid();
}
function setSavedCollectionBusy(value){
 savedCollectionBusy=!!value;
 document.querySelectorAll('#savedStickerGrid input[type="checkbox"]').forEach(input=>{input.disabled=savedCollectionBusy||demoMode||input.dataset.available==='false'});
 document.querySelectorAll('#savedStickerGrid .remove-saved').forEach(button=>button.disabled=savedCollectionBusy||demoMode);
 $('collectionPackName').disabled=savedCollectionBusy||demoMode;
 updateSavedCollectionGroupControls();updateSavedCollectionSelection();
}
function renderSavedCollection(items){
 const groupNames=new Map(savedCollectionGroups.map(group=>[group.id,group.name]));
 savedCollectionItems=(Array.isArray(items)?items:[]).map(item=>{
  const groupId=groupNames.has(item.groupId)?item.groupId:'ungrouped';
  return {...item,groupId,groupName:groupNames.get(groupId)||'Ungrouped'};
 });
 const itemIds=new Set(savedCollectionItems.map(item=>item.id));
 savedCollectionSelection=new Set([...savedCollectionSelection].filter(id=>itemIds.has(id)));
 updateSavedCollectionGroupControls();updateSavedCollectionFilterTabs();
 const root=$('savedStickerGrid');root.replaceChildren();
 for(const item of savedCollectionVisibleItems()){
  const card=document.createElement('article');card.className='saved-sticker-card';
  card.addEventListener('contextmenu',event=>{event.preventDefault();openSavedStickerContextMenu(event,item.id)});
  const check=document.createElement('input');check.type='checkbox';check.id=`saved-pick-${item.id}`;check.className='saved-pick';check.dataset.available=item.available?'true':'false';check.checked=savedCollectionSelection.has(item.id);check.disabled=demoMode||savedCollectionBusy||!item.available;
  check.addEventListener('change',()=>{if(check.checked)savedCollectionSelection.add(item.id);else savedCollectionSelection.delete(item.id);updateSavedCollectionSelection()});
  const label=document.createElement('label');label.className='saved-sticker-main';label.htmlFor=check.id;
  const img=document.createElement('img');img.className='saved-sticker-thumb';img.loading='lazy';img.alt=`Saved sticker ${item.emoji||''}`;startPreviewLoading(img);applyAdultBlur(img,!!item.isAdult);
  img.onerror=()=>{if(item.previewUrl&&!img.dataset.fallback){img.dataset.fallback='1';startPreviewLoading(img);img.src=item.thumbUrl;return}failPreview(img)};
  img.src=item.previewUrl||item.thumbUrl;
  attachHoldPreview(img,item.largePreviewUrl||item.previewUrl||item.thumbUrl,item.thumbUrl,`${item.sourceTitle||'Saved sticker'} · ${item.emoji||''}`,!!item.isAdult);
  const title=document.createElement('strong');title.className='saved-sticker-title';title.textContent=item.emoji?`${item.emoji} ${item.name||'Sticker'}`:(item.name||'Sticker');
  const meta=document.createElement('span');meta.className='saved-sticker-meta';meta.textContent=`${item.sourceTitle||item.source||'Sticker pack'} · ${item.formatLabel||item.format}${item.isAdult?' · 18+':''}`;
  const groupLabel=document.createElement('span');groupLabel.className='saved-sticker-group';groupLabel.textContent=item.groupName||'Ungrouped';
  label.append(img,title,meta,groupLabel);
  const remove=document.createElement('button');remove.type='button';remove.className='soft-btn remove-saved';remove.textContent='Remove';remove.disabled=demoMode||savedCollectionBusy;
  remove.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();removeSavedSticker(item.id,remove)});
  card.append(check,label,remove);root.append(card);
 }
 const hasItems=savedCollectionItems.length>0,visible=savedCollectionVisibleItems();
 $('savedCollectionLoading').classList.add('hidden');$('savedGroupTools').classList.remove('hidden');
 $('savedCollectionEmpty').classList.toggle('hidden',visible.length>0);
 if(!hasItems)$('savedCollectionEmpty').innerHTML='No stickers saved yet. Fetch a pack and use <b>Save for later</b> on stickers; new saves start in <b>Ungrouped</b>.';
 else if(!visible.length){const group=savedCollectionGroups.find(item=>item.id===savedCollectionGroupFilter);$('savedCollectionEmpty').textContent=`No saved stickers in ${group?.name||'this group'} for the selected type. Choose another group or move stickers here.`}
 $('savedStickerGrid').classList.toggle('hidden',visible.length===0);$('savedCollectionControls').classList.toggle('hidden',!hasItems&&!savedUndoToken);
 $('savedCollectionCreate').classList.toggle('hidden',!hasItems);
 updateSavedCount(savedCollectionItems.length);setSavedCollectionBusy(savedCollectionBusy);
}
async function loadSavedCollection(){
 $('savedCollectionStatus').className='saved-collection-status';$('savedCollectionStatus').textContent='Loading your saved stickers…';
 $('savedCollectionLoading').classList.remove('hidden');$('savedGroupTools').classList.add('hidden');$('savedStickerGrid').classList.add('hidden');$('savedCollectionEmpty').classList.add('hidden');$('savedCollectionControls').classList.add('hidden');$('savedCollectionCreate').classList.add('hidden');
 try{
  const response=await fetch('/api/collection',{cache:'no-store'});const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not load My saves');
  savedCollectionGroups=Array.isArray(data.groups)?data.groups:[{id:'ungrouped',name:'Ungrouped'}];
  if(!savedCollectionGroups.some(group=>group.id==='ungrouped'))savedCollectionGroups.unshift({id:'ungrouped',name:'Ungrouped'});
  if(savedCollectionGroupFilter!=='all'&&!savedCollectionGroups.some(group=>group.id===savedCollectionGroupFilter))savedCollectionGroupFilter='all';
  renderSavedCollection(data.items||[]);$('savedCollectionStatus').textContent=`${Number(data.count||0)} saved sticker${Number(data.count||0)===1?'':'s'} on this PC · ${savedCollectionGroups.length-1} group${savedCollectionGroups.length===2?'':'s'}.`;
 }catch(e){$('savedCollectionLoading').classList.add('hidden');$('savedGroupTools').classList.add('hidden');$('savedCollectionStatus').className='saved-collection-status error-text';$('savedCollectionStatus').textContent=e.message;$('savedStickerGrid').classList.add('hidden');$('savedCollectionEmpty').classList.add('hidden')}
}
function showSavedCollectionFlow(){
 $('savedCollectionFlow').classList.remove('hidden');$('savedCollectionFlow').append($('activityPanel'),$('resultPanel'));
}
function openSavedCollection(){
 savedCollectionPreviousFocus=document.activeElement;$('savedCollectionModal').classList.remove('hidden');document.body.style.overflow='hidden';
 if(savedCollectionJobStatus)showSavedCollectionFlow();else $('savedCollectionFlow').classList.add('hidden');
 setTimeout(()=>{if(!$('savedCollectionModal').classList.contains('hidden'))$('closeSavedCollection').focus()},0);loadSavedCollection();
}
function closeSavedCollection(){
 if($('savedCollectionModal').classList.contains('hidden'))return;
 if(!$('stickerPreviewModal').classList.contains('hidden'))closeStickerPreview();
 if($('activityPanel').parentNode===$('savedCollectionFlow'))$('activityHome').append($('activityPanel'));
 if($('resultPanel').parentNode===$('savedCollectionFlow'))$('packsHome').append($('resultPanel'));
 $('savedCollectionFlow').classList.add('hidden');$('savedCollectionModal').classList.add('hidden');document.body.style.removeProperty('overflow');
 if(savedCollectionPreviousFocus&&document.contains(savedCollectionPreviousFocus))savedCollectionPreviousFocus.focus();
}
function showSavedUndo(token,message){
 if(savedUndoTimer)clearTimeout(savedUndoTimer);
 savedUndoToken=token||null;$('savedUndoMessage').textContent=message||'Undo the last My saves change.';
 $('savedUndoNotice').classList.toggle('hidden',!savedUndoToken);
 if(savedUndoToken&&!savedCollectionItems.length)$('savedCollectionControls').classList.remove('hidden');
 $('undoSavedAction').disabled=!savedUndoToken||savedUndoBusy||demoMode;
 savedUndoTimer=savedUndoToken?setTimeout(hideSavedUndo,600000):null;
}
function hideSavedUndo(){if(savedUndoTimer)clearTimeout(savedUndoTimer);savedUndoTimer=null;savedUndoToken=null;$('savedUndoNotice').classList.add('hidden');$('undoSavedAction').disabled=true;if(!savedCollectionItems.length)$('savedCollectionControls').classList.add('hidden')}
async function undoSavedAction(){
 if(!savedUndoToken||savedUndoBusy||demoMode)return;
 const token=savedUndoToken;savedUndoBusy=true;$('undoSavedAction').disabled=true;$('undoSavedAction').textContent='Undoing…';let undoResponse=null;
 try{
  undoResponse=await fetch('/api/collection/undo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({undoToken:token})});
  const data=await undoResponse.json();if(!undoResponse.ok)throw new Error(data.error||'Could not undo this My saves change');
  if(savedUndoToken===token)hideSavedUndo();
  savedCollectionGroupFilter=data.kind==='move'?'all':(data.groupId||'ungrouped');
  await loadSavedCollection();updateSavedCount(data.count);
  $('savedCollectionStatus').className='saved-collection-status';
  if(data.kind==='move'){
   const restored=Number(data.restored)||0,fallback=Number(data.fallbackUngrouped)||0;
   $('savedCollectionStatus').textContent=`Undid the group move for ${restored} sticker${restored===1?'':'s'}.${fallback?` ${fallback} returned to Ungrouped because the original group no longer exists.`:''}`;
  }else{
   const group=savedCollectionGroups.find(item=>item.id===data.groupId);
   $('savedCollectionStatus').textContent=`Restored the removed sticker to ${group?.name||'My saves'}.`;
  }
 }catch(e){
  if(undoResponse&&undoResponse.status>=400&&undoResponse.status<500&&savedUndoToken===token)hideSavedUndo();
  $('savedCollectionStatus').className='saved-collection-status error-text';$('savedCollectionStatus').textContent=e.message;
 }
 finally{savedUndoBusy=false;$('undoSavedAction').textContent='Undo';$('undoSavedAction').disabled=!savedUndoToken||demoMode}
}
async function removeSavedSticker(itemId,button){
 if(demoMode||savedCollectionBusy)return;
 const removedItem=savedCollectionItems.find(item=>item.id===itemId);
 button.disabled=true;button.textContent='Removing…';
 try{
  const response=await fetch('/api/collection/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:itemId})});const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not remove this sticker');
  savedCollectionSelection.delete(itemId);await loadSavedCollection();updateSavedCount(data.count);
  $('savedCollectionStatus').className='saved-collection-status';$('savedCollectionStatus').textContent=`Removed ${removedItem?.name||'sticker'} from My saves.`;
  showSavedUndo(data.undoToken,`Removed ${removedItem?.name||'sticker'}. Undo is available for 10 minutes.`);
 }catch(e){button.disabled=false;button.textContent='Remove';$('savedCollectionStatus').className='saved-collection-status error-text';$('savedCollectionStatus').textContent=e.message}
}
async function createSavedGroup(){
 if(demoMode||savedCollectionBusy)return;
 const name=$('newSavedGroupName').value.trim();
 if(!name){$('savedCollectionStatus').className='saved-collection-status error-text';$('savedCollectionStatus').textContent='Enter a name for the new group.';return}
 $('createSavedGroup').disabled=true;
 try{
  const response=await fetch('/api/collection/groups/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not create the saved-sticker group');
  savedCollectionGroups=Array.isArray(data.groups)?data.groups:savedCollectionGroups;
  if(data.group?.id)savedCollectionGroupFilter=data.group.id;
  $('newSavedGroupName').value='';$('newSavedGroupForm').classList.add('hidden');
  renderSavedCollection(savedCollectionItems);
  $('savedCollectionStatus').className='saved-collection-status';$('savedCollectionStatus').textContent=`Created “${data.group?.name||name}”. New saves still start in Ungrouped; use Move to… to organize selected stickers.`;
 }catch(e){$('savedCollectionStatus').className='saved-collection-status error-text';$('savedCollectionStatus').textContent=e.message}
 finally{updateSavedCollectionGroupControls()}
}
async function moveSavedStickersToGroup(groupId,idsOverride=null){
 if(demoMode||savedCollectionBusy)return;
 const requested=Array.isArray(idsOverride)?new Set(idsOverride):savedCollectionSelection;
 const ids=savedCollectionItems.filter(item=>requested.has(item.id)&&item.available).map(item=>item.id);
 const group=savedCollectionGroups.find(item=>item.id===groupId);
 if(!ids.length){$('savedCollectionStatus').className='saved-collection-status error-text';$('savedCollectionStatus').textContent='Select available saved stickers to move.';return}
 if(!group){$('savedCollectionStatus').className='saved-collection-status error-text';$('savedCollectionStatus').textContent='Choose a destination group.';return}
 closeSavedContextMenus();$('openSavedMoveMenu').disabled=true;
 try{
  const response=await fetch('/api/collection/groups/move',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids,groupId})});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not move the selected stickers');
  const moved=Number(data.moved)||0;
  if(moved){if(Array.isArray(idsOverride))ids.forEach(id=>savedCollectionSelection.delete(id));else savedCollectionSelection.clear()}
  savedCollectionGroupFilter=groupId;await loadSavedCollection();$('savedCollectionStatus').className='saved-collection-status';
  $('savedCollectionStatus').textContent=moved?`Moved ${moved} sticker${moved===1?'':'s'} to “${group.name}”.`:`The selected stickers are already in “${group.name}”.`;
  if(moved&&data.undoToken)showSavedUndo(data.undoToken,`Moved ${moved} sticker${moved===1?'':'s'} to “${group.name}”. Undo is available for 10 minutes.`);
 }catch(e){$('savedCollectionStatus').className='saved-collection-status error-text';$('savedCollectionStatus').textContent=e.message}
 finally{updateSavedCollectionGroupControls();updateSavedCollectionSelection()}
}
async function deleteSavedGroup(groupId=savedCollectionGroupFilter){
 if(demoMode||savedCollectionBusy)return;
 const group=savedCollectionGroups.find(item=>item.id===groupId);
 if(!group||group.id==='ungrouped')return;
 if(!window.confirm(`Delete “${group.name}”? Its saved stickers will move to Ungrouped.`))return;
 closeSavedContextMenus();
 try{
  const response=await fetch('/api/collection/groups/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({groupId})});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not delete the group');
  savedCollectionGroupFilter='all';await loadSavedCollection();
  $('savedCollectionStatus').className='saved-collection-status';$('savedCollectionStatus').textContent=`Deleted “${data.name||group.name}”. ${Number(data.moved)||0} sticker${Number(data.moved)===1?' was':'s were'} moved to Ungrouped.`;
 }catch(e){$('savedCollectionStatus').className='saved-collection-status error-text';$('savedCollectionStatus').textContent=e.message}
 finally{updateSavedCollectionGroupControls();updateSavedCollectionSelection()}
}
async function createPackFromSaved(){
 if(demoMode||savedCollectionBusy)return;
 const selected=savedCollectionItems.filter(item=>savedCollectionSelection.has(item.id)&&item.available);
 const title=$('collectionPackName').value.trim();
 if(!title){$('savedCollectionStatus').className='saved-collection-status error-text';$('savedCollectionStatus').textContent='Enter a name for your WhatsApp pack.';return}
 const hasStatic=selected.some(item=>item.kind!=='Animated'),hasAnimated=selected.some(item=>item.kind==='Animated');
 if(hasStatic&&hasAnimated){$('savedCollectionStatus').className='saved-collection-status error-text';$('savedCollectionStatus').textContent='Choose only static or only animated stickers. WhatsApp cannot mix them; this action creates one pack.';return}
 if(!savedCollectionSelectionValid()){$('savedCollectionStatus').className='saved-collection-status error-text';$('savedCollectionStatus').textContent='Select at least 3 stickers of one type to create one WhatsApp pack.';return}
 showSavedCollectionFlow();$('resultPanel').classList.add('hidden');setSavedCollectionBusy(true);savedCollectionJobStatus='queued';
 $('savedCollectionStatus').className='saved-collection-status';$('savedCollectionStatus').textContent='Starting your saved-sticker pack…';error('');warning('');
 try{
  const response=await fetch('/api/collection/convert',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({selected:selected.map(item=>item.id),title})});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not start your saved-sticker pack');
  activeJobId=data.id;sessionStorage.setItem('stickerStudioJob',data.id);
  showJob({id:data.id,status:'queued',phase:'Starting',message:'Preparing your saved stickers…',done:0,total:selected.length,packs:[],source:'Collection',title,isAdult:selected.some(item=>item.isAdult)});poll(data.id);
 }catch(e){setSavedCollectionBusy(false);savedCollectionJobStatus='failed';$('savedCollectionStatus').className='saved-collection-status error-text';$('savedCollectionStatus').textContent=e.message;error(e.message)}
}
function setStickerFilter(filter){

 if(!['all','static','animated'].includes(filter)||filter===stickerFilter)return;
 stickerFilter=filter;updateStickerFilters();renderStickerPage();
}
function renderSelection(job){
 window.currentStickerLoading=job.status==='running'&&job.phase==='Preparing previews';
 const samePack=job.source==='Fullyst'&&job.sourceSlug&&job.sourceSlug===window.currentFullystSlug;
 const selectedSources=new Set();
 if(samePack)for(const old of (window.currentStickers||[]))if(selectedStickerIndexes.has(Number(old.index)))selectedSources.add(Number(old.sourceIndex)||Number(old.index)+1);
 window.currentStickers=job.stickers||[];window.currentStickerJobId=job.id;
 if(window.currentSavedJobId!==job.id){window.currentSavedJobId=job.id;window.currentSavedStickerIndexes=new Set()}
 if(samePack)selectedStickerIndexes=new Set(window.currentStickers.filter(s=>selectedSources.has(Number(s.sourceIndex)||Number(s.index)+1)).map(s=>Number(s.index)));
 else{selectedStickerIndexes.clear();focusedStickerIndex=null;lastClickedStickerIndex=null}
 window.currentFullystSlug=job.source==='Fullyst'?(job.sourceSlug||''):'';
 window.currentFullystIsAdult=job.source==='Fullyst'&&!!job.isAdult;
 window.currentFullystTitle=job.source==='Fullyst'?(job.title||job.sourceSlug||'Fullyst pack'):'';
 window.currentFailedCount=job.source==='Fullyst'?Number(job.failedCount||0):0;
 window.currentPackIsAdult=!!job.isAdult;
 window.currentPackPreviewsHidden=hideAdultContent&&job.source==='Fullyst'&&!!job.isAdult;
 stickerFilter='all';
 const selectionTitle=job.title||'Fetched stickers';$('selectionTitle').textContent=window.currentPackIsAdult?`${selectionTitle} · 18+`:selectionTitle;
 updateStickerFilters();renderStickerPage();
 $('selectionPanel').classList.remove('hidden');$('resultPanel').classList.add('hidden');
}
function renderResults(job){
 const root=$('resultGrid');root.replaceChildren();
 for(const pack of (job.packs||[])){
   const card=document.createElement('article');card.className='result-card';
   const title=document.createElement('h3');title.textContent=pack.name||pack.fileName;
   const meta=document.createElement('div');meta.className='result-meta';meta.textContent=`${pack.kind} · ${pack.count} stickers · ${pack.sizeLabel}${job.isAdult?' · 18+':''}`;
   const a=document.createElement('a');a.className='download';a.href=pack.downloadUrl;a.textContent='↓ Download .wastickers';
   const grid=document.createElement('div'),hidePreview=hideAdultContent&&job.source==='Fullyst'&&!!job.isAdult;grid.className='pack-preview';
   for(const [previewIndex,src] of (pack.previewUrls||[]).entries()){
    if(hidePreview){grid.append(hiddenPreview('pack-preview-hidden','Fullyst preview hidden while adult-content hiding is enabled'));continue}
    const img=document.createElement('img');img.loading='lazy';img.alt='Created sticker preview';startPreviewLoading(img);applyAdultBlur(img,!!job.isAdult);img.onerror=()=>failPreview(img);img.src=src;attachHoldPreview(img,src,src,`${pack.name||'Created pack'} · Sticker ${previewIndex+1}`,!!job.isAdult);grid.append(img)
   }
   card.append(title,meta,a,grid);root.append(card);
 }
 $('resultMessage').textContent=job.message||'';
 $('resultPanel').classList.remove('hidden');$('selectionPanel').classList.add('hidden');
}
function renderFullystPacks(packs){
 fullystCurrentPacks=Array.isArray(packs)?packs:[];
 const visiblePacks=hideAdultContent?fullystCurrentPacks.filter(pack=>!pack.isAdult):fullystCurrentPacks;
 const root=$('fullystGrid');root.replaceChildren();
 for(const pack of visiblePacks){
  const card=document.createElement('article');card.className='fullyst-card';
  const img=document.createElement('img');img.className='fullyst-sample';img.loading='lazy';img.decoding='async';img.alt=pack.isAdult?'18+ sticker preview':'Sticker sample';
  const isAnimatedPack=pack.isAnimated===true||/animated|video/i.test(String(pack.kind||''));
  const animatedPreviewUrl=isAnimatedPack?`/api/fullyst/animated-preview?slug=${encodeURIComponent(pack.slug||'')}`:'';
  if(animatedPreviewUrl||pack.sampleUrl){startPreviewLoading(img);applyAdultBlur(img,!!pack.isAdult);img.onerror=()=>{
   if(animatedPreviewUrl&&pack.sampleUrl&&!img.dataset.animatedFallback){img.dataset.animatedFallback='1';startPreviewLoading(img);img.src=pack.sampleUrl;return}
   failPreview(img)
  };img.src=animatedPreviewUrl||pack.sampleUrl}
  else img.classList.add('hidden')
  const body=document.createElement('div');body.className='fullyst-card-body';
  const title=document.createElement('h3');title.textContent=pack.title||pack.slug;
  const meta=document.createElement('div');meta.className='meta';meta.textContent=`${pack.kind||'Sticker pack'}${pack.isAdult?' · 18+':''} · ${pack.slug}`;
  const button=document.createElement('button');button.type='button';button.className='cta secondary';button.textContent='View pack';
  button.addEventListener('click',()=>fetchFullystPack(pack.slug,button,!!pack.isAdult,pack.title||pack.slug));
  body.append(title,meta);
  if(pack.isAdult){const badge=document.createElement('span');badge.className='adult-badge';badge.textContent=adultPreviewEnabled()?'18+ preview':'18+ · blurred';body.append(badge)}
  body.append(button);card.append(img,body);root.append(card);
 }
 return visiblePacks.length;
}
function updateFullystVisitStatus(message='',wasVisited=false){
 const status=$('fullystVisitStatus');status.classList.toggle('repeat',!!wasVisited);
 if(fullystVisitHistoryError){status.textContent=fullystVisitHistoryError;return}
 if(fullystQuery){status.textContent=`${fullystVisitedPageCount.toLocaleString()} unfiltered catalog pages visited; search-result pages are not tracked.`;return}
 const total=fullystPageCount>1?` of ${Number(fullystPageCount).toLocaleString()}`:'';
 status.textContent=`${message?message+' · ':''}${fullystVisitedPageCount.toLocaleString()}${total} unfiltered catalog pages visited.`;
}
async function loadFullystVisitHistory(){
 if(fullystVisitHistoryLoaded)return;
 fullystVisitHistoryLoaded=true;
 try{
  const response=await fetch('/api/fullyst/visited-pages',{cache:'no-store'});const data=await response.json();
  if(!response.ok)throw new Error(data.error||'Could not load visited-page history');
  const pages=Array.isArray(data.pages)?data.pages.map(Number).filter(page=>Number.isInteger(page)&&page>0):[];
  fullystVisitedPages=new Set(pages);fullystVisitedPageCount=Number(data.count)||pages.length;fullystVisitHistoryError='';
  updateFullystVisitStatus();
 }catch(e){fullystVisitHistoryLoaded=false;fullystVisitHistoryError='Page history is unavailable; catalog browsing still works.';updateFullystVisitStatus()}
}
async function markFullystPageVisited(page){
 if(fullystQuery){updateFullystVisitStatus();return}
 try{
  const response=await fetch('/api/fullyst/visited-pages',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({page:Number(page)})});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not save this page');
  const wasVisited=!!data.alreadyVisited;fullystVisitedPages.add(Number(page));
  fullystVisitedPageCount=Number(data.count)||fullystVisitedPages.size;fullystVisitHistoryError='';
  updateFullystVisitStatus(wasVisited?`Page ${Number(page).toLocaleString()} was already visited.`:`Saved page ${Number(page).toLocaleString()}.`,wasVisited);
 }catch(e){fullystVisitHistoryError='Could not save visited pages; check local data-folder access.';updateFullystVisitStatus()}
}
function updateFullystSearchStatus(){
 if(fullystSearching)return;
 const total=fullystCurrentPacks.length,visible=hideAdultContent?fullystCurrentPacks.filter(pack=>!pack.isAdult).length:total,hiddenAdults=total-visible;
 const safeNote=hideAdultContent?' Unmarked pack previews remain visible.':'';
 if(visible)$('fullystSearchStatus').textContent=`${visible} pack${visible===1?'':'s'} on this page${fullystQuery?` matching “${fullystQuery}”`:''}.${hiddenAdults?` ${hiddenAdults} adult pack${hiddenAdults===1?'':'s'} hidden in Settings.`:''}${safeNote}`;
 else if(total)$('fullystSearchStatus').textContent=`No visible packs on this page. Adult-marked packs are hidden in Settings.${safeNote}`;
 else $('fullystSearchStatus').textContent='No packs found. Try another search.';
}
function updateFullystPager(){
 const page=Math.max(1,Number(fullystPage)||1),pageCount=Math.max(page,Number(fullystPageCount)||1);
 $('fullystPageLabel').textContent=pageCount>1?`Page ${page} of ${pageCount}`:`Page ${page}`;
 $('fullystPageInput').value=String(page);$('fullystPageInput').min='1';$('fullystPageInput').max=String(pageCount);
 $('fullystPager').classList.toggle('hidden',!fullystCurrentPacks.length&&page<=1);
 $('fullystPrev').disabled=demoMode||page<=1;$('fullystNext').disabled=demoMode||!fullystHasMore;
}
function updateFullystSearchControls(){$('backToCatalog').classList.toggle('hidden',!fullystQuery)}
function backToCatalog(){
 if(demoMode||fullystSearching)return;
 $('fullystQuery').value='';
 const saved=fullystCatalogReturnState;fullystCatalogReturnState=null;
 if(saved){
  fullystQuery=saved.query||'';fullystPage=Number(saved.page)||1;fullystPageCount=Number(saved.pageCount)||1;
  fullystHasMore=!!saved.hasMore;fullystCurrentPacks=Array.isArray(saved.packs)?saved.packs:[];
  $('fullystGrid').replaceChildren();renderFullystPacks(fullystCurrentPacks);
  updateFullystSearchControls();updateFullystPager();updateFullystSearchStatus();
  const wasVisited=fullystVisitedPages.has(fullystPage);updateFullystVisitStatus(wasVisited?`Page ${fullystPage.toLocaleString()} is in your history.`:'',wasVisited);
  $('fullystQuery').focus();return;
 }
 fullystQuery='';searchFullyst(true);
}
function updateFullystPageJumpHint(){
 if(fullystQuery){updateFullystVisitStatus();return}
 const raw=String($('fullystPageInput').value||'').trim(),page=Number(raw);
 if(/^\d+$/.test(raw)&&Number.isInteger(page)&&fullystVisitedPages.has(page))
  updateFullystVisitStatus(`Page ${page.toLocaleString()} was already visited.`,true);
 else updateFullystVisitStatus();
}
function jumpFullystPage(){
 if(demoMode||fullystSearching)return;
 const raw=String($('fullystPageInput').value||'').trim(),page=Number(raw);
 if(!/^\d+$/.test(raw)||!Number.isInteger(page)||page<1||page>fullystPageCount){
  $('fullystSearchStatus').textContent=`Enter a page from 1 to ${fullystPageCount}.`;return;
 }
 if(page!==fullystPage)return searchFullyst(false,page);
}
async function searchFullyst(reset=true,targetPage=null){
 if(demoMode||fullystSearching)return;
 if(!fullystVisitHistoryLoaded)await loadFullystVisitHistory();
 const query=$('fullystQuery').value.trim();
 if(!reset&&query!==fullystQuery)reset=true;
 const page=reset?1:Number(targetPage??fullystPage+1);
 if(!reset&&(!Number.isInteger(page)||page<1||page>fullystPageCount)){
  $('fullystSearchStatus').textContent=`Enter a page from 1 to ${fullystPageCount}.`;updateFullystPager();return;
 }
 if(reset){
  if(query&&!fullystQuery&&fullystLoaded){fullystCatalogReturnState={query:fullystQuery,page:fullystPage,pageCount:fullystPageCount,hasMore:fullystHasMore,packs:fullystCurrentPacks.slice()}}
  if(!query)fullystCatalogReturnState=null;
  fullystQuery=query;fullystPage=0;fullystPageCount=1;fullystHasMore=false;fullystCurrentPacks=[];
  $('fullystGrid').replaceChildren();$('fullystPager').classList.add('hidden');updateFullystSearchControls();
 }
 fullystSearching=true;$('fullystSearchButton').disabled=true;$('fullystPrev').disabled=true;$('fullystNext').disabled=true;$('fullystGoPage').disabled=true;$('fullystPageInput').disabled=true;$('backToCatalog').disabled=true;
 $('fullystSearchStatus').textContent=reset?'Searching Fullyst…':`Loading page ${page}…`;
 try{
  const params=new URLSearchParams({q:query,page:String(page)});
  const r=await fetch('/api/fullyst/search?'+params.toString(),{cache:'no-store'});
  const data=await r.json();if(!r.ok)throw new Error(data.error||'Catalog search failed');
  const packs=Array.isArray(data.packs)?data.packs:[];
  $('fullystGrid').replaceChildren();renderFullystPacks(packs);
  fullystPage=Number(data.page||page);fullystPageCount=Math.max(fullystPage,Number(data.pageCount||fullystPage));
  fullystHasMore=Boolean(data.hasMore);fullystQuery=query;
  if(query)updateFullystVisitStatus();else await markFullystPageVisited(fullystPage);
  updateFullystSearchControls();updateFullystPager();updateFullystSearchStatus();
 }catch(e){$('fullystSearchStatus').textContent='Could not search Fullyst: '+e.message}
 finally{
  fullystSearching=false;$('fullystSearchButton').disabled=demoMode;$('fullystGoPage').disabled=demoMode;$('fullystPageInput').disabled=demoMode;$('backToCatalog').disabled=demoMode;
  updateFullystPager();
 }
}
async function fetchFullystPack(slug,button,isAdult=false,title=''){
 if(demoMode)return;
 openPackModal(slug,title||window.currentFullystTitle||slug,isAdult);
 error('');warning('');clearActivityLog();busy(true);
 $('statusTitle').textContent='Fetching Fullyst pack…';$('statusMessage').textContent='Contacting Fullyst…';pill('FETCHING');
 setPackModalStatus('Connecting to Fullyst…','Loading pack details and sticker previews.',true);
 if(button)button.disabled=true;
 try{
  const r=await fetch('/api/fullyst/fetch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slug,is_adult:!!isAdult})});
  const data=await r.json();if(!r.ok)throw new Error(data.error||'Could not fetch the Fullyst pack');
  activeJobId=data.id;sessionStorage.setItem('stickerStudioJob',data.id);
  if(button)button.disabled=false;
  showJob({id:data.id,status:'queued',phase:'Starting',message:'Starting Fullyst import…',done:0,total:0,source:'Fullyst',sourceSlug:slug,title:title||slug,isAdult:!!isAdult});poll(data.id);
 }catch(e){error(e.message);busy(false);pill('ERROR',true);$('statusTitle').textContent='Could not start';$('statusMessage').textContent='The pack could not be started.';setPackModalStatus('Could not start',e.message,false);if(window.currentFullystSlug===slug&&(window.currentStickers||[]).length)$('selectionPanel').classList.remove('hidden');if(button)button.disabled=false}
}
function showJob(job){
 activeJobId=job.id||activeJobId;window.currentJob=job;
 if(Array.isArray(job.activityLog))renderActivityLog(job);
 const isRunning=job.status==='queued'||job.status==='running';
 $('statusTitle').textContent=job.status==='ready'?'Pack fetched':job.status==='done'?(job.packs?.length?'Pack created':'No pack created'):job.status==='failed'?'Could not finish':isRunning?'Working…':'Ready when you are';
 $('statusMessage').textContent=job.message||'';
 pill(job.status==='ready'?'SELECT':job.status==='done'?(job.packs?.length?'READY':'CHECK'):job.status==='failed'?'ERROR':isRunning?'WORKING':'READY',job.status==='failed');
 $('progressWrap').classList.toggle('hidden',!isRunning);
 const total=Number(job.total||0),done=Number(job.done||0);
 $('progressFill').style.width=total?Math.min(100,Math.round(done/total*100))+'%':(job.phase==='Downloading'?'6%':'0%');
 $('progressLabel').textContent=job.phase||'Preparing…';$('progressCount').textContent=total?`${done} / ${total}`:'';
 error(job.error||'');warning(job.rememberWarning||'');
  busy(isRunning);
  const preparingPreviews=isRunning&&job.status==='running'&&job.phase==='Preparing previews'&&Array.isArray(job.stickers);
  if(preparingPreviews){
   if(progressiveStickerJobId!==job.id){progressiveStickerJobId=job.id;renderSelection(job)}
   else{window.currentStickerLoading=true;updateProgressiveStickerPreviews(job)}
   $('selectionPanel').classList.remove('hidden');
  }else if(isRunning&&progressiveStickerJobId!==job.id)$('selectionPanel').classList.add('hidden');
  if(job.status==='ready'){progressiveStickerJobId=null;window.currentStickerLoading=false;renderSelection(job)}
 if(job.status==='done')renderResults(job);
 if(job.status==='failed'){
  if(progressiveStickerJobId===job.id){progressiveStickerJobId=null;window.currentStickerLoading=false;window.currentStickers=[];selectedStickerIndexes.clear()}
  $('resultPanel').classList.add('hidden');
  const canKeepSelection=job.source!=='Collection'&&(window.currentStickers||[]).length>0&&(job.source!=='Fullyst'||job.sourceSlug===window.currentFullystSlug);
  $('selectionPanel').classList.toggle('hidden',!canKeepSelection);
 }
 const failedCount=job.status==='ready'?Number(job.failedCount||0):Number(window.currentFailedCount||0);
 const retrySlug=job.status==='ready'?job.sourceSlug:window.currentFullystSlug;
 const retryVisible=!!retrySlug&&failedCount>0&&job.status!=='done';
 $('retryFailed').classList.toggle('hidden',!retryVisible);
 $('retryFailed').textContent=retryVisible?`Retry ${failedCount} skipped`:'Retry skipped stickers';
 $('retryFailed').disabled=demoMode||isRunning;
 updatePackModalStatus(job);
 if(job.source==='Collection'){
  savedCollectionJobStatus=job.status;setSavedCollectionBusy(isRunning);
  if(!$('savedCollectionModal').classList.contains('hidden')){
   showSavedCollectionFlow();$('savedCollectionStatus').className=job.status==='failed'?'saved-collection-status error-text':'saved-collection-status';
   $('savedCollectionStatus').textContent=job.status==='done'?(job.message||'Your saved-sticker pack is ready.'):
    job.status==='failed'?(job.error||job.message||'Could not create your pack.'):(job.message||'Creating your saved-sticker pack…');
  }
 }
}
async function poll(id){
 try{
  const r=await fetch('/api/job/'+encodeURIComponent(id),{cache:'no-store'});
  if(!r.ok)throw new Error('This app session expired. Fetch the pack again.');
  const job=await r.json();showJob(job);
  if(job.status==='queued'||job.status==='running')pollTimer=setTimeout(()=>poll(id),650);
  else if(job.status==='ready')sessionStorage.setItem('stickerStudioJob',id);
  else sessionStorage.removeItem('stickerStudioJob');
 }catch(e){error(e.message);$('statusMessage').textContent='Please fetch the pack again.';busy(false);sessionStorage.removeItem('stickerStudioJob');if(!$('packModal').classList.contains('hidden'))setPackModalStatus('Reconnect required',e.message,false)}
}
async function startFetch(e){
 e.preventDefault();error('');warning('');clearActivityLog();$('selectionPanel').classList.add('hidden');$('resultPanel').classList.add('hidden');
 if(demoMode){error('This is a view-only preview. Run the app locally to fetch a pack.');return}
 busy(true);$('statusTitle').textContent='Fetching pack…';$('statusMessage').textContent='Connecting to Telegram…';pill('FETCHING');
 try{
  const body={pack_link:$('packLink').value.trim(),token:$('botToken').value,remember_token:$('rememberToken').checked};
  const r=await fetch('/api/fetch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await r.json();if(!r.ok)throw new Error(data.error||'Could not fetch pack');
  $('botToken').value='';activeJobId=data.id;sessionStorage.setItem('stickerStudioJob',data.id);
  showJob({id:data.id,status:'queued',phase:'Starting',message:'Starting…',done:0,total:0});poll(data.id);
 }catch(e){error(e.message);busy(false);pill('ERROR',true);$('statusTitle').textContent='Could not start'}
}
async function startConversion(mode){
 if(!activeJobId||demoMode)return;
 error('');warning('');busy(true);$('statusTitle').textContent='Creating pack…';pill('CONVERTING');
 if(!$('packModal').classList.contains('hidden'))setPackModalStatus('Starting conversion…','Preparing your WhatsApp sticker pack…',true);
 const selected=mode==='selected'?selectedIndexes():[];
 try{
  const r=await fetch('/api/convert',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:activeJobId,mode,selected})});
  const data=await r.json();if(!r.ok)throw new Error(data.error||'Could not start conversion');
  sessionStorage.setItem('stickerStudioJob',data.id);showJob({id:data.id,status:'queued',phase:'Starting',message:'Creating your pack…',done:0,total:0,packs:[]});poll(data.id);
 }catch(e){error(e.message);busy(false);pill('SELECT',false);$('statusTitle').textContent='Choose stickers';if(!$('packModal').classList.contains('hidden'))setPackModalStatus('Could not start conversion',e.message,false)}
}
$('closePackModal').addEventListener('click',closePackModal);
$('packModalBackdrop').addEventListener('click',closePackModal);
$('closeStickerPreview').addEventListener('click',closeStickerPreview);
$('stickerPreviewBackdrop').addEventListener('click',closeStickerPreview);
$('openSettings').addEventListener('click',openSettings);
$('closeSettings').addEventListener('click',closeSettings);
$('settingsBackdrop').addEventListener('click',closeSettings);
$('openSavedCollection').addEventListener('click',openSavedCollection);
$('closeSavedCollection').addEventListener('click',closeSavedCollection);
$('savedCollectionBackdrop').addEventListener('click',closeSavedCollection);
$('openNewSavedGroup').addEventListener('click',showNewSavedGroupForm);
$('cancelNewSavedGroup').addEventListener('click',cancelNewSavedGroup);
$('createSavedGroup').addEventListener('click',createSavedGroup);
$('newSavedGroupName').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();event.stopPropagation();createSavedGroup()}else if(event.key==='Escape'){event.preventDefault();event.stopPropagation();cancelNewSavedGroup()}});
$('openSavedMoveMenu').addEventListener('click',toggleSavedGroupMoveMenu);
$('undoSavedAction').addEventListener('click',undoSavedAction);
document.addEventListener('click',event=>{
 if(event.target.closest?.('.saved-context-menu')||event.target.closest?.('#savedGroupMoveMenu')||event.target.closest?.('.saved-group-more')||event.target.id==='openSavedMoveMenu')return;
 closeSavedContextMenus();
});
$('selectAllSaved').addEventListener('change',()=>{
 const visible=savedCollectionVisibleItems().filter(item=>item.available);
 if($('selectAllSaved').checked)visible.forEach(item=>savedCollectionSelection.add(item.id));
 else visible.forEach(item=>savedCollectionSelection.delete(item.id));
 document.querySelectorAll('#savedStickerGrid input[type="checkbox"]').forEach(input=>{input.checked=savedCollectionSelection.has(input.id.replace('saved-pick-',''))});
 updateSavedCollectionSelection();
});
$('clearSavedSelection').addEventListener('click',()=>{savedCollectionSelection.clear();document.querySelectorAll('#savedStickerGrid input[type="checkbox"]').forEach(input=>input.checked=false);updateSavedCollectionSelection()});
$('createSavedPack').addEventListener('click',createPackFromSaved);
document.addEventListener('pointerup',finishHeldPreview,true);
document.addEventListener('pointercancel',finishHeldPreview,true);
document.addEventListener('click',dismissSuppressedHoldClick,true);
document.addEventListener('keydown',event=>{
 if(event.key==='Escape'){
  if(!$('savedContextMenu').classList.contains('hidden')||!$('savedGroupMoveMenu').classList.contains('hidden')){closeSavedContextMenus();event.preventDefault();return}
  if(!$('newSavedGroupForm').classList.contains('hidden')){cancelNewSavedGroup();event.preventDefault();return}
  if(!$('stickerPreviewModal').classList.contains('hidden')){closeStickerPreview();event.preventDefault()}
  else if(!$('settingsModal').classList.contains('hidden')){closeSettings();event.preventDefault()}
  else if(!$('savedCollectionModal').classList.contains('hidden')){closeSavedCollection();event.preventDefault()}
  else if(!$('packModal').classList.contains('hidden')){closePackModal();event.preventDefault()}
  return;
 }
 if(event.key!=='Tab')return;
 const dialog=!$('stickerPreviewModal').classList.contains('hidden')?document.querySelector('#stickerPreviewModal .preview-dialog'):!$('settingsModal').classList.contains('hidden')?document.querySelector('#settingsModal .settings-dialog'):!$('savedCollectionModal').classList.contains('hidden')?document.querySelector('#savedCollectionModal .saved-collection-dialog'):!$('packModal').classList.contains('hidden')?document.querySelector('#packModal .pack-dialog'):null;
 if(!dialog)return;
 const menuItems=!$('savedContextMenu').classList.contains('hidden')?[...$('savedContextMenu').querySelectorAll('button:not([disabled])')]:[];
 const items=[...dialog.querySelectorAll('button:not([disabled]),input:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])'),...menuItems].filter(item=>!item.closest('.hidden'));
 if(!items.length)return;
 const first=items[0],last=items[items.length-1];
 if(event.shiftKey&&document.activeElement===first){last.focus();event.preventDefault()}
 else if(!event.shiftKey&&document.activeElement===last){first.focus();event.preventDefault()}
});
$('fetchForm').addEventListener('submit',startFetch);
$('showFullyst').addEventListener('click',async()=>{
 const panel=$('fullystBrowser');panel.classList.toggle('hidden');
 if(!panel.classList.contains('hidden')){panel.scrollIntoView({behavior:'smooth',block:'start'});if(!fullystLoaded){fullystLoaded=true;await searchFullyst(true)}}
});
$('hideFullyst').addEventListener('click',()=>$('fullystBrowser').classList.add('hidden'));
$('adultToggle').addEventListener('change',()=>{
 showAdultPreviews=$('adultToggle').checked&&!hideAdultContent;
 renderFullystPacks(fullystCurrentPacks);
 if((window.currentStickers||[]).length)renderStickerPage();
 refreshAdultBlur();
});
$('hideAdultToggle').addEventListener('change',()=>{
 hideAdultContent=$('hideAdultToggle').checked;
 if(hideAdultContent){showAdultPreviews=false;$('adultToggle').checked=false}
 $('adultToggle').disabled=hideAdultContent||demoMode;
 renderFullystPacks(fullystCurrentPacks);updateFullystSearchStatus();
 window.currentPackPreviewsHidden=hideAdultContent&&window.currentJob?.source==='Fullyst'&&!!window.currentJob?.isAdult;
 if((window.currentStickers||[]).length)renderStickerPage();
 if(window.currentJob?.status==='done'&&window.currentJob.source==='Fullyst'&&!$('resultPanel').classList.contains('hidden'))renderResults(window.currentJob);
 refreshAdultBlur();
});
$('fullystSearchForm').addEventListener('submit',e=>{e.preventDefault();searchFullyst(true)});
$('fullystPrev').addEventListener('click',()=>searchFullyst(false,fullystPage-1));
$('fullystNext').addEventListener('click',()=>searchFullyst(false,fullystPage+1));
$('fullystGoPage').addEventListener('click',jumpFullystPage);
$('fullystPageInput').addEventListener('input',updateFullystPageJumpHint);
$('fullystPageInput').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();jumpFullystPage()}});
$('backToCatalog').addEventListener('click',backToCatalog);
$('toggleToken').addEventListener('click',()=>{const x=$('botToken'),show=x.type==='password';x.type=show?'text':'password';$('toggleToken').textContent=show?'Hide':'Show'});
$('selectAll').addEventListener('change',()=>{lastClickedStickerIndex=null;const all=window.currentStickers||[];if($('selectAll').checked)all.forEach(s=>selectedStickerIndexes.add(Number(s.index)));else selectedStickerIndexes.clear();renderStickerPage()});
$('clearSelection').addEventListener('click',()=>{selectedStickerIndexes.clear();lastClickedStickerIndex=null;renderStickerPage()});
$('retryFailed').addEventListener('click',()=>{if(window.currentFullystSlug)fetchFullystPack(window.currentFullystSlug,$('retryFailed'),!!window.currentFullystIsAdult)});
document.querySelectorAll('[data-sticker-filter]').forEach(button=>button.addEventListener('click',()=>setStickerFilter(button.dataset.stickerFilter)));
document.querySelectorAll('[data-saved-filter]').forEach(button=>button.addEventListener('click',()=>setSavedCollectionFilter(button.dataset.savedFilter)));
$('createSelected').addEventListener('click',()=>startConversion('selected'));
$('convertAll').addEventListener('click',()=>startConversion('full'));
$('rememberToken').addEventListener('change',()=>{
 if($('rememberToken').checked)$('rememberNote').textContent='Windows encrypts the token for this user account.';
 else if(config.tokenSaved)$('rememberNote').textContent='It can be used for this fetch, then removed from this PC.';
 else $('rememberNote').textContent='The token will be used once and not saved.';
});
$('forgetToken').addEventListener('click',async()=>{
  try{const r=await fetch('/api/token/forget',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const d=await r.json();if(!r.ok)throw new Error(d.error||'Could not forget token');
  config.tokenSaved=false;$('rememberToken').checked=false;$('forgetToken').classList.add('hidden');$('rememberNote').textContent='Saved token removed from this PC.';$('tokenHint').textContent='Enter a Bot token to fetch stickers.';
 }catch(e){error(e.message)}
});
(async()=>{
 try{
  config=await(await fetch('/api/config',{cache:'no-store'})).json();demoMode=!!config.demo;refreshSavedCount();
  if(demoMode){$('demoBanner').style.display='block';$('fetchButton').disabled=true;$('botToken').disabled=true;$('rememberToken').disabled=true;$('toggleToken').disabled=true;$('showFullyst').disabled=true;$('fullystQuery').disabled=true;$('fullystSearchButton').disabled=true;$('fullystPrev').disabled=true;$('fullystNext').disabled=true;$('fullystGoPage').disabled=true;$('fullystPageInput').disabled=true;$('backToCatalog').disabled=true;$('adultToggle').disabled=true;$('hideAdultToggle').disabled=true}
  if(!config.rememberSupported){$('rememberToken').disabled=true;$('rememberNote').textContent='Secure token saving is available on Windows only.'}
  if(config.tokenSaved){$('rememberToken').checked=true;$('forgetToken').classList.remove('hidden');$('tokenHint').textContent='A token is saved securely on this PC. Leave blank to reuse it, or enter a new one.';$('rememberNote').textContent='Windows encrypts it for this user account.'}
  else if(config.tokenConfigured){$('tokenHint').textContent='A token is configured in this local Python process; leave blank to use it.'}
  const id=sessionStorage.getItem('stickerStudioJob');
  if(id){await poll(id);return}
  const latest=await(await fetch('/api/latest',{cache:'no-store'})).json();if(latest.id)await poll(latest.id);
 }catch(_){$('statusMessage').textContent='The local app is ready. Reload this page to reconnect.'}
})();
</script>
</body>
</html>'''


def _json_bytes(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _copy_public_state(job: dict) -> dict:
    """Return only fields needed by the browser; never expose credentials or file paths."""
    keys = ("id", "status", "phase", "message", "done", "total", "packs", "error",
            "title", "source", "sourceSlug", "failedCount", "isAdult",
            "rememberWarning", "activityLog")
    result = {k: job.get(k) for k in keys if k in job}
    if job.get("status") == "ready" or (
            job.get("status") == "running" and job.get("phase") == "Preparing previews"
            and "stickers" in job):
        result["stickers"] = job.get("stickers", [])
    return result


def _job_stickers_for_picker(job_id: str, descriptors: list[dict],
                             thumbnails: list[bytes | None], *, preparing: bool = False) -> list[dict]:
    """Public picker metadata; during preparation, each thumbnail becomes available independently."""
    stickers = []
    for index, desc in enumerate(descriptors):
        sticker = ({k: desc[k] for k in ("index", "sourceIndex", "formatLabel", "kind", "emoji")}
                   | {"thumbUrl": f"/api/job/{job_id}/thumb/{desc['index']}",
                      "largePreviewUrl": f"/api/job/{job_id}/preview/{desc['index']}"})
        if desc["kind"] == "Animated":
            sticker["previewUrl"] = f"/api/job/{job_id}/preview/{desc['index']}"
        if preparing:
            sticker["previewReady"] = False
            sticker["previewAvailable"] = False
        else:
            thumbnail = thumbnails[index] if index < len(thumbnails) else None
            sticker["previewReady"] = True
            sticker["previewAvailable"] = bool(thumbnail)
        stickers.append(sticker)
    return stickers


def _append_activity_log(job: dict, phase: str, message: str) -> None:
    message = str(message or "").strip()
    phase = str(phase or "Activity").strip()
    if not message:
        return
    entries = job.setdefault("activityLog", [])
    if entries and entries[-1].get("phase") == phase and entries[-1].get("message") == message:
        return
    seq = int(job.get("_activitySeq", 0)) + 1
    job["_activitySeq"] = seq
    entries.append({"seq": seq, "time": datetime.now().strftime("%H:%M:%S"),
                    "phase": phase[:60], "message": message[:240]})
    if len(entries) > 600:
        del entries[:-600]


def _update(job_id: str, **fields) -> None:
    with STATE_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            old_phase = job.get("phase")
            old_message = job.get("message")
            old_status = job.get("status")
            job.update(fields)
            phase = job.get("phase") or "Activity"
            message = job.get("message") or ""
            if message and (phase != old_phase or message != old_message
                            or job.get("status") != old_status):
                _append_activity_log(job, phase, message)


def _cache_root(set_name: str) -> Path:
    key = hashlib.sha256(set_name.casefold().encode("utf-8")).hexdigest()[:16]
    return CACHE_DIR / key


def _cache_folder(set_name: str) -> Path:
    return _cache_root(set_name) / "raw"


def _remember_supported() -> bool:
    return os.name == "nt"


def _token_path() -> Path | None:
    if not _remember_supported():
        return None
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(root) / "StickerStudio" / "telegram-token.dpapi"


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    """Encrypt/decrypt bytes with per-Windows-user DPAPI. Never use machine scope."""
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI is not available on this operating system.")
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    from ctypes import wintypes
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    destination = DATA_BLOB()
    if protect:
        fn = crypt32.CryptProtectData
        fn.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR,
                       ctypes.POINTER(DATA_BLOB), wintypes.LPVOID, wintypes.LPVOID,
                       wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
        fn.restype = wintypes.BOOL
        ok = fn(ctypes.byref(source), "Sticker Studio Telegram token", None,
                None, None, 0x1, ctypes.byref(destination))
    else:
        description = wintypes.LPWSTR()
        fn = crypt32.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR),
                       ctypes.POINTER(DATA_BLOB), wintypes.LPVOID, wintypes.LPVOID,
                       wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
        fn.restype = wintypes.BOOL
        ok = fn(ctypes.byref(source), ctypes.byref(description), None,
                None, None, 0x1, ctypes.byref(destination))
        if description:
            local_free(ctypes.cast(description, ctypes.c_void_p))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        local_free(ctypes.cast(destination.pbData, ctypes.c_void_p))


def _load_saved_token() -> str:
    path = _token_path()
    if path is None or not path.exists():
        return ""
    try:
        return _dpapi(path.read_bytes(), protect=False).decode("utf-8").strip()
    except Exception:                                               # noqa: BLE001
        try:
            path.unlink()
        except OSError:
            pass
        return ""


def _save_token(token: str) -> None:
    path = _token_path()
    if path is None:
        raise RuntimeError("Secure token saving is available on Windows only.")
    encrypted = _dpapi(token.encode("utf-8"), protect=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_bytes(encrypted)
    os.replace(temp, path)


def _clear_saved_token() -> None:
    path = _token_path()
    if path is not None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _make_thumbnail(item: dict) -> bytes | None:
    """Make a small, local PNG preview from the first frame of any Telegram sticker type."""
    from io import BytesIO
    from PIL import Image

    blob = item["bytes"]
    fmt = item["format"]
    image = None
    resample = getattr(Image, "Resampling", Image).LANCZOS
    with tempfile.TemporaryDirectory(prefix="sticker_thumb_") as folder:
        folder = Path(folder)
        if fmt == "tgs":
            from rlottie_python import LottieAnimation
            source = folder / "sticker.tgs"
            source.write_bytes(blob)
            anim = LottieAnimation.from_tgs(str(source))
            width, height = anim.lottie_animation_get_size()
            scale = 160 / max(width, height, 1)
            image = anim.render_pillow_frame(frame_num=0,
                                             width=max(1, round(width * scale)),
                                             height=max(1, round(height * scale))).convert("RGBA")
        elif fmt == "webm":
            source, frame = folder / "sticker.webm", folder / "frame.png"
            source.write_bytes(blob)
            vf = ("scale=160:160:force_original_aspect_ratio=decrease,"
                  "pad=160:160:(ow-iw)/2:(oh-ih)/2:color=0x00000000,format=rgba")
            result = convert.run([convert.need_ffmpeg(), "-hide_banner", "-nostdin",
                                  "-loglevel", "error", "-y", "-threads", "1",
                                  "-i", str(source), "-vf", vf, "-frames:v", "1", str(frame)],
                                 timeout=30)
            if result.returncode != 0 or not frame.exists():
                return None
            image = Image.open(frame).convert("RGBA")
        else:
            image = Image.open(BytesIO(blob))
            try:
                image.seek(0)
            except Exception:
                pass
            image = image.convert("RGBA")

        image.thumbnail((160, 160), resample)
        canvas = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
        canvas.alpha_composite(image, ((160 - image.width) // 2, (160 - image.height) // 2))
        output = BytesIO()
        canvas.save(output, "PNG", optimize=True)
        return output.getvalue()


def _thumbnail_cache_path(item: dict) -> Path | None:
    """Build a versioned, content-addressed key for the static poster thumbnail only."""
    blob = item.get("bytes")
    fmt = str(item.get("format") or "").lower()
    if not isinstance(blob, bytes) or not blob or not fmt:
        return None
    key = hashlib.sha256(
        f"sticker-studio-thumbnail-v{THUMBNAIL_CACHE_VERSION}:{fmt}:".encode("utf-8")
        + blob
    ).hexdigest()
    return THUMBNAIL_CACHE_DIR / key[:2] / f"{key}.png"


def _read_cached_thumbnail(path: Path) -> bytes | None:
    png_signature = b"\x89PNG\r\n\x1a\n"
    try:
        if path.stat().st_size > THUMBNAIL_CACHE_MAX_ITEM_BYTES:
            path.unlink()
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if not data.startswith(png_signature):
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return data


def _write_cached_thumbnail(path: Path, data: bytes) -> None:
    png_signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(png_signature) or len(data) > THUMBNAIL_CACHE_MAX_ITEM_BYTES:
        return
    temp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_bytes(data)
        os.replace(temp_path, path)
    except OSError:
        # The cache is an optimization; a read-only or full disk must not break fetching.
        pass
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def _prune_thumbnail_cache(*, force: bool = False) -> None:
    """Keep the local poster cache bounded, pruning oldest entries only when needed."""
    global _THUMBNAIL_CACHE_LAST_MAINTENANCE
    now = time.monotonic()
    with _THUMBNAIL_CACHE_MAINTENANCE_LOCK:
        if not force and now - _THUMBNAIL_CACHE_LAST_MAINTENANCE < 60:
            return
        _THUMBNAIL_CACHE_LAST_MAINTENANCE = now
        try:
            files = []
            for path in THUMBNAIL_CACHE_DIR.rglob("*.png"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                files.append((stat.st_mtime_ns, stat.st_size, path))
        except OSError:
            return
        total_bytes = sum(size for _mtime, size, _path in files)
        if total_bytes <= THUMBNAIL_CACHE_MAX_BYTES:
            return
        for _mtime, size, path in sorted(files):
            if total_bytes <= THUMBNAIL_CACHE_MAX_BYTES:
                break
            try:
                path.unlink()
                total_bytes -= size
            except OSError:
                pass


def _get_or_create_thumbnail(item: dict) -> tuple[bytes | None, bool]:
    """Return a cached first-frame PNG when possible; never substitute for an animated preview."""
    path = _thumbnail_cache_path(item)
    if path:
        cached = _read_cached_thumbnail(path)
        if cached:
            return cached, True
    with THUMBNAIL_RENDER_SEMAPHORE:
        # Another request may have filled the same content key while this one waited.
        if path:
            cached = _read_cached_thumbnail(path)
            if cached:
                return cached, True
        thumbnail = _make_thumbnail(item)
        if path and thumbnail:
            _write_cached_thumbnail(path, thumbnail)
    if path and thumbnail:
        _prune_thumbnail_cache()
    return thumbnail, False


def _prepare_thumbnails(items: list[dict], progress_callback=None) -> tuple[list[bytes | None], int]:
    """Render poster thumbnails concurrently with a small CPU-aware worker limit."""
    if not items:
        return [], 0
    from concurrent.futures import ThreadPoolExecutor, as_completed

    thumbnails: list[bytes | None] = [None] * len(items)
    cache_hits = 0
    completed = 0
    workers = max(1, min(PREVIEW_RENDER_WORKERS, len(items)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sticker-thumbnails") as pool:
        futures = {pool.submit(_get_or_create_thumbnail, item): index
                   for index, item in enumerate(items)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                thumbnail, from_cache = future.result()
            except Exception:                                           # noqa: BLE001
                thumbnail, from_cache = None, False
            thumbnails[index] = thumbnail
            completed += 1
            cache_hits += int(from_cache)
            if progress_callback:
                progress_callback({"index": index, "thumbnail": thumbnail,
                                   "done": completed, "total": len(items),
                                   "cacheHits": cache_hits})
    _prune_thumbnail_cache(force=True)
    return thumbnails, cache_hits


def _make_animated_preview(item: dict) -> bytes | None:
    """Render a lightweight looping animated GIF preview for TGS/WebM/animated-WebP stickers."""
    from io import BytesIO
    from PIL import Image

    fmt = item.get("format")
    if fmt not in ("tgs", "webm", "webp"):
        return None
    frames = []
    frame_duration_ms = 125
    resample = getattr(Image, "Resampling", Image).LANCZOS
    with tempfile.TemporaryDirectory(prefix="sticker_anim_preview_") as folder:
        folder = Path(folder)
        if fmt == "tgs":
            from rlottie_python import LottieAnimation
            source = folder / "sticker.tgs"
            source.write_bytes(item["bytes"])
            anim = LottieAnimation.from_tgs(str(source))
            width, height = anim.lottie_animation_get_size()
            source_fps = float(anim.lottie_animation_get_framerate() or 30)
            total_frames = int(anim.lottie_animation_get_totalframe())
            if total_frames < 2 or source_fps <= 0:
                return None
            duration_s = min(3.0, total_frames / source_fps)
            frame_count = min(total_frames, 24, max(2, round(duration_s * 8)))
            frame_duration_ms = max(20, round(duration_s * 1000 / frame_count))
            scale = 160 / max(width, height, 1)
            rw, rh = max(1, round(width * scale)), max(1, round(height * scale))
            for i in range(frame_count):
                frame_num = min(total_frames - 1,
                                round(i * duration_s * source_fps / frame_count))
                image = anim.render_pillow_frame(frame_num=frame_num, width=rw, height=rh).convert("RGBA")
                image.thumbnail((160, 160), resample)
                canvas = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
                canvas.alpha_composite(image, ((160 - image.width) // 2,
                                               (160 - image.height) // 2))
                frames.append(canvas)
        elif fmt == "webm":
            source = folder / "sticker.webm"
            source.write_bytes(item["bytes"])
            pattern = folder / "frame_%03d.png"
            vf = ("fps=8,scale=160:160:force_original_aspect_ratio=decrease,"
                  "pad=160:160:(ow-iw)/2:(oh-ih)/2:color=0x00000000,format=rgba")
            result = convert.run([convert.need_ffmpeg(), "-hide_banner", "-nostdin",
                                  "-loglevel", "error", "-y", "-threads", "1",
                                  "-i", str(source), "-t", "3", "-vf", vf,
                                  "-frames:v", "24", str(pattern)], timeout=30)
            if result.returncode != 0:
                return None
            for path in sorted(folder.glob("frame_*.png")):
                with Image.open(path) as source_frame:
                    frames.append(source_frame.convert("RGBA").copy())
            if len(frames) < 2:
                return None
        else:
            with Image.open(BytesIO(item["bytes"])) as source:
                total_frames = int(getattr(source, "n_frames", 1))
                if source.format != "WEBP" or total_frames < 2:
                    return None
                durations = []
                for i in range(total_frames):
                    source.seek(i)
                    durations.append(max(20, int(source.info.get("duration", 125) or 125)))
                total_ms = sum(durations)
                sample_ms = min(3000, total_ms)
                frame_count = min(total_frames, 24, max(2, round(sample_ms / 1000 * 8)))
                frame_duration_ms = max(20, round(sample_ms / frame_count))
                source_count = max(frame_count, round(total_frames * sample_ms / max(total_ms, 1)))
                for i in range(frame_count):
                    frame_index = min(total_frames - 1, round(i * source_count / frame_count))
                    source.seek(frame_index)
                    image = source.convert("RGBA").copy()
                    image.thumbnail((160, 160), resample)
                    canvas = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
                    canvas.alpha_composite(image, ((160 - image.width) // 2,
                                                   (160 - image.height) // 2))
                    frames.append(canvas)

    if len(frames) < 2:
        return None
    # GIF is used only for UI previews: it animates in every common browser, including
    # browsers that display an animated WebP as a still image. Keep a shared palette
    # across frames to avoid color flicker, reserving index 255 for transparency.
    sheet = Image.new("RGB", (160, 160 * len(frames)))
    for i, frame in enumerate(frames):
        sheet.paste(frame.convert("RGB"), (0, 160 * i))
    quantize = getattr(Image, "Quantize", Image)
    dither = getattr(Image, "Dither", Image)
    palette_source = sheet.quantize(colors=255,
                                   method=getattr(quantize, "MEDIANCUT", 0),
                                   dither=getattr(dither, "NONE", 0))
    palette = (palette_source.getpalette() or [])[:768]
    palette += [0] * max(0, 768 - len(palette))
    palette[765:768] = [0, 0, 0]
    palette_source.putpalette(palette)
    gif_frames = []
    for frame in frames:
        indexed = frame.convert("RGB").quantize(
            palette=palette_source, dither=getattr(dither, "NONE", 0))
        indexed.putpalette(palette)
        transparent = frame.getchannel("A").point(lambda alpha: 255 if alpha < 40 else 0)
        indexed.paste(255, mask=transparent)
        gif_frames.append(indexed)
    output = BytesIO()
    gif_frames[0].save(output, "GIF", save_all=True, append_images=gif_frames[1:],
                       duration=frame_duration_ms, loop=0, disposal=2,
                       transparency=255, optimize=False)
    return output.getvalue()


_ADULT_TEXT_RE = re.compile(
    r"(?<![a-z0-9])(?:18\s*(?:\+|plus)|nsfw|not\s+safe\s+for\s+work|adult(?:s|[-_ ]?only)?|"
    r"mature|explicit|porn(?:o|hub|ography)?|hentai|rule\s*34|r\s*34|ecchi|xxx|sex|sexy|sexual|sexting|"
    r"nudes?|naked|erotic|lewd|bdsm|fetish|only\s*fans|milf|boobs?|breasts?|tits?|nipples?|"
    r"pussy|vagina|penis|dicks?|cocks?|cum|orgasm|blowjobs?|handjobs?|anal|genitals?)"
    r"(?![a-z0-9])", re.IGNORECASE)


def _fullyst_looks_adult(*values: str) -> bool:
    text = " ".join(re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value or ""))
                    for value in values).casefold()
    text = re.sub(r"[_./-]+", " ", text)
    if _ADULT_TEXT_RE.search(text):
        return True
    # Catch common obfuscations in catalog titles/slugs without an external classifier.
    leetspeak = text.translate(str.maketrans({"0": "o", "1": "i", "3": "e",
                                               "4": "a", "5": "s", "7": "t"}))
    return bool(_ADULT_TEXT_RE.search(leetspeak))


def _fullyst_adult_marker(attrs: dict) -> bool:
    classes = str(attrs.get("class") or "")
    if _fullyst_looks_adult(classes):
        return True
    positive_values = {"1", "true", "yes", "adult", "nsfw", "explicit", "18+"}
    text_attributes = {"aria-label", "title", "alt", "data-title", "data-tags",
                       "data-category", "data-rating", "data-content-rating",
                       "data-description", "data-label"}
    for key, value in attrs.items():
        key = str(key).casefold()
        value = str(value or "").strip()
        if key in {"data-nsfw", "data-adult", "data-sensitive", "data-explicit"} \
                and value.casefold() in positive_values:
            return True
        if key in text_attributes and _fullyst_looks_adult(value):
            return True
    return False


class _FullystCatalogParser(HTMLParser):
    """Extract the small result cards from Fullyst's public catalog HTML."""
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
             "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.packs: list[dict] = []
        self.current: dict | None = None
        self.stack: list[str] = []
        self.capture = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set((attrs.get("class") or "").split())
        if self.current is None:
            if tag == "div" and "sticker-set" in classes:
                self.current = {"titleParts": [], "kindParts": [], "textParts": [], "href": "", "sampleUrl": "",
                                "adultMarked": _fullyst_adult_marker(attrs)}
                self.stack = [tag]
            return
        if tag not in self._VOID:
            self.stack.append(tag)
        if _fullyst_adult_marker(attrs):
            self.current["adultMarked"] = True
        if tag == "h2":
            self.capture = "title"
        elif tag == "span" and "type-label" in classes:
            self.capture = "kind"
        elif tag == "a" and "btn-stickers" in classes:
            self.current["href"] = attrs.get("href", "")
        elif tag == "img" and not self.current["sampleUrl"]:
            src = attrs.get("src", "")
            parsed = urlparse(src)
            if parsed.scheme == "https" and parsed.hostname == FULLYST_STICKER_HOST:
                self.current["sampleUrl"] = src

    def handle_data(self, data):
        if self.current is None:
            return
        if data.strip():
            self.current["textParts"].append(data)
        if self.capture == "title":
            self.current["titleParts"].append(data)
        elif self.capture == "kind":
            self.current["kindParts"].append(data)

    def handle_endtag(self, tag):
        if self.current is None:
            return
        if tag == "h2" and self.capture == "title":
            self.capture = ""
        elif tag == "span" and self.capture == "kind":
            self.capture = ""
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i] == tag:
                del self.stack[i:]
                break
        if not self.stack:
            href = self.current.get("href", "")
            slug = urlparse(href).path.rstrip("/").split("/")[-1]
            title = " ".join("".join(self.current["titleParts"]).split())
            kind = " ".join("".join(self.current["kindParts"]).split())
            if title and re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", slug):
                card_text = " ".join(self.current.get("textParts", []))
                is_adult = bool(self.current.get("adultMarked") or
                                _fullyst_looks_adult(title, slug, kind, card_text))
                is_animated = bool(re.search(r"\b(?:animated|animation|video)\b", kind,
                                             flags=re.IGNORECASE))
                self.packs.append({"title": title, "kind": kind or "Sticker pack",
                                   "slug": slug, "sampleUrl": self.current["sampleUrl"],
                                   "isAdult": is_adult, "isAnimated": is_animated})
            self.current = None
            self.stack = []
            self.capture = ""


def _fullyst_read(request: Request, limit: int = FULLYST_MAX_RESPONSE_BYTES) -> bytes:
    try:
        with urlopen(request, timeout=25) as response:
            data = response.read(limit + 1)
            if len(data) > limit:
                raise RuntimeError("Fullyst response exceeded the local size limit.")
            return data
    except Exception as exc:                                            # noqa: BLE001
        raise RuntimeError(f"Could not reach Fullyst: {exc}") from exc


def _fullyst_retryable(exc: Exception) -> bool:
    cause = exc.__cause__
    if isinstance(cause, HTTPError):
        return cause.code in {408, 425, 429, 500, 502, 503, 504}
    if isinstance(cause, URLError):
        return True
    if isinstance(cause, (TimeoutError, ConnectionError, OSError)):
        return True
    return isinstance(exc, (TimeoutError, ConnectionError, OSError))


def _fullyst_error_text(exc: Exception) -> str:
    cause = exc.__cause__
    if isinstance(cause, HTTPError):
        return f"HTTP {cause.code} {cause.reason}"
    if isinstance(cause, URLError):
        return f"Network error: {cause.reason}"
    return str(exc).replace("Could not reach Fullyst: ", "")[:180]


def _fullyst_read_retry(request: Request, limit: int = FULLYST_MAX_RESPONSE_BYTES,
                        retry_callback=None) -> bytes:
    for attempt in range(1, FULLYST_RETRY_ATTEMPTS + 1):
        try:
            return _fullyst_read(request, limit=limit)
        except Exception as exc:                                      # noqa: BLE001
            if attempt >= FULLYST_RETRY_ATTEMPTS or not _fullyst_retryable(exc):
                raise
            if retry_callback:
                retry_callback(attempt, _fullyst_error_text(exc))
            time.sleep(FULLYST_RETRY_DELAYS[attempt - 1])
    raise RuntimeError("Fullyst request exhausted its retry attempts.")


def _fullyst_search(query: str, page: int) -> dict:
    query = query.strip()
    if len(query) > 100:
        raise ValueError("Search text is too long (maximum 100 characters).")
    if not 1 <= page <= 10000:
        raise ValueError("Catalog page is out of range.")
    params = {}
    if page > 1:
        params["page"] = str(page)
    if query:
        params["search"] = query
    url = f"{FULLYST_BASE}/en/stickers"
    if params:
        url += "?" + urlencode(params)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 StickerStudio/1.0",
                                   "Accept": "text/html,application/xhtml+xml"})
    page_html = _fullyst_read_retry(request).decode("utf-8", "replace")
    parser = _FullystCatalogParser()
    parser.feed(page_html)
    packs = parser.packs[:10]
    pager = re.search(r'<div\b[^>]*class=["\'][^"\']*\bpagination\b[^"\']*["\'][^>]*>(.*?)</div>',
                      page_html, flags=re.IGNORECASE | re.DOTALL)
    page_numbers = [int(value) for value in re.findall(r"page=(\d+)", pager.group(1),
                                                       flags=re.IGNORECASE)] if pager else []
    page_count = max(page_numbers) if page_numbers else 1
    page = min(page, page_count)
    return {"packs": packs, "page": page, "pageCount": page_count, "query": query,
            "hasMore": bool(packs) and page < page_count}


def _read_fullyst_visited_pages() -> set[int]:
    if not FULLYST_VISITED_PAGES_FILE.is_file():
        return set()
    try:
        payload = json.loads(FULLYST_VISITED_PAGES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("The Fullyst page-visit history could not be read.") from exc
    pages = payload.get("pages") if isinstance(payload, dict) else None
    if not isinstance(pages, list):
        raise RuntimeError("The Fullyst page-visit history has an invalid format.")
    return {page for page in pages
            if type(page) is int and 1 <= page <= FULLYST_MAX_CATALOG_PAGE}


def _get_fullyst_visited_pages() -> list[int]:
    with FULLYST_VISITED_PAGES_LOCK:
        return sorted(_read_fullyst_visited_pages())


def _mark_fullyst_page_visited(page: int) -> dict:
    if type(page) is not int or not 1 <= page <= FULLYST_MAX_CATALOG_PAGE:
        raise ValueError("Enter a valid Fullyst catalog page number.")
    with FULLYST_VISITED_PAGES_LOCK:
        pages = _read_fullyst_visited_pages()
        already_visited = page in pages
        if not already_visited:
            pages.add(page)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            temp_path = FULLYST_VISITED_PAGES_FILE.with_name(
                FULLYST_VISITED_PAGES_FILE.name + f".{uuid.uuid4().hex}.tmp")
            try:
                temp_path.write_text(json.dumps({"version": 1, "pages": sorted(pages)},
                                                separators=(",", ":")), encoding="utf-8")
                os.replace(temp_path, FULLYST_VISITED_PAGES_FILE)
            except OSError as exc:
                raise RuntimeError("Could not save Fullyst page history in this PC's data folder.") from exc
            finally:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass
        return {"alreadyVisited": already_visited, "count": len(pages)}


def _fullyst_fetch_batch(slug: str, offset: int, limit: int = FULLYST_PAGE_SIZE) -> tuple[list[dict], dict]:
    request_body = urlencode({"set": slug, "stickers": "00" if offset == 0 else str(offset),
                              "limit": str(limit)}).encode("ascii")
    request = Request(f"{FULLYST_BASE}/receiver/stickers", data=request_body,
                      headers={"User-Agent": "Mozilla/5.0 StickerStudio/1.0",
                               "Content-Type": "application/x-www-form-urlencoded",
                               "X-Requested-With": "XMLHttpRequest",
                               "Referer": f"{FULLYST_BASE}/en/stickers/{quote(slug, safe='')}"})
    try:
        response = json.loads(_fullyst_read_retry(request).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Fullyst returned an unreadable sticker list.") from exc
    if not isinstance(response, list) or not response or not isinstance(response[0], dict):
        raise RuntimeError("Fullyst returned an unexpected sticker-list response.")
    rows = response[0].get("stickers")
    set_info = response[0].get("set")
    if not isinstance(rows, list) or not isinstance(set_info, dict):
        raise RuntimeError("Fullyst did not return pack metadata for this link.")
    return rows, set_info


def _download_fullyst_asset(set_uuid: str, file_uid: str, fmt: str,
                            retry_callback=None) -> bytes:
    ext = "webm" if fmt == "webm" else "webp"
    url = f"https://{FULLYST_STICKER_HOST}/{quote(set_uuid, safe='-')}/full/{quote(file_uid, safe='_-')}.{ext}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 StickerStudio/1.0",
                                   "Accept": "image/webp,video/webm,*/*"})
    return _fullyst_read_retry(request, limit=20 * 1024 * 1024,
                               retry_callback=retry_callback)


def _fullyst_asset_signature_ok(blob: bytes, fmt: str) -> bool:
    if fmt == "webm":
        return len(blob) >= 4 and blob[:4] == b"\x1a\x45\xdf\xa3"
    return len(blob) >= 12 and blob[:4] == b"RIFF" and blob[8:12] == b"WEBP"


def _fullyst_animated_catalog_preview(slug: str) -> bytes:
    """Render one sticker as a GIF for an animated search-result card, caching briefly in RAM."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", slug):
        raise ValueError("This Fullyst pack link is not valid.")
    with FULLYST_PREVIEW_CACHE_LOCK:
        cached = FULLYST_PREVIEW_CACHE.get(slug)
    if cached:
        return cached
    rows, set_info = _fullyst_fetch_batch(slug, 0, limit=1)
    if not rows:
        raise RuntimeError("Fullyst returned no sticker preview for this pack.")
    row = rows[0]
    is_video = str(row.get("video", "0")).strip().lower() in {"1", "true", "yes"}
    is_animated = str(row.get("animated", "0")).strip().lower() in {"1", "true", "yes"}
    if not is_video and not is_animated:
        raise RuntimeError("Fullyst did not return an animated preview for this pack.")
    pack_uuid = str(set_info.get("uuid") or "")
    file_uid = str(row.get("file_uid") or "")
    if not re.fullmatch(r"[A-Fa-f0-9-]{36}", pack_uuid) \
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", file_uid):
        raise RuntimeError("Fullyst returned invalid animated-preview metadata.")
    fmt = "webm" if is_video else "webp"
    blob = _download_fullyst_asset(pack_uuid, file_uid, fmt)
    if not _fullyst_asset_signature_ok(blob, fmt):
        raise RuntimeError(f"Fullyst returned an invalid {fmt.upper()} preview.")
    item = {"title": str(set_info.get("title") or set_info.get("name") or slug),
            "name": f"01.{fmt}", "format": fmt,
            "emoji": str(row.get("emoji") or "📎"),
            "file_unique_id": file_uid, "bytes": blob}
    preview = _make_animated_preview(item)
    if not preview:
        raise RuntimeError("The animated sticker preview could not be rendered.")
    with FULLYST_PREVIEW_CACHE_LOCK:
        FULLYST_PREVIEW_CACHE[slug] = preview
        while len(FULLYST_PREVIEW_CACHE) > FULLYST_PREVIEW_CACHE_MAX:
            FULLYST_PREVIEW_CACHE.pop(next(iter(FULLYST_PREVIEW_CACHE)))
    return preview


def _fullyst_pack_items(slug: str, progress_callback=None) -> tuple[str, str, list[dict], list[dict]]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", slug):
        raise ValueError("This Fullyst pack link is not valid.")
    records: list[dict] = []
    set_info = {}
    offset = 0
    while offset < FULLYST_MAX_PACK_STICKERS:
        rows, meta = _fullyst_fetch_batch(slug, offset)
        if not set_info:
            set_info = meta
        if not rows:
            break
        records.extend(rows)
        if len(records) > FULLYST_MAX_PACK_STICKERS:
            raise RuntimeError(f"This pack is larger than the {FULLYST_MAX_PACK_STICKERS}-sticker import limit.")
        if len(rows) < FULLYST_PAGE_SIZE:
            break
        offset += FULLYST_PAGE_SIZE
    if not records:
        raise RuntimeError("Fullyst returned no stickers for this pack.")
    set_uuid = str(set_info.get("uuid") or "")
    if not re.fullmatch(r"[A-Fa-f0-9-]{36}", set_uuid):
        raise RuntimeError("Fullyst returned an invalid pack identifier.")
    title = str(set_info.get("title") or set_info.get("name") or slug).strip()[:140]
    cache_name = f"fullyst_{set_uuid}_{slug}"
    raw_dir = _cache_folder(cache_name)
    raw_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict | None] = [None] * len(records)
    failures: list[dict] = []
    done = 0
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def download_one(index: int, row: dict) -> tuple[int, dict | None, dict | None]:
        source_index = index + 1
        file_uid = str(row.get("file_uid") or "")
        is_video = str(row.get("video", "0")) == "1"
        is_animated = str(row.get("animated", "0")) == "1"
        fmt = "webm" if is_video else "webp"
        try:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", file_uid):
                raise RuntimeError("Fullyst returned an invalid file id.")
            cache_path = raw_dir / f"{source_index:03d}_{file_uid}.{fmt}"
            blob = b""
            cache_hit = False
            if cache_path.is_file():
                try:
                    cached = cache_path.read_bytes()
                    if _fullyst_asset_signature_ok(cached, fmt):
                        blob = cached
                        cache_hit = True
                    else:
                        cache_path.unlink()
                except OSError:
                    pass
            if not blob:
                def retry_notice(attempt: int, reason: str) -> None:
                    if progress_callback:
                        progress_callback({"type": "retry", "index": source_index,
                                           "total": len(records), "attempt": attempt,
                                           "maxAttempts": FULLYST_RETRY_ATTEMPTS,
                                           "error": reason})

                blob = _download_fullyst_asset(set_uuid, file_uid, fmt,
                                                retry_callback=retry_notice)
                if not _fullyst_asset_signature_ok(blob, fmt):
                    raise RuntimeError(f"Fullyst returned an invalid {fmt.upper()} file.")
                temp_path = cache_path.with_name(cache_path.name + f".{uuid.uuid4().hex}.tmp")
                try:
                    temp_path.write_bytes(blob)
                    temp_path.replace(cache_path)
                finally:
                    try:
                        temp_path.unlink()
                    except FileNotFoundError:
                        pass
            item = {"title": title, "name": f"{source_index:02d}.{fmt}",
                    "format": fmt, "emoji": str(row.get("emoji") or "📎"),
                    "file_unique_id": file_uid, "sourceIndex": source_index,
                    "bytes": blob, "fromCache": cache_hit,
                    "kind": "Animated" if is_video or is_animated else "Static"}
            return index, item, None
        except Exception as exc:                                      # noqa: BLE001
            failure = {"sourceIndex": source_index, "fileUniqueId": file_uid,
                       "format": fmt, "error": _fullyst_error_text(exc)}
            return index, None, failure

    workers = min(6, os.cpu_count() or 1, len(records))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(download_one, i, row) for i, row in enumerate(records)]
        for future in as_completed(futures):
            index, item, failure = future.result()
            done += 1
            if item is not None:
                items[index] = item
                event = {"type": "complete", "done": done, "total": len(records),
                         "index": index + 1, "format": item["format"],
                         "fromCache": item["fromCache"]}
            else:
                failures.append(failure or {"sourceIndex": index + 1,
                                             "error": "Unknown download failure."})
                event = {"type": "failed", "done": done, "total": len(records),
                         "index": index + 1, "error": failures[-1]["error"]}
            if progress_callback:
                progress_callback(event)
    failures.sort(key=lambda failure: failure["sourceIndex"])
    return cache_name, title, [item for item in items if item is not None], failures


def _migrate_legacy_collection() -> None:
    """Copy an older app-folder collection into stable per-user storage once."""
    global _COLLECTION_MIGRATED
    if _COLLECTION_MIGRATED:
        return
    with COLLECTION_MIGRATION_LOCK:
        if _COLLECTION_MIGRATED:
            return
        legacy_index = LEGACY_COLLECTION_DIR / "collection.json"
        if COLLECTION_INDEX.exists() or not legacy_index.is_file():
            _COLLECTION_MIGRATED = True
            return
        try:
            COLLECTION_DIR.mkdir(parents=True, exist_ok=True)
            for folder in ("assets", "thumbs", "previews"):
                source_dir = LEGACY_COLLECTION_DIR / folder
                if not source_dir.is_dir():
                    continue
                target_dir = COLLECTION_DIR / folder
                target_dir.mkdir(parents=True, exist_ok=True)
                for source_file in source_dir.iterdir():
                    if source_file.is_file():
                        shutil.copy2(source_file, target_dir / source_file.name)
            temp_index = COLLECTION_INDEX.with_name(COLLECTION_INDEX.name + ".migrate.tmp")
            try:
                shutil.copy2(legacy_index, temp_index)
                os.replace(temp_index, COLLECTION_INDEX)
            finally:
                try:
                    temp_index.unlink()
                except FileNotFoundError:
                    pass
            _COLLECTION_MIGRATED = True
        except OSError as exc:
            raise RuntimeError(
                "Could not move your existing My saves into this PC's Sticker Studio data folder. "
                "The original collection is still in the old app folder; close the app and check disk access."
            ) from exc


def _default_collection_groups() -> list[dict]:
    return [{"id": UNGROUPED_GROUP_ID, "name": "Ungrouped"}]


def _normalize_collection_groups(raw_groups) -> list[dict]:
    groups = _default_collection_groups()
    if not isinstance(raw_groups, list):
        return groups
    used_ids = {UNGROUPED_GROUP_ID}
    used_names = {"ungrouped"}
    for raw in raw_groups:
        if not isinstance(raw, dict):
            continue
        group_id = str(raw.get("id") or "")
        name = " ".join(str(raw.get("name") or "").split())
        if (group_id == UNGROUPED_GROUP_ID or not COLLECTION_GROUP_ID_RE.fullmatch(group_id)
                or not name or len(name) > MAX_SAVED_GROUP_NAME):
            continue
        name_key = name.casefold()
        if group_id in used_ids or name_key in used_names:
            continue
        groups.append({"id": group_id, "name": name})
        used_ids.add(group_id)
        used_names.add(name_key)
        if len(groups) >= MAX_SAVED_GROUPS + 1:
            break
    return groups


def _collection_payload_read() -> tuple[list[dict], list[dict]]:
    with COLLECTION_LOCK:
        _migrate_legacy_collection()
        if not COLLECTION_INDEX.exists():
            return [], _default_collection_groups()
        try:
            payload = json.loads(COLLECTION_INDEX.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("The saved-sticker collection file could not be read.") from exc
        if isinstance(payload, dict):
            entries = payload.get("items")
            groups = _normalize_collection_groups(payload.get("groups", []))
        else:
            entries = payload
            groups = _default_collection_groups()
        if not isinstance(entries, list):
            raise RuntimeError("The saved-sticker collection file has an invalid format.")
        valid = []
        group_ids = {group["id"] for group in groups}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item_id = str(entry.get("id", ""))
            fmt = str(entry.get("format", "")).lower()
            if re.fullmatch(r"[0-9a-f]{32}", item_id) and fmt in COLLECTION_FORMATS:
                normalized = dict(entry)
                group_id = str(normalized.get("groupId") or UNGROUPED_GROUP_ID)
                normalized["groupId"] = group_id if group_id in group_ids else UNGROUPED_GROUP_ID
                valid.append(normalized)
        return valid, groups


def _collection_read() -> list[dict]:
    return _collection_payload_read()[0]


def _collection_write(entries: list[dict], groups: list[dict] | None = None) -> None:
    if groups is None:
        _, groups = _collection_payload_read()
    groups = _normalize_collection_groups(groups)
    group_ids = {group["id"] for group in groups}
    normalized_entries = []
    for entry in entries:
        normalized = dict(entry)
        group_id = str(normalized.get("groupId") or UNGROUPED_GROUP_ID)
        normalized["groupId"] = group_id if group_id in group_ids else UNGROUPED_GROUP_ID
        normalized_entries.append(normalized)
    COLLECTION_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = COLLECTION_INDEX.with_name(COLLECTION_INDEX.name + ".tmp")
    try:
        temp_path.write_text(json.dumps({"version": 2, "groups": groups,
                                         "items": normalized_entries},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, COLLECTION_INDEX)
    except OSError as exc:
        raise RuntimeError("Could not update the saved-sticker collection on this PC.") from exc
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _collection_find(entries: list[dict], item_id: str) -> dict | None:
    return next((entry for entry in entries if entry.get("id") == item_id), None)


def _collection_asset_path(entry: dict) -> Path:
    item_id = str(entry.get("id", ""))
    fmt = str(entry.get("format", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{32}", item_id) or fmt not in COLLECTION_FORMATS:
        raise RuntimeError("The saved sticker is invalid.")
    return COLLECTION_ASSETS_DIR / f"{item_id}.{fmt}"


def _collection_thumb_path(item_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", str(item_id)):
        raise ValueError("The saved sticker ID is invalid.")
    return COLLECTION_THUMBS_DIR / f"{item_id}.png"


def _collection_public_item(entry: dict) -> dict:
    item_id = str(entry.get("id", ""))
    try:
        available = _collection_asset_path(entry).is_file()
    except RuntimeError:
        available = False
    result = {
        "id": item_id, "name": str(entry.get("name") or "Saved sticker"),
        "sourceTitle": str(entry.get("sourceTitle") or "Sticker pack"),
        "source": str(entry.get("source") or "Telegram"),
        "sourceSlug": str(entry.get("sourceSlug") or ""),
        "format": str(entry.get("format") or "webp"),
        "formatLabel": str(entry.get("formatLabel") or str(entry.get("format", "webp")).upper()),
        "kind": str(entry.get("kind") or "Static"),
        "emoji": str(entry.get("emoji") or "📎"),
        "groupId": str(entry.get("groupId") or UNGROUPED_GROUP_ID),
        "isAdult": bool(entry.get("isAdult")), "addedAt": str(entry.get("addedAt") or ""),
        "available": available, "thumbUrl": f"/api/collection/thumb/{item_id}",
        "largePreviewUrl": f"/api/collection/preview/{item_id}",
    }
    if result["kind"] == "Animated":
        result["previewUrl"] = result["largePreviewUrl"]
    return result


def _collection_public_data() -> tuple[list[dict], list[dict]]:
    with COLLECTION_LOCK:
        _cleanup_collection_undo_actions_locked()
        entries, groups = _collection_payload_read()
    group_names = {group["id"]: group["name"] for group in groups}
    items = []
    for entry in reversed(entries):
        item = _collection_public_item(entry)
        group_id = item["groupId"]
        if group_id not in group_names:
            group_id = UNGROUPED_GROUP_ID
        item["groupId"] = group_id
        item["groupName"] = group_names[group_id]
        items.append(item)
    return items, groups


def _collection_public_items() -> list[dict]:
    return _collection_public_data()[0]


def _create_collection_group(name: str) -> tuple[dict, list[dict]]:
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("Enter a name for the new group.")
    if len(name) > MAX_SAVED_GROUP_NAME:
        raise ValueError(f"Group names can be up to {MAX_SAVED_GROUP_NAME} characters.")
    if name.casefold() == "ungrouped":
        raise ValueError("Ungrouped is reserved for stickers without a folder.")
    with COLLECTION_LOCK:
        entries, groups = _collection_payload_read()
        if any(group["name"].casefold() == name.casefold() for group in groups):
            raise ValueError("A saved-sticker group with that name already exists.")
        if len(groups) - 1 >= MAX_SAVED_GROUPS:
            raise ValueError(f"You can create up to {MAX_SAVED_GROUPS} saved-sticker groups.")
        group = {"id": uuid.uuid4().hex, "name": name}
        groups.append(group)
        _collection_write(entries, groups)
        return group, groups


def _discard_collection_undo_action_locked(token: str, action: dict) -> None:
    if action.get("kind") == "remove":
        shutil.rmtree(COLLECTION_UNDO_DIR / token, ignore_errors=True)


def _cleanup_collection_undo_actions_locked(now: float | None = None) -> None:
    now = time.time() if now is None else now
    for token, action in list(COLLECTION_UNDO_ACTIONS.items()):
        if float(action.get("expiresAt", 0)) <= now:
            COLLECTION_UNDO_ACTIONS.pop(token, None)
            _discard_collection_undo_action_locked(token, action)
    active_tokens = set(COLLECTION_UNDO_ACTIONS)
    try:
        children = list(COLLECTION_UNDO_DIR.iterdir())
    except OSError:
        return
    for child in children:
        if (re.fullmatch(r"[0-9a-f]{32}", child.name) and child.name not in active_tokens
                and child.is_dir() and not child.is_symlink()):
            shutil.rmtree(child, ignore_errors=True)


def _register_collection_undo_action_locked(action: dict) -> str:
    _cleanup_collection_undo_actions_locked()
    token = uuid.uuid4().hex
    saved = dict(action)
    saved["expiresAt"] = time.time() + COLLECTION_UNDO_TTL_SECONDS
    COLLECTION_UNDO_ACTIONS[token] = saved
    while len(COLLECTION_UNDO_ACTIONS) > MAX_COLLECTION_UNDO_ACTIONS:
        oldest_token = next(iter(COLLECTION_UNDO_ACTIONS))
        oldest_action = COLLECTION_UNDO_ACTIONS.pop(oldest_token)
        _discard_collection_undo_action_locked(oldest_token, oldest_action)
    return token


def _collection_sticker_file_paths(entry: dict) -> list[tuple[str, Path]]:
    item_id = str(entry.get("id", ""))
    return [
        ("assets", _collection_asset_path(entry)),
        ("thumbs", _collection_thumb_path(item_id)),
        ("previews", COLLECTION_PREVIEWS_DIR / f"{item_id}.gif"),
    ]


def _collection_path_for_undo_file(relative_path: str) -> Path:
    relative = str(relative_path).replace("\\", "/")
    folder, separator, filename = relative.partition("/")
    directories = {
        "assets": COLLECTION_ASSETS_DIR,
        "thumbs": COLLECTION_THUMBS_DIR,
        "previews": COLLECTION_PREVIEWS_DIR,
    }
    if (not separator or folder not in directories or not filename or "/" in filename
            or filename in (".", "..")):
        raise RuntimeError("The saved-sticker undo data is invalid.")
    return directories[folder] / filename


def _move_collection_files_to_undo(entry: dict, token: str) -> list[str]:
    moved: list[str] = []
    undo_dir = COLLECTION_UNDO_DIR / token
    try:
        for folder, source in _collection_sticker_file_paths(entry):
            if not source.is_file():
                continue
            relative = f"{folder}/{source.name}"
            staged = undo_dir / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, staged)
            moved.append(relative)
    except OSError as exc:
        for relative in reversed(moved):
            try:
                staged = undo_dir / relative
                original = _collection_path_for_undo_file(relative)
                original.parent.mkdir(parents=True, exist_ok=True)
                if staged.exists():
                    os.replace(staged, original)
            except OSError:
                pass
        shutil.rmtree(undo_dir, ignore_errors=True)
        raise RuntimeError("Could not safely remove the saved sticker on this PC.") from exc
    return moved


def _restore_collection_files_from_undo(token: str, relative_paths: list[str]) -> list[tuple[Path, Path]]:
    undo_dir = COLLECTION_UNDO_DIR / token
    pairs: list[tuple[Path, Path]] = []
    for relative in relative_paths:
        staged = undo_dir / relative
        original = _collection_path_for_undo_file(relative)
        if staged.exists():
            if original.exists():
                raise RuntimeError("A saved-sticker file already exists, so this removal cannot be undone safely.")
            pairs.append((staged, original))
    restored: list[tuple[Path, Path]] = []
    try:
        for staged, original in pairs:
            original.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, original)
            restored.append((staged, original))
    except OSError as exc:
        _move_restored_collection_files_back(restored)
        raise RuntimeError("Could not restore the saved sticker files on this PC.") from exc
    return restored


def _move_restored_collection_files_back(restored: list[tuple[Path, Path]]) -> None:
    for staged, original in reversed(restored):
        try:
            staged.parent.mkdir(parents=True, exist_ok=True)
            if original.exists():
                os.replace(original, staged)
        except OSError:
            pass


def _delete_collection_sticker_files(entry: dict) -> None:
    for _folder, path in _collection_sticker_file_paths(entry):
        try:
            path.unlink()
        except OSError:
            pass


def _move_collection_stickers(item_ids: list[str], group_id: str, *, record_undo: bool = False) -> int | tuple[int, str | None]:
    if not isinstance(item_ids, list) or not item_ids or len(item_ids) > MAX_SAVED_STICKERS:
        raise ValueError("Select saved stickers to move.")
    if any(not isinstance(item_id, str) or not re.fullmatch(r"[0-9a-f]{32}", item_id)
           for item_id in item_ids):
        raise ValueError("The saved-sticker selection is invalid.")
    if len(set(item_ids)) != len(item_ids):
        raise ValueError("The saved-sticker selection contains duplicates.")
    group_id = str(group_id or "")
    with COLLECTION_LOCK:
        entries, groups = _collection_payload_read()
        if group_id != UNGROUPED_GROUP_ID and not COLLECTION_GROUP_ID_RE.fullmatch(group_id):
            raise ValueError("Choose a valid saved-sticker group.")
        if not any(group["id"] == group_id for group in groups):
            raise ValueError("That saved-sticker group no longer exists. Refresh My saves.")
        selected = set(item_ids)
        entry_ids = {entry["id"] for entry in entries}
        if not selected.issubset(entry_ids):
            raise ValueError("One or more stickers are no longer saved. Refresh My saves.")
        moved = 0
        previous_groups = {}
        for entry in entries:
            if entry["id"] in selected and entry.get("groupId") != group_id:
                previous_groups[entry["id"]] = entry.get("groupId") or UNGROUPED_GROUP_ID
                entry["groupId"] = group_id
                moved += 1
        undo_token = None
        if moved:
            _collection_write(entries, groups)
            if record_undo:
                undo_token = _register_collection_undo_action_locked({
                    "kind": "move", "toGroup": group_id, "previousGroups": previous_groups,
                })
        return (moved, undo_token) if record_undo else moved


def _undo_collection_action(token: str) -> dict:
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("The undo action is invalid.")
    with COLLECTION_LOCK:
        _cleanup_collection_undo_actions_locked()
        action = COLLECTION_UNDO_ACTIONS.get(token)
        if action is None:
            raise ValueError("This undo action has expired or was already used.")
        kind = action.get("kind")
        entries, groups = _collection_payload_read()
        if kind == "move":
            previous_groups = action.get("previousGroups")
            if not isinstance(previous_groups, dict) or not previous_groups:
                raise RuntimeError("This group move cannot be undone safely.")
            by_id = {entry["id"]: entry for entry in entries}
            if any(item_id not in by_id for item_id in previous_groups):
                raise RuntimeError("One or more stickers have since been removed; the move cannot be undone safely.")
            to_group = action.get("toGroup")
            if any(by_id[item_id].get("groupId") != to_group for item_id in previous_groups):
                raise RuntimeError("These stickers have changed groups since the move; undo would overwrite a newer change.")
            group_ids = {group["id"] for group in groups}
            fallback_count = 0
            for item_id, previous_group in previous_groups.items():
                destination = previous_group if previous_group in group_ids else UNGROUPED_GROUP_ID
                if destination != previous_group:
                    fallback_count += 1
                by_id[item_id]["groupId"] = destination
            _collection_write(entries, groups)
            COLLECTION_UNDO_ACTIONS.pop(token, None)
            return {"ok": True, "kind": "move", "restored": len(previous_groups),
                    "fallbackUngrouped": fallback_count, "count": len(entries)}
        if kind == "remove":
            entry = action.get("entry")
            if not isinstance(entry, dict) or _collection_find(entries, entry.get("id", "")):
                raise RuntimeError("This sticker is already in My saves or its undo data is invalid.")
            entry = dict(entry)
            group_ids = {group["id"] for group in groups}
            if entry.get("groupId") not in group_ids:
                entry["groupId"] = UNGROUPED_GROUP_ID
            files = action.get("files") if isinstance(action.get("files"), list) else []
            restored_files = _restore_collection_files_from_undo(token, files)
            try:
                _collection_write(entries + [entry], groups)
            except Exception:
                _move_restored_collection_files_back(restored_files)
                raise
            COLLECTION_UNDO_ACTIONS.pop(token, None)
            shutil.rmtree(COLLECTION_UNDO_DIR / token, ignore_errors=True)
            return {"ok": True, "kind": "remove", "id": entry["id"],
                    "groupId": entry["groupId"], "count": len(entries) + 1}
        raise RuntimeError("This undo action is not supported.")


def _delete_collection_group(group_id: str) -> tuple[str, int, list[dict]]:
    group_id = str(group_id or "")
    if group_id == UNGROUPED_GROUP_ID:
        raise ValueError("The Ungrouped group cannot be deleted.")
    if not COLLECTION_GROUP_ID_RE.fullmatch(group_id):
        raise ValueError("Choose a valid saved-sticker group.")
    with COLLECTION_LOCK:
        entries, groups = _collection_payload_read()
        group = next((group for group in groups if group["id"] == group_id), None)
        if group is None:
            raise ValueError("That saved-sticker group no longer exists. Refresh My saves.")
        moved = 0
        for entry in entries:
            if entry.get("groupId") == group_id:
                entry["groupId"] = UNGROUPED_GROUP_ID
                moved += 1
        groups = [item for item in groups if item["id"] != group_id]
        _collection_write(entries, groups)
        return group["name"], moved, groups


def _save_collection_sticker(job_id: str, index: int) -> dict:
    with STATE_LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("status") != "ready":
            raise ValueError("Fetch a sticker pack before saving stickers.")
        descriptor = next((item for item in job.get("sourceItems", [])
                           if item.get("index") == index), None)
        if descriptor is None:
            raise ValueError("That sticker is not in the current pack.")
        set_name = str(job.get("setName", ""))
        title = str(job.get("title", set_name))
        source = str(job.get("source") or "Telegram")
        source_slug = str(job.get("sourceSlug") or "")
        is_adult = bool(job.get("isAdult"))
        descriptor = dict(descriptor)
    try:
        item = _raw_item(set_name, descriptor, title)
    except (OSError, RuntimeError, KeyError, ValueError) as exc:
        raise RuntimeError("Could not read this sticker from the local pack cache. Fetch the pack again and retry.") from exc
    raw = item.get("bytes")
    if not isinstance(raw, bytes) or not raw:
        raise RuntimeError("The sticker file is empty and could not be saved.")
    fmt = str(descriptor.get("format") or item.get("format") or "").lower()
    if fmt not in COLLECTION_FORMATS:
        raise ValueError("This sticker format cannot be saved to the collection.")
    content_hash = hashlib.sha256(raw).hexdigest()
    try:
        thumb, _ = _get_or_create_thumbnail(item)
    except Exception:                                                 # noqa: BLE001
        thumb = None
    with COLLECTION_LOCK:
        entries = _collection_read()
        duplicate = next((entry for entry in entries
                          if entry.get("contentHash") == content_hash
                          and entry.get("format") == fmt), None)
        if duplicate:
            if is_adult and not duplicate.get("isAdult"):
                duplicate["isAdult"] = True
                _collection_write(entries)
            return {"item": _collection_public_item(duplicate),
                    "alreadySaved": True, "count": len(entries)}
        if len(entries) >= MAX_SAVED_STICKERS:
            raise ValueError(f"The collection is full ({MAX_SAVED_STICKERS} stickers). Remove some saved stickers first.")
        item_id = uuid.uuid4().hex
        entry = {
            "id": item_id, "name": str(descriptor.get("name") or f"{index + 1:02d}.{fmt}"),
            "sourceTitle": title, "source": source, "sourceSlug": source_slug,
            "format": fmt, "formatLabel": str(descriptor.get("formatLabel") or fmt.upper()),
            "kind": str(descriptor.get("kind") or "Static"),
            "emoji": str(descriptor.get("emoji") or "📎"), "isAdult": is_adult,
            "groupId": UNGROUPED_GROUP_ID,
            "contentHash": content_hash, "addedAt": datetime.now().isoformat(timespec="seconds"),
        }
        COLLECTION_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        COLLECTION_THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        asset_path = _collection_asset_path(entry)
        thumb_path = _collection_thumb_path(item_id)
        try:
            asset_path.write_bytes(raw)
            if thumb:
                thumb_path.write_bytes(thumb)
            entries.append(entry)
            _collection_write(entries)
        except Exception as exc:                                      # noqa: BLE001
            for path in (asset_path, thumb_path):
                try:
                    path.unlink()
                except OSError:
                    pass
            raise RuntimeError("Could not write the saved sticker to this PC.") from exc
        return {"item": _collection_public_item(entry),
                "alreadySaved": False, "count": len(entries)}


def _remove_collection_sticker(item_id: str, *, record_undo: bool = False) -> int | tuple[int, str]:
    if not re.fullmatch(r"[0-9a-f]{32}", str(item_id)):
        raise ValueError("The saved-sticker ID is invalid.")
    with COLLECTION_LOCK:
        entries, groups = _collection_payload_read()
        entry = _collection_find(entries, item_id)
        if entry is None:
            raise ValueError("That sticker is not in the collection.")
        with STATE_LOCK:
            active = JOBS.get(ACTIVE_JOB_ID or "", {})
            if active.get("status") in ("queued", "running") and active.get("source") == "Collection":
                if any(d.get("collectionId") == item_id for d in active.get("sourceItems", [])):
                    raise RuntimeError("This saved sticker is being used to create a pack. Try removing it after conversion finishes.")
        updated = [item for item in entries if item.get("id") != item_id]
        if not record_undo:
            _collection_write(updated, groups)
            _delete_collection_sticker_files(entry)
            return len(updated)

        undo_token = _register_collection_undo_action_locked({
            "kind": "remove", "entry": dict(entry), "files": [],
        })
        index_removed = False
        files = []
        try:
            _collection_write(updated, groups)
            index_removed = True
            files = _move_collection_files_to_undo(entry, undo_token)
            COLLECTION_UNDO_ACTIONS[undo_token]["files"] = files
        except Exception:
            if index_removed:
                try:
                    _collection_write(entries, groups)
                except Exception:
                    pass
            if files:
                try:
                    _restore_collection_files_from_undo(undo_token, files)
                except Exception:
                    pass
            COLLECTION_UNDO_ACTIONS.pop(undo_token, None)
            shutil.rmtree(COLLECTION_UNDO_DIR / undo_token, ignore_errors=True)
            raise
        return len(updated), undo_token


def _start_collection_conversion(selected_ids: list[str], title: str) -> str:
    global ACTIVE_JOB_ID, LATEST_JOB_ID
    if not selected_ids or len(selected_ids) > MAX_SAVED_STICKERS:
        raise ValueError("Select saved stickers first.")
    if len(set(selected_ids)) != len(selected_ids):
        raise ValueError("The saved-sticker selection contains duplicates.")
    if not title or len(title) > 60:
        raise ValueError("Enter a pack name between 1 and 60 characters.")
    if any(not re.fullmatch(r"[0-9a-f]{32}", str(item_id)) for item_id in selected_ids):
        raise ValueError("The saved-sticker selection is invalid.")
    with COLLECTION_LOCK:
        entries = _collection_read()
        entry_map = {entry["id"]: entry for entry in entries}
        if any(item_id not in entry_map for item_id in selected_ids):
            raise ValueError("One or more selected stickers are no longer saved. Refresh the collection.")
        selected_entries = [entry_map[item_id] for item_id in selected_ids]
        descriptors = []
        for index, entry in enumerate(selected_entries):
            if not _collection_asset_path(entry).is_file():
                raise ValueError(f"Saved sticker {entry.get('name') or index + 1} is missing. Remove it from the collection and save it again.")
            descriptors.append({
                "index": index, "sourceIndex": index + 1,
                "format": entry["format"], "formatLabel": entry.get("formatLabel") or entry["format"].upper(),
                "kind": entry.get("kind") or "Static", "emoji": entry.get("emoji") or "📎",
                "fileUniqueId": entry["id"], "name": entry.get("name") or f"{index + 1:02d}.{entry['format']}",
                "collectionId": entry["id"], "isAdult": bool(entry.get("isAdult")),
            })
        static_count = sum(1 for item in descriptors if item["kind"] != "Animated")
        animated_count = len(descriptors) - static_count
        if static_count and animated_count:
            raise ValueError("Choose only static or only animated saved stickers. WhatsApp cannot mix types, so one create action makes one pack.")
        if max(static_count, animated_count) < 3:
            raise ValueError("Select at least 3 saved stickers of one type to create a WhatsApp pack.")
        with STATE_LOCK:
            if ACTIVE_JOB_ID and JOBS.get(ACTIVE_JOB_ID, {}).get("status") in ("queued", "running"):
                raise RuntimeError("A job is already running. Wait for it to finish first.")
            _trim_jobs()
            job_id = _new_job_id()
            set_name = f"saved_collection_{job_id}"
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            job_dir = RUNS_DIR / f"{stamp}_{convert._slug(title)}_{job_id}"
            job_dir.mkdir(parents=True, exist_ok=True)
            JOBS[job_id] = {
                "id": job_id, "status": "queued", "phase": "Starting",
                "message": f"Creating {len(descriptors)} saved stickers…", "done": 0,
                "total": len(descriptors), "packs": [], "packFiles": [],
                "previewEntries": [], "source": "Collection", "sourceSlug": "",
                "sourceItems": descriptors, "setName": set_name, "title": title,
                "isAdult": any(item.get("isAdult") for item in selected_entries),
                "activityLog": [], "_activitySeq": 0, "outputDir": str(job_dir),
                "created": time.time(),
            }
            _append_activity_log(JOBS[job_id], "Queued", "Preparing your saved stickers…")
            ACTIVE_JOB_ID = LATEST_JOB_ID = job_id
    threading.Thread(target=_run_conversion, args=(job_id, [item["index"] for item in descriptors], "selected"),
                     name=f"saved-sticker-convert-{job_id}", daemon=True).start()
    return job_id


def _new_job_id() -> str:
    return uuid.uuid4().hex[:12]


def _trim_jobs() -> None:
    if len(JOBS) < MAX_JOBS:
        return
    old = [key for key, value in JOBS.items()
           if value.get("status") not in ("queued", "running")]
    for key in old[:max(0, len(JOBS) - MAX_JOBS + 1)]:
        JOBS.pop(key, None)


def _run_fetch(job_id: str, set_name: str, token: str, remember: bool) -> None:
    jobs = min(os.cpu_count() or 1, 8)
    try:
        _update(job_id, status="running", phase="Connecting", message="Contacting Telegram…",
                done=0, total=0)

        def download_progress(event: dict) -> None:
            _update(job_id, phase="Downloading",
                    message=f"Downloading stickers · {event['done']} of {event['total']}",
                    done=event["done"], total=event["total"])

        missing = convert.preflight(fetching=True, suffixes={".tgs", ".webm"})
        if missing:
            packages = ", ".join(pkg for pkg, _why in missing)
            raise RuntimeError(f"Missing required package(s): {packages}. Run python install.py, then restart the app.")
        items = convert.fetch_pack(set_name, token, cache_dir=_cache_folder(set_name),
                                   verbose=False, jobs=jobs, progress_callback=download_progress)
        if not items:
            raise RuntimeError("Telegram returned an empty sticker set.")
        title = (items[0].get("title") or set_name).strip() or set_name
        descriptors = []
        labels = {"tgs": "Animated TGS", "webm": "Video WebM", "webp": "Static WebP"}
        for i, item in enumerate(items):
            fmt = item["format"]
            descriptors.append({
                "index": i,
                "sourceIndex": i + 1,
                "format": fmt,
                "formatLabel": labels.get(fmt, fmt.upper()),
                "kind": "Animated" if fmt in ("tgs", "webm") else "Static",
                "emoji": item.get("emoji") or "📎",
                "fileUniqueId": str(item.get("file_unique_id") or ""),
                "name": str(item.get("name") or f"{i + 1:02d}.{fmt}"),
            })

        thumbnails: list[bytes | None] = [None] * len(items)
        public_stickers = _job_stickers_for_picker(
            job_id, descriptors, thumbnails, preparing=True)
        with STATE_LOCK:
            job = JOBS.get(job_id)
            if job is not None:
                job.update(sourceItems=descriptors, thumbnails=thumbnails,
                           stickers=public_stickers, setName=set_name, title=title,
                           source="Telegram")
        _update(job_id, phase="Preparing previews",
                message=f"Preparing sticker previews · 0 of {len(items)}",
                done=0, total=len(items))

        def thumbnail_progress(event: dict) -> None:
            index = event["index"]
            thumbnail = event["thumbnail"]
            with STATE_LOCK:
                job = JOBS.get(job_id)
                if job is not None:
                    job_thumbnails = job.get("thumbnails", [])
                    job_stickers = job.get("stickers", [])
                    if index < len(job_thumbnails):
                        job_thumbnails[index] = thumbnail
                    if index < len(job_stickers):
                        job_stickers[index]["previewReady"] = True
                        job_stickers[index]["previewAvailable"] = bool(thumbnail)
            reused = f" · {event['cacheHits']} cached" if event["cacheHits"] else ""
            _update(job_id, phase="Preparing previews",
                    message=(f"Preparing sticker previews · {event['done']} of "
                             f"{event['total']}{reused}"),
                    done=event["done"], total=event["total"])

        thumbnails, thumbnail_cache_hits = _prepare_thumbnails(items, thumbnail_progress)
        public_stickers = _job_stickers_for_picker(job_id, descriptors, thumbnails)
        remember_warning = ""
        if remember:
            try:
                _save_token(token)
            except Exception as exc:                                # noqa: BLE001
                remember_warning = f"The pack was fetched, but the token could not be saved securely: {exc}"

        with STATE_LOCK:
            job = JOBS.get(job_id)
            if job is not None:
                job.update(sourceItems=descriptors, thumbnails=thumbnails,
                           stickers=public_stickers, setName=set_name, title=title,
                           thumbnailCacheHits=thumbnail_cache_hits,
                           rememberWarning=remember_warning)
        msg = f"{len(items)} stickers ready. Select some or convert the full pack."
        if thumbnail_cache_hits:
            msg += f" Reused {thumbnail_cache_hits} cached poster thumbnails."
        if remember_warning:
            msg += " Token preference was not fully applied."
        _update(job_id, status="ready", phase="Choose stickers", message=msg,
                done=len(items), total=len(items), title=title, stickers=public_stickers)
    except Exception as exc:                                          # noqa: BLE001
        safe = str(exc)
        if token:
            safe = safe.replace(token, "[redacted]")
        _update(job_id, status="failed", phase="Stopped", message="Could not fetch the pack.",
                error=safe[:900])
    finally:
        token = ""
        with STATE_LOCK:
            global ACTIVE_JOB_ID
            if ACTIVE_JOB_ID == job_id:
                ACTIVE_JOB_ID = None


def _run_fullyst_fetch(job_id: str, slug: str) -> None:
    try:
        _update(job_id, status="running", phase="Connecting to Fullyst",
                message="Loading pack details from Fullyst…", done=0, total=0)

        def download_progress(event: dict) -> None:
            event_type = event.get("type")
            index, total = event.get("index", 0), event.get("total", 0)
            if event_type == "retry":
                _update(job_id, phase="Retrying Fullyst download",
                        message=(f"Sticker {index} of {total}: temporary error · "
                                 f"retry {event['attempt']} of {max(1, event['maxAttempts'] - 1)} "
                                 f"({event.get('error', 'network error')})"))
            elif event_type == "failed":
                _update(job_id, phase="Sticker download skipped",
                        message=f"Sticker {index} of {total} unavailable: {event.get('error', 'download failed')}. Continuing.",
                        done=event["done"], total=total)
            else:
                origin = "from local cache" if event.get("fromCache") else "downloaded"
                _update(job_id, phase="Downloading from Fullyst",
                        message=f"Sticker {index} of {total} {origin} · {event['done']} of {total} complete.",
                        done=event["done"], total=total)

        set_name, title, items, failures = _fullyst_pack_items(
            slug, progress_callback=download_progress)
        source_count = len(items) + len(failures)
        if not items:
            if failures:
                first = failures[0]
                raise RuntimeError(
                    f"None of the {source_count} stickers could be downloaded. "
                    f"First failure: sticker {first['sourceIndex']} · {first['error']}")
            raise RuntimeError("Fullyst returned an empty sticker set.")
        descriptors = []
        labels = {"webm": "Video WebM", "webp": "Static WebP"}
        for i, item in enumerate(items):
            source_index = int(item.get("sourceIndex", i + 1))
            fmt = item["format"]
            kind = item["kind"]
            label = "Animated WebP" if fmt == "webp" and kind == "Animated" else labels.get(fmt, fmt.upper())
            descriptors.append({
                "index": i, "sourceIndex": source_index, "format": fmt,
                "formatLabel": label, "kind": kind, "emoji": item.get("emoji") or "📎",
                "fileUniqueId": str(item.get("file_unique_id") or ""),
                "name": str(item.get("name") or f"{source_index:02d}.{fmt}"),
            })

        thumbnails: list[bytes | None] = [None] * len(items)
        public_stickers = _job_stickers_for_picker(
            job_id, descriptors, thumbnails, preparing=True)
        with STATE_LOCK:
            job = JOBS.get(job_id)
            if job is not None:
                job.update(sourceItems=descriptors, thumbnails=thumbnails,
                           stickers=public_stickers, setName=set_name, title=title,
                           source="Fullyst", sourceSlug=slug,
                           failedItems=failures, failedCount=len(failures),
                           sourceItemCount=source_count)
        _update(job_id, phase="Preparing previews",
                message=f"Preparing sticker previews · 0 of {len(items)}",
                done=0, total=len(items))

        def thumbnail_progress(event: dict) -> None:
            index = event["index"]
            thumbnail = event["thumbnail"]
            with STATE_LOCK:
                job = JOBS.get(job_id)
                if job is not None:
                    job_thumbnails = job.get("thumbnails", [])
                    job_stickers = job.get("stickers", [])
                    if index < len(job_thumbnails):
                        job_thumbnails[index] = thumbnail
                    if index < len(job_stickers):
                        job_stickers[index]["previewReady"] = True
                        job_stickers[index]["previewAvailable"] = bool(thumbnail)
            reused = f" · {event['cacheHits']} cached" if event["cacheHits"] else ""
            _update(job_id, phase="Preparing previews",
                    message=(f"Preparing sticker previews · {event['done']} of "
                             f"{event['total']}{reused}"),
                    done=event["done"], total=event["total"])

        thumbnails, thumbnail_cache_hits = _prepare_thumbnails(items, thumbnail_progress)
        public_stickers = _job_stickers_for_picker(job_id, descriptors, thumbnails)

        with STATE_LOCK:
            job = JOBS.get(job_id)
            if job is not None:
                is_adult = bool(job.get("isAdult") or _fullyst_looks_adult(slug, title))
                job.update(sourceItems=descriptors, thumbnails=thumbnails,
                           stickers=public_stickers, setName=set_name, title=title,
                           source="Fullyst", sourceSlug=slug,
                           thumbnailCacheHits=thumbnail_cache_hits,
                           isAdult=is_adult, failedItems=failures,
                           failedCount=len(failures), sourceItemCount=source_count)
        message = f"{len(items)} stickers ready from Fullyst. Select some or convert the full pack."
        if failures:
            message = (f"{len(items)} of {source_count} stickers ready from Fullyst; "
                       f"{len(failures)} skipped. See the live activity log for details. "
                       "Fetch this pack again later to retry skipped stickers; successful downloads are reused from cache.")
        if thumbnail_cache_hits:
            message += f" Reused {thumbnail_cache_hits} cached poster thumbnails."
        _update(job_id, status="ready", phase="Choose stickers", message=message,
                done=source_count, total=source_count, title=title, stickers=public_stickers)
    except Exception as exc:                                          # noqa: BLE001
        _update(job_id, status="failed", phase="Stopped", message="Could not fetch the Fullyst pack.",
                error=str(exc)[:900])
    finally:
        with STATE_LOCK:
            global ACTIVE_JOB_ID
            if ACTIVE_JOB_ID == job_id:
                ACTIVE_JOB_ID = None


def _raw_item(set_name: str, descriptor: dict, title: str) -> dict:
    collection_id = descriptor.get("collectionId")
    if collection_id:
        with COLLECTION_LOCK:
            saved = _collection_find(_collection_read(), str(collection_id))
        if saved is None:
            raise RuntimeError("A saved sticker is no longer in the collection.")
        path = _collection_asset_path(saved)
        unique_id = str(descriptor.get("fileUniqueId") or collection_id)
        if not path.is_file():
            raise RuntimeError("A saved sticker file is missing from this PC.")
        return {"title": title, "name": descriptor.get("name") or saved.get("name"),
                "format": descriptor.get("format") or saved["format"],
                "emoji": descriptor.get("emoji") or saved.get("emoji") or "📎",
                "file_unique_id": unique_id, "bytes": path.read_bytes()}
    raw_dir = _cache_folder(set_name)
    unique_id = descriptor["fileUniqueId"]
    if not unique_id or "/" in unique_id or "\\" in unique_id:
        raise RuntimeError("A cached Telegram sticker file is missing. Fetch the pack again.")
    path = raw_dir / f"{descriptor['sourceIndex']:03d}_{unique_id}.{descriptor['format']}"
    if not path.is_file():
        raise RuntimeError("A cached Telegram sticker file is missing. Fetch the pack again.")
    return {"title": title, "name": descriptor["name"], "format": descriptor["format"],
            "emoji": descriptor["emoji"], "file_unique_id": unique_id,
            "bytes": path.read_bytes()}


def _run_conversion(job_id: str, selected_indexes: list[int], mode: str) -> None:
    started = time.perf_counter()
    jobs = min(os.cpu_count() or 1, 8)
    job_dir: Path | None = None
    cache_root: Path | None = None
    source = ""
    try:
        with STATE_LOCK:
            job = JOBS[job_id]
            set_name = job["setName"]
            title = job["title"]
            source = job.get("source", "")
            descriptors = list(job["sourceItems"])
            job_dir = Path(job["outputDir"])
        selected = [d for d in descriptors if d["index"] in set(selected_indexes)]
        items = [_raw_item(set_name, descriptor, title) for descriptor in selected]
        if not items:
            raise RuntimeError("No stickers were selected.")
        _update(job_id, status="running", phase="Converting",
                message=f"Preparing {len(items)} stickers…", done=0, total=len(items))
        cache_root = _cache_root(set_name)
        work = cache_root / "work"
        report = convert.convert_pack(
            items, title, work, publisher="wa-sticker-bridge", verbose=False,
            static_format="webp", jobs=jobs, webm_method=DEFAULT_WEBM_METHOD,
            process_start_method="spawn",
            progress_callback=lambda event: _update(
                job_id, phase="Converting",
                message=f"Converting stickers · {event['done']} of {event['total']}",
                done=event["done"], total=event["total"]))

        packs, pack_files, preview_entries = [], [], []
        for pack_index, pack in enumerate(report["pack"].get("wastickers", [])):
            source_path = Path(pack["file"])
            output_path = job_dir / source_path.name
            shutil.copy2(source_path, output_path)
            entries = [str(entry["file"]) for entry in pack.get("entries", [])]
            preview_entries.append(entries)
            packs.append({
                "name": pack.get("name") or output_path.stem,
                "fileName": output_path.name,
                "kind": pack["kind"],
                "count": pack["count"],
                "sizeLabel": f"{output_path.stat().st_size / (1024 * 1024):.1f} MB",
                "downloadUrl": f"/download/{job_id}/{pack_index}",
                "previewUrls": [f"/api/job/{job_id}/output/{pack_index}/{i}"
                                for i in range(len(entries))],
            })
            pack_files.append({"name": output_path.name, "path": str(output_path)})

        valid = all(v.get("pass") for v in report["validation"])
        lint_ok = all(p.get("lint", {}).get("ok")
                      for p in report["pack"].get("wastickers", []))
        loose_count = len(report["pack"].get("loose", []))
        # Keep only importable archives in the visible run folder; source and converted
        # Telegram/Fullyst caches stay available for safe matching-source repeat conversions.
        # A saved-collection build has its inputs in the durable collection, so its unique
        # per-job cache can be discarded after the output has been copied.
        if source == "Collection":
            shutil.rmtree(cache_root, ignore_errors=True)
        else:
            for child in cache_root.iterdir():
                if child.name in {"raw", "work"}:
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try:
                        child.unlink()
                    except OSError:
                        pass

        if not packs:
            if job_dir is not None:
                shutil.rmtree(job_dir, ignore_errors=True)
            message = "No WhatsApp pack could be made. Each pack needs at least 3 stickers of the same type."
        elif not valid or not lint_ok:
            message = "A pack was created, but validation found a problem. Please try again."
        else:
            message = f"{len(packs)} WhatsApp pack(s) ready."
            if loose_count:
                message += f" {loose_count} sticker(s) were left out because they could not form a pack of 3."
        _update(job_id, status="done", phase="Complete", message=message,
                done=len(items), total=len(items), packs=packs, packFiles=pack_files,
                previewEntries=preview_entries,
                elapsedSeconds=round(time.perf_counter() - started, 1))
    except Exception as exc:                                          # noqa: BLE001
        if source == "Collection" and cache_root is not None:
            shutil.rmtree(cache_root, ignore_errors=True)
        if job_dir is not None:
            shutil.rmtree(job_dir, ignore_errors=True)
        _update(job_id, status="failed", phase="Stopped", message="Could not create the pack.",
                error=str(exc)[:900])
    finally:
        with STATE_LOCK:
            global ACTIVE_JOB_ID
            if ACTIVE_JOB_ID == job_id:
                ACTIVE_JOB_ID = None


class Handler(BaseHTTPRequestHandler):
    server_version = "StickerStudio/1.0"

    def log_message(self, _format, *args):
        return

    def _send(self, status: int, body: bytes,
              content_type: str = "application/json; charset=utf-8",
              extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        cache_control = (PREVIEW_HTTP_CACHE_CONTROL
                         if content_type.lower().startswith("image/") else "no-store")
        if extra_headers:
            cache_control = next((value for key, value in extra_headers.items()
                                  if key.lower() == "cache-control"), cache_control)
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra_headers:
            for key, value in extra_headers.items():
                if key.lower() != "cache-control":
                    self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, _json_bytes(obj))

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/config":
            saved = bool(_load_saved_token()) if not DEMO_MODE else False
            self._json(200, {"demo": DEMO_MODE,
                             "tokenConfigured": bool(os.environ.get("TG_BOT_TOKEN")) and not DEMO_MODE,
                             "tokenSaved": saved,
                             "rememberSupported": _remember_supported() and not DEMO_MODE})
            return
        if path == "/api/latest":
            with STATE_LOCK:
                self._json(200, {"id": LATEST_JOB_ID})
            return
        if path == "/api/collection":
            try:
                items, groups = _collection_public_data()
                self._json(200, {"items": items, "count": len(items), "groups": groups})
            except RuntimeError as exc:
                self._json(500, {"error": str(exc)})
            return
        if path == "/api/fullyst/visited-pages":
            if DEMO_MODE:
                self._json(200, {"pages": [], "count": 0})
                return
            try:
                pages = _get_fullyst_visited_pages()
                self._json(200, {"pages": pages, "count": len(pages)})
            except RuntimeError as exc:
                self._json(500, {"error": str(exc)})
            return
        if path == "/api/fullyst/animated-preview":
            if DEMO_MODE:
                self._json(403, {"error": "Catalog previews are disabled in the view-only preview."})
                return
            slug = str(parse_qs(urlparse(self.path).query).get("slug", [""])[0]).strip()
            try:
                preview = _fullyst_animated_catalog_preview(slug)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            except Exception as exc:                                   # noqa: BLE001
                self._json(502, {"error": str(exc)[:400]})
                return
            self._send(200, preview, "image/gif")
            return
        if path == "/api/fullyst/search":
            if DEMO_MODE:
                self._json(403, {"error": "Catalog search is disabled in the view-only preview. Run the app locally."})
                return
            params = parse_qs(urlparse(self.path).query)
            try:
                query = str(params.get("q", [""])[0])
                page = int(params.get("page", ["1"])[0])
                result = _fullyst_search(query, page)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            except Exception as exc:                                   # noqa: BLE001
                self._json(502, {"error": str(exc)[:500]})
                return
            self._json(200, result)
            return
        bits = path.strip("/").split("/")
        if len(bits) == 4 and bits[:3] == ["api", "collection", "thumb"]:
            item_id = bits[3]
            try:
                thumb_path = _collection_thumb_path(item_id)
            except ValueError:
                self._json(404, {"error": "Saved sticker preview not found."})
                return
            if not thumb_path.is_file():
                self._json(404, {"error": "Saved sticker preview not available."})
                return
            try:
                self._send(200, thumb_path.read_bytes(), "image/png")
            except OSError:
                self._json(404, {"error": "Saved sticker preview not available."})
            return
        if len(bits) == 4 and bits[:3] == ["api", "collection", "preview"]:
            item_id = bits[3]
            try:
                with COLLECTION_LOCK:
                    entry = _collection_find(_collection_read(), item_id)
                if entry is None:
                    self._json(404, {"error": "Saved sticker not found."})
                    return
                asset_path = _collection_asset_path(entry)
            except (ValueError, RuntimeError):
                self._json(404, {"error": "Saved sticker not found."})
                return
            if not asset_path.is_file():
                self._json(404, {"error": "Saved sticker file is missing."})
                return
            if entry.get("kind") == "Animated":
                preview_path = COLLECTION_PREVIEWS_DIR / f"{item_id}.gif"
                preview = None
                try:
                    with ANIM_PREVIEW_LOCK:
                        if preview_path.is_file():
                            preview = preview_path.read_bytes()
                        else:
                            raw_item = {"title": entry.get("sourceTitle", "Saved stickers"),
                                        "name": entry.get("name", "saved-sticker"),
                                        "format": entry.get("format", "webp"),
                                        "emoji": entry.get("emoji", "📎"),
                                        "bytes": asset_path.read_bytes()}
                            preview = _make_animated_preview(raw_item)
                            if preview:
                                COLLECTION_PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
                                temp_preview = preview_path.with_suffix(".gif.tmp")
                                temp_preview.write_bytes(preview)
                                os.replace(temp_preview, preview_path)
                except Exception:                                       # noqa: BLE001
                    preview = None
                if preview:
                    self._send(200, preview, "image/gif")
                    return
                thumb_path = _collection_thumb_path(item_id)
                if thumb_path.is_file():
                    self._send(200, thumb_path.read_bytes(), "image/png")
                    return
                self._json(404, {"error": "Saved sticker preview is unavailable."})
                return
            media_type = {"webp": "image/webp", "png": "image/png",
                          "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
                              str(entry.get("format", "")).lower(), "image/webp")
            try:
                self._send(200, asset_path.read_bytes(), media_type)
            except OSError:
                self._json(404, {"error": "Saved sticker preview is unavailable."})
            return
        if len(bits) == 5 and bits[:2] == ["api", "job"] and bits[3] == "thumb":
            job_id = bits[2]
            if not bits[4].isdigit():
                self._json(404, {"error": "Preview not found."})
                return
            index = int(bits[4])
            with STATE_LOCK:
                job = JOBS.get(job_id)
                thumbs = job.get("thumbnails", []) if job else []
                thumb = thumbs[index] if index < len(thumbs) else None
            if not thumb:
                self._json(404, {"error": "Preview not available."})
                return
            self._send(200, thumb, "image/png")
            return
        if len(bits) == 5 and bits[:2] == ["api", "job"] and bits[3] == "preview":
            job_id = bits[2]
            if not bits[4].isdigit():
                self._json(404, {"error": "Preview not found."})
                return
            index = int(bits[4])
            with STATE_LOCK:
                job = JOBS.get(job_id)
                descriptors = job.get("sourceItems", []) if job else []
                if index >= len(descriptors):
                    self._json(404, {"error": "Preview not found."})
                    return
                descriptor = descriptors[index]
                cached = job.get("animatedPreviews", {}).get(index)
                fallback = job.get("thumbnails", [])[index] if index < len(job.get("thumbnails", [])) else None
                set_name, title = job.get("setName", ""), job.get("title", "")
            if cached:
                self._send(200, cached, "image/gif")
                return
            if descriptor.get("kind") != "Animated":
                try:
                    item = _raw_item(set_name, descriptor, title)
                    media_type = {"webp": "image/webp", "png": "image/png",
                                  "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
                                      str(descriptor.get("format", "")).lower(), "image/webp")
                    self._send(200, item["bytes"], media_type)
                except (OSError, RuntimeError, KeyError, ValueError):
                    if fallback:
                        self._send(200, fallback, "image/png")
                    else:
                        self._json(404, {"error": "Preview not available."})
                return
            try:
                with ANIM_PREVIEW_LOCK:
                    with STATE_LOCK:
                        job = JOBS.get(job_id)
                        cached = job.get("animatedPreviews", {}).get(index) if job else None
                        descriptors = job.get("sourceItems", []) if job else []
                        descriptor = descriptors[index] if index < len(descriptors) else None
                        fallback = job.get("thumbnails", [])[index] if job and index < len(job.get("thumbnails", [])) else None
                        set_name, title = (job.get("setName", ""), job.get("title", "")) if job else ("", "")
                    if not cached and descriptor:
                        cached = _make_animated_preview(_raw_item(set_name, descriptor, title))
                        if cached:
                            with STATE_LOCK:
                                job = JOBS.get(job_id)
                                if job is not None:
                                    job.setdefault("animatedPreviews", {})[index] = cached
            except Exception:                                          # noqa: BLE001
                cached = None
            if cached:
                self._send(200, cached, "image/gif")
            elif fallback:
                self._send(200, fallback, "image/png")
            else:
                self._json(404, {"error": "Preview not available."})
            return
        if len(bits) == 6 and bits[:2] == ["api", "job"] and bits[3] == "output":
            job_id = bits[2]
            if not bits[4].isdigit() or not bits[5].isdigit():
                self._json(404, {"error": "Preview not found."})
                return
            pack_index, sticker_index = int(bits[4]), int(bits[5])
            with STATE_LOCK:
                job = JOBS.get(job_id)
                files = job.get("packFiles", []) if job and job.get("status") == "done" else []
                entries = job.get("previewEntries", []) if job else []
                if pack_index >= len(files) or pack_index >= len(entries) \
                        or sticker_index >= len(entries[pack_index]):
                    self._json(404, {"error": "Preview not found."})
                    return
                archive_path = Path(files[pack_index]["path"])
                entry_name = entries[pack_index][sticker_index]
            try:
                with ZipFile(archive_path) as archive:
                    data = archive.read(entry_name)
            except (OSError, BadZipFile, KeyError):
                self._json(404, {"error": "Preview not available."})
                return
            self._send(200, data, "image/webp")
            return
        if len(bits) == 3 and bits[:2] == ["api", "job"]:
            job_id = bits[2]
            with STATE_LOCK:
                job = JOBS.get(job_id)
                if job:
                    self._json(200, _copy_public_state(job))
                else:
                    self._json(404, {"error": "This app session has expired. Fetch the pack again."})
            return
        if path.startswith("/download/"):
            parts = path.strip("/").split("/")
            if len(parts) != 3 or not parts[2].isdigit():
                self._json(404, {"error": "File not found."})
                return
            job_id, index = parts[1], int(parts[2])
            with STATE_LOCK:
                job = JOBS.get(job_id)
                files = job.get("packFiles", []) if job and job.get("status") == "done" else []
                if index >= len(files):
                    self._json(404, {"error": "File not found."})
                    return
                file_path = Path(files[index]["path"])
                filename = files[index]["name"]
            try:
                data = file_path.read_bytes()
            except OSError:
                self._json(404, {"error": "The pack is no longer available."})
                return
            disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
            self._send(200, data, "application/octet-stream", {"Content-Disposition": disposition})
            return
        self._json(404, {"error": "Not found."})

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/token/forget":
            if DEMO_MODE:
                self._json(403, {"error": "This preview is view-only."})
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json":
                self._json(415, {"error": "JSON request required."})
                return
            _clear_saved_token()
            self._json(200, {"ok": True})
            return
        if path not in ("/api/fetch", "/api/convert", "/api/fullyst/fetch",
                        "/api/fullyst/visited-pages", "/api/collection/save",
                        "/api/collection/remove", "/api/collection/undo", "/api/collection/convert",
                        "/api/collection/groups/create", "/api/collection/groups/move",
                        "/api/collection/groups/delete"):
            self._json(404, {"error": "Not found."})
            return
        if DEMO_MODE:
            self._json(403, {"error": "This preview is view-only. Run the app locally to fetch and convert packs."})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY:
                raise ValueError("Request is empty or too large.")
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("The request must be a JSON object.")
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
            return

        global ACTIVE_JOB_ID, LATEST_JOB_ID
        if path == "/api/fullyst/visited-pages":
            try:
                page = data.get("page")
                if type(page) is not int:
                    raise ValueError("Enter a valid Fullyst catalog page number.")
                result = _mark_fullyst_page_visited(page)
                self._json(200, result)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                self._json(500, {"error": str(exc)})
            return
        if path == "/api/collection/save":
            try:
                job_id = str(data.get("job_id", "")).strip()
                index = data.get("index")
                if type(index) is not int:
                    raise ValueError("Choose a sticker from the current pack.")
                result = _save_collection_sticker(job_id, index)
                self._json(200, result)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                self._json(409, {"error": str(exc)})
            return
        if path == "/api/collection/remove":
            try:
                item_id = str(data.get("id", ""))
                count, undo_token = _remove_collection_sticker(item_id, record_undo=True)
                self._json(200, {"ok": True, "count": count, "undoToken": undo_token})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                self._json(409, {"error": str(exc)})
            return
        if path == "/api/collection/undo":
            try:
                token = str(data.get("undoToken", ""))
                self._json(200, _undo_collection_action(token))
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                self._json(409, {"error": str(exc)})
            return
        if path == "/api/collection/groups/create":
            try:
                name = data.get("name")
                if not isinstance(name, str):
                    raise ValueError("Enter a name for the new group.")
                group, groups = _create_collection_group(name)
                self._json(200, {"ok": True, "group": group, "groups": groups})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                self._json(409, {"error": str(exc)})
            return
        if path == "/api/collection/groups/move":
            try:
                item_ids = data.get("ids", [])
                group_id = data.get("groupId", UNGROUPED_GROUP_ID)
                if not isinstance(group_id, str):
                    raise ValueError("Choose a valid saved-sticker group.")
                moved, undo_token = _move_collection_stickers(item_ids, group_id, record_undo=True)
                self._json(200, {"ok": True, "moved": moved, "undoToken": undo_token})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                self._json(409, {"error": str(exc)})
            return
        if path == "/api/collection/groups/delete":
            try:
                group_id = data.get("groupId", "")
                if not isinstance(group_id, str):
                    raise ValueError("Choose a valid saved-sticker group.")
                name, moved, groups = _delete_collection_group(group_id)
                self._json(200, {"ok": True, "name": name, "moved": moved, "groups": groups})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                self._json(409, {"error": str(exc)})
            return
        if path == "/api/collection/convert":
            try:
                selected = data.get("selected", [])
                if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
                    raise ValueError("The saved-sticker selection is invalid.")
                title = str(data.get("title", "")).strip()
                job_id = _start_collection_conversion(selected, title)
                self._json(202, {"id": job_id})
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                self._json(409, {"error": str(exc)})
            return
        if path == "/api/fullyst/fetch":

            try:
                slug = str(data.get("slug", "")).strip()
                if "://" in slug:
                    parsed = urlparse(slug)
                    if parsed.scheme != "https" or parsed.hostname != "fullyst.com":
                        raise ValueError("Use a Fullyst pack page URL.")
                    slug = parsed.path.rstrip("/").split("/")[-1]
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", slug):
                    raise ValueError("Select a pack from the Fullyst results first.")
                adult_hint = data.get("is_adult") is True or _fullyst_looks_adult(slug)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            with STATE_LOCK:
                if ACTIVE_JOB_ID and JOBS.get(ACTIVE_JOB_ID, {}).get("status") in ("queued", "running"):
                    self._json(409, {"error": "A job is already running. Wait for it to finish first."})
                    return
                _trim_jobs()
                job_id = _new_job_id()
                JOBS[job_id] = {"id": job_id, "status": "queued", "phase": "Starting",
                                "message": "Preparing Fullyst import…", "done": 0,
                                "total": 0, "packs": [], "packFiles": [], "source": "Fullyst",
                                "sourceSlug": slug, "failedCount": 0, "isAdult": adult_hint,
                                "activityLog": [], "_activitySeq": 0,
                                "outputDir": None, "created": time.time()}
                _append_activity_log(JOBS[job_id], "Queued", "Preparing Fullyst import…")
                ACTIVE_JOB_ID = LATEST_JOB_ID = job_id
            threading.Thread(target=_run_fullyst_fetch, args=(job_id, slug),
                             name=f"fullyst-fetch-{job_id}", daemon=True).start()
            self._json(202, {"id": job_id})
            return
        if path == "/api/fetch":
            try:
                link = str(data.get("pack_link", "")).strip()
                set_name = convert.parse_set_name(link)
                supplied = str(data.get("token", "")).strip()
                remember = data.get("remember_token") is True
                if remember and not _remember_supported():
                    raise ValueError("Secure token saving is supported on Windows only.")
                token = supplied or _load_saved_token() or os.environ.get("TG_BOT_TOKEN", "")
                if not token:
                    raise ValueError("Enter your Telegram Bot token.")
                if not remember:
                    try:
                        _clear_saved_token()
                    except OSError as exc:
                        raise ValueError("Could not remove the saved token from this PC.") from exc
            except (ValueError, TypeError) as exc:
                self._json(400, {"error": str(exc)})
                return
            with STATE_LOCK:
                if ACTIVE_JOB_ID and JOBS.get(ACTIVE_JOB_ID, {}).get("status") in ("queued", "running"):
                    self._json(409, {"error": "A job is already running. Wait for it to finish first."})
                    return
                _trim_jobs()
                job_id = _new_job_id()
                JOBS[job_id] = {"id": job_id, "status": "queued", "phase": "Starting",
                                "message": "Preparing to fetch stickers…", "done": 0,
                                "total": 0, "packs": [], "packFiles": [],
                                "activityLog": [], "_activitySeq": 0,
                                "outputDir": None, "created": time.time()}
                _append_activity_log(JOBS[job_id], "Queued", "Preparing to fetch stickers…")
                ACTIVE_JOB_ID = LATEST_JOB_ID = job_id
            threading.Thread(target=_run_fetch, args=(job_id, set_name, token, remember),
                             name=f"sticker-fetch-{job_id}", daemon=True).start()
            self._json(202, {"id": job_id})
            return

        try:
            job_id = str(data.get("job_id", "")).strip()
            mode = str(data.get("mode", ""))
            selected = data.get("selected", [])
            if mode not in ("selected", "full"):
                raise ValueError("Choose selected stickers or the full pack.")
            if not isinstance(selected, list) or any(type(x) is not int for x in selected):
                raise ValueError("The sticker selection is invalid.")
        except (ValueError, TypeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        with STATE_LOCK:
            if ACTIVE_JOB_ID and JOBS.get(ACTIVE_JOB_ID, {}).get("status") in ("queued", "running"):
                self._json(409, {"error": "A job is already running. Wait for it to finish first."})
                return
            job = JOBS.get(job_id)
            if not job or job.get("status") != "ready":
                self._json(409, {"error": "Fetch a sticker pack before converting it."})
                return
            descriptors = list(job.get("sourceItems", []))
            available = {item["index"] for item in descriptors}
            if mode == "full":
                selected = sorted(available)
            else:
                if not selected or len(set(selected)) != len(selected) \
                        or any(index not in available for index in selected):
                    self._json(400, {"error": "Select stickers from the preview first."})
                    return
                selected = sorted(selected)
                kinds = {kind: sum(1 for item in descriptors
                                   if item["index"] in selected and item["kind"] == kind)
                         for kind in ("Animated", "Static")}
                if max(kinds.values(), default=0) < 3:
                    self._json(400, {"error": "Select at least 3 animated or 3 static stickers to create a WhatsApp pack."})
                    return
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            job_dir = RUNS_DIR / f"{stamp}_{convert._slug(job.get('title') or job['setName'])}_{job_id}"
            job_dir.mkdir(parents=True, exist_ok=True)
            job.update(status="queued", phase="Starting", message="Preparing your pack…",
                       done=0, total=len(selected), packs=[], packFiles=[], previewEntries=[],
                       outputDir=str(job_dir))
            _append_activity_log(job, "Starting", "Preparing your pack…")
            ACTIVE_JOB_ID = LATEST_JOB_ID = job_id
        threading.Thread(target=_run_conversion, args=(job_id, selected, mode),
                         name=f"sticker-convert-{job_id}", daemon=True).start()
        self._json(202, {"id": job_id})


def main() -> int:
    global DEMO_MODE
    parser = argparse.ArgumentParser(description="Local browser UI for Telegram sticker conversion")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (default: 127.0.0.1; keep local for token safety)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--demo", action="store_true",
                        help="serve a view-only UI preview; never accept Bot tokens or run conversions")
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open a browser window automatically")
    args = parser.parse_args()
    DEMO_MODE = args.demo
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"Could not start on {args.host}:{args.port} ({exc}). Try: python webapp.py --port 8766",
              file=sys.stderr)
        return 2
    server.daemon_threads = True
    local_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{local_host}:{args.port}/"
    mode = " · UI preview only" if DEMO_MODE else " · local conversions"
    print(f"Sticker Studio is ready{mode}: {url}", flush=True)
    print("Close this window to stop the app. Downloads are saved under webapp-runs/.", flush=True)
    if not args.no_browser and not DEMO_MODE:
        try:
            webbrowser.open(url)
        except Exception:                                             # noqa: BLE001
            pass
    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        print("\nStopping Sticker Studio…", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
