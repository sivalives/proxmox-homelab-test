
"""
FilmOn -> Jellyfin helper (new site API).

- Scans FilmOn channels via /ajax/getChannelInfo
- Writes M3U to /iptv/filmon.m3u
- Serves /stream/<id>.m3u8 as a stable local HLS URL
- Proxies nested HLS playlists and media segments
- Refreshes short-lived FilmOn HLS URLs
- Uses a sequence-based rolling segment cache
- Prefetches complete HLS live windows before publishing them
- Builds an XMLTV EPG guide

Endpoints:
  GET /                  status page
  GET /playlist.m3u      same playlist over HTTP
  GET /stream/<id>.m3u8  stable local HLS entry point
  GET /refresh           force re-scan + rewrite M3U
  GET /health            channel count + HLS/cache stats
  GET /guide.xml         XMLTV guide
"""

import json
import os
import re
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quoteattr, urljoin, urlparse
from xml.sax.saxutils import escape

import requests
from flask import Flask, Response, abort


# ============================================================
# SETTINGS
# ============================================================

def env_bool(name: str, default: bool) -> bool:
    return os.getenv(
        name,
        str(default),
    ).lower() in (
        "1",
        "true",
        "yes",
    )


PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "http://localhost:8765",
).rstrip("/")


M3U_OUT_PATH = Path(
    os.getenv(
        "M3U_OUT_PATH",
        "/iptv/filmon.m3u",
    )
)


CACHE_FILE = Path(
    os.getenv(
        "CACHE_FILE",
        "/data/filmon_channels.json",
    )
)


REFRESH_HOURS = float(
    os.getenv(
        "REFRESH_HOURS",
        "24",
    )
)


MIN_CHANNEL_ID = int(
    os.getenv(
        "MIN_CHANNEL_ID",
        "1",
    )
)


MAX_CHANNEL_ID = int(
    os.getenv(
        "MAX_CHANNEL_ID",
        "2000",
    )
)


WORKERS = int(
    os.getenv(
        "WORKERS",
        "8",
    )
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
    os.getenv(
        "EPG_OUT_PATH",
        "/iptv/filmon.xml",
    )
)


EPG_REFRESH_HOURS = float(
    os.getenv(
        "EPG_REFRESH_HOURS",
        "6",
    )
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


# ============================================================
# HLS SETTINGS
# ============================================================

HLS_TOKEN_TTL = int(
    os.getenv(
        "HLS_TOKEN_TTL",
        "86400",
    )
)


# Refresh FilmOn playlist URLs frequently enough that signed
# URLs are not allowed to age too long.
HLS_UPSTREAM_REFRESH = int(
    os.getenv(
        "HLS_UPSTREAM_REFRESH",
        "15",
    )
)


HLS_TIMEOUT = int(
    os.getenv(
        "HLS_TIMEOUT",
        "20",
    )
)


HLS_RETRY_COUNT = int(
    os.getenv(
        "HLS_RETRY_COUNT",
        "3",
    )
)


# Keep segments considerably longer than the upstream live
# playlist so a slow client can catch up.
HLS_SEGMENT_CACHE_TTL = int(
    os.getenv(
        "HLS_SEGMENT_CACHE_TTL",
        "900",
    )
)


# This means UNIQUE (channel, sequence) segments.
HLS_SEGMENT_CACHE_MAX = int(
    os.getenv(
        "HLS_SEGMENT_CACHE_MAX",
        "4096",
    )
)


# Number of segments to prefetch concurrently.
HLS_PREFETCH_WORKERS = int(
    os.getenv(
        "HLS_PREFETCH_WORKERS",
        "8",
    )
)


# Background active-channel refresh interval.
HLS_BACKGROUND_INTERVAL = int(
    os.getenv(
        "HLS_BACKGROUND_INTERVAL",
        "2",
    )
)


# ============================================================
# HTTP
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


app = Flask(__name__)

lock = threading.Lock()

channels: list = []

stats: Counter = Counter()


# ============================================================
# FILMON SESSION
# ============================================================

_local = threading.local()


WARMUP_URL = os.getenv(
    "FILMON_WARMUP_URL",
    "https://www.filmon.com/tv/bbc-news",
)


_logged_cookies = False


def new_session() -> requests.Session:

    global _logged_cookies

    s = requests.Session()

    s.headers.update(
        HEADERS
    )

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

    if not hasattr(
        _local,
        "s",
    ):

        _local.s = new_session()

    return _local.s


# ============================================================
# FILMON API
# ============================================================

def get_channel_data(
    n: int,
    quality: str = "high",
):

    j = None

    for attempt in (
        1,
        2,
    ):

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

            if r.status_code in (
                404,
                405,
            ):

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

            if stats[
                "request_failed"
            ] < 3:

                print(
                    f"Request for channel {n} failed: {e!r}"
                )

            stats[
                "request_failed"
            ] += 1

            return None

        if (
            isinstance(j, dict)
            and j.get(
                "streamName"
            ) == "maintenance"
            and attempt == 1
            and not FILMON_COOKIE
        ):

            _local.s = new_session()

            continue

        break

    if (
        isinstance(j, dict)
        and isinstance(
            j.get("data"),
            dict,
        )
    ):

        j = j["data"]

    return (
        j
        if isinstance(j, dict)
        else None
    )


def _get_stream_url(
    channel: int,
) -> str | None:

    d = get_channel_data(
        channel,
        PREFERRED_QUALITY,
    )

    if not d:
        return None

    if not d.get("streams"):
        return None

    streams = d["streams"]

    pick = next(
        (
            s
            for s in streams
            if s.get(
                "quality"
            ) == PREFERRED_QUALITY
        ),
        streams[0],
    )

    return pick.get("url")


# ============================================================
# CHANNEL SCANNING
# ============================================================

def probe(n: int):

    d = get_channel_data(
        n,
        "high",
    )

    if d is None:
        return None

    if (
        not d.get("title")
        or not d.get("streams")
    ):

        if (
            d.get("streamName")
            == "maintenance"
        ):

            stats[
                "maintenance_placeholder"
            ] += 1

        else:

            stats[
                "no_channel_or_no_streams"
            ] += 1

        if sum(
            stats[k]
            for k in (
                "maintenance_placeholder",
                "no_channel_or_no_streams",
            )
        ) <= 2:

            print(
                f"Channel {n}: "
                f"no title/streams in response: "
                f"{str(d)[:200]}"
            )

        return None

    if (
        (
            SKIP_VR_EXP
            and d.get("is360")
        )
        or (
            SKIP_ADULT
            and d.get("is_adult")
        )
        or (
            SKIP_VOD_VOX
            and (
                d.get("is_vod")
                or d.get("is_vox")
            )
        )
    ):

        stats[
            "skipped_type"
        ] += 1

        return None

    if (
        FREE_ONLY
        and not d.get("is_free")
    ):

        stats[
            "not_free"
        ] += 1

        return None

    if (
        HD_ONLY
        and not any(
            s.get("quality") == "high"
            for s in d["streams"]
        )
    ):

        stats[
            "no_hd_stream"
        ] += 1

        return None

    stats[
        "kept"
    ] += 1

    if not d.get("is_free"):

        stats[
            "kept_but_not_flagged_free"
        ] += 1

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
            f"{PUBLIC_BASE_URL}/stream/"
            f"{c['id']}.m3u8"
        )

    return (
        "\n".join(lines)
        + "\n"
    )


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

            if (
                age
                < REFRESH_HOURS * 3600
            ):

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
# XMLTV EPG
# ============================================================

epg_xml: str = ""

epg_stats: Counter = Counter()


def parse_guide(j) -> list:

    if isinstance(
        j,
        dict,
    ):

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

        epg_stats[
            "failed"
        ] += 1

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

        epg_stats[
            "empty"
        ] += 1

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

    return (
        "\n".join(out)
        + "\n"
    )


def refresh_epg() -> None:

    global epg_xml

    snapshot = list(
        channels
    )

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
# HLS PROXY STATE
# ============================================================

_hls_lock = threading.RLock()


#
# token -> {
#     "channel": int,
#     "url": str,
#     "created": float,
#     "last_refresh": float,
#     "kind": "playlist" | "variant" | "segment",
#     "variant_index": int | None,
#     "sequence": int | None,
#     "parent_variant_token": str | None,
# }
#

_hls_urls = {}


#
# Actual segment cache.
#
# KEY:
#
#     (channel, sequence)
#
# VALUE:
#
#     {
#         "body": bytes,
#         "content_type": str,
#         "created": float,
#     }
#

_hls_segment_cache = {}


#
# Stable segment token mapping.
#
# KEY:
#
#     (channel, sequence)
#
# VALUE:
#
#     token
#

_hls_segment_tokens = {}


#
# Active media playlists.
#
# channel -> {
#     "variant_token": token,
#     "last_seen": timestamp,
#     "url": upstream URL,
# }
#

_hls_active_variants = {}


def _cleanup_hls_state() -> None:

    now = time.time()

    with _hls_lock:

        # ----------------------------------------------------
        # HLS token cleanup.
        # ----------------------------------------------------

        token_cutoff = (
            now
            - HLS_TOKEN_TTL
        )

        for token, value in list(
            _hls_urls.items()
        ):

            if (
                value.get(
                    "created",
                    0,
                )
                < token_cutoff
            ):

                _hls_urls.pop(
                    token,
                    None,
                )

        # ----------------------------------------------------
        # Segment TTL cleanup.
        # ----------------------------------------------------

        segment_cutoff = (
            now
            - HLS_SEGMENT_CACHE_TTL
        )

        for key, value in list(
            _hls_segment_cache.items()
        ):

            if (
                value.get(
                    "created",
                    0,
                )
                < segment_cutoff
            ):

                _hls_segment_cache.pop(
                    key,
                    None,
                )

                token = (
                    _hls_segment_tokens.pop(
                        key,
                        None,
                    )
                )

                if token:

                    _hls_urls.pop(
                        token,
                        None,
                    )

        # ----------------------------------------------------
        # Maximum UNIQUE segment count.
        # ----------------------------------------------------

        if (
            len(_hls_segment_cache)
            > HLS_SEGMENT_CACHE_MAX
        ):

            ordered = sorted(
                _hls_segment_cache.items(),
                key=lambda x:
                    x[1].get(
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

                token = (
                    _hls_segment_tokens.pop(
                        key,
                        None,
                    )
                )

                if token:

                    _hls_urls.pop(
                        token,
                        None,
                    )


# ============================================================
# HLS TOKEN MANAGEMENT
# ============================================================

def _hls_token(
    channel: int,
    url: str,
    kind: str = "unknown",
    variant_index: int | None = None,
    parent_variant_token: str | None = None,
    sequence: int | None = None,
) -> str:

    #
    # SEGMENTS:
    #
    # Stable token for (channel, sequence).
    #

    if (
        kind == "segment"
        and sequence is not None
    ):

        key = (
            channel,
            sequence,
        )

        with _hls_lock:

            existing = (
                _hls_segment_tokens.get(
                    key
                )
            )

            if existing:

                item = _hls_urls.get(
                    existing
                )

                if item:

                    item["url"] = url

                    item[
                        "last_refresh"
                    ] = time.time()

                    if parent_variant_token:

                        item[
                            "parent_variant_token"
                        ] = (
                            parent_variant_token
                        )

                    return existing

            token = uuid.uuid4().hex

            now = time.time()

            _hls_segment_tokens[
                key
            ] = token

            _hls_urls[token] = {
                "url": url,
                "channel": channel,
                "created": now,
                "last_refresh": now,
                "kind": "segment",
                "variant_index": None,
                "sequence": sequence,
                "parent_variant_token":
                    parent_variant_token,
            }

            return token

    #
    # PLAYLIST / VARIANT:
    #
    # Normal random token.
    #

    token = uuid.uuid4().hex

    now = time.time()

    with _hls_lock:

        _hls_urls[token] = {
            "url": url,
            "channel": channel,
            "created": now,
            "last_refresh": now,
            "kind": kind,
            "variant_index": variant_index,
            "sequence": sequence,
            "parent_variant_token":
                parent_variant_token,
        }

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

        path = urlparse(
            url
        ).path.lower()

        if (
            path.endswith(".m3u8")
            or path.endswith(".m3u")
        ):

            kind = "playlist"

        else:

            kind = "segment"

    token = _hls_token(
        channel,
        url,
        kind,
        variant_index,
        parent_variant_token,
        sequence,
    )

    suffix = (
        ".ts"
        if kind == "segment"
        else ""
    )

    return (
        f"{PUBLIC_BASE_URL}"
        f"/hls/{channel}/"
        f"{token}{suffix}"
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

        if (
            item.get(
                "channel"
            )
            != channel
        ):

            return None

        return dict(
            item
        )


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

        if (
            item.get(
                "channel"
            )
            != channel
        ):

            return False

        item["url"] = url

        item[
            "last_refresh"
        ] = time.time()

        return True


# ============================================================
# HLS VARIANT DISCOVERY
# ============================================================

def _get_variant_urls(
    manifest_url: str,
    body: str,
) -> list[str]:

    variants = []

    lines = body.splitlines()

    waiting_for_variant = False

    for line in lines:

        stripped = line.strip()

        if not stripped:

            continue

        if stripped.startswith(
            "#EXT-X-STREAM-INF:"
        ):

            waiting_for_variant = True

            continue

        if waiting_for_variant:

            if stripped.startswith(
                "#"
            ):

                continue

            variants.append(
                urljoin(
                    manifest_url,
                    stripped,
                )
            )

            waiting_for_variant = False

    return variants


# ============================================================
# HLS SESSION REFRESH
# ============================================================

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

    kind = item.get(
        "kind",
        "unknown",
    )

    # --------------------------------------------------------
    # VARIANT PLAYLIST
    # --------------------------------------------------------

    if kind == "variant":

        fresh_master_url = _get_stream_url(
            channel
        )

        if not fresh_master_url:

            print(
                f"HLS: unable to get fresh "
                f"master URL "
                f"channel={channel}"
            )

            return None

        try:

            r = _fetch_hls(
                fresh_master_url
            )

        except requests.RequestException as e:

            print(
                f"HLS: variant master refresh "
                f"failed channel={channel}: "
                f"{e!r}"
            )

            return None

        if r.status_code != 200:

            print(
                f"HLS: variant master refresh "
                f"HTTP {r.status_code} "
                f"channel={channel}"
            )

            return None

        master_body = r.text

        if "#EXTM3U" not in master_body:

            return None

        variants = _get_variant_urls(
            r.url,
            master_body,
        )

        index = item.get(
            "variant_index"
        )

        if (
            index is None
            or index < 0
            or index >= len(variants)
        ):

            print(
                f"HLS: invalid variant index "
                f"channel={channel} "
                f"index={index} "
                f"available={len(variants)}"
            )

            return None

        fresh_variant_url = variants[
            index
        ]

        if not _update_hls_url(
            channel,
            token,
            fresh_variant_url,
        ):

            return None

        with _hls_lock:

            _hls_active_variants[
                channel
            ] = {
                "variant_token": token,
                "last_seen": time.time(),
                "url": fresh_variant_url,
            }

        print(
            f"HLS: refreshed variant "
            f"channel={channel} "
            f"variant={index}"
        )

        return fresh_variant_url

    # --------------------------------------------------------
    # ROOT MASTER PLAYLIST
    # --------------------------------------------------------

    if kind == "playlist":

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

    return item["url"]


# ============================================================
# HLS HTTP HELPERS
# ============================================================

def _fetch_hls(
    url: str,
):

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

        return (
            "#EXTM3U"
            in probe
        )

    except Exception:

        return False


# ============================================================
# MEDIA SEQUENCE PARSING
# ============================================================

def _get_media_segments(
    manifest_url: str,
    body: str,
):

    media_sequence = None

    lines = body.splitlines()

    for line in lines:

        stripped = line.strip()

        if stripped.startswith(
            "#EXT-X-MEDIA-SEQUENCE:"
        ):

            try:

                media_sequence = int(
                    stripped.split(
                        ":",
                        1,
                    )[1]
                )

            except Exception:

                media_sequence = None

            break

    if media_sequence is None:

        return None, []

    segments = []

    sequence = media_sequence

    waiting_segment = False

    for line in lines:

        stripped = line.strip()

        if not stripped:

            continue

        if stripped.startswith(
            "#EXTINF:"
        ):

            waiting_segment = True

            continue

        if stripped.startswith(
            "#"
        ):

            continue

        if waiting_segment:

            absolute = urljoin(
                manifest_url,
                stripped,
            )

            segments.append(
                (
                    sequence,
                    absolute,
                )
            )

            sequence += 1

            waiting_segment = False

    return (
        media_sequence,
        segments,
    )


# ============================================================
# SEGMENT CACHE
# ============================================================

def _cache_segment(
    channel: int,
    sequence: int,
    body: bytes,
    content_type: str,
) -> None:

    if not body:

        return

    key = (
        channel,
        sequence,
    )

    with _hls_lock:

        _hls_segment_cache[key] = {
            "body": body,
            "content_type": (
                content_type
                or "video/mp2t"
            ),
            "created": time.time(),
        }

    _cleanup_hls_state()


def _get_cached_segment(
    channel: int,
    sequence: int,
):

    key = (
        channel,
        sequence,
    )

    with _hls_lock:

        item = _hls_segment_cache.get(
            key
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
                key,
                None,
            )

            token = (
                _hls_segment_tokens.pop(
                    key,
                    None,
                )
            )

            if token:

                _hls_urls.pop(
                    token,
                    None,
                )

            return None

        return {
            "body": item["body"],
            "content_type": (
                item[
                    "content_type"
                ]
                or "video/mp2t"
            ),
        }


# ============================================================
# PREFETCH ONE SEGMENT
# ============================================================

def _prefetch_one_segment(
    channel: int,
    sequence: int,
    url: str,
    parent_variant_token: str | None,
):

    cached = _get_cached_segment(
        channel,
        sequence,
    )

    if cached:

        _hls_token(
            channel,
            url,
            kind="segment",
            parent_variant_token=
                parent_variant_token,
            sequence=sequence,
        )

        return True

    last_error = None

    for attempt in range(
        HLS_RETRY_COUNT + 1
    ):

        try:

            r = _fetch_hls(
                url
            )

            if r.status_code == 200:

                body = r.content

                if not body:

                    last_error = (
                        "empty response"
                    )

                    continue

                content_type = (
                    r.headers.get(
                        "Content-Type"
                    )
                    or "video/mp2t"
                )

                _cache_segment(
                    channel,
                    sequence,
                    body,
                    content_type,
                )

                _hls_token(
                    channel,
                    r.url,
                    kind="segment",
                    parent_variant_token=
                        parent_variant_token,
                    sequence=sequence,
                )

                print(
                    f"HLS: prefetched segment "
                    f"channel={channel} "
                    f"sequence={sequence}"
                )

                return True

            last_error = (
                f"HTTP {r.status_code}"
            )

            print(
                f"HLS prefetch HTTP "
                f"{r.status_code} "
                f"channel={channel} "
                f"sequence={sequence}"
            )

        except requests.RequestException as e:

            last_error = repr(e)

            print(
                f"HLS prefetch request failed "
                f"channel={channel} "
                f"sequence={sequence} "
                f"attempt={attempt + 1}: "
                f"{e!r}"
            )

        if attempt < HLS_RETRY_COUNT:

            time.sleep(
                0.2
            )

    print(
        f"HLS prefetch failed "
        f"channel={channel} "
        f"sequence={sequence} "
        f"error={last_error}"
    )

    return False


# ============================================================
# PREFETCH MEDIA PLAYLIST
# ============================================================

def _prefetch_segments(
    channel: int,
    manifest_url: str,
    body: str,
    parent_variant_token: str | None,
):

    media_sequence, segments = (
        _get_media_segments(
            manifest_url,
            body,
        )
    )

    if not segments:

        return []


    #
    # Prefetch complete advertised window.
    #

    workers = min(
        HLS_PREFETCH_WORKERS,
        max(
            1,
            len(segments),
        ),
    )

    results = []

    with ThreadPoolExecutor(
        max_workers=workers
    ) as ex:

        futures = {
            ex.submit(
                _prefetch_one_segment,
                channel,
                sequence,
                url,
                parent_variant_token,
            ): sequence
            for sequence, url
            in segments
        }

        for future in futures:

            sequence = futures[
                future
            ]

            try:

                if future.result():

                    results.append(
                        sequence
                    )

            except Exception as e:

                print(
                    f"HLS prefetch worker "
                    f"failed channel={channel} "
                    f"sequence={sequence}: "
                    f"{e!r}"
                )

    return results


# ============================================================
# HLS MANIFEST REWRITING
# ============================================================

def _rewrite_hls_manifest(
    channel: int,
    manifest_url: str,
    body: str,
    parent_variant_token: str | None = None,
) -> str:

    #
    # First determine whether this is a media playlist.
    #

    media_sequence, segments = (
        _get_media_segments(
            manifest_url,
            body,
        )
    )

    # ========================================================
    # MEDIA PLAYLIST
    # ========================================================

    if segments:

        #
        # PREFETCH BEFORE PUBLISHING.
        #

        ready_sequences = set(
            _prefetch_segments(
                channel,
                manifest_url,
                body,
                parent_variant_token,
            )
        )

        out = []

        sequence_map = {
            url: sequence
            for sequence, url
            in segments
        }

        for line in body.splitlines():

            stripped = line.strip()

            if not stripped:

                out.append(line)

                continue

            if stripped.startswith(
                "#"
            ):

                #
                # Preserve HLS tags.
                #
                # For URI attributes such as encryption/key
                # URLs, rewrite them as normal playlist URLs.
                #

                if "URI=" in line:

                    def replace_uri(
                        match
                    ):

                        quote = match.group(1)

                        raw = match.group(2)

                        absolute = urljoin(
                            manifest_url,
                            raw,
                        )

                        return (
                            f"URI={quote}"
                            f"{_hls_url(
                                channel,
                                absolute,
                                kind="playlist",
                            )}"
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
            # Normal segment URI.
            #

            absolute = urljoin(
                manifest_url,
                stripped,
            )

            sequence = sequence_map.get(
                absolute
            )

            if sequence is None:

                continue

            #
            # Only publish successfully prefetched segments.
            #

            if sequence not in ready_sequences:

                #
                # If it was already cached, allow it.
                #

                if not _get_cached_segment(
                    channel,
                    sequence,
                ):

                    continue

            out.append(
                _hls_url(
                    channel,
                    absolute,
                    kind="segment",
                    parent_variant_token=
                        parent_variant_token,
                    sequence=sequence,
                )
            )

        return (
            "\n".join(out)
            + "\n"
        )

    # ========================================================
    # MASTER PLAYLIST
    # ========================================================

    out = []

    variant_index = 0

    waiting_for_variant = False

    for line in body.splitlines():

        stripped = line.strip()

        if not stripped:

            out.append(line)

            continue

        if stripped.startswith(
            "#"
        ):

            if "URI=" in line:

                def replace_uri(
                    match
                ):

                    quote = match.group(1)

                    raw = match.group(2)

                    absolute = urljoin(
                        manifest_url,
                        raw,
                    )

                    return (
                        f"URI={quote}"
                        f"{_hls_url(
                            channel,
                            absolute,
                            kind="playlist",
                        )}"
                        f"{quote}"
                    )

                line = re.sub(
                    r'URI=(["\'])(.*?)\1',
                    replace_uri,
                    line,
                )

            out.append(line)

            if stripped.startswith(
                "#EXT-X-STREAM-INF:"
            ):

                waiting_for_variant = True

            continue

        absolute = urljoin(
            manifest_url,
            stripped,
        )

        if waiting_for_variant:

            token = _hls_token(
                channel,
                absolute,
                kind="variant",
                variant_index=variant_index,
            )

            with _hls_lock:

                _hls_active_variants[
                    channel
                ] = {
                    "variant_token": token,
                    "last_seen": time.time(),
                    "url": absolute,
                }

            out.append(
                _hls_url(
                    channel,
                    absolute,
                    kind="variant",
                    variant_index=variant_index,
                )
            )

            variant_index += 1

            waiting_for_variant = False

        else:

            out.append(
                _hls_url(
                    channel,
                    absolute,
                    kind="playlist",
                )
            )

    return (
        "\n".join(out)
        + "\n"
    )


# ============================================================
# PUBLIC ENDPOINTS
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

    with _hls_lock:

        segment_cache_count = len(
            _hls_segment_cache
        )

        hls_token_count = len(
            _hls_urls
        )

        active_channels = len(
            _hls_active_variants
        )

    return {
        "channels": len(channels),
        "last_scan_stats": dict(stats),
        "hls": {
            "segment_cache":
                segment_cache_count,
            "hls_tokens":
                hls_token_count,
            "active_channels":
                active_channels,
            "cache_ttl":
                HLS_SEGMENT_CACHE_TTL,
            "cache_max":
                HLS_SEGMENT_CACHE_MAX,
        },
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


@app.get("/guide.xml")
def guide():

    if not epg_xml:

        abort(404)

    return Response(
        epg_xml,
        mimetype="application/xml",
        headers={
            "Cache-Control":
                "no-store",
                "no-cache",
                "must-revalidate",
                "max-age=0",
        },
    )


# ============================================================
# MAIN STABLE HLS ENTRY POINT
# ============================================================

@app.get("/stream/<int:n>.m3u8")
def stream(n: int):

    url = _get_stream_url(
        n
    )

    if not url:

        abort(404)

    if not REWRITE_PLAYLIST:

        return Response(
            "",
            status=302,
            headers={
                "Location": url
            },
        )

    r = _fetch_hls(
        url
    )

    if r.status_code in (
        401,
        403,
        404,
    ):

        url = _get_stream_url(
            n
        )

        if not url:

            abort(404)

        r = _fetch_hls(
            url
        )

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

    #
    # Create one local master token.
    #

    master_token = _hls_token(
        n,
        final,
        kind="playlist",
    )

    out = _rewrite_hls_manifest(
        n,
        final,
        body,
        parent_variant_token=
            master_token,
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
            "Pragma":
                "no-cache",
            "Content-Length":
                str(
                    len(
                        out.encode(
                            "utf-8"
                        )
                    )
                ),
        },
    )


# ============================================================
# HLS PROXY
# ============================================================

@app.get("/hls/<int:n>/<path:token>")
def hls_proxy(
    n: int,
    token: str,
):

    #
    # FFmpeg/Jellyfin/VLC requests:
    #
    #     token.ts
    #
    # Strip extension internally.
    #

    if token.endswith(
        ".ts"
    ):

        token = token[
            :-3
        ]

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

    # ========================================================
    # MEDIA SEGMENT
    # ========================================================

    if kind == "segment":

        sequence = item.get(
            "sequence"
        )

        #
        # The sequence cache is authoritative.
        #

        if sequence is not None:

            cached = (
                _get_cached_segment(
                    n,
                    sequence,
                )
            )

            if cached:

                body = cached[
                    "body"
                ]

                return Response(
                    body,
                    content_type=(
                        cached[
                            "content_type"
                        ]
                        or "video/mp2t"
                    ),
                    headers={
                        "Cache-Control":
                            "no-store, "
                            "no-cache, "
                            "must-revalidate, "
                            "max-age=0",
                        "Content-Length":
                            str(
                                len(body)
                            ),
                    },
                )

    # ========================================================
    # PLAYLIST / VARIANT
    # ========================================================

    if kind in (
        "playlist",
        "variant",
    ):

        upstream_url = (
            _refresh_hls_session(
                n,
                token,
            )
        )

        if not upstream_url:

            print(
                f"HLS: unable to refresh "
                f"playlist "
                f"channel={n} "
                f"kind={kind}"
            )

            abort(502)

    else:

        upstream_url = item[
            "url"
        ]

    # ========================================================
    # UPSTREAM FETCH
    # ========================================================

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
        # Only playlist/variant URLs can be refreshed by
        # requesting a fresh FilmOn master.
        #

        if kind in (
            "playlist",
            "variant",
        ):

            upstream_url = (
                _refresh_hls_session(
                    n,
                    token,
                    force=True,
                )
            )

            if not upstream_url:

                break

        time.sleep(
            0.25
        )

    # ========================================================
    # UPSTREAM FAILED
    # ========================================================

    if (
        not r
        or r.status_code != 200
    ):

        #
        # Last chance for media segments:
        #
        # serve the cached sequence if available.
        #

        if (
            kind == "segment"
            and item.get(
                "sequence"
            ) is not None
        ):

            cached = (
                _get_cached_segment(
                    n,
                    item[
                        "sequence"
                    ],
                )
            )

            if cached:

                body = cached[
                    "body"
                ]

                print(
                    f"HLS: served cached "
                    f"segment after upstream "
                    f"failure "
                    f"channel={n} "
                    f"sequence={item['sequence']}"
                )

                return Response(
                    body,
                    content_type=(
                        cached[
                            "content_type"
                        ]
                        or "video/mp2t"
                    ),
                    headers={
                        "Cache-Control":
                            "no-store, "
                            "no-cache, "
                            "must-revalidate, "
                            "max-age=0",
                        "Content-Length":
                            str(
                                len(body)
                            ),
                    },
                )

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

    # ========================================================
    # NESTED PLAYLIST
    # ========================================================

    if _is_manifest(body):

        text_body = body.decode(
            "utf-8",
            errors="replace",
        )

        if kind in (
            "playlist",
            "variant",
        ):

            _update_hls_url(
                n,
                token,
                r.url,
            )

        out = _rewrite_hls_manifest(
            n,
            r.url,
            text_body,
            parent_variant_token=
                token,
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
                "Pragma":
                    "no-cache",
                "Content-Length":
                    str(
                        len(
                            out.encode(
                                "utf-8"
                            )
                        )
                    ),
            },
        )

    # ========================================================
    # MEDIA SEGMENT
    # ========================================================

    if kind == "segment":

        sequence = item.get(
            "sequence"
        )

        if sequence is not None:

            _cache_segment(
                n,
                sequence,
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
            "Content-Length":
                str(
                    len(body)
                ),
        },
    )


# ============================================================
# BACKGROUND HLS PREFETCH
# ============================================================

def _background_hls_prefetch() -> None:

    """
    Keep active channels warm.

    We do NOT continuously scan every FilmOn channel.

    Only channels that have recently had a variant playlist
    requested are refreshed.
    """

    while True:

        try:

            now = time.time()

            with _hls_lock:

                active = [
                    (
                        channel,
                        dict(value),
                    )
                    for channel, value
                    in _hls_active_variants.items()
                    if (
                        now
                        - value.get(
                            "last_seen",
                            0,
                        )
                    ) < HLS_TOKEN_TTL
                ]

            for channel, value in active:

                variant_token = value.get(
                    "variant_token"
                )

                if not variant_token:

                    continue

                try:

                    item = _get_hls_item(
                        channel,
                        variant_token,
                    )

                    if not item:

                        continue

                    upstream_url = (
                        _refresh_hls_session(
                            channel,
                            variant_token,
                        )
                    )

                    if not upstream_url:

                        continue

                    r = _fetch_hls(
                        upstream_url
                    )

                    if r.status_code != 200:

                        print(
                            f"HLS background HTTP "
                            f"{r.status_code} "
                            f"channel={channel}"
                        )

                        continue

                    body = r.content

                    if not _is_manifest(
                        body
                    ):

                        continue

                    text_body = body.decode(
                        "utf-8",
                        errors="replace",
                    )

                    _prefetch_segments(
                        channel,
                        r.url,
                        text_body,
                        variant_token,
                    )

                    with _hls_lock:

                        if channel in (
                            _hls_active_variants
                        ):

                            _hls_active_variants[
                                channel
                            ][
                                "last_seen"
                            ] = time.time()

                except Exception as e:

                    print(
                        f"HLS background refresh "
                        f"failed channel={channel}: "
                        f"{e!r}"
                    )

            _cleanup_hls_state()

        except Exception as e:

            print(
                f"HLS background worker "
                f"failed: {e!r}"
            )

        time.sleep(
            HLS_BACKGROUND_INTERVAL
        )


# ============================================================
# START BACKGROUND WORKERS
# ============================================================

threading.Thread(
    target=background,
    daemon=True,
    name="filmon-scan",
).start()


if EPG_ENABLED:

    threading.Thread(
        target=epg_loop,
        daemon=True,
        name="filmon-epg",
    ).start()


threading.Thread(
    target=_background_hls_prefetch,
    daemon=True,
    name="filmon-hls-prefetch",
).start()


# ============================================================
# MAIN
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