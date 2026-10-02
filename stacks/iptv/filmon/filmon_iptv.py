import os
import re
import time
import uuid
import json
import hashlib
import logging
import threading
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import Flask, Response, abort, jsonify, request

# ============================================================
# Configuration
# ============================================================

PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "http://192.168.1.107:8765",
).rstrip("/")

FILMON_API = os.getenv(
    "FILMON_API",
    "https://www.filmon.com/tv/api",
).rstrip("/")

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "3600"))

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))

HLS_TOKEN_TTL = int(os.getenv("HLS_TOKEN_TTL", "86400"))
HLS_UPSTREAM_REFRESH = int(os.getenv("HLS_UPSTREAM_REFRESH", "15"))
HLS_TIMEOUT = int(os.getenv("HLS_TIMEOUT", "20"))
HLS_RETRY_COUNT = int(os.getenv("HLS_RETRY_COUNT", "3"))

# Exact-sequence segment cache.
HLS_SEGMENT_CACHE_TTL = int(
    os.getenv("HLS_SEGMENT_CACHE_TTL", "900")
)

HLS_SEGMENT_CACHE_MAX = int(
    os.getenv("HLS_SEGMENT_CACHE_MAX", "4096")
)

# IMPORTANT:
# Do NOT synchronously prefetch the live playlist.
#
# The old implementation downloaded the complete live window
# before returning the playlist. That delayed FFmpeg/Jellyfin
# enough to fall behind the live window.
#
# Prefetch is disabled by default and is ONLY executed when
# explicitly enabled.
HLS_PREFETCH_ENABLED = os.getenv(
    "HLS_PREFETCH_ENABLED",
    "false",
).lower() in ("1", "true", "yes", "on")

HLS_PREFETCH_WORKERS = int(
    os.getenv("HLS_PREFETCH_WORKERS", "8")
)

HLS_PREFETCH_MAX_SEGMENTS = int(
    os.getenv("HLS_PREFETCH_MAX_SEGMENTS", "5")
)

USER_AGENT = os.getenv(
    "FILMON_USER_AGENT",
    "Mozilla/5.0",
)

# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)

log = logging.getLogger("filmon")

# ============================================================
# Flask
# ============================================================

app = Flask(__name__)

# ============================================================
# Global FilmOn state
# ============================================================

_channels = {}
_channels_lock = threading.RLock()

_last_scan_stats = {}
_last_scan_time = 0

_scan_lock = threading.Lock()

# ============================================================
# HTTP sessions
# ============================================================

_thread_local = threading.local()


def get_session():
    """
    One requests.Session per worker thread.

    This avoids sharing a requests.Session between concurrent
    HLS requests.
    """
    session = getattr(_thread_local, "session", None)

    if session is None:
        session = requests.Session()

        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Accept-Encoding": "identity",
                "Connection": "keep-alive",
            }
        )

        _thread_local.session = session

    return session


# ============================================================
# HLS state
# ============================================================

_hls_lock = threading.RLock()

# local token -> metadata
#
# {
#   "channel": int,
#   "url": str,
#   "created": float,
#   "last_refresh": float,
#   "kind": "playlist" | "variant" | "segment",
#   "variant_index": int | None,
#   "parent_variant_token": str | None,
#   "sequence": int | None,
# }
_hls_urls = {}

# Token cache.
#
# token -> {
#   "body": bytes,
#   "content_type": str,
#   "created": float,
# }
_hls_segment_cache = {}

# Canonical segment cache.
#
# (channel, sequence) -> {
#   "body": bytes,
#   "content_type": str,
#   "created": float,
#   "variant_index": int | None,
# }
#
# This is the important cache.
#
# Random local URLs are NOT the identity of a live segment.
# MEDIA-SEQUENCE is.
_hls_sequence_cache = {}

# Used to avoid multiple simultaneous refreshes of the same
# parent variant.
_hls_refresh_locks = {}

_hls_executor = ThreadPoolExecutor(
    max_workers=HLS_PREFETCH_WORKERS
)


# ============================================================
# Utility
# ============================================================

def now():
    return time.time()


def absolute_url(base_url, value):
    return urljoin(base_url, value)


def content_type_for_url(url):
    path = urlparse(url).path.lower()

    if path.endswith(".m3u8") or path.endswith(".m3u"):
        return "application/vnd.apple.mpegurl"

    if path.endswith(".ts"):
        return "video/mp2t"

    return "application/octet-stream"


# ============================================================
# HLS cleanup
# ============================================================

def _cleanup_hls_state():
    cutoff = now()

    with _hls_lock:
        # ----------------------------------------------------
        # Token cleanup
        # ----------------------------------------------------

        token_expired = []

        for token, item in list(_hls_urls.items()):
            if cutoff - item.get("created", cutoff) > HLS_TOKEN_TTL:
                token_expired.append(token)

        for token in token_expired:
            _hls_urls.pop(token, None)
            _hls_segment_cache.pop(token, None)

        # ----------------------------------------------------
        # Sequence cache TTL cleanup
        # ----------------------------------------------------

        expired_sequences = []

        for key, item in list(_hls_sequence_cache.items()):
            if (
                cutoff - item.get("created", cutoff)
                > HLS_SEGMENT_CACHE_TTL
            ):
                expired_sequences.append(key)

        for key in expired_sequences:
            _hls_sequence_cache.pop(key, None)

        # ----------------------------------------------------
        # Sequence cache size cleanup
        #
        # Oldest entries are removed first.
        # ----------------------------------------------------

        if len(_hls_sequence_cache) > HLS_SEGMENT_CACHE_MAX:
            ordered = sorted(
                _hls_sequence_cache.items(),
                key=lambda pair: pair[1].get("created", 0),
            )

            remove_count = (
                len(_hls_sequence_cache)
                - HLS_SEGMENT_CACHE_MAX
            )

            for key, _ in ordered[:remove_count]:
                _hls_sequence_cache.pop(key, None)


# ============================================================
# HLS token generation
# ============================================================

def _hls_token(
    channel,
    url,
    kind,
    variant_index=None,
    parent_variant_token=None,
    sequence=None,
):
    """
    Generate a local HLS token.

    PLAYLIST / VARIANT:
        Random token.

    SEGMENT:
        Stable token based on:

            channel
            variant
            MEDIA-SEQUENCE

        This means a segment does not receive a new local URL
        every time the live playlist is refreshed.
    """

    if kind == "segment" and sequence is not None:
        variant_key = (
            "unknown"
            if variant_index is None
            else str(variant_index)
        )

        raw = (
            f"segment:"
            f"{channel}:"
            f"{variant_key}:"
            f"{sequence}"
        )

        token = hashlib.sha256(
            raw.encode("utf-8")
        ).hexdigest()[:32]

    else:
        token = uuid.uuid4().hex

    timestamp = now()

    with _hls_lock:
        existing = _hls_urls.get(token)

        if existing is None:
            _hls_urls[token] = {
                "channel": channel,
                "url": url,
                "created": timestamp,
                "last_refresh": timestamp,
                "kind": kind,
                "variant_index": variant_index,
                "parent_variant_token": parent_variant_token,
                "sequence": sequence,
            }

        else:
            # Stable segment token:
            # update the upstream signed URL while keeping
            # the local URL stable.
            existing["url"] = url
            existing["last_refresh"] = timestamp
            existing["kind"] = kind
            existing["variant_index"] = variant_index
            existing["parent_variant_token"] = (
                parent_variant_token
            )
            existing["sequence"] = sequence

    return token


def _hls_url(
    channel,
    url,
    kind=None,
    variant_index=None,
    parent_variant_token=None,
    sequence=None,
):
    if kind is None:
        path = urlparse(url).path.lower()

        if path.endswith(".m3u8") or path.endswith(".m3u"):
            kind = "playlist"
        else:
            kind = "segment"

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


def _get_hls_item(channel, token):
    with _hls_lock:
        item = _hls_urls.get(token)

        if item is None:
            return None

        if item.get("channel") != channel:
            return None

        return dict(item)


# ============================================================
# FilmOn API / scanning
# ============================================================

def _filmon_request(path, params=None):
    session = get_session()

    url = absolute_url(FILMON_API + "/", path)

    response = session.get(
        url,
        params=params or {},
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


def _extract_channels(data):
    """
    FilmOn has changed API response structures over time.

    Accept several common shapes.
    """

    if not data:
        return []

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in (
            "channels",
            "channel",
            "items",
            "results",
            "data",
        ):
            value = data.get(key)

            if isinstance(value, list):
                return value

            if isinstance(value, dict):
                for nested in (
                    "channels",
                    "items",
                    "results",
                ):
                    nested_value = value.get(nested)

                    if isinstance(nested_value, list):
                        return nested_value

    return []


def _normalise_channel(item):
    if not isinstance(item, dict):
        return None

    channel_id = (
        item.get("id")
        or item.get("channel_id")
        or item.get("channelId")
    )

    if channel_id is None:
        return None

    try:
        channel_id = int(channel_id)
    except Exception:
        return None

    name = (
        item.get("title")
        or item.get("name")
        or item.get("channel_name")
        or f"Channel {channel_id}"
    )

    logo = (
        item.get("logo")
        or item.get("logo_url")
        or item.get("image")
        or ""
    )

    return {
        "id": channel_id,
        "name": str(name),
        "logo": str(logo),
        "raw": item,
    }


def scan_channels():
    global _last_scan_stats
    global _last_scan_time

    with _scan_lock:
        started = now()

        try:
            # ------------------------------------------------
            # Main channel API
            # ------------------------------------------------

            candidates = []

            api_paths = [
                "channels",
                "channels.json",
            ]

            for path in api_paths:
                try:
                    data = _filmon_request(path)
                    candidates = _extract_channels(data)

                    if candidates:
                        break

                except Exception as exc:
                    log.warning(
                        "FilmOn channel API failed path=%s: %s",
                        path,
                        exc,
                    )

            normalised = []

            for item in candidates:
                channel = _normalise_channel(item)

                if channel is not None:
                    normalised.append(channel)

            with _channels_lock:
                _channels.clear()

                for channel in normalised:
                    _channels[channel["id"]] = channel

            elapsed = round(now() - started, 2)

            _last_scan_stats = {
                "channels": len(normalised),
                "elapsed": elapsed,
                "success": True,
            }

            _last_scan_time = now()

            log.info(
                "FilmOn scan complete channels=%s elapsed=%ss",
                len(normalised),
                elapsed,
            )

            return normalised

        except Exception as exc:
            elapsed = round(now() - started, 2)

            _last_scan_stats = {
                "channels": len(_channels),
                "elapsed": elapsed,
                "success": False,
                "error": str(exc),
            }

            log.exception("FilmOn scan failed")

            return list(_channels.values())


# ============================================================
# FilmOn stream URL
# ============================================================

def _get_stream_url(channel):
    """
    Get a fresh FilmOn HLS master URL.

    This function intentionally returns the MASTER playlist.
    Variant selection happens separately.
    """

    session = get_session()

    attempts = max(1, HLS_RETRY_COUNT)

    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            # Common FilmOn stream endpoint.
            #
            # Keep the request flexible because FilmOn's API
            # response structure has changed historically.

            possible_paths = [
                f"channel/{channel}",
                f"channel/{channel}/stream",
                f"streams/{channel}",
            ]

            for path in possible_paths:
                try:
                    data = _filmon_request(path)

                    urls = _find_urls_in_json(data)

                    for url in urls:
                        lower = url.lower()

                        if (
                            ".m3u8" in lower
                            or "m3u8" in lower
                        ):
                            return url

                except Exception:
                    continue

            # Fallback direct endpoint.
            response = session.get(
                f"{FILMON_API}/channel/{channel}",
                params={"channel_id": channel},
                timeout=HLS_TIMEOUT,
            )

            response.raise_for_status()

            data = response.json()

            urls = _find_urls_in_json(data)

            for url in urls:
                lower = url.lower()

                if (
                    ".m3u8" in lower
                    or "m3u8" in lower
                ):
                    return url

            raise RuntimeError(
                "No HLS master URL found"
            )

        except Exception as exc:
            last_error = exc

            if attempt < attempts:
                time.sleep(0.4 * attempt)

    raise RuntimeError(
        f"Unable to get FilmOn stream URL: {last_error}"
    )


def _find_urls_in_json(value):
    found = []

    if isinstance(value, str):
        if (
            value.startswith("http://")
            or value.startswith("https://")
        ):
            found.append(value)

    elif isinstance(value, list):
        for item in value:
            found.extend(
                _find_urls_in_json(item)
            )

    elif isinstance(value, dict):
        for key, item in value.items():
            key_lower = str(key).lower()

            if (
                "url" in key_lower
                or "stream" in key_lower
                or "playlist" in key_lower
                or "hls" in key_lower
            ):
                found.extend(
                    _find_urls_in_json(item)
                )
            else:
                found.extend(
                    _find_urls_in_json(item)
                )

    return found


# ============================================================
# Upstream HLS requests
# ============================================================

def _fetch_upstream(url):
    session = get_session()

    attempts = max(1, HLS_RETRY_COUNT)

    last_response = None
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            response = session.get(
                url,
                timeout=HLS_TIMEOUT,
                allow_redirects=True,
            )

            last_response = response

            if response.status_code == 200:
                return response

            if response.status_code in (
                401,
                403,
                404,
                410,
            ):
                log.warning(
                    "HLS upstream HTTP %s url=%s attempt=%s",
                    response.status_code,
                    url,
                    attempt,
                )

            elif response.status_code >= 500:
                log.warning(
                    "HLS upstream HTTP %s url=%s attempt=%s",
                    response.status_code,
                    url,
                    attempt,
                )

            if attempt < attempts:
                time.sleep(
                    min(0.5 * attempt, 1.5)
                )

        except requests.RequestException as exc:
            last_error = exc

            log.warning(
                "HLS upstream request failed "
                "url=%s attempt=%s error=%s",
                url,
                attempt,
                exc,
            )

            if attempt < attempts:
                time.sleep(
                    min(0.5 * attempt, 1.5)
                )

    if last_response is not None:
        return last_response

    raise RuntimeError(
        f"Upstream request failed: {last_error}"
    )


# ============================================================
# Master / variant parsing
# ============================================================

def _get_variant_urls(master_url, body):
    variants = []

    lines = body.splitlines()

    pending_variant = None
    variant_index = 0

    for line in lines:
        line = line.strip()

        if not line:
            continue

        if line.startswith("#EXT-X-STREAM-INF:"):
            pending_variant = line
            continue

        if (
            pending_variant is not None
            and not line.startswith("#")
        ):
            variant_url = absolute_url(
                master_url,
                line,
            )

            variants.append(
                {
                    "index": variant_index,
                    "url": variant_url,
                    "inf": pending_variant,
                }
            )

            variant_index += 1
            pending_variant = None

    return variants


# ============================================================
# Media playlist parsing
# ============================================================

def _get_media_segments(manifest_url, body):
    """
    Return:

        media_sequence_start,
        [
            (sequence, absolute_url),
            ...
        ]
    """

    media_sequence = 0

    match = re.search(
        r"#EXT-X-MEDIA-SEQUENCE\s*:\s*(\d+)",
        body,
    )

    if match:
        media_sequence = int(match.group(1))

    segments = []
    sequence = media_sequence

    lines = body.splitlines()

    waiting_for_uri = False

    for line in lines:
        line = line.strip()

        if not line:
            continue

        if line.startswith("#EXTINF:"):
            waiting_for_uri = True
            continue

        if line.startswith("#"):
            continue

        if waiting_for_uri:
            segments.append(
                (
                    sequence,
                    absolute_url(
                        manifest_url,
                        line,
                    ),
                )
            )

            sequence += 1
            waiting_for_uri = False

    return media_sequence, segments


# ============================================================
# Segment cache
# ============================================================

def _cache_segment(
    token,
    channel,
    sequence,
    body,
    content_type,
    variant_index=None,
):
    timestamp = now()

    item = {
        "body": body,
        "content_type": content_type,
        "created": timestamp,
        "variant_index": variant_index,
    }

    with _hls_lock:
        # Token cache.
        _hls_segment_cache[token] = {
            "body": body,
            "content_type": content_type,
            "created": timestamp,
        }

        # Canonical sequence cache.
        _hls_sequence_cache[
            (channel, sequence)
        ] = item

    _cleanup_hls_state()


def _get_cached_segment(
    token,
    channel,
    sequence,
):
    timestamp = now()

    with _hls_lock:
        # ----------------------------------------------------
        # Canonical sequence cache first.
        # ----------------------------------------------------

        if sequence is not None:
            item = _hls_sequence_cache.get(
                (channel, sequence)
            )

            if item is not None:
                if (
                    timestamp - item["created"]
                    <= HLS_SEGMENT_CACHE_TTL
                ):
                    return item

                _hls_sequence_cache.pop(
                    (channel, sequence),
                    None,
                )

        # ----------------------------------------------------
        # Token cache fallback.
        # ----------------------------------------------------

        item = _hls_segment_cache.get(token)

        if item is not None:
            if (
                timestamp - item["created"]
                <= HLS_SEGMENT_CACHE_TTL
            ):
                return item

            _hls_segment_cache.pop(
                token,
                None,
            )

    return None


# ============================================================
# Variant refresh
# ============================================================

def _refresh_variant_token(
    channel,
    token,
    force=False,
):
    """
    Refresh a variant by fetching a fresh MASTER and selecting
    the SAME variant index.

    Never replace a variant URL with the master URL.
    """

    item = _get_hls_item(
        channel,
        token,
    )

    if item is None:
        return None

    variant_index = item.get(
        "variant_index"
    )

    if variant_index is None:
        return None

    current_time = now()

    if not force:
        if (
            current_time
            - item.get("last_refresh", 0)
            < HLS_UPSTREAM_REFRESH
        ):
            return item.get("url")

    with _hls_lock:
        lock = _hls_refresh_locks.get(token)

        if lock is None:
            lock = threading.Lock()
            _hls_refresh_locks[token] = lock

    with lock:
        # Check again after acquiring lock.
        item = _get_hls_item(
            channel,
            token,
        )

        if item is None:
            return None

        current_time = now()

        if not force:
            if (
                current_time
                - item.get("last_refresh", 0)
                < HLS_UPSTREAM_REFRESH
            ):
                return item.get("url")

        try:
            master_url = _get_stream_url(
                channel
            )

            response = _fetch_upstream(
                master_url
            )

            if response.status_code != 200:
                log.warning(
                    "HLS master refresh failed "
                    "channel=%s status=%s",
                    channel,
                    response.status_code,
                )
                return None

            master_body = response.text

            variants = _get_variant_urls(
                master_url,
                master_body,
            )

            if variant_index >= len(variants):
                log.warning(
                    "HLS variant disappeared "
                    "channel=%s variant=%s count=%s",
                    channel,
                    variant_index,
                    len(variants),
                )
                return None

            new_url = variants[
                variant_index
            ]["url"]

            with _hls_lock:
                stored = _hls_urls.get(token)

                if stored is not None:
                    stored["url"] = new_url
                    stored["last_refresh"] = now()

            log.info(
                "HLS: refreshed variant "
                "channel=%s variant=%s",
                channel,
                variant_index,
            )

            return new_url

        except Exception as exc:
            log.warning(
                "HLS variant refresh failed "
                "channel=%s variant=%s error=%s",
                channel,
                variant_index,
                exc,
            )

            return None


# ============================================================
# Segment refresh by exact MEDIA-SEQUENCE
# ============================================================

def _refresh_segment_from_parent(
    channel,
    token,
):
    """
    Refresh a segment using its parent variant.

    CRITICAL:
        Find the EXACT same MEDIA-SEQUENCE.

    Never substitute the newest segment.
    """

    item = _get_hls_item(
        channel,
        token,
    )

    if item is None:
        return None

    sequence = item.get("sequence")
    parent_token = item.get(
        "parent_variant_token"
    )

    if sequence is None:
        return None

    if not parent_token:
        return None

    variant_url = _refresh_variant_token(
        channel,
        parent_token,
        force=True,
    )

    if not variant_url:
        return None

    try:
        response = _fetch_upstream(
            variant_url
        )

        if response.status_code != 200:
            log.warning(
                "HLS parent variant failed "
                "channel=%s sequence=%s status=%s",
                channel,
                sequence,
                response.status_code,
            )
            return None

        media_body = response.text

        _, segments = _get_media_segments(
            variant_url,
            media_body,
        )

        exact_url = None

        for seq, segment_url in segments:
            if seq == sequence:
                exact_url = segment_url
                break

        if exact_url is None:
            if segments:
                first_seq = segments[0][0]
                last_seq = segments[-1][0]

                log.warning(
                    "HLS: requested segment sequence "
                    "%s is no longer available "
                    "channel=%s live_window=%s-%s",
                    sequence,
                    channel,
                    first_seq,
                    last_seq,
                )

            return None

        with _hls_lock:
            stored = _hls_urls.get(token)

            if stored is not None:
                stored["url"] = exact_url
                stored["last_refresh"] = now()

        log.info(
            "HLS: refreshed exact segment "
            "channel=%s sequence=%s",
            channel,
            sequence,
        )

        return exact_url

    except Exception as exc:
        log.warning(
            "HLS exact segment refresh failed "
            "channel=%s sequence=%s error=%s",
            channel,
            sequence,
            exc,
        )

        return None


# ============================================================
# HLS manifest rewriting
# ============================================================

def _rewrite_hls_manifest(
    channel,
    manifest_url,
    body,
    parent_variant_token=None,
):
    """
    Rewrite upstream HLS URLs to local stable URLs.

    Media playlists:
        Segment identity = MEDIA-SEQUENCE.

    Master playlists:
        Variant identity = variant index.
    """

    lines = body.splitlines()

    is_media_playlist = (
        "#EXT-X-MEDIA-SEQUENCE:" in body
        or "#EXTINF:" in body
    )

    # --------------------------------------------------------
    # Media playlist
    # --------------------------------------------------------

    if is_media_playlist:
        _, segments = _get_media_segments(
            manifest_url,
            body,
        )

        sequence_map = {
            url: sequence
            for sequence, url in segments
        }

        parent_variant_index = None

        if parent_variant_token:
            parent_item = _get_hls_item(
                channel,
                parent_variant_token,
            )

            if parent_item:
                parent_variant_index = (
                    parent_item.get(
                        "variant_index"
                    )
                )

        output = []

        for line in lines:
            stripped = line.strip()

            if (
                not stripped
                or stripped.startswith("#")
            ):
                output.append(line)
                continue

            upstream_segment = absolute_url(
                manifest_url,
                stripped,
            )

            sequence = sequence_map.get(
                upstream_segment
            )

            if sequence is None:
                output.append(line)
                continue

            local = _hls_url(
                channel=channel,
                url=upstream_segment,
                kind="segment",
                variant_index=parent_variant_index,
                parent_variant_token=parent_variant_token,
                sequence=sequence,
            )

            output.append(local)

        rewritten = "\n".join(output)

        if body.endswith("\n"):
            rewritten += "\n"

        # ----------------------------------------------------
        # IMPORTANT:
        # No synchronous prefetch here.
        #
        # Return the live playlist immediately.
        # ----------------------------------------------------

        return rewritten

    # --------------------------------------------------------
    # Master playlist
    # --------------------------------------------------------

    variants = _get_variant_urls(
        manifest_url,
        body,
    )

    variant_lookup = {
        item["url"]: item["index"]
        for item in variants
    }

    output = []

    pending_variant_index = None

    for line in lines:
        stripped = line.strip()

        if not stripped:
            output.append(line)
            continue

        if stripped.startswith(
            "#EXT-X-STREAM-INF:"
        ):
            output.append(line)

            pending_variant_index = (
                len(
                    [
                        x
                        for x in output
                        if x.startswith(
                            "#EXT-X-STREAM-INF:"
                        )
                    ]
                )
                - 1
            )

            continue

        if (
            pending_variant_index is not None
            and not stripped.startswith("#")
        ):
            upstream_variant = absolute_url(
                manifest_url,
                stripped,
            )

            variant_index = variant_lookup.get(
                upstream_variant,
                pending_variant_index,
            )

            local = _hls_url(
                channel=channel,
                url=upstream_variant,
                kind="variant",
                variant_index=variant_index,
            )

            output.append(local)

            pending_variant_index = None

            continue

        # URI="..." attributes.
        #
        # Preserve tags but rewrite HLS playlist URIs.
        if 'URI="' in line:
            def replace_uri(match):
                original = match.group(1)

                absolute = absolute_url(
                    manifest_url,
                    original,
                )

                if (
                    ".m3u8" in absolute.lower()
                    or ".m3u" in absolute.lower()
                ):
                    local = _hls_url(
                        channel=channel,
                        url=absolute,
                        kind="playlist",
                    )
                    return f'URI="{local}"'

                return match.group(0)

            line = re.sub(
                r'URI="([^"]+)"',
                replace_uri,
                line,
            )

        output.append(line)

    rewritten = "\n".join(output)

    if body.endswith("\n"):
        rewritten += "\n"

    return rewritten


# ============================================================
# Optional background prefetch
# ============================================================

def _prefetch_one(
    channel,
    token,
):
    item = _get_hls_item(
        channel,
        token,
    )

    if item is None:
        return

    sequence = item.get("sequence")

    if sequence is None:
        return

    cached = _get_cached_segment(
        token,
        channel,
        sequence,
    )

    if cached is not None:
        return

    url = item.get("url")

    if not url:
        return

    try:
        response = _fetch_upstream(url)

        if response.status_code != 200:
            log.warning(
                "HLS prefetch HTTP %s "
                "channel=%s sequence=%s",
                response.status_code,
                channel,
                sequence,
            )
            return

        body = response.content

        if not body:
            return

        _cache_segment(
            token=token,
            channel=channel,
            sequence=sequence,
            body=body,
            content_type=(
                response.headers.get(
                    "Content-Type"
                )
                or "video/mp2t"
            ),
            variant_index=item.get(
                "variant_index"
            ),
        )

        log.info(
            "HLS: prefetched segment "
            "channel=%s sequence=%s",
            channel,
            sequence,
        )

    except Exception as exc:
        log.warning(
            "HLS prefetch failed "
            "channel=%s sequence=%s error=%s",
            channel,
            sequence,
            exc,
        )


def _background_prefetch(
    channel,
    manifest_url,
    body,
    parent_variant_token,
):
    # --------------------------------------------------------
    # HARD GUARD:
    #
    # This function must never do anything when prefetch is
    # disabled.
    # --------------------------------------------------------

    if not HLS_PREFETCH_ENABLED:
        return

    try:
        _, segments = _get_media_segments(
            manifest_url,
            body,
        )

        segments = segments[
            :HLS_PREFETCH_MAX_SEGMENTS
        ]

        tokens = []

        parent_item = _get_hls_item(
            channel,
            parent_variant_token,
        )

        variant_index = None

        if parent_item:
            variant_index = parent_item.get(
                "variant_index"
            )

        for sequence, url in segments:
            token = _hls_token(
                channel=channel,
                url=url,
                kind="segment",
                variant_index=variant_index,
                parent_variant_token=parent_variant_token,
                sequence=sequence,
            )

            tokens.append(token)

        for token in tokens:
            _hls_executor.submit(
                _prefetch_one,
                channel,
                token,
            )

    except Exception as exc:
        log.warning(
            "HLS background prefetch setup failed: %s",
            exc,
        )


# ============================================================
# HLS root stream
# ============================================================

@app.get("/stream/<int:n>.m3u8")
def stream(n):
    try:
        master_url = _get_stream_url(n)

        response = _fetch_upstream(
            master_url
        )

        if response.status_code != 200:
            return Response(
                "Upstream stream unavailable\n",
                status=502,
                mimetype="text/plain",
            )

        body = response.text

        rewritten = _rewrite_hls_manifest(
            channel=n,
            manifest_url=master_url,
            body=body,
        )

        encoded = rewritten.encode(
            "utf-8"
        )

        return Response(
            encoded,
            status=200,
            mimetype="application/vnd.apple.mpegurl",
            headers={
                "Content-Length": str(
                    len(encoded)
                ),
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Connection": "keep-alive",
            },
        )

    except Exception as exc:
        log.exception(
            "HLS master failed channel=%s",
            n,
        )

        return Response(
            f"HLS error: {exc}\n",
            status=502,
            mimetype="text/plain",
        )


# ============================================================
# HLS proxy
# ============================================================

@app.get("/hls/<int:n>/<path:token>")
def hls_proxy(n, token):
    # --------------------------------------------------------
    # Strip .ts from stable local segment URLs.
    # --------------------------------------------------------

    is_segment_request = token.endswith(".ts")

    if is_segment_request:
        token = token[:-3]

    item = _get_hls_item(
        n,
        token,
    )

    if item is None:
        log.warning(
            "HLS unknown token channel=%s token=%s",
            n,
            token,
        )
        abort(404)

    kind = item.get("kind")
    sequence = item.get("sequence")

    # ========================================================
    # SEGMENT
    # ========================================================

    if kind == "segment":
        # ----------------------------------------------------
        # First try canonical sequence cache.
        # ----------------------------------------------------

        cached = _get_cached_segment(
            token=token,
            channel=n,
            sequence=sequence,
        )

        if cached is not None:
            body = cached["body"]

            return Response(
                body,
                status=200,
                mimetype=(
                    cached.get(
                        "content_type"
                    )
                    or "video/mp2t"
                ),
                headers={
                    "Content-Length": str(
                        len(body)
                    ),
                    "Cache-Control": "no-store",
                    "Pragma": "no-cache",
                    "Connection": "keep-alive",
                },
            )

        # ----------------------------------------------------
        # Fetch current upstream segment URL.
        # ----------------------------------------------------

        upstream_url = item.get("url")

        if not upstream_url:
            abort(502)

        try:
            response = _fetch_upstream(
                upstream_url
            )

        except Exception as exc:
            log.warning(
                "HLS segment upstream exception "
                "channel=%s sequence=%s error=%s",
                n,
                sequence,
                exc,
            )

            response = None

        # ----------------------------------------------------
        # If signed URL expired, refresh parent variant and
        # find the EXACT same MEDIA-SEQUENCE.
        # ----------------------------------------------------

        if (
            response is None
            or response.status_code
            in (401, 403, 404, 410)
        ):
            refreshed_url = (
                _refresh_segment_from_parent(
                    n,
                    token,
                )
            )

            if refreshed_url:
                try:
                    response = _fetch_upstream(
                        refreshed_url
                    )

                except Exception as exc:
                    log.warning(
                        "HLS refreshed segment "
                        "request failed "
                        "channel=%s sequence=%s "
                        "error=%s",
                        n,
                        sequence,
                        exc,
                    )

                    response = None

        # ----------------------------------------------------
        # Final failure.
        # ----------------------------------------------------

        if (
            response is None
            or response.status_code != 200
        ):
            status = (
                response.status_code
                if response is not None
                else 502
            )

            log.warning(
                "HLS upstream failed permanently "
                "channel=%s kind=segment "
                "sequence=%s token=%s status=%s",
                n,
                sequence,
                token,
                status,
            )

            return Response(
                "HLS segment unavailable\n",
                status=502,
                mimetype="text/plain",
            )

        body = response.content

        if not body:
            return Response(
                "Empty HLS segment\n",
                status=502,
                mimetype="text/plain",
            )

        content_type = (
            response.headers.get(
                "Content-Type"
            )
            or "video/mp2t"
        )

        # ----------------------------------------------------
        # Cache only COMPLETE successful bytes.
        # ----------------------------------------------------

        _cache_segment(
            token=token,
            channel=n,
            sequence=sequence,
            body=body,
            content_type=content_type,
            variant_index=item.get(
                "variant_index"
            ),
        )

        return Response(
            body,
            status=200,
            mimetype=content_type.split(";")[0],
            headers={
                "Content-Length": str(
                    len(body)
                ),
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "Connection": "keep-alive",
            },
        )

    # ========================================================
    # VARIANT / PLAYLIST
    # ========================================================

    if kind in (
        "playlist",
        "variant",
    ):
        upstream_url = item.get("url")

        if not upstream_url:
            abort(502)

        try:
            response = _fetch_upstream(
                upstream_url
            )

        except Exception:
            response = None

        # ----------------------------------------------------
        # Variant signed URL expired.
        # ----------------------------------------------------

        if (
            response is None
            or response.status_code
            in (401, 403, 404, 410)
        ):
            if kind == "variant":
                refreshed = (
                    _refresh_variant_token(
                        n,
                        token,
                        force=True,
                    )
                )

                if refreshed:
                    upstream_url = refreshed

                    try:
                        response = _fetch_upstream(
                            upstream_url
                        )
                    except Exception:
                        response = None

        if (
            response is None
            or response.status_code != 200
        ):
            status = (
                response.status_code
                if response is not None
                else 502
            )

            log.warning(
                "HLS upstream failed "
                "channel=%s kind=%s token=%s status=%s",
                n,
                kind,
                token,
                status,
            )

            return Response(
                "HLS playlist unavailable\n",
                status=502,
                mimetype="text/plain",
            )

        body = response.text

        # ----------------------------------------------------
        # Keep local token pointed at current upstream URL.
        # ----------------------------------------------------

        with _hls_lock:
            stored = _hls_urls.get(token)

            if stored is not None:
                stored["url"] = upstream_url
                stored["last_refresh"] = now()

        rewritten = _rewrite_hls_manifest(
            channel=n,
            manifest_url=upstream_url,
            body=body,
            parent_variant_token=(
                token
                if kind == "variant"
                else None
            ),
        )

        # ----------------------------------------------------
        # Optional NON-BLOCKING prefetch.
        #
        # This is guarded in BOTH places:
        #
        # 1. here at the call site
        # 2. inside _background_prefetch()
        #
        # Therefore PREFETCH=false means NO PREFETCH.
        # ----------------------------------------------------

        if (
            kind == "variant"
            and HLS_PREFETCH_ENABLED
        ):
            _background_prefetch(
                channel=n,
                manifest_url=upstream_url,
                body=body,
                parent_variant_token=token,
            )

        encoded = rewritten.encode(
            "utf-8"
        )

        return Response(
            encoded,
            status=200,
            mimetype="application/vnd.apple.mpegurl",
            headers={
                "Content-Length": str(
                    len(encoded)
                ),
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Connection": "keep-alive",
            },
        )

    abort(404)


# ============================================================
# M3U
# ============================================================

@app.get("/playlist.m3u")
@app.get("/iptv/filmon.m3u")
def playlist():
    with _channels_lock:
        channels = list(
            _channels.values()
        )

    lines = [
        "#EXTM3U",
        "",
    ]

    for channel in channels:
        channel_id = channel["id"]
        name = channel["name"]
        logo = channel.get("logo", "")

        attributes = [
            f'tvg-id="{channel_id}"',
            f'tvg-name="{name}"',
        ]

        if logo:
            attributes.append(
                f'tvg-logo="{logo}"'
            )

        lines.append(
            "#EXTINF:-1 "
            + " ".join(attributes)
            + f",{name}"
        )

        lines.append(
            f"{PUBLIC_BASE_URL}"
            f"/stream/{channel_id}.m3u8"
        )

        lines.append("")

    output = "\n".join(lines)

    return Response(
        output,
        mimetype="audio/x-mpegurl",
        headers={
            "Content-Length": str(
                len(output.encode("utf-8"))
            ),
            "Cache-Control": "no-store",
        },
    )


# ============================================================
# XMLTV EPG
# ============================================================

def _xml_escape(value):
    value = str(value)

    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def build_epg():
    """
    Basic XMLTV generation from available channel metadata.

    If FilmOn exposes programme information in the channel
    response, include it. Otherwise generate channel entries
    without programmes.
    """

    with _channels_lock:
        channels = list(
            _channels.values()
        )

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<tv generator-info-name="filmon-iptv">',
    ]

    for channel in channels:
        channel_id = channel["id"]
        name = channel["name"]
        logo = channel.get("logo", "")

        lines.append(
            f'  <channel id="{_xml_escape(channel_id)}">'
        )

        lines.append(
            f"    <display-name>"
            f"{_xml_escape(name)}"
            f"</display-name>"
        )

        if logo:
            lines.append(
                f'    <icon src="{_xml_escape(logo)}"/>'
            )

        lines.append(
            "  </channel>"
        )

    lines.append("</tv>")

    return "\n".join(lines) + "\n"


@app.get("/guide.xml")
@app.get("/iptv/filmon.xml")
def guide():
    output = build_epg()

    return Response(
        output,
        mimetype="application/xml",
        headers={
            "Content-Length": str(
                len(output.encode("utf-8"))
            ),
            "Cache-Control": "no-store",
        },
    )


# ============================================================
# Health
# ============================================================

@app.get("/health")
def health():
    with _channels_lock:
        channel_count = len(_channels)

    with _hls_lock:
        token_count = len(_hls_urls)
        cached_count = len(
            _hls_sequence_cache
        )

    return jsonify(
        {
            "cached_segments": cached_count,
            "channels": channel_count,
            "hls_tokens": token_count,
            "hls_sequence_cache": cached_count,
            "last_scan_stats": _last_scan_stats,
            "last_scan_time": _last_scan_time,
            "prefetch_enabled": HLS_PREFETCH_ENABLED,
        }
    )


# ============================================================
# Root
# ============================================================

@app.get("/")
def index():
    return jsonify(
        {
            "service": "filmon-iptv",
            "status": "ok",
            "playlist": (
                f"{PUBLIC_BASE_URL}"
                "/iptv/filmon.m3u"
            ),
            "epg": (
                f"{PUBLIC_BASE_URL}"
                "/iptv/filmon.xml"
            ),
            "health": (
                f"{PUBLIC_BASE_URL}"
                "/health"
            ),
        }
    )


# ============================================================
# Background scanner
# ============================================================

def _scan_loop():
    while True:
        try:
            scan_channels()
        except Exception:
            log.exception(
                "Background scan failed"
            )

        time.sleep(
            max(60, SCAN_INTERVAL)
        )


def _cleanup_loop():
    while True:
        try:
            _cleanup_hls_state()
        except Exception:
            log.exception(
                "HLS cleanup failed"
            )

        time.sleep(30)


# ============================================================
# Startup
# ============================================================

def startup():
    log.info(
        "Starting filmon-iptv "
        "PUBLIC_BASE_URL=%s",
        PUBLIC_BASE_URL,
    )

    log.info(
        "HLS settings: refresh=%ss timeout=%ss "
        "retries=%s segment_ttl=%ss "
        "segment_max=%s prefetch=%s",
        HLS_UPSTREAM_REFRESH,
        HLS_TIMEOUT,
        HLS_RETRY_COUNT,
        HLS_SEGMENT_CACHE_TTL,
        HLS_SEGMENT_CACHE_MAX,
        HLS_PREFETCH_ENABLED,
    )

    try:
        scan_channels()
    except Exception:
        log.exception(
            "Initial FilmOn scan failed"
        )

    threading.Thread(
        target=_scan_loop,
        name="filmon-scan",
        daemon=True,
    ).start()

    threading.Thread(
        target=_cleanup_loop,
        name="hls-cleanup",
        daemon=True,
    ).start()


startup()


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv("PORT", "8765")
        ),
        threaded=True,
    )