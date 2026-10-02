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
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

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
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36",
)
# ------------------------------------------------------------

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
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


def get_channel_data(n: int, quality: str = "high"):
    """Returns the channel dict from FilmOn, or None on any failure."""
    try:
        r = requests.post(API_URL, data={"channel_id": n, "quality": quality},
                          headers=HEADERS, timeout=15)
        if r.status_code in (404, 405):  # fall back to GET if POST isn't accepted
            r = requests.get(API_URL, params={"channel_id": n, "quality": quality},
                             headers=HEADERS, timeout=15)
        j = r.json()
    except Exception as e:
        if stats["request_failed"] < 3:
            print(f"Request for channel {n} failed: {e!r}")
        stats["request_failed"] += 1
        return None
    if isinstance(j, dict) and isinstance(j.get("data"), dict):
        j = j["data"]  # tolerate a {"data": {...}} wrapper
    return j if isinstance(j, dict) else None


def probe(n: int):
    d = get_channel_data(n, "high")
    if d is None:
        return None
    if not d.get("title") or not d.get("streams"):
        stats["no_channel_or_no_streams"] += 1
        if stats["no_channel_or_no_streams"] <= 2:
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


threading.Thread(target=background, daemon=True).start()


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


@app.get("/stream/<int:n>.m3u8")
def stream(n: int):
    d = get_channel_data(n, PREFERRED_QUALITY)
    if not d or not d.get("streams"):
        abort(404)
    streams = d["streams"]
    pick = next((s for s in streams if s.get("quality") == PREFERRED_QUALITY), streams[0])
    url = pick["url"]

    if not REWRITE_PLAYLIST:
        return redirect(url, code=302)

    r = requests.get(url, headers=HEADERS, timeout=15)
    if r.status_code != 200:
        abort(502)
    final = r.url
    out = []
    for line in r.text.splitlines():
        if line and not line.startswith("#"):
            line = urljoin(final, line)
        out.append(line)
    return Response("\n".join(out), mimetype="application/vnd.apple.mpegurl")