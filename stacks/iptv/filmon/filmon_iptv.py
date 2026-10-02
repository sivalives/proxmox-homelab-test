#!/usr/bin/env python3
"""
FilmOn -> Jellyfin / VLC helper (hybrid).

Fixes the two problems that were observed:

1. API-v2 returns 502 → we stay on the working /ajax/getChannelInfo path.
2. Jellyfin hangs while VLC plays → segment local URLs are now stable
   (derived from channel + variant + MEDIA-SEQUENCE).  Playlist refreshes
   no longer invent new tokens, so FFmpeg/Jellyfin can track the live window.

Endpoints
  GET /                     status
  GET /playlist.m3u         M3U
  GET /iptv/filmon.m3u
  GET /stream/<id>.m3u8     stable HLS entry point
  GET /hls/<id>/<token>     rewritten playlists + segments
  GET /guide.xml            XMLTV
  GET /iptv/filmon.xml
  GET /health
  GET /refresh              force channel re-scan
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.sax.saxutils import escape, quoteattr

import requests
from flask import Flask, Response, abort, jsonify


# ============================================================
# SETTINGS
# ============================================================

def env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")


PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL", "http://192.168.1.107:8765"
).rstrip("/")

M3U_OUT_PATH = Path(os.getenv("M3U_OUT_PATH", "/iptv/filmon.m3u"))
CACHE_FILE = Path(os.getenv("CACHE_FILE", "/data/filmon_channels.json"))
EPG_OUT_PATH = Path(os.getenv("EPG_OUT_PATH", "/iptv/filmon.xml"))

REFRESH_HOURS = float(os.getenv("REFRESH_HOURS", "24"))
EPG_REFRESH_HOURS = float(os.getenv("EPG_REFRESH_HOURS", "6"))
EPG_ENABLED = env_bool("EPG_ENABLED", True)

MIN_CHANNEL_ID = int(os.getenv("MIN_CHANNEL_ID", "1"))
MAX_CHANNEL_ID = int(os.getenv("MAX_CHANNEL_ID", "2000"))
WORKERS = int(os.getenv("WORKERS", "8"))

LOGO_SIZE = os.getenv("LOGO_SIZE", "extra_big")
SKIP_ADULT = env_bool("SKIP_ADULT", True)
SKIP_VR_EXP = env_bool("SKIP_VR_EXP", True)
SKIP_VOD_VOX = env_bool("SKIP_VOD_VOX", True)
HD_ONLY = env_bool("HD_ONLY", True)
FREE_ONLY = env_bool("FREE_ONLY", False)
PREFERRED_QUALITY = os.getenv("PREFERRED_QUALITY", "high")

FILMON_COOKIE = os.getenv("FILMON_COOKIE", "")
API_URL = os.getenv(
    "FILMON_API", "https://www.filmon.com/ajax/getChannelInfo"
)
EPG_API = os.getenv(
    "FILMON_EPG_API", "https://www.filmon.com/api-v2/tvguide/{}"
)
WARMUP_URL = os.getenv(
    "FILMON_WARMUP_URL", "https://www.filmon.com/tv/bbc-news"
)

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
)

# HLS
HLS_TOKEN_TTL = int(os.getenv("HLS_TOKEN_TTL", "86400"))
HLS_UPSTREAM_REFRESH = int(os.getenv("HLS_UPSTREAM_REFRESH", "15"))
HLS_TIMEOUT = int(os.getenv("HLS_TIMEOUT", "20"))
HLS_RETRY_COUNT = int(os.getenv("HLS_RETRY_COUNT", "3"))
HLS_SEGMENT_CACHE_TTL = int(os.getenv("HLS_SEGMENT_CACHE_TTL", "900"))
HLS_SEGMENT_CACHE_MAX = int(os.getenv("HLS_SEGMENT_CACHE_MAX", "4096"))
# Never block the playlist response with a prefetch.
HLS_PREFETCH_ENABLED = env_bool("HLS_PREFETCH_ENABLED", False)
HLS_PREFETCH_WORKERS = int(os.getenv("HLS_PREFETCH_WORKERS", "8"))
HLS_PREFETCH_MAX_SEGMENTS = int(os.getenv("HLS_PREFETCH_MAX_SEGMENTS", "5"))

# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("filmon")

# ============================================================
# Flask + global state
# ============================================================

app = Flask(__name__)

lock = threading.Lock()
channels: list = []
stats: Counter = Counter()

_hls_lock = threading.RLock()
# token -> metadata
_hls_urls: dict = {}
# token -> {body, content_type, created}
_hls_segment_cache: dict = {}
# (channel, sequence) -> {body, content_type, created, variant_index}
_hls_sequence_cache: dict = {}
_hls_refresh_locks: dict = {}
_hls_executor = ThreadPoolExecutor(max_workers=HLS_PREFETCH_WORKERS)

# ============================================================
# HTTP session (one per worker thread)
# ============================================================

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Origin": "https://www.filmon.com",
    "Referer": "https://www.filmon.com/",
    "X-Requested-With": "XMLHttpRequest",
}
if FILMON_COOKIE:
    HEADERS["Cookie"] = FILMON_COOKIE

_local = threading.local()
_logged_cookies = False


def new_session() -> requests.Session:
    global _logged_cookies
    s = requests.Session()
    s.headers.update(HEADERS)

    if not FILMON_COOKIE:
        s.cookies.set(
            "ftv_site_mode", "classic",
            domain="www.filmon.com", path="/",
        )
        try:
            s.get(WARMUP_URL, timeout=15)
        except Exception as e:
            log.warning("Warm-up request failed: %r", e)

        if not _logged_cookies:
            _logged_cookies = True
            log.info(
                "Session cookies from warm-up: %s",
                sorted(s.cookies.keys()),
            )
    return s


def get_session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = new_session()
    return _local.s


# ============================================================
# FilmOn channel API (working ajax path)
# ============================================================

def get_channel_data(n: int, quality: str = "high") -> dict | None:
    j = None
    for attempt in (1, 2):
        try:
            s = get_session()
            r = s.post(
                API_URL,
                data={"channel_id": n, "quality": quality},
                timeout=15,
            )
            if r.status_code in (404, 405):
                r = s.get(
                    API_URL,
                    params={"channel_id": n, "quality": quality},
                    timeout=15,
                )
            j = r.json()
        except Exception as e:
            if stats["request_failed"] < 3:
                log.warning("Request for channel %s failed: %r", n, e)
            stats["request_failed"] += 1
            return None

        if (
            isinstance(j, dict)
            and j.get("streamName") == "maintenance"
            and attempt == 1
            and not FILMON_COOKIE
        ):
            _local.s = new_session()
            continue
        break

    if isinstance(j, dict) and isinstance(j.get("data"), dict):
        j = j["data"]
    return j if isinstance(j, dict) else None


def _get_stream_url(channel: int) -> str | None:
    d = get_channel_data(channel, PREFERRED_QUALITY)
    if not d or not d.get("streams"):
        return None
    streams = d["streams"]
    pick = next(
        (s for s in streams if s.get("quality") == PREFERRED_QUALITY),
        streams[0],
    )
    url = pick.get("url")
    if not url or "m3u8" not in url.lower():
        return None
    return url


# ============================================================
# Channel scanning
# ============================================================

def probe(n: int):
    d = get_channel_data(n, "high")
    if d is None:
        return None

    if not d.get("title") or not d.get("streams"):
        if d.get("streamName") == "maintenance":
            stats["maintenance_placeholder"] += 1
        else:
            stats["no_channel_or_no_streams"] += 1
        return None

    if (
        (SKIP_VR_EXP and d.get("is360"))
        or (SKIP_ADULT and d.get("is_adult"))
        or (SKIP_VOD_VOX and (d.get("is_vod") or d.get("is_vox")))
    ):
        stats["skipped_type"] += 1
        return None

    if FREE_ONLY and not d.get("is_free"):
        stats["not_free"] += 1
        return None

    if HD_ONLY and not any(
        s.get("quality") == "high" for s in d["streams"]
    ):
        stats["no_hd_stream"] += 1
        return None

    stats["kept"] += 1
    if not d.get("is_free"):
        stats["kept_but_not_flagged_free"] += 1

    return {
        "id": n,
        "name": d["title"],
        "group": (d.get("group") or "Other").title(),
    }


def build_m3u() -> str:
    lines = ["#EXTM3U"]
    for c in channels:
        logo = (
            f"https://static.filmon.com/assets/channels/"
            f"{c['id']}/{LOGO_SIZE}_logo.png"
        )
        lines.append(
            f'#EXTINF:-1 tvg-id="{c["id"]}" '
            f'tvg-logo="{logo}" '
            f'group-title="{c["group"]}",'
            f'{c["name"]}'
        )
        lines.append(f"{PUBLIC_BASE_URL}/stream/{c['id']}.m3u8")
    return "\n".join(lines) + "\n"


def write_m3u() -> None:
    try:
        M3U_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = M3U_OUT_PATH.with_suffix(".tmp")
        tmp.write_text(build_m3u(), encoding="utf-8")
        tmp.replace(M3U_OUT_PATH)
        os.chmod(M3U_OUT_PATH, 0o644)
        log.info("Wrote %s", M3U_OUT_PATH)
    except Exception as e:
        log.warning("Could not write %s: %s", M3U_OUT_PATH, e)


def scan() -> None:
    global channels
    log.info("Scanning FilmOn channels...")
    stats.clear()

    with ThreadPoolExecutor(WORKERS) as ex:
        found = [
            c
            for c in ex.map(probe, range(MIN_CHANNEL_ID, MAX_CHANNEL_ID))
            if c
        ]

    log.info("Scan stats: %s", dict(stats))
    if not found:
        log.warning("Scan found nothing – keeping previous list")
        return

    with lock:
        channels = found
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(json.dumps(found), encoding="utf-8")
        except Exception as e:
            log.warning("Cache write failed: %s", e)

    log.info("Scanned: %s channels", len(found))
    write_m3u()


def background() -> None:
    global channels
    if CACHE_FILE.exists():
        try:
            channels = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            write_m3u()
            age = time.time() - CACHE_FILE.stat().st_mtime
            if age < REFRESH_HOURS * 3600:
                time.sleep(REFRESH_HOURS * 3600 - age)
        except Exception:
            pass

    while True:
        scan()
        time.sleep(REFRESH_HOURS * 3600)


# ============================================================
# XMLTV EPG (unchanged from working script)
# ============================================================

epg_xml: str = ""
epg_stats: Counter = Counter()


def parse_guide(j) -> list:
    if isinstance(j, dict):
        for key in ("data", "programmes", "programs", "tvguide"):
            if isinstance(j.get(key), list):
                return j[key]
        return [j] if "programme_name" in j else []
    return j if isinstance(j, list) else []


def fetch_guide(n: int) -> list:
    try:
        j = get_session().get(EPG_API.format(n), timeout=15).json()
    except Exception:
        epg_stats["failed"] += 1
        return []
    items = [
        p
        for p in parse_guide(j)
        if (
            isinstance(p, dict)
            and p.get("programme_name")
            and p.get("startdatetime")
            and p.get("enddatetime")
        )
    ]
    if not items:
        epg_stats["empty"] += 1
    return items


def xmltv_time(ts) -> str:
    return datetime.fromtimestamp(
        int(ts), tz=timezone.utc
    ).strftime("%Y%m%d%H%M%S +0000")


def build_epg_xml(snapshot: list, guides: dict) -> str:
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<tv generator-info-name="filmon-proxy">',
    ]
    for c in snapshot:
        logo = (
            f"https://static.filmon.com/assets/channels/"
            f"{c['id']}/{LOGO_SIZE}_logo.png"
        )
        out.append(
            f'<channel id="{c["id"]}">'
            f'<display-name>{escape(c["name"])}</display-name>'
            f'<icon src={quoteattr(logo)}/>'
            f"</channel>"
        )
    for c in snapshot:
        for p in guides.get(c["id"], []):
            try:
                start = xmltv_time(p["startdatetime"])
                stop = xmltv_time(p["enddatetime"])
            except Exception:
                continue
            parts = [
                f'<programme start="{start}" stop="{stop}" '
                f'channel="{c["id"]}">',
                f'<title>{escape(str(p["programme_name"]))}</title>',
            ]
            if p.get("programme_description"):
                parts.append(
                    f'<desc>{escape(str(p["programme_description"]))}</desc>'
                )
            imgs = [
                i.get("url")
                for i in (p.get("images") or [])
                if isinstance(i, dict) and i.get("url")
            ]
            if imgs:
                parts.append(f'<icon src={quoteattr(imgs[0])}/>')
            parts.append("</programme>")
            out.append("".join(parts))
    out.append("</tv>")
    return "\n".join(out) + "\n"


def refresh_epg() -> None:
    global epg_xml
    snapshot = list(channels)
    if not snapshot:
        return
    epg_stats.clear()
    ids = [c["id"] for c in snapshot]
    with ThreadPoolExecutor(WORKERS) as ex:
        guides = dict(zip(ids, ex.map(fetch_guide, ids)))
    total = sum(len(v) for v in guides.values())
    log.info(
        "EPG: %s programmes for %s of %s channels; stats %s",
        total,
        sum(1 for v in guides.values() if v),
        len(ids),
        dict(epg_stats),
    )
    if total == 0:
        log.warning("EPG empty – not writing guide file")
        return
    epg_xml = build_epg_xml(snapshot, guides)
    try:
        EPG_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = EPG_OUT_PATH.with_suffix(".tmp")
        tmp.write_text(epg_xml, encoding="utf-8")
        tmp.replace(EPG_OUT_PATH)
        os.chmod(EPG_OUT_PATH, 0o644)
        log.info("Wrote %s", EPG_OUT_PATH)
    except Exception as e:
        log.warning("Could not write %s: %s", EPG_OUT_PATH, e)


def epg_loop() -> None:
    while not channels:
        time.sleep(10)
    while True:
        try:
            refresh_epg()
        except Exception as e:
            log.exception("EPG refresh failed: %r", e)
        time.sleep(EPG_REFRESH_HOURS * 3600)


# ============================================================
# HLS helpers
# ============================================================

def now() -> float:
    return time.time()


def absolute_url(base: str, value: str) -> str:
    return urljoin(base, value)


def _cleanup_hls_state() -> None:
    cutoff = now()
    with _hls_lock:
        expired = [
            t
            for t, item in list(_hls_urls.items())
            if cutoff - item.get("created", cutoff) > HLS_TOKEN_TTL
        ]
        for t in expired:
            _hls_urls.pop(t, None)
            _hls_segment_cache.pop(t, None)

        expired_seq = [
            k
            for k, item in list(_hls_sequence_cache.items())
            if cutoff - item.get("created", cutoff) > HLS_SEGMENT_CACHE_TTL
        ]
        for k in expired_seq:
            _hls_sequence_cache.pop(k, None)

        if len(_hls_sequence_cache) > HLS_SEGMENT_CACHE_MAX:
            ordered = sorted(
                _hls_sequence_cache.items(),
                key=lambda p: p[1].get("created", 0),
            )
            remove = len(_hls_sequence_cache) - HLS_SEGMENT_CACHE_MAX
            for k, _ in ordered[:remove]:
                _hls_sequence_cache.pop(k, None)


def _hls_token(
    channel: int,
    url: str,
    kind: str,
    variant_index: int | None = None,
    parent_variant_token: str | None = None,
    sequence: int | None = None,
) -> str:
    """
    PLAYLIST / VARIANT → random token.
    SEGMENT → stable token derived from channel + variant + MEDIA-SEQUENCE.
    """
    if kind == "segment" and sequence is not None:
        variant_key = "unknown" if variant_index is None else str(variant_index)
        raw = f"segment:{channel}:{variant_key}:{sequence}"
        token = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    else:
        token = uuid.uuid4().hex

    ts = now()
    with _hls_lock:
        existing = _hls_urls.get(token)
        if existing is None:
            _hls_urls[token] = {
                "channel": channel,
                "url": url,
                "created": ts,
                "last_refresh": ts,
                "kind": kind,
                "variant_index": variant_index,
                "parent_variant_token": parent_variant_token,
                "sequence": sequence,
            }
        else:
            # Keep local URL stable, only refresh upstream signed URL.
            existing["url"] = url
            existing["last_refresh"] = ts
            existing["kind"] = kind
            existing["variant_index"] = variant_index
            existing["parent_variant_token"] = parent_variant_token
            existing["sequence"] = sequence
    return token


def _hls_url(
    channel: int,
    url: str,
    kind: str | None = None,
    variant_index: int | None = None,
    parent_variant_token: str | None = None,
    sequence: int | None = None,
) -> str:
    if kind is None:
        path = urlparse(url).path.lower()
        kind = (
            "playlist"
            if path.endswith((".m3u8", ".m3u"))
            else "segment"
        )
    token = _hls_token(
        channel=channel,
        url=url,
        kind=kind,
        variant_index=variant_index,
        parent_variant_token=parent_variant_token,
        sequence=sequence,
    )
    suffix = ".ts" if kind == "segment" else ""
    return f"{PUBLIC_BASE_URL}/hls/{channel}/{token}{suffix}"


def _get_hls_item(channel: int, token: str) -> dict | None:
    with _hls_lock:
        item = _hls_urls.get(token)
        if item is None or item.get("channel") != channel:
            return None
        return dict(item)


# ============================================================
# Upstream fetch
# ============================================================

def _fetch_upstream(url: str):
    session = get_session()
    attempts = max(1, HLS_RETRY_COUNT)
    last_response = None
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            response = session.get(
                url, timeout=HLS_TIMEOUT, allow_redirects=True
            )
            last_response = response
            if response.status_code == 200:
                return response
            if response.status_code in (401, 403, 404, 410) or response.status_code >= 500:
                log.warning(
                    "HLS upstream HTTP %s url=%s attempt=%s",
                    response.status_code, url, attempt,
                )
            if attempt < attempts:
                time.sleep(min(0.5 * attempt, 1.5))
        except requests.RequestException as exc:
            last_error = exc
            log.warning(
                "HLS upstream request failed url=%s attempt=%s error=%s",
                url, attempt, exc,
            )
            if attempt < attempts:
                time.sleep(min(0.5 * attempt, 1.5))

    if last_response is not None:
        return last_response
    raise RuntimeError(f"Upstream request failed: {last_error}")


# ============================================================
# Manifest parsing
# ============================================================

def _get_variant_urls(master_url: str, body: str) -> list[dict]:
    variants = []
    pending = None
    idx = 0
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending = line
            continue
        if pending is not None and not line.startswith("#"):
            variants.append(
                {
                    "index": idx,
                    "url": absolute_url(master_url, line),
                    "inf": pending,
                }
            )
            idx += 1
            pending = None
    return variants


def _get_media_segments(manifest_url: str, body: str):
    """Return (media_sequence_start, [(sequence, absolute_url), ...])."""
    media_sequence = 0
    m = re.search(r"#EXT-X-MEDIA-SEQUENCE\s*:\s*(\d+)", body)
    if m:
        media_sequence = int(m.group(1))

    segments = []
    sequence = media_sequence
    waiting = False
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            waiting = True
            continue
        if line.startswith("#"):
            continue
        if waiting:
            segments.append(
                (sequence, absolute_url(manifest_url, line))
            )
            sequence += 1
            waiting = False
    return media_sequence, segments


# ============================================================
# Segment cache
# ============================================================

def _cache_segment(
    token: str,
    channel: int,
    sequence: int | None,
    body: bytes,
    content_type: str,
    variant_index: int | None = None,
) -> None:
    ts = now()
    item = {
        "body": body,
        "content_type": content_type,
        "created": ts,
        "variant_index": variant_index,
    }
    with _hls_lock:
        _hls_segment_cache[token] = {
            "body": body,
            "content_type": content_type,
            "created": ts,
        }
        if sequence is not None:
            _hls_sequence_cache[(channel, sequence)] = item
    _cleanup_hls_state()


def _get_cached_segment(
    token: str, channel: int, sequence: int | None
):
    ts = now()
    with _hls_lock:
        if sequence is not None:
            item = _hls_sequence_cache.get((channel, sequence))
            if item is not None:
                if ts - item["created"] <= HLS_SEGMENT_CACHE_TTL:
                    return item
                _hls_sequence_cache.pop((channel, sequence), None)

        item = _hls_segment_cache.get(token)
        if item is not None:
            if ts - item["created"] <= HLS_SEGMENT_CACHE_TTL:
                return item
            _hls_segment_cache.pop(token, None)
    return None


# ============================================================
# Variant / segment refresh (exact sequence)
# ============================================================

def _refresh_variant_token(
    channel: int, token: str, force: bool = False
) -> str | None:
    item = _get_hls_item(channel, token)
    if item is None:
        return None
    variant_index = item.get("variant_index")
    if variant_index is None:
        return None

    if not force:
        if now() - item.get("last_refresh", 0) < HLS_UPSTREAM_REFRESH:
            return item.get("url")

    with _hls_lock:
        lock = _hls_refresh_locks.setdefault(token, threading.Lock())

    with lock:
        item = _get_hls_item(channel, token)
        if item is None:
            return None
        if not force:
            if now() - item.get("last_refresh", 0) < HLS_UPSTREAM_REFRESH:
                return item.get("url")

        try:
            master_url = _get_stream_url(channel)
            if not master_url:
                return None
            response = _fetch_upstream(master_url)
            if response.status_code != 200:
                log.warning(
                    "HLS master refresh failed channel=%s status=%s",
                    channel, response.status_code,
                )
                return None
            variants = _get_variant_urls(master_url, response.text)
            if variant_index >= len(variants):
                log.warning(
                    "HLS variant disappeared channel=%s variant=%s count=%s",
                    channel, variant_index, len(variants),
                )
                return None
            new_url = variants[variant_index]["url"]
            with _hls_lock:
                stored = _hls_urls.get(token)
                if stored is not None:
                    stored["url"] = new_url
                    stored["last_refresh"] = now()
            log.info(
                "HLS: refreshed variant channel=%s variant=%s",
                channel, variant_index,
            )
            return new_url
        except Exception as exc:
            log.warning(
                "HLS variant refresh failed channel=%s variant=%s error=%s",
                channel, variant_index, exc,
            )
            return None


def _refresh_segment_from_parent(channel: int, token: str) -> str | None:
    """Locate the exact MEDIA-SEQUENCE again after a signed-URL expiry."""
    item = _get_hls_item(channel, token)
    if item is None:
        return None
    sequence = item.get("sequence")
    parent_token = item.get("parent_variant_token")
    if sequence is None or not parent_token:
        return None

    variant_url = _refresh_variant_token(channel, parent_token, force=True)
    if not variant_url:
        return None

    try:
        response = _fetch_upstream(variant_url)
        if response.status_code != 200:
            return None
        _, segments = _get_media_segments(variant_url, response.text)
        exact_url = None
        for seq, seg_url in segments:
            if seq == sequence:
                exact_url = seg_url
                break
        if exact_url is None:
            if segments:
                log.warning(
                    "HLS: sequence %s no longer in window channel=%s "
                    "live_window=%s-%s",
                    sequence, channel, segments[0][0], segments[-1][0],
                )
            return None
        with _hls_lock:
            stored = _hls_urls.get(token)
            if stored is not None:
                stored["url"] = exact_url
                stored["last_refresh"] = now()
        log.info(
            "HLS: refreshed exact segment channel=%s sequence=%s",
            channel, sequence,
        )
        return exact_url
    except Exception as exc:
        log.warning(
            "HLS exact segment refresh failed channel=%s sequence=%s error=%s",
            channel, sequence, exc,
        )
        return None


# ============================================================
# Manifest rewriting (stable local URLs)
# ============================================================

def _rewrite_hls_manifest(
    channel: int,
    manifest_url: str,
    body: str,
    parent_variant_token: str | None = None,
) -> str:
    lines = body.splitlines()
    is_media = "#EXT-X-MEDIA-SEQUENCE:" in body or "#EXTINF:" in body

    if is_media:
        _, segments = _get_media_segments(manifest_url, body)
        sequence_map = {url: seq for seq, url in segments}

        parent_variant_index = None
        if parent_variant_token:
            parent_item = _get_hls_item(channel, parent_variant_token)
            if parent_item:
                parent_variant_index = parent_item.get("variant_index")

        output = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                output.append(line)
                continue
            upstream = absolute_url(manifest_url, stripped)
            sequence = sequence_map.get(upstream)
            if sequence is None:
                output.append(line)
                continue
            local = _hls_url(
                channel=channel,
                url=upstream,
                kind="segment",
                variant_index=parent_variant_index,
                parent_variant_token=parent_variant_token,
                sequence=sequence,
            )
            output.append(local)
        rewritten = "\n".join(output)
        if body.endswith("\n"):
            rewritten += "\n"
        return rewritten

    # Master playlist
    variants = _get_variant_urls(manifest_url, body)
    variant_lookup = {item["url"]: item["index"] for item in variants}
    output = []
    pending_idx = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            output.append(line)
            continue
        if stripped.startswith("#EXT-X-STREAM-INF:"):
            output.append(line)
            pending_idx = (
                len([x for x in output if x.startswith("#EXT-X-STREAM-INF:")])
                - 1
            )
            continue
        if pending_idx is not None and not stripped.startswith("#"):
            upstream = absolute_url(manifest_url, stripped)
            vidx = variant_lookup.get(upstream, pending_idx)
            local = _hls_url(
                channel=channel,
                url=upstream,
                kind="variant",
                variant_index=vidx,
            )
            output.append(local)
            pending_idx = None
            continue

        if 'URI="' in line:
            def replace_uri(match):
                original = match.group(1)
                absolute = absolute_url(manifest_url, original)
                if ".m3u8" in absolute.lower() or ".m3u" in absolute.lower():
                    local = _hls_url(
                        channel=channel, url=absolute, kind="playlist"
                    )
                    return f'URI="{local}"'
                return match.group(0)

            line = re.sub(r'URI="([^"]+)"', replace_uri, line)
        output.append(line)

    rewritten = "\n".join(output)
    if body.endswith("\n"):
        rewritten += "\n"
    return rewritten


# ============================================================
# Optional non-blocking prefetch (disabled by default)
# ============================================================

def _prefetch_one(channel: int, token: str) -> None:
    item = _get_hls_item(channel, token)
    if item is None or item.get("sequence") is None:
        return
    if _get_cached_segment(token, channel, item["sequence"]) is not None:
        return
    url = item.get("url")
    if not url:
        return
    try:
        response = _fetch_upstream(url)
        if response.status_code != 200 or not response.content:
            return
        _cache_segment(
            token=token,
            channel=channel,
            sequence=item["sequence"],
            body=response.content,
            content_type=response.headers.get("Content-Type") or "video/mp2t",
            variant_index=item.get("variant_index"),
        )
        log.info(
            "HLS: prefetched segment channel=%s sequence=%s",
            channel, item["sequence"],
        )
    except Exception as exc:
        log.warning(
            "HLS prefetch failed channel=%s sequence=%s error=%s",
            channel, item.get("sequence"), exc,
        )


def _background_prefetch(
    channel: int,
    manifest_url: str,
    body: str,
    parent_variant_token: str,
) -> None:
    if not HLS_PREFETCH_ENABLED:
        return
    try:
        _, segments = _get_media_segments(manifest_url, body)
        segments = segments[:HLS_PREFETCH_MAX_SEGMENTS]
        parent_item = _get_hls_item(channel, parent_variant_token)
        variant_index = parent_item.get("variant_index") if parent_item else None
        for sequence, url in segments:
            token = _hls_token(
                channel=channel,
                url=url,
                kind="segment",
                variant_index=variant_index,
                parent_variant_token=parent_variant_token,
                sequence=sequence,
            )
            _hls_executor.submit(_prefetch_one, channel, token)
    except Exception as exc:
        log.warning("HLS background prefetch setup failed: %s", exc)


# ============================================================
# Public endpoints
# ============================================================

@app.get("/")
def index():
    return jsonify(
        {
            "service": "filmon-iptv",
            "status": "ok",
            "channels": len(channels),
            "playlist": f"{PUBLIC_BASE_URL}/iptv/filmon.m3u",
            "epg": f"{PUBLIC_BASE_URL}/iptv/filmon.xml",
            "health": f"{PUBLIC_BASE_URL}/health",
        }
    )


@app.get("/health")
def health():
    with _hls_lock:
        token_count = len(_hls_urls)
        cached_count = len(_hls_sequence_cache)
    return jsonify(
        {
            "channels": len(channels),
            "hls_tokens": token_count,
            "cached_segments": cached_count,
            "last_scan_stats": dict(stats),
            "prefetch_enabled": HLS_PREFETCH_ENABLED,
        }
    )


@app.get("/refresh")
def refresh():
    scan()
    return f"OK, {len(channels)} channels"


@app.get("/playlist.m3u")
@app.get("/iptv/filmon.m3u")
def playlist():
    return Response(
        build_m3u(),
        mimetype="audio/x-mpegurl",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/guide.xml")
@app.get("/iptv/filmon.xml")
def guide():
    output = epg_xml or (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<tv generator-info-name="filmon-proxy"></tv>\n'
    )
    return Response(
        output,
        mimetype="application/xml",
        headers={"Cache-Control": "no-store"},
    )


# ============================================================
# Stable HLS entry point
# ============================================================
#
# For Jellyfin we deliberately return a *single* preferred-quality
# media playlist instead of a master.  Masters cause constant token
# churn and an extra network hop that triggers levelLoadTimeOut.
# ============================================================

def _pick_preferred_variant(master_url: str, master_body: str) -> str | None:
    """
    Choose the best media-playlist URL from a master.
    Preference order:
      1. quality matching PREFERRED_QUALITY (high / low / 480p …)
      2. highest bandwidth
      3. first variant
    """
    variants = _get_variant_urls(master_url, master_body)
    if not variants:
        # Upstream already gave us a media playlist
        if "#EXTINF:" in master_body or "#EXT-X-MEDIA-SEQUENCE:" in master_body:
            return master_url
        return None

    # Try to match by resolution / bandwidth heuristics
    preferred = PREFERRED_QUALITY.lower()
    scored = []
    for v in variants:
        inf = v.get("inf", "").lower()
        score = 0
        if preferred in ("high", "720", "720p", "hd") and ("720" in inf or "900x720" in inf or "1280x720" in inf):
            score = 100
        elif preferred in ("low", "360", "360p", "sd") and ("360" in inf or "450x360" in inf):
            score = 100
        elif preferred in ("480", "480p") and ("480" in inf or "600x480" in inf):
            score = 100
        # bandwidth as secondary sort key
        m = re.search(r"BANDWIDTH=(\d+)", v.get("inf", ""))
        bw = int(m.group(1)) if m else 0
        scored.append((score, bw, v["url"]))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return scored[0][2]


@app.get("/stream/<int:n>.m3u8")
def stream(n: int):
    try:
        master_url = _get_stream_url(n)
        if not master_url:
            abort(404)

        # 1. Fetch master (or direct media playlist)
        response = _fetch_upstream(master_url)
        if response.status_code != 200:
            return Response(
                "Upstream stream unavailable\n",
                status=502,
                mimetype="text/plain",
            )

        body = response.text
        if "#EXTM3U" not in body:
            return Response(
                "Upstream returned non-HLS content\n",
                status=502,
                mimetype="text/plain",
            )

        # 2. If this is already a media playlist, just rewrite segments
        is_media = (
            "#EXT-X-MEDIA-SEQUENCE:" in body
            or "#EXTINF:" in body
        )

        if is_media:
            media_url = master_url
            media_body = body
            parent_token = None
        else:
            # 3. Pick preferred variant and fetch the media playlist
            media_url = _pick_preferred_variant(master_url, body)
            if not media_url:
                return Response(
                    "No usable variant found\n",
                    status=502,
                    mimetype="text/plain",
                )

            media_resp = _fetch_upstream(media_url)
            if media_resp.status_code != 200:
                return Response(
                    "Upstream media playlist unavailable\n",
                    status=502,
                    mimetype="text/plain",
                )
            media_body = media_resp.text

            # Create a stable-ish parent variant token so segment
            # refreshes can re-resolve the exact MEDIA-SEQUENCE.
            parent_token = _hls_token(
                channel=n,
                url=media_url,
                kind="variant",
                variant_index=0,
            )

        # 4. Rewrite segment URLs to stable local tokens
        rewritten = _rewrite_hls_manifest(
            channel=n,
            manifest_url=media_url,
            body=media_body,
            parent_variant_token=parent_token,
        )

        encoded = rewritten.encode("utf-8")
        return Response(
            encoded,
            status=200,
            mimetype="application/vnd.apple.mpegurl",
            headers={
                "Content-Length": str(len(encoded)),
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Connection": "keep-alive",
            },
        )
    except Exception as exc:
        log.exception("HLS stream failed channel=%s", n)
        return Response(
            f"HLS error: {exc}\n",
            status=502,
            mimetype="text/plain",
        )


# ============================================================
# HLS proxy
# ============================================================

@app.get("/hls/<int:n>/<path:token>")
def hls_proxy(n: int, token: str):
    is_segment_request = token.endswith(".ts")
    if is_segment_request:
        token = token[:-3]

    item = _get_hls_item(n, token)
    if item is None:
        log.warning("HLS unknown token channel=%s token=%s", n, token)
        abort(404)

    kind = item.get("kind")
    sequence = item.get("sequence")

    # ---------- SEGMENT ----------
    if kind == "segment":
        cached = _get_cached_segment(token, n, sequence)
        if cached is not None:
            body = cached["body"]
            return Response(
                body,
                status=200,
                mimetype=cached.get("content_type") or "video/mp2t",
                headers={
                    "Content-Length": str(len(body)),
                    "Cache-Control": "no-store",
                    "Pragma": "no-cache",
                    "Connection": "keep-alive",
                },
            )

        upstream_url = item.get("url")
        if not upstream_url:
            abort(502)

        try:
            response = _fetch_upstream(upstream_url)
        except Exception as exc:
            log.warning(
                "HLS segment upstream exception channel=%s sequence=%s error=%s",
                n, sequence, exc,
            )
            response = None

        if response is None or response.status_code in (401, 403, 404, 410):
            refreshed = _refresh_segment_from_parent(n, token)
            if refreshed:
                try:
                    response = _fetch_upstream(refreshed)
                except Exception as exc:
                    log.warning(
                        "HLS refreshed segment request failed "
                        "channel=%s sequence=%s error=%s",
                        n, sequence, exc,
                    )
                    response = None

        if response is None or response.status_code != 200:
            status = response.status_code if response is not None else 502
            log.warning(
                "HLS upstream failed permanently channel=%s kind=segment "
                "sequence=%s token=%s status=%s",
                n, sequence, token, status,
            )
            return Response(
                "HLS segment unavailable\n",
                status=502,
                mimetype="text/plain",
            )

        body = response.content
        if not body:
            return Response(
                "Empty HLS segment\n", status=502, mimetype="text/plain"
            )

        content_type = (
            response.headers.get("Content-Type") or "video/mp2t"
        )
        _cache_segment(
            token=token,
            channel=n,
            sequence=sequence,
            body=body,
            content_type=content_type,
            variant_index=item.get("variant_index"),
        )
        return Response(
            body,
            status=200,
            mimetype=content_type.split(";")[0],
            headers={
                "Content-Length": str(len(body)),
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "Connection": "keep-alive",
            },
        )

    # ---------- VARIANT / PLAYLIST ----------
    if kind in ("playlist", "variant"):
        upstream_url = item.get("url")
        if not upstream_url:
            abort(502)

        try:
            response = _fetch_upstream(upstream_url)
        except Exception:
            response = None

        if response is None or response.status_code in (401, 403, 404, 410):
            if kind == "variant":
                refreshed = _refresh_variant_token(n, token, force=True)
                if refreshed:
                    upstream_url = refreshed
                    try:
                        response = _fetch_upstream(upstream_url)
                    except Exception:
                        response = None

        if response is None or response.status_code != 200:
            status = response.status_code if response is not None else 502
            log.warning(
                "HLS upstream failed channel=%s kind=%s token=%s status=%s",
                n, kind, token, status,
            )
            return Response(
                "HLS playlist unavailable\n",
                status=502,
                mimetype="text/plain",
            )

        body = response.text
        with _hls_lock:
            stored = _hls_urls.get(token)
            if stored is not None:
                stored["url"] = upstream_url
                stored["last_refresh"] = now()

        rewritten = _rewrite_hls_manifest(
            channel=n,
            manifest_url=upstream_url,
            body=body,
            parent_variant_token=token if kind == "variant" else None,
        )

        if kind == "variant" and HLS_PREFETCH_ENABLED:
            _background_prefetch(
                channel=n,
                manifest_url=upstream_url,
                body=body,
                parent_variant_token=token,
            )

        encoded = rewritten.encode("utf-8")
        return Response(
            encoded,
            status=200,
            mimetype="application/vnd.apple.mpegurl",
            headers={
                "Content-Length": str(len(encoded)),
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Connection": "keep-alive",
            },
        )

    abort(404)


# ============================================================
# Background workers + startup
# ============================================================

def _cleanup_loop() -> None:
    while True:
        try:
            _cleanup_hls_state()
        except Exception:
            log.exception("HLS cleanup failed")
        time.sleep(30)


threading.Thread(target=background, daemon=True, name="filmon-scan").start()
threading.Thread(target=_cleanup_loop, daemon=True, name="hls-cleanup").start()
if EPG_ENABLED:
    threading.Thread(target=epg_loop, daemon=True, name="filmon-epg").start()

log.info(
    "Starting filmon-iptv PUBLIC_BASE_URL=%s  prefetch=%s",
    PUBLIC_BASE_URL, HLS_PREFETCH_ENABLED,
)

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8765")),
        threaded=True,
    )