"""
FilmOn -> Jellyfin helper (new site API).

- Scans FilmOn channels via the site's own /ajax/getChannelInfo endpoint
  (on start, then every REFRESH_HOURS)
- Writes an M3U file into a shared folder (default /iptv/filmon.m3u)
- Serves /stream/<id>.m3u8 with a stable local URL
- Proxies nested HLS playlists and media segments
- Continuously refreshes short-lived FilmOn stream URLs
- Refreshes expired HLS media segments through their parent variant playlist
- Tracks HLS MEDIA-SEQUENCE so expired segments are remapped correctly
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


HLS_UPSTREAM_REFRESH = int(
    os.getenv(
        "HLS_UPSTREAM_REFRESH",
        "30",
    )
)


HLS_TIMEOUT = int(
    os.getenv(
        "HLS_TIMEOUT",
        "15",
    )
)


HLS_RETRY_COUNT = int(
    os.getenv(
        "HLS_RETRY_COUNT",
        "2",
    )
)


HLS_SEGMENT_CACHE_TTL = int(
    os.getenv(
        "HLS_SEGMENT_CACHE_TTL",
        "120",
    )
)


HLS_SEGMENT_CACHE_MAX = int(
    os.getenv(
        "HLS_SEGMENT_CACHE_MAX",
        "512",
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

_hls_lock = threading.Lock()


#
# token -> {
#     "channel": int,
#     "url": str,
#     "created": float,
#     "last_refresh": float,
#     "kind": "playlist" | "variant" | "segment",
#     "variant_index": int | None,
#     "parent_variant_token": str | None,
#     "sequence": int | None,
# }
#

_hls_urls = {}


#
# Recently downloaded media segments.
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

            if (
                value.get(
                    "created",
                    0,
                )
                < cutoff
            ):

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

            if (
                value.get(
                    "created",
                    0,
                )
                < segment_cutoff
            ):

                _hls_segment_cache.pop(
                    key,
                    None
                )

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


def _hls_token(
    channel: int,
    url: str,
    kind: str = "unknown",
    variant_index: int | None = None,
    parent_variant_token: str | None = None,
    sequence: int | None = None,
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
            "variant_index": variant_index,
            "parent_variant_token": parent_variant_token,
            "sequence": sequence,
        }

    _cleanup_hls_state()

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

    return (
        f"{PUBLIC_BASE_URL}"
        f"/hls/{channel}/"
        f"{token}"
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


def _update_hls_segment(
    channel: int,
    token: str,
    url: str,
    sequence: int | None,
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

        item["sequence"] = sequence

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

            if stripped.startswith("#"):
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
# HLS MEDIA SEGMENT PARSER
# ============================================================

def _get_media_segments(
    manifest_url: str,
    body: str,
) -> tuple[int, list[tuple[int, str]]]:
    """
    Parse a media playlist and return:

        (media_sequence_start, [(sequence, absolute_url), ...])

    Example:

        #EXT-X-MEDIA-SEQUENCE:1120

        segment A -> 1120
        segment B -> 1121
        segment C -> 1122

    This lets us refresh an expired signed URL while preserving
    the exact HLS segment Jellyfin requested.
    """

    media_sequence = 0

    for line in body.splitlines():

        stripped = line.strip()

        if stripped.startswith(
            "#EXT-X-MEDIA-SEQUENCE:"
        ):

            value = stripped.split(
                ":",
                1,
            )[1].strip()

            try:

                media_sequence = int(
                    value
                )

            except ValueError:

                media_sequence = 0

            break

    segments = []

    sequence = media_sequence

    waiting_for_segment = False

    for line in body.splitlines():

        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith("#"):

            if stripped.startswith(
                "#EXTINF:"
            ):

                waiting_for_segment = True

            continue

        # A normal URI following EXTINF is a media segment.
        if waiting_for_segment:

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

            waiting_for_segment = False

    return (
        media_sequence,
        segments,
    )


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

            print(
                f"HLS: fresh master is not "
                f"a manifest channel={channel}"
            )

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

        print(
            f"HLS: refreshed variant "
            f"channel={channel} "
            f"variant={index}"
        )

        return fresh_variant_url

    # --------------------------------------------------------
    # ROOT / MASTER PLAYLIST
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
# HLS SEGMENT REFRESH - SEQUENCE AWARE
# ============================================================

def _refresh_segment_from_parent(
    channel: int,
    segment_token: str,
):
    """
    Refresh an expired segment through its parent variant.

    IMPORTANT:

    We do NOT fall back to the newest segment.

    Instead, the segment token contains the HLS
    MEDIA-SEQUENCE number that was assigned when the original
    local playlist was generated.

    Example:

        local segment token
        sequence=1120

    Parent variant is refreshed:

        1118
        1119
        1120   <-- exact match
        1121
        1122

    Only sequence 1120 is accepted.

    If FilmOn has already removed sequence 1120 from its live
    window, this function returns None rather than giving
    Jellyfin a different segment.
    """

    item = _get_hls_item(
        channel,
        segment_token,
    )

    if not item:
        return None

    parent_token = item.get(
        "parent_variant_token"
    )

    if not parent_token:

        print(
            f"HLS: segment has no parent variant "
            f"channel={channel} "
            f"token={segment_token}"
        )

        return None

    sequence = item.get(
        "sequence"
    )

    if sequence is None:

        print(
            f"HLS: segment has no MEDIA-SEQUENCE "
            f"channel={channel} "
            f"token={segment_token}"
        )

        return None

    parent = _get_hls_item(
        channel,
        parent_token,
    )

    if not parent:

        print(
            f"HLS: parent variant token missing "
            f"channel={channel} "
            f"parent={parent_token}"
        )

        return None

    # --------------------------------------------------------
    # Force refresh the parent variant.
    # --------------------------------------------------------

    variant_url = _refresh_hls_session(
        channel,
        parent_token,
        force=True,
    )

    if not variant_url:

        print(
            f"HLS: unable to refresh parent "
            f"variant channel={channel}"
        )

        return None

    try:

        r = _fetch_hls(
            variant_url
        )

    except requests.RequestException as e:

        print(
            f"HLS: segment parent request "
            f"failed channel={channel}: "
            f"{e!r}"
        )

        return None

    if r.status_code != 200:

        print(
            f"HLS: refreshed variant HTTP "
            f"{r.status_code} "
            f"channel={channel}"
        )

        return None

    body = r.content

    if not _is_manifest(body):

        print(
            f"HLS: refreshed parent is not "
            f"a manifest channel={channel}"
        )

        return None

    text_body = body.decode(
        "utf-8",
        errors="replace",
    )

    _media_sequence, fresh_segments = (
        _get_media_segments(
            r.url,
            text_body,
        )
    )

    if not fresh_segments:

        print(
            f"HLS: refreshed variant has no "
            f"segments channel={channel}"
        )

        return None

    # --------------------------------------------------------
    # Find EXACT requested MEDIA-SEQUENCE.
    # --------------------------------------------------------

    for fresh_sequence, fresh_url in fresh_segments:

        if fresh_sequence == sequence:

            if not _update_hls_segment(
                channel,
                segment_token,
                fresh_url,
                fresh_sequence,
            ):

                return None

            print(
                f"HLS: refreshed segment "
                f"channel={channel} "
                f"sequence={sequence}"
            )

            return fresh_url

    # --------------------------------------------------------
    # Requested segment has already left the live window.
    #
    # DO NOT substitute a newer segment.
    # --------------------------------------------------------

    first_sequence = fresh_segments[0][0]
    last_sequence = fresh_segments[-1][0]

    print(
        f"HLS: requested segment sequence "
        f"{sequence} is no longer available "
        f"channel={channel} "
        f"live_window={first_sequence}-{last_sequence}"
    )

    return None


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
# HLS MANIFEST REWRITING
# ============================================================

def _rewrite_hls_manifest(
    channel: int,
    manifest_url: str,
    body: str,
    parent_variant_token: str | None = None,
) -> str:
    """
    Rewrite all URLs inside an HLS manifest.

    Master playlist variants are stored as:

        kind="variant"

    Media segments are stored as:

        kind="segment"

    Every media segment stores:

        - parent_variant_token
        - MEDIA-SEQUENCE

    This makes segment refresh sequence-aware.
    """

    # --------------------------------------------------------
    # Determine whether this is a media playlist.
    # --------------------------------------------------------

    is_media_playlist = (
        "#EXT-X-MEDIA-SEQUENCE:" in body
        or "#EXTINF:" in body
    )

    media_sequence_start = 0

    if is_media_playlist:

        for line in body.splitlines():

            stripped = line.strip()

            if stripped.startswith(
                "#EXT-X-MEDIA-SEQUENCE:"
            ):

                try:

                    media_sequence_start = int(
                        stripped.split(
                            ":",
                            1,
                        )[1].strip()
                    )

                except ValueError:

                    media_sequence_start = 0

                break

    out = []

    lines = body.splitlines()

    variant_index = 0

    waiting_for_variant = False

    segment_sequence = media_sequence_start

    waiting_for_segment = False

    for line in lines:

        stripped = line.strip()

        if not stripped:

            out.append(line)

            continue

        # ----------------------------------------------------
        # HLS tags
        # ----------------------------------------------------

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
                        f"{_hls_url(
                            channel,
                            absolute,
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

            if stripped.startswith(
                "#EXTINF:"
            ):

                waiting_for_segment = True

            continue

        # ----------------------------------------------------
        # URI after EXT-X-STREAM-INF
        #
        # This is a variant media playlist.
        # ----------------------------------------------------

        absolute = urljoin(
            manifest_url,
            stripped,
        )

        if waiting_for_variant:

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

            continue

        # ----------------------------------------------------
        # Media segment.
        # ----------------------------------------------------

        if is_media_playlist:

            out.append(
                _hls_url(
                    channel,
                    absolute,
                    kind="segment",
                    parent_variant_token=(
                        parent_variant_token
                    ),
                    sequence=segment_sequence,
                )
            )

            segment_sequence += 1

            waiting_for_segment = False

        else:

            # Generic URI not associated with a known media
            # playlist. Preserve it as a proxied segment.
            out.append(
                _hls_url(
                    channel,
                    absolute,
                    kind="segment",
                    parent_variant_token=(
                        parent_variant_token
                    ),
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

        return redirect(
            url,
            code=302,
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

    out = _rewrite_hls_manifest(
        n,
        final,
        body,
        parent_variant_token=None,
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


# ============================================================
# HLS PROXY
# ============================================================

@app.get("/hls/<int:n>/<token>")
def hls_proxy(
    n: int,
    token: str,
):

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

    # --------------------------------------------------------
    # SEGMENT CACHE
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # PLAYLIST / VARIANT REFRESH
    # --------------------------------------------------------

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
                f"FilmOn playlist "
                f"channel={n} "
                f"kind={kind}"
            )

            abort(502)

    else:

        # Media segment.
        upstream_url = item["url"]

    # --------------------------------------------------------
    # FETCH UPSTREAM
    # --------------------------------------------------------

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

        # ----------------------------------------------------
        # Playlist / variant:
        # refresh their own URL.
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Segment:
        #
        # Refresh parent variant and locate the EXACT
        # MEDIA-SEQUENCE requested by Jellyfin.
        # ----------------------------------------------------

        elif kind == "segment":

            upstream_url = (
                _refresh_segment_from_parent(
                    n,
                    token,
                )
            )

            if not upstream_url:

                # The requested segment has probably already
                # left the upstream live window.
                break

        time.sleep(
            0.25
        )

    # --------------------------------------------------------
    # UPSTREAM FAILED
    # --------------------------------------------------------

    if (
        not r
        or r.status_code != 200
    ):

        print(
            f"HLS upstream failed "
            f"permanently "
            f"channel={n} "
            f"kind={kind} "
            f"token={token}"
        )

        abort(502)

    body = r.content

    # --------------------------------------------------------
    # Protect against empty upstream responses.
    # --------------------------------------------------------

    if not body:

        print(
            f"HLS upstream returned empty body "
            f"channel={n} "
            f"kind={kind}"
        )

        abort(502)

    content_type = (
        r.headers.get(
            "Content-Type"
        )
        or ""
    ).lower()

    # --------------------------------------------------------
    # NESTED PLAYLIST
    # --------------------------------------------------------

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
            parent_variant_token=(
                token
                if kind == "variant"
                else None
            ),
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

    # --------------------------------------------------------
    # MEDIA SEGMENT
    # --------------------------------------------------------

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
# BACKGROUND WORKERS
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