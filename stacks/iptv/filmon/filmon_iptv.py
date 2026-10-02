"""
FilmOn -> Jellyfin helper (new site API).

- Scans FilmOn channels via the site's own /ajax/getChannelInfo endpoint
  (on start, then every REFRESH_HOURS)
- Writes an M3U file into a shared folder (default /iptv/filmon.m3u)
- Serves /stream/<id>.m3u8 with a stable local URL
- Proxies nested HLS playlists and media segments
- Continuously refreshes short-lived FilmOn stream URLs
- Builds an XMLTV EPG guide

This only fetches what FilmOn itself serves to your session. It does not remove
any time limits or subscription checks.

Endpoints:
  GET /                  status page
  GET /playlist.m3u      same playlist over HTTP
  GET /stream/<id>.m3u8  stable local HLS entry point
  GET /refresh           force re-scan + rewrite M3U
  GET /health            channel count + last scan stats
  GET /guide.xml         XMLTV guide
"""

import json
import os
import re
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from xml.sax.saxutils import escape, quoteattr
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from flask import Flask, Response, abort, redirect


# ---------------- settings (env-overridable) ----------------

def env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes")


PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "http://localhost:8765",
).rstrip("/")

M3U_OUT_PATH = Path(
    os.getenv("M3U_OUT_PATH", "/iptv/filmon.m3u")
)

CACHE_FILE = Path(
    os.getenv("CACHE_FILE", "/data/filmon_channels.json")
)

REFRESH_HOURS = float(
    os.getenv("REFRESH_HOURS", "24")
)

MIN_CHANNEL_ID = int(
    os.getenv("MIN_CHANNEL_ID", "1")
)

MAX_CHANNEL_ID = int(
    os.getenv("MAX_CHANNEL_ID", "2000")
)

WORKERS = int(
    os.getenv("WORKERS", "8")
)

LOGO_SIZE = os.getenv(
    "LOGO_SIZE",
    "extra_big",
)

SKIP_ADULT = env_bool(
    "SKIP_ADULT",
    True,
)

SKIP_VR_EXP = env_bool(
    "SKIP_VR_EXP",
    True,
)

SKIP_VOD_VOX = env_bool(
    "SKIP_VOD_VOX",
    True,
)

HD_ONLY = env_bool(
    "HD_ONLY",
    True,
)

FREE_ONLY = env_bool(
    "FREE_ONLY",
    False,
)

PREFERRED_QUALITY = os.getenv(
    "PREFERRED_QUALITY",
    "high",
)

REWRITE_PLAYLIST = env_bool(
    "REWRITE_PLAYLIST",
    True,
)

FILMON_COOKIE = os.getenv(
    "FILMON_COOKIE",
    "",
)

API_URL = os.getenv(
    "FILMON_API",
    "https://www.filmon.com/ajax/getChannelInfo",
)

EPG_ENABLED = env_bool(
    "EPG_ENABLED",
    True,
)

EPG_OUT_PATH = Path(
    os.getenv("EPG_OUT_PATH", "/iptv/filmon.xml")
)

EPG_REFRESH_HOURS = float(
    os.getenv("EPG_REFRESH_HOURS", "6")
)

EPG_API = os.getenv(
    "FILMON_EPG_API",
    "https://www.filmon.com/api-v2/tvguide/{}",
)

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36",
)


# ---------------- HLS settings ----------------
#
# Jellyfin receives a stable local token.
#
# FilmOn URLs behind that token are refreshed periodically.
#

HLS_TOKEN_TTL = int(
    os.getenv("HLS_TOKEN_TTL", "86400")
)

HLS_UPSTREAM_REFRESH = int(
    os.getenv("HLS_UPSTREAM_REFRESH", "30")
)

HLS_TIMEOUT = int(
    os.getenv("HLS_TIMEOUT", "15")
)

HLS_RETRY_COUNT = int(
    os.getenv("HLS_RETRY_COUNT", "2")
)

# Keep recently successful media responses in memory.
#
# This is especially useful because Jellyfin can request an HLS segment
# slightly after FilmOn's signed URL has expired.
HLS_SEGMENT_CACHE_TTL = int(
    os.getenv("HLS_SEGMENT_CACHE_TTL", "120")
)

HLS_SEGMENT_CACHE_MAX = int(
    os.getenv("HLS_SEGMENT_CACHE_MAX", "512")
)


# ------------------------------------------------------------

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Origin": "https://www.filmon.com",
    "Referer": "https://www.filmon.com/",
    "X-Requested-With": "XMLHttpRequest",
}

if FILMON_COOKIE:
    HEADERS["Cookie"] = FILMON_COOKIE


app = Flask(__name__)

lock = threading.Lock()

channels: list = []

stats: Counter = Counter()


# ============================================================
# FilmOn session handling
# ============================================================

_local = threading.local()

WARMUP_URL = os.getenv(
    "FILMON_WARMUP_URL",
    "https://www.filmon.com/tv/bbc-news",
)

_logged_cookies = False


def new_session() -> requests.Session:
    """
    Create a fresh FilmOn HTTP session.

    When FILMON_COOKIE is not supplied, visit the FilmOn page first so
    FilmOn can establish its normal guest cookies.
    """

    global _logged_cookies

    s = requests.Session()
    s.headers.update(HEADERS)

    if not FILMON_COOKIE:
        s.cookies.set(
            "ftv_site_mode",
            "classic",
            domain="www.filmon.com",
            path="/",
        )

        try:
            s.get(
                WARMUP_URL,
                timeout=15,
            )
        except Exception as e:
            print(
                f"Warm-up request failed: {e!r}"
            )

        if not _logged_cookies:
            _logged_cookies = True

            print(
                "Session cookies received from warm-up: "
                f"{sorted(s.cookies.keys())}"
            )

    return s


def get_session() -> requests.Session:
    """
    Each Gunicorn worker/thread gets its own requests.Session.
    """

    if not hasattr(_local, "s"):
        _local.s = new_session()

    return _local.s


# ============================================================
# FilmOn API
# ============================================================

def get_channel_data(
    n: int,
    quality: str = "high",
):
    """
    Return FilmOn channel data or None.
    """

    j = None

    for attempt in (1, 2):

        try:
            s = get_session()

            r = s.post(
                API_URL,
                data={
                    "channel_id": n,
                    "quality": quality,
                },
                timeout=15,
            )

            if r.status_code in (404, 405):
                r = s.get(
                    API_URL,
                    params={
                        "channel_id": n,
                        "quality": quality,
                    },
                    timeout=15,
                )

            j = r.json()

        except Exception as e:

            if stats["request_failed"] < 3:
                print(
                    f"Request for channel {n} failed: {e!r}"
                )

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

    if (
        isinstance(j, dict)
        and isinstance(j.get("data"), dict)
    ):
        j = j["data"]

    return j if isinstance(j, dict) else None


def _get_stream_url(channel: int) -> str | None:
    """
    Ask FilmOn for a fresh stream URL.

    This function is deliberately called repeatedly during HLS playback.
    """

    d = get_channel_data(
        channel,
        PREFERRED_QUALITY,
    )

    if not d or not d.get("streams"):
        return None

    streams = d["streams"]

    pick = next(
        (
            s
            for s in streams
            if s.get("quality") == PREFERRED_QUALITY
        ),
        streams[0],
    )

    return pick.get("url")


# ============================================================
# Channel scanning
# ============================================================

def probe(n: int):

    d = get_channel_data(
        n,
        "high",
    )

    if d is None:
        return None

    if not d.get("title") or not d.get("streams"):

        if d.get("streamName") == "maintenance":
            stats["maintenance_placeholder"] += 1
        else:
            stats["no_channel_or_no_streams"] += 1

        if sum(
            stats[k]
            for k in (
                "maintenance_placeholder",
                "no_channel_or_no_streams",
            )
        ) <= 2:
            print(
                f"Channel {n}: no title/streams in response: "
                f"{str(d)[:200]}"
            )

        return None

    if (
        (SKIP_VR_EXP and d.get("is360"))
        or
        (SKIP_ADULT and d.get("is_adult"))
        or
        (
            SKIP_VOD_VOX
            and (
                d.get("is_vod")
                or d.get("is_vox")
            )
        )
    ):
        stats["skipped_type"] += 1
        return None

    if FREE_ONLY and not d.get("is_free"):
        stats["not_free"] += 1
        return None

    if HD_ONLY and not any(
        s.get("quality") == "high"
        for s in d["streams"]
    ):
        stats["no_hd_stream"] += 1
        return None

    stats["kept"] += 1

    if not d.get("is_free"):
        stats["kept_but_not_flagged_free"] += 1

    return {
        "id": n,
        "name": d["title"],
        "group": (
            d.get("group")
            or "Other"
        ).title(),
    }


def build_m3u() -> str:

    lines = [
        "#EXTM3U"
    ]

    for c in channels:

        logo = (
            "https://static.filmon.com/assets/channels/"
            f"{c['id']}/{LOGO_SIZE}_logo.png"
        )

        lines.append(
            f'#EXTINF:-1 tvg-id="{c["id"]}" '
            f'tvg-logo="{logo}" '
            f'group-title="{c["group"]}",'
            f'{c["name"]}'
        )

        lines.append(
            f"{PUBLIC_BASE_URL}/stream/{c['id']}.m3u8"
        )

    return "\n".join(lines) + "\n"


def write_m3u() -> None:

    try:

        M3U_OUT_PATH.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        tmp = M3U_OUT_PATH.with_suffix(
            ".tmp"
        )

        tmp.write_text(
            build_m3u(),
            encoding="utf-8",
        )

        tmp.replace(
            M3U_OUT_PATH
        )

        os.chmod(
            M3U_OUT_PATH,
            0o644,
        )

        print(
            f"Wrote {M3U_OUT_PATH}"
        )

    except Exception as e:

        print(
            f"Could not write {M3U_OUT_PATH}: {e}"
        )


def scan() -> None:

    global channels

    print(
        "Scanning FilmOn channels..."
    )

    stats.clear()

    with ThreadPoolExecutor(
        WORKERS
    ) as ex:

        found = [
            c
            for c in ex.map(
                probe,
                range(
                    MIN_CHANNEL_ID,
                    MAX_CHANNEL_ID,
                ),
            )
            if c
        ]

    print(
        f"Scan stats: {dict(stats)}"
    )

    if not found:

        print(
            "Scan found nothing "
            "(network/API issue?) - "
            "keeping previous list"
        )

        return

    with lock:

        channels = found

        try:

            CACHE_FILE.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            CACHE_FILE.write_text(
                json.dumps(found),
                encoding="utf-8",
            )

        except Exception as e:

            print(
                f"Cache write failed: {e}"
            )

    print(
        f"Scanned: {len(found)} channels"
    )

    write_m3u()


def background() -> None:

    global channels

    if CACHE_FILE.exists():

        try:

            channels = json.loads(
                CACHE_FILE.read_text(
                    encoding="utf-8"
                )
            )

            write_m3u()

            age = (
                time.time()
                - CACHE_FILE.stat().st_mtime
            )

            if age < REFRESH_HOURS * 3600:

                time.sleep(
                    REFRESH_HOURS * 3600
                    - age
                )

        except Exception:
            pass

    while True:

        scan()

        time.sleep(
            REFRESH_HOURS * 3600
        )


# ============================================================
# TV Guide / XMLTV
# ============================================================

epg_xml: str = ""

epg_stats: Counter = Counter()


def parse_guide(j) -> list:

    if isinstance(j, dict):

        for key in (
            "data",
            "programmes",
            "programs",
            "tvguide",
        ):

            if isinstance(
                j.get(key),
                list,
            ):
                return j[key]

        return (
            [j]
            if "programme_name" in j
            else []
        )

    return (
        j
        if isinstance(j, list)
        else []
    )


def fetch_guide(n: int) -> list:

    try:

        j = get_session().get(
            EPG_API.format(n),
            timeout=15,
        ).json()

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
        int(ts),
        tz=timezone.utc,
    ).strftime(
        "%Y%m%d%H%M%S +0000"
    )


def build_epg_xml(
    snapshot: list,
    guides: dict,
) -> str:

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<tv generator-info-name="filmon-proxy">',
    ]

    for c in snapshot:

        logo = (
            "https://static.filmon.com/assets/channels/"
            f"{c['id']}/{LOGO_SIZE}_logo.png"
        )

        out.append(
            f'<channel id="{c["id"]}">'
            f'<display-name>{escape(c["name"])}</display-name>'
            f'<icon src={quoteattr(logo)}/>'
            f'</channel>'
        )

    for c in snapshot:

        for p in guides.get(
            c["id"],
            [],
        ):

            try:

                start = xmltv_time(
                    p["startdatetime"]
                )

                stop = xmltv_time(
                    p["enddatetime"]
                )

            except Exception:
                continue

            parts = [
                f'<programme start="{start}" '
                f'stop="{stop}" '
                f'channel="{c["id"]}">',
                f'<title>{escape(str(p["programme_name"]))}</title>',
            ]

            if p.get(
                "programme_description"
            ):
                parts.append(
                    f'<desc>{escape(str(p["programme_description"]))}</desc>'
                )

            imgs = [
                i.get("url")
                for i in (
                    p.get("images")
                    or []
                )
                if (
                    isinstance(i, dict)
                    and i.get("url")
                )
            ]

            if imgs:
                parts.append(
                    f'<icon src={quoteattr(imgs[0])}/>'
                )

            parts.append(
                "</programme>"
            )

            out.append(
                "".join(parts)
            )

    out.append(
        "</tv>"
    )

    return "\n".join(out) + "\n"


def refresh_epg() -> None:

    global epg_xml

    snapshot = list(channels)

    if not snapshot:
        return

    epg_stats.clear()

    ids = [
        c["id"]
        for c in snapshot
    ]

    with ThreadPoolExecutor(
        WORKERS
    ) as ex:

        guides = dict(
            zip(
                ids,
                ex.map(
                    fetch_guide,
                    ids,
                ),
            )
        )

    total = sum(
        len(v)
        for v in guides.values()
    )

    print(
        f"EPG: {total} programmes for "
        f"{sum(1 for v in guides.values() if v)} "
        f"of {len(ids)} channels; "
        f"stats {dict(epg_stats)}"
    )

    if total == 0:

        print(
            "EPG came back empty - "
            "not writing a guide file"
        )

        return

    epg_xml = build_epg_xml(
        snapshot,
        guides,
    )

    try:

        EPG_OUT_PATH.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        tmp = EPG_OUT_PATH.with_suffix(
            ".tmp"
        )

        tmp.write_text(
            epg_xml,
            encoding="utf-8",
        )

        tmp.replace(
            EPG_OUT_PATH
        )

        os.chmod(
            EPG_OUT_PATH,
            0o644,
        )

        print(
            f"Wrote {EPG_OUT_PATH}"
        )

    except Exception as e:

        print(
            f"Could not write {EPG_OUT_PATH}: {e}"
        )


def epg_loop() -> None:

    while not channels:
        time.sleep(10)

    while True:

        try:
            refresh_epg()

        except Exception as e:

            print(
                f"EPG refresh failed: {e!r}"
            )

        time.sleep(
            EPG_REFRESH_HOURS * 3600
        )


# ============================================================
# HLS proxy state
# ============================================================

_hls_lock = threading.Lock()

#
# token -> {
#     "channel": int,
#     "url": str,
#     "created": float,
#     "last_refresh": float,
#     "kind": "playlist" | "segment",
# }
#
_hls_urls = {}


#
# Recently downloaded media segments.
#
# cache_key -> {
#     "body": bytes,
#     "content_type": str,
#     "created": float,
# }
#
_hls_segment_cache = {}


def _cleanup_hls_state() -> None:

    now = time.time()

    with _hls_lock:

        cutoff = (
            now
            - HLS_TOKEN_TTL
        )

        for key, value in list(
            _hls_urls.items()
        ):

            if value.get(
                "created",
                0,
            ) < cutoff:

                _hls_urls.pop(
                    key,
                    None,
                )

        segment_cutoff = (
            now
            - HLS_SEGMENT_CACHE_TTL
        )

        for key, value in list(
            _hls_segment_cache.items()
        ):

            if value.get(
                "created",
                0,
            ) < segment_cutoff:

                _hls_segment_cache.pop(
                    key,
                    None,
                )

        #
        # Hard limit so a long-running server
        # cannot grow indefinitely.
        #
        if len(_hls_segment_cache) > HLS_SEGMENT_CACHE_MAX:

            ordered = sorted(
                _hls_segment_cache.items(),
                key=lambda x: x[1].get(
                    "created",
                    0,
                ),
            )

            remove_count = (
                len(_hls_segment_cache)
                - HLS_SEGMENT_CACHE_MAX
            )

            for key, _ in ordered[
                :remove_count
            ]:

                _hls_segment_cache.pop(
                    key,
                    None,
                )


def _hls_token(
    channel: int,
    url: str,
    kind: str = "unknown",
) -> str:

    token = uuid.uuid4().hex

    now = time.time()

    with _hls_lock:

        _hls_urls[token] = {
            "url": url,
            "channel": channel,
            "created": now,
            "last_refresh": now,
            "kind": kind,
        }

    _cleanup_hls_state()

    return token


def _hls_url(
    channel: int,
    url: str,
) -> str:

    path = urlparse(url).path.lower()

    if (
        path.endswith(".m3u8")
        or path.endswith(".m3u")
    ):
        kind = "playlist"
    else:
        kind = "segment"

    return (
        f"{PUBLIC_BASE_URL}"
        f"/hls/{channel}/"
        f"{_hls_token(channel, url, kind)}"
    )


def _get_hls_item(
    channel: int,
    token: str,
):

    with _hls_lock:

        item = _hls_urls.get(
            token
        )

        if not item:
            return None

        if item.get(
            "channel"
        ) != channel:
            return None

        return dict(item)


def _update_hls_url(
    channel: int,
    token: str,
    url: str,
) -> bool:

    with _hls_lock:

        item = _hls_urls.get(
            token
        )

        if not item:
            return False

        if item.get(
            "channel"
        ) != channel:
            return False

        item["url"] = url
        item["last_refresh"] = time.time()

        return True


def _refresh_hls_session(
    channel: int,
    token: str,
    force: bool = False,
):

    item = _get_hls_item(
        channel,
        token,
    )

    if not item:
        return None

    now = time.time()

    if (
        not force
        and (
            now
            - item.get(
                "last_refresh",
                0,
            )
        )
        < HLS_UPSTREAM_REFRESH
    ):

        return item["url"]

    #
    # Only refresh the FilmOn master/entry
    # stream URL here.
    #
    fresh_url = _get_stream_url(
        channel
    )

    if not fresh_url:
        return None

    if not _update_hls_url(
        channel,
        token,
        fresh_url,
    ):
        return None

    return fresh_url


# ============================================================
# HLS HTTP helpers
# ============================================================

def _fetch_hls(
    url: str,
):
    """
    Fetch an HLS resource using the current FilmOn session.
    """

    return get_session().get(
        url,
        headers=HEADERS,
        timeout=HLS_TIMEOUT,
        allow_redirects=True,
    )


def _is_manifest(
    body: bytes,
) -> bool:

    try:

        probe = body[
            :1024
        ].decode(
            "utf-8",
            errors="ignore",
        )

        return "#EXTM3U" in probe

    except Exception:

        return False


def _cache_segment(
    token: str,
    body: bytes,
    content_type: str,
) -> None:

    with _hls_lock:

        _hls_segment_cache[token] = {
            "body": body,
            "content_type": content_type,
            "created": time.time(),
        }

    _cleanup_hls_state()


def _get_cached_segment(
    token: str,
):

    with _hls_lock:

        item = _hls_segment_cache.get(
            token
        )

        if not item:
            return None

        if (
            time.time()
            - item.get(
                "created",
                0,
            )
        ) > HLS_SEGMENT_CACHE_TTL:

            _hls_segment_cache.pop(
                token,
                None,
            )

            return None

        return {
            "body": item["body"],
            "content_type": item[
                "content_type"
            ],
        }


# ============================================================
# HLS manifest rewriting
# ============================================================

def _rewrite_hls_manifest(
    channel: int,
    manifest_url: str,
    body: str,
) -> str:

    """
    Rewrite all URLs inside an HLS manifest.

    Jellyfin only sees:

        /hls/<channel>/<local-token>

    FilmOn signed URLs never reach Jellyfin.
    """

    out = []

    for line in body.splitlines():

        stripped = line.strip()

        if not stripped:

            out.append(line)

            continue

        #
        # HLS tags containing URI="..."
        #
        if stripped.startswith("#"):

            if "URI=" in line:

                def replace_uri(match):

                    quote = match.group(1)

                    raw = match.group(2)

                    absolute = urljoin(
                        manifest_url,
                        raw,
                    )

                    return (
                        f"URI={quote}"
                        f"{_hls_url(channel, absolute)}"
                        f"{quote}"
                    )

                line = re.sub(
                    r'URI=(["\'])(.*?)\1',
                    replace_uri,
                    line,
                )

            out.append(line)

            continue

        #
        # Normal HLS URI line.
        #
        absolute = urljoin(
            manifest_url,
            stripped,
        )

        out.append(
            _hls_url(
                channel,
                absolute,
            )
        )

    return (
        "\n".join(out)
        + "\n"
    )


# ============================================================
# Public endpoints
# ============================================================

@app.get("/")
def index():

    return (
        f"filmon-iptv running. "
        f"{len(channels)} channels. "
        f"Try /playlist.m3u, "
        f"/health, /refresh"
    )


@app.get("/health")
def health():

    return {
        "channels": len(channels),
        "last_scan_stats": dict(stats),
    }


@app.get("/refresh")
def refresh():

    scan()

    return (
        f"OK, {len(channels)} channels"
    )


@app.get("/playlist.m3u")
def playlist():

    return Response(
        build_m3u(),
        mimetype="audio/x-mpegurl",
    )


# ============================================================
# Main stable HLS entry point
# ============================================================

@app.get("/stream/<int:n>.m3u8")
def stream(n: int):

    """
    Stable Jellyfin entry point.

    Jellyfin should always use:

        /stream/<channel>.m3u8

    This endpoint obtains a fresh FilmOn stream URL every time
    Jellyfin starts playback.
    """

    url = _get_stream_url(n)

    if not url:
        abort(404)

    #
    # Optional direct redirect mode.
    #
    if not REWRITE_PLAYLIST:
        return redirect(
            url,
            code=302,
        )

    #
    # Fetch FilmOn entry playlist.
    #
    r = _fetch_hls(url)

    #
    # Retry with a completely fresh FilmOn URL.
    #
    if r.status_code in (
        401,
        403,
        404,
    ):

        url = _get_stream_url(n)

        if not url:
            abort(404)

        r = _fetch_hls(url)

    if r.status_code != 200:

        print(
            f"FilmOn HLS failed for "
            f"channel {n}: "
            f"HTTP {r.status_code}"
        )

        abort(502)

    final = r.url

    body = r.text

    if "#EXTM3U" not in body:

        print(
            f"FilmOn returned non-HLS "
            f"response for channel {n}"
        )

        abort(502)

    out = _rewrite_hls_manifest(
        n,
        final,
        body,
    )

    return Response(
        out,
        mimetype="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control":
                "no-store, "
                "no-cache, "
                "must-revalidate, "
                "max-age=0",
            "Pragma": "no-cache",
        },
    )


# ============================================================
# HLS proxy
# ============================================================

@app.get("/hls/<int:n>/<token>")
def hls_proxy(
    n: int,
    token: str,
):

    """
    Proxy one HLS resource.

    Playlist:
        - refresh FilmOn URL periodically
        - fetch current playlist
        - rewrite nested URLs

    Segment:
        - serve cached segment if available
        - otherwise fetch FilmOn signed URL
        - cache successful response

    The important part is that Jellyfin never receives the FilmOn
    signed URL directly.
    """

    item = _get_hls_item(
        n,
        token,
    )

    if not item:
        abort(404)

    kind = item.get(
        "kind",
        "unknown",
    )

    #
    # --------------------------------------------------------
    # Segment cache
    # --------------------------------------------------------
    #
    # If Jellyfin asks for the same local segment token again,
    # don't depend on FilmOn's signed URL still being valid.
    #
    if kind == "segment":

        cached = _get_cached_segment(
            token
        )

        if cached:

            return Response(
                cached["body"],
                content_type=(
                    cached["content_type"]
                    or "video/mp2t"
                ),
                headers={
                    "Cache-Control":
                        "no-store, "
                        "no-cache, "
                        "must-revalidate, "
                        "max-age=0",
                },
            )

    #
    # --------------------------------------------------------
    # Playlist URL refresh
    # --------------------------------------------------------
    #

    if kind == "playlist":

        upstream_url = (
            _refresh_hls_session(
                n,
                token,
            )
        )

        if not upstream_url:

            print(
                f"HLS: unable to refresh "
                f"FilmOn playlist "
                f"channel={n}"
            )

            abort(502)

    else:

        #
        # For media segments, use the exact URL that was
        # advertised by the current FilmOn playlist.
        #
        upstream_url = item["url"]

    #
    # --------------------------------------------------------
    # Fetch upstream
    # --------------------------------------------------------
    #

    r = None

    for attempt in range(
        HLS_RETRY_COUNT + 1
    ):

        try:

            r = _fetch_hls(
                upstream_url
            )

            if r.status_code == 200:
                break

            print(
                f"HLS upstream HTTP "
                f"{r.status_code} "
                f"channel={n} "
                f"kind={kind} "
                f"attempt={attempt + 1}"
            )

        except requests.RequestException as e:

            print(
                f"HLS upstream request "
                f"failed channel={n} "
                f"kind={kind} "
                f"attempt={attempt + 1}: "
                f"{e!r}"
            )

        #
        # If this is a playlist, get a completely new
        # FilmOn stream URL.
        #
        if kind == "playlist":

            upstream_url = (
                _refresh_hls_session(
                    n,
                    token,
                    force=True,
                )
            )

            if not upstream_url:
                break

        #
        # For a segment, don't blindly replace the segment
        # URL with a new master URL. The segment token is tied
        # to the exact segment advertised by FilmOn.
        #
        time.sleep(0.25)

    #
    # --------------------------------------------------------
    # Upstream failed
    # --------------------------------------------------------
    #

    if not r or r.status_code != 200:

        print(
            f"HLS upstream failed "
            f"permanently "
            f"channel={n} "
            f"kind={kind} "
            f"token={token}"
        )

        abort(502)

    body = r.content

    content_type = (
        r.headers.get(
            "Content-Type"
        )
        or ""
    ).lower()

    #
    # --------------------------------------------------------
    # Nested playlist
    # --------------------------------------------------------
    #

    if _is_manifest(body):

        text_body = body.decode(
            "utf-8",
            errors="replace",
        )

        #
        # Update this local token to the current final
        # upstream URL. This is important when FilmOn
        # redirects to a different CDN URL.
        #
        if kind == "playlist":

            _update_hls_url(
                n,
                token,
                r.url,
            )

        out = _rewrite_hls_manifest(
            n,
            r.url,
            text_body,
        )

        return Response(
            out,
            mimetype=(
                "application/vnd.apple.mpegurl"
            ),
            headers={
                "Cache-Control":
                    "no-store, "
                    "no-cache, "
                    "must-revalidate, "
                    "max-age=0",
                "Pragma": "no-cache",
            },
        )

    #
    # --------------------------------------------------------
    # Media segment
    # --------------------------------------------------------
    #

    if kind == "segment":

        _cache_segment(
            token,
            body,
            content_type
            or "video/mp2t",
        )

    return Response(
        body,
        content_type=(
            content_type
            or "video/mp2t"
        ),
        headers={
            "Cache-Control":
                "no-store, "
                "no-cache, "
                "must-revalidate, "
                "max-age=0",
        },
    )


# ============================================================
# Background workers
# ============================================================

threading.Thread(
    target=background,
    daemon=True,
).start()


if EPG_ENABLED:

    threading.Thread(
        target=epg_loop,
        daemon=True,
    ).start()


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8765",
            )
        ),
        threaded=True,
    )