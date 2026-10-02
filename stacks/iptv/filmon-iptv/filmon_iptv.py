"""
FilmOn -> Jellyfin helper.

- Scans FilmOn channels (on start, then every REFRESH_HOURS)
- Writes an M3U file into a shared folder (default /iptv/filmon.m3u) that Jellyfin can read directly
- Serves /stream/<id>.m3u8 which fetches a FRESH stream URL on every play, so links never expire

Endpoints:
  GET /playlist.m3u      same playlist over HTTP
  GET /stream/<id>.m3u8  fresh stream for a channel
  GET /refresh           force re-scan + rewrite M3U
  GET /health
"""

import json
import os
import threading
import time
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
MAX_CHANNEL_ID = int(os.getenv("MAX_CHANNEL_ID", "2000"))
WORKERS = int(os.getenv("WORKERS", "16"))
LOGO_SIZE = os.getenv("LOGO_SIZE", "extra_big")        # 'extra_big', 'big', or ''
SKIP_ADULT = env_bool("SKIP_ADULT", True)
SKIP_VR_EXP = env_bool("SKIP_VR_EXP", True)
SKIP_VOD_VOX = env_bool("SKIP_VOD_VOX", True)
PREFERRED_QUALITY = os.getenv("PREFERRED_QUALITY", "high")  # 'high' or 'low'
REWRITE_PLAYLIST = env_bool("REWRITE_PLAYLIST", True)       # False = plain 302 redirect
# ------------------------------------------------------------

API = "https://www.filmon.com/api-v2/channel/{}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:101.0) Gecko/20100101 Firefox/101.0"
}

app = Flask(__name__)
lock = threading.Lock()
channels: list = []


def get_channel_data(n: int):
    try:
        r = requests.get(API.format(n), params={"protocol": "hls"},
                         headers=HEADERS, timeout=10)
        return r.json()
    except Exception:
        return None


def probe(n: int):
    j = get_channel_data(n)
    if not j or j.get("code") in (403, 404) or "data" not in j:
        return None
    d = j["data"]
    if (SKIP_VR_EXP and d.get("is360")) or (SKIP_ADULT and d.get("is_adult")) \
            or (SKIP_VOD_VOX and (d.get("is_vod") or d.get("is_vox"))):
        return None
    return {"id": n, "name": d.get("title", f"Channel {n}"),
            "group": (d.get("group") or "Other").title()}


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
        tmp.replace(M3U_OUT_PATH)  # atomic, Jellyfin never sees a half-written file
        os.chmod(M3U_OUT_PATH, 0o644)
        print(f"Wrote {M3U_OUT_PATH}")
    except Exception as e:
        print(f"Could not write {M3U_OUT_PATH}: {e}")


def scan() -> None:
    global channels
    print("Scanning FilmOn channels...")
    with ThreadPoolExecutor(WORKERS) as ex:
        found = [c for c in ex.map(probe, range(1, MAX_CHANNEL_ID)) if c]
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


@app.get("/health")
def health():
    return {"channels": len(channels)}


@app.get("/refresh")
def refresh():
    scan()
    return f"OK, {len(channels)} channels"


@app.get("/playlist.m3u")
def playlist():
    return Response(build_m3u(), mimetype="audio/x-mpegurl")


@app.get("/stream/<int:n>.m3u8")
def stream(n: int):
    j = get_channel_data(n)
    if not j or "data" not in j:
        abort(404)
    streams = j["data"].get("streams") or []
    if not streams:
        abort(404)
    pick = next((s for s in streams if s.get("quality") == PREFERRED_QUALITY), streams[0])
    url = pick["url"]

    if not REWRITE_PLAYLIST:
        return redirect(url, code=302)

    r = requests.get(url, headers=HEADERS, timeout=10)
    if r.status_code != 200:
        abort(502)
    final = r.url
    out = []
    for line in r.text.splitlines():
        if line and not line.startswith("#"):
            line = urljoin(final, line)
        out.append(line)
    return Response("\n".join(out), mimetype="application/vnd.apple.mpegurl")