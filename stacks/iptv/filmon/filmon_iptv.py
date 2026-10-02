"""
FilmOn -> Jellyfin helper (new site API).

- Scans FilmOn channels via the site's own /ajax/getChannelInfo endpoint
  (on start, then every REFRESH_HOURS)
- Writes an M3U file into a shared folder (default /iptv/filmon.m3u) that Jellyfin can read directly
- Serves /stream/<id>.m3u8, which asks FilmOn for a fresh stream URL on every play

This only fetches what FilmOn itself serves to your session. It does not remove any
time limits or subscription checks. If your account/session only gets a short preview
for a channel, that is what Jellyfin will get too.

Endpoints:
  GET /                  status page
  GET /playlist.m3u      same playlist over HTTP
  GET /stream/<id>.m3u8  fresh stream for a channel
  GET /refresh           force re-scan + rewrite M3U
  GET /health            channel count + last scan stats
"""

import json
import os
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


PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8765").rstrip("/")
M3U_OUT_PATH = Path(os.getenv("M3U_OUT_PATH", "/iptv/filmon.m3u"))
CACHE_FILE = Path(os.getenv("CACHE_FILE", "/data/filmon_channels.json"))
REFRESH_HOURS = float(os.getenv("REFRESH_HOURS", "24"))
MIN_CHANNEL_ID = int(os.getenv("MIN_CHANNEL_ID", "1"))
MAX_CHANNEL_ID = int(os.getenv("MAX_CHANNEL_ID", "2000"))
WORKERS = int(os.getenv("WORKERS", "8"))
LOGO_SIZE = os.getenv("LOGO_SIZE", "extra_big")          # 'extra_big', 'big', or ''
SKIP_ADULT = env_bool("SKIP_ADULT", True)
SKIP_VR_EXP = env_bool("SKIP_VR_EXP", True)
SKIP_VOD_VOX = env_bool("SKIP_VOD_VOX", True)
HD_ONLY = env_bool("HD_ONLY", True)                      # keep channels that offer a 'high' stream
FREE_ONLY = env_bool("FREE_ONLY", False)                 # keep only channels flagged is_free
PREFERRED_QUALITY = os.getenv("PREFERRED_QUALITY", "high")  # 'high' (HD) or 'low' (SD)
REWRITE_PLAYLIST = env_bool("REWRITE_PLAYLIST", True)    # False = plain 302 redirect
FILMON_COOKIE = os.getenv("FILMON_COOKIE", "")           # optional: your own logged-in session cookie
API_URL = os.getenv("FILMON_API", "https://www.filmon.com/ajax/getChannelInfo")
EPG_ENABLED = env_bool("EPG_ENABLED", True)              # also build an XMLTV guide file
EPG_OUT_PATH = Path(os.getenv("EPG_OUT_PATH", "/iptv/filmon.xml"))
EPG_REFRESH_HOURS = float(os.getenv("EPG_REFRESH_HOURS", "6"))
EPG_API = os.getenv("FILMON_EPG_API", "https://www.filmon.com/api-v2/tvguide/{}")
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36",
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

# HLS proxy state.  FilmOn's stream URLs are short-lived, so never expose them
# directly to Jellyfin.  Instead, every HLS URI is proxied through this process.
_hls_lock = threading.Lock()
_hls_urls = {}  # token -> {"url": str, "channel": int, "created": float}
HLS_TOKEN_TTL = int(os.getenv("HLS_TOKEN_TTL", "600"))
HLS_TIMEOUT = int(os.getenv("HLS_TIMEOUT", "15"))


_local = threading.local()
WARMUP_URL = os.getenv("FILMON_WARMUP_URL", "https://www.filmon.com/tv/bbc-news")
_logged_cookies = False


def new_session() -> requests.Session:
    """A fresh session that first visits a FilmOn page, like a browser, so FilmOn can
    hand out its normal guest session cookies. Skipped if you supply FILMON_COOKIE."""
    global _logged_cookies
    s = requests.Session()
    s.headers.update(HEADERS)
    if not FILMON_COOKIE:
        # FilmOn only starts its guest session on the "classic" site; without this cookie
        # the visit redirects to the new /v2/ app and no session cookies are issued.
        s.cookies.set("ftv_site_mode", "classic", domain="www.filmon.com", path="/")
        try:
            s.get(WARMUP_URL, timeout=15)
        except Exception as e:
            print(f"Warm-up request failed: {e!r}")
        if not _logged_cookies:
            _logged_cookies = True
            print(f"Session cookies received from warm-up: {sorted(s.cookies.keys())}")
    return s


def get_session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = new_session()
    return _local.s


def get_channel_data(n: int, quality: str = "high"):
    """Returns the channel dict from FilmOn, or None on any failure."""
    j = None
    for attempt in (1, 2):
        try:
            s = get_session()
            r = s.post(API_URL, data={"channel_id": n, "quality": quality}, timeout=15)
            if r.status_code in (404, 405):  # fall back to GET if POST isn't accepted
                r = s.get(API_URL, params={"channel_id": n, "quality": quality}, timeout=15)
            j = r.json()
        except Exception as e:
            if stats["request_failed"] < 3:
                print(f"Request for channel {n} failed: {e!r}")
            stats["request_failed"] += 1
            return None
        if isinstance(j, dict) and j.get("streamName") == "maintenance" \
                and attempt == 1 and not FILMON_COOKIE:
            _local.s = new_session()  # session may have gone stale: start a new one and retry once
            continue
        break
    if isinstance(j, dict) and isinstance(j.get("data"), dict):
        j = j["data"]  # tolerate a {"data": {...}} wrapper
    return j if isinstance(j, dict) else None


def probe(n: int):
    d = get_channel_data(n, "high")
    if d is None:
        return None
    if not d.get("title") or not d.get("streams"):
        if d.get("streamName") == "maintenance":
            stats["maintenance_placeholder"] += 1
        else:
            stats["no_channel_or_no_streams"] += 1
        if sum(stats[k] for k in ("maintenance_placeholder", "no_channel_or_no_streams")) <= 2:
            print(f"Channel {n}: no title/streams in response: {str(d)[:200]}")
        return None
    if (SKIP_VR_EXP and d.get("is360")) or (SKIP_ADULT and d.get("is_adult")) \
            or (SKIP_VOD_VOX and (d.get("is_vod") or d.get("is_vox"))):
        stats["skipped_type"] += 1
        return None
    if FREE_ONLY and not d.get("is_free"):
        stats["not_free"] += 1
        return None
    if HD_ONLY and not any(s.get("quality") == "high" for s in d["streams"]):
        stats["no_hd_stream"] += 1
        return None
    stats["kept"] += 1
    if not d.get("is_free"):
        stats["kept_but_not_flagged_free"] += 1
    return {"id": n, "name": d["title"], "group": (d.get("group") or "Other").title()}


def build_m3u() -> str:
    lines = ["#EXTM3U"]
    for c in channels:
        logo = f"https://static.filmon.com/assets/channels/{c['id']}/{LOGO_SIZE}_logo.png"
        lines.append(f'#EXTINF:-1 tvg-id="{c["id"]}" tvg-logo="{logo}" '
                     f'group-title="{c["group"]}",{c["name"]}')
        lines.append(f"{PUBLIC_BASE_URL}/stream/{c['id']}.m3u8")
    return "\n".join(lines) + "\n"


def write_m3u() -> None:
    try:
        M3U_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = M3U_OUT_PATH.with_suffix(".tmp")
        tmp.write_text(build_m3u(), encoding="utf-8")
        tmp.replace(M3U_OUT_PATH)  # atomic: Jellyfin never sees a half-written file
        os.chmod(M3U_OUT_PATH, 0o644)
        print(f"Wrote {M3U_OUT_PATH}")
    except Exception as e:
        print(f"Could not write {M3U_OUT_PATH}: {e}")


def scan() -> None:
    global channels
    print("Scanning FilmOn channels...")
    stats.clear()
    with ThreadPoolExecutor(WORKERS) as ex:
        found = [c for c in ex.map(probe, range(MIN_CHANNEL_ID, MAX_CHANNEL_ID)) if c]
    print(f"Scan stats: {dict(stats)}")
    if not found:
        print("Scan found nothing (network/API issue?) - keeping previous list")
        return
    with lock:
        channels = found
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(json.dumps(found))
        except Exception as e:
            print(f"Cache write failed: {e}")
    print(f"Scanned: {len(found)} channels")
    write_m3u()


def background() -> None:
    global channels
    if CACHE_FILE.exists():
        try:
            channels = json.loads(CACHE_FILE.read_text())
            write_m3u()  # M3U available immediately from cache
            age = time.time() - CACHE_FILE.stat().st_mtime
            if age < REFRESH_HOURS * 3600:
                time.sleep(REFRESH_HOURS * 3600 - age)
        except Exception:
            pass
    while True:
        scan()
        time.sleep(REFRESH_HOURS * 3600)


# ---------------- TV guide (XMLTV) ----------------
epg_xml: str = ""
epg_stats: Counter = Counter()


def parse_guide(j) -> list:
    """Accepts a list, a single programme, or a dict wrapping a list."""
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
    items = [p for p in parse_guide(j) if isinstance(p, dict)
             and p.get("programme_name") and p.get("startdatetime") and p.get("enddatetime")]
    if not items:
        epg_stats["empty"] += 1
    return items


def xmltv_time(ts) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def build_epg_xml(snapshot: list, guides: dict) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<tv generator-info-name="filmon-proxy">']
    for c in snapshot:
        logo = f"https://static.filmon.com/assets/channels/{c['id']}/{LOGO_SIZE}_logo.png"
        out.append(f'<channel id="{c["id"]}"><display-name>{escape(c["name"])}</display-name>'
                   f'<icon src={quoteattr(logo)}/></channel>')
    for c in snapshot:
        for p in guides.get(c["id"], []):
            try:
                start, stop = xmltv_time(p["startdatetime"]), xmltv_time(p["enddatetime"])
            except Exception:
                continue
            parts = [f'<programme start="{start}" stop="{stop}" channel="{c["id"]}">',
                     f'<title>{escape(str(p["programme_name"]))}</title>']
            if p.get("programme_description"):
                parts.append(f'<desc>{escape(str(p["programme_description"]))}</desc>')
            imgs = [i.get("url") for i in (p.get("images") or []) if isinstance(i, dict) and i.get("url")]
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
    print(f"EPG: {total} programmes for {sum(1 for v in guides.values() if v)} of "
          f"{len(ids)} channels; stats {dict(epg_stats)}")
    if total == 0:
        print("EPG came back empty - not writing a guide file")
        return
    epg_xml = build_epg_xml(snapshot, guides)
    try:
        EPG_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = EPG_OUT_PATH.with_suffix(".tmp")
        tmp.write_text(epg_xml, encoding="utf-8")
        tmp.replace(EPG_OUT_PATH)
        os.chmod(EPG_OUT_PATH, 0o644)
        print(f"Wrote {EPG_OUT_PATH}")
    except Exception as e:
        print(f"Could not write {EPG_OUT_PATH}: {e}")


def epg_loop() -> None:
    while not channels:
        time.sleep(10)
    while True:
        try:
            refresh_epg()
        except Exception as e:
            print(f"EPG refresh failed: {e!r}")
        time.sleep(EPG_REFRESH_HOURS * 3600)


@app.get("/guide.xml")
def guide():
    if not epg_xml:
        abort(404)
    return Response(epg_xml, mimetype="application/xml")


threading.Thread(target=background, daemon=True).start()
if EPG_ENABLED:
    threading.Thread(target=epg_loop, daemon=True).start()


@app.get("/")
def index():
    return (f"filmon-iptv running. {len(channels)} channels. "
            f"Try /playlist.m3u, /health, /refresh")


@app.get("/health")
def health():
    return {"channels": len(channels), "last_scan_stats": dict(stats)}


@app.get("/refresh")
def refresh():
    scan()
    return f"OK, {len(channels)} channels"


@app.get("/playlist.m3u")
def playlist():
    return Response(build_m3u(), mimetype="audio/x-mpegurl")


def _hls_token(channel: int, url: str) -> str:
    token = uuid.uuid4().hex
    with _hls_lock:
        _hls_urls[token] = {
            "url": url,
            "channel": channel,
            "created": time.time(),
        }
        # Keep the in-memory map small.
        cutoff = time.time() - HLS_TOKEN_TTL
        for key, value in list(_hls_urls.items()):
            if value["created"] < cutoff:
                _hls_urls.pop(key, None)
    return token


def _hls_url(channel: int, url: str) -> str:
    return f"{PUBLIC_BASE_URL}/hls/{channel}/{_hls_token(channel, url)}"


def _get_stream_url(channel: int) -> str | None:
    d = get_channel_data(channel, PREFERRED_QUALITY)
    if not d or not d.get("streams"):
        return None
    streams = d["streams"]
    pick = next(
        (s for s in streams if s.get("quality") == PREFERRED_QUALITY),
        streams[0],
    )
    return pick.get("url")


def _fetch_hls(url: str):
    """Fetch an HLS resource with the same FilmOn session/cookies."""
    return get_session().get(
        url,
        headers=HEADERS,
        timeout=HLS_TIMEOUT,
        allow_redirects=True,
    )


def _rewrite_hls_manifest(channel: int, manifest_url: str, body: str) -> str:
    """Rewrite every URI in an HLS manifest so Jellyfin stays on our proxy."""
    out = []

    for line in body.splitlines():
        stripped = line.strip()

        if not stripped:
            out.append(line)
            continue

        # URI=... attributes occur in tags such as EXT-X-MEDIA and
        # EXT-X-I-FRAME-STREAM-INF, not only as standalone lines.
        if stripped.startswith("#"):
            if "URI=" in line:
                import re

                def replace_uri(match):
                    quote = match.group(1)
                    raw = match.group(2)
                    absolute = urljoin(manifest_url, raw)
                    return f"URI={quote}{_hls_url(channel, absolute)}{quote}"

                line = re.sub(
                    r'URI=(["\'])(.*?)\1',
                    replace_uri,
                    line,
                )
            out.append(line)
            continue

        absolute = urljoin(manifest_url, stripped)
        out.append(_hls_url(channel, absolute))

    return "\n".join(out) + "\n"


@app.get("/stream/<int:n>.m3u8")
def stream(n: int):
    """
    Return a fresh FilmOn HLS playlist, but proxy ALL nested playlists and
    media segments.  This prevents Jellyfin from holding stale FilmOn
    segment URLs that expire after a short period.
    """
    url = _get_stream_url(n)
    if not url:
        abort(404)

    if not REWRITE_PLAYLIST:
        return redirect(url, code=302)

    r = _fetch_hls(url)

    # FilmOn can occasionally return an expired stream URL.  Ask the API for
    # a new one and retry once.
    if r.status_code in (401, 403, 404):
        url = _get_stream_url(n)
        if not url:
            abort(404)
        r = _fetch_hls(url)

    if r.status_code != 200:
        print(f"FilmOn HLS failed for channel {n}: HTTP {r.status_code}")
        abort(502)

    final = r.url
    body = r.text

    if "#EXTM3U" not in body:
        abort(502)

    out = _rewrite_hls_manifest(n, final, body)
    return Response(
        out,
        mimetype="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/hls/<int:n>/<token>")
def hls_proxy(n: int, token: str):
    """
    Proxy a single HLS resource.

    If a short-lived FilmOn URL has expired, refresh the channel's stream
    URL and try to locate the same resource in the new manifest.  This makes
    the proxy resilient to FilmOn's ~45-second URL/token lifetime.
    """
    with _hls_lock:
        item = _hls_urls.get(token)

    if not item or item["channel"] != n:
        abort(404)

    url = item["url"]
    r = _fetch_hls(url)

    if r.status_code in (401, 403, 404):
        # The signed URL has probably expired. Get a fresh stream URL.
        fresh = _get_stream_url(n)
        if not fresh:
            abort(404)

        fresh_r = _fetch_hls(fresh)
        if fresh_r.status_code != 200:
            abort(502)

        fresh_final = fresh_r.url
        fresh_body = fresh_r.text

        if "#EXTM3U" in fresh_body:
            # For a playlist request, return the refreshed playlist.
            requested_path = urlparse(url).path.rsplit("/", 1)[-1]
            fresh_lines = fresh_body.splitlines()

            # If the old request looked like a playlist, the new response is
            # directly usable.
            if requested_path.endswith((".m3u8", ".m3u")) or not requested_path:
                refreshed = _rewrite_hls_manifest(n, fresh_final, fresh_body)
                return Response(
                    refreshed,
                    mimetype="application/vnd.apple.mpegurl",
                    headers={"Cache-Control": "no-store"},
                )

            # For an expired segment, locate the same segment filename in the
            # new playlist and fetch its newly signed URL.
            wanted_name = requested_path
            candidate = None
            for line in fresh_lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                candidate_url = urljoin(fresh_final, stripped)
                if urlparse(candidate_url).path.rsplit("/", 1)[-1] == wanted_name:
                    candidate = candidate_url
                    break

            if candidate:
                r = _fetch_hls(candidate)
                url = candidate

    if r.status_code != 200:
        abort(502)

    content_type = (r.headers.get("Content-Type") or "").lower()
    body = r.content

    # Nested/variant HLS playlists must also have all their URIs proxied.
    if "#EXTM3U" in body[:1024].decode("utf-8", errors="ignore"):
        text_body = body.decode("utf-8", errors="replace")
        return Response(
            _rewrite_hls_manifest(n, r.url, text_body),
            mimetype="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-store"},
        )

    return Response(
        body,
        content_type=content_type or "video/mp2t",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        },
    )
