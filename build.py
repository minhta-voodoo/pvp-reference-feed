#!/usr/bin/env python3
"""Build the PvP reference feed: Notion database -> one static page.

The page has two tabs:
  * This week - recent additions as cards with a short muted gameplay loop
  * Library   - the whole database as a searchable, sortable text table

Standard library only. Clips need ffmpeg/ffprobe on PATH.

Environment:
  NOTION_TOKEN        Notion internal-integration secret (required unless --fixture)
  NOTION_DATABASE_ID  defaults to the PvP Reference Library
  FEED_DAYS           how far back the feed goes (default 28)

Usage:
  python3 build.py                      # normal run (Notion + Steam + ffmpeg)
  python3 build.py --fixture rows.json  # offline: rows already normalised
  python3 build.py --no-clips           # skip Steam and ffmpeg (posters only)
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "4008752aa1484179a4d107cd8e91c1d1")
NOTION_URL = f"https://www.notion.so/{DATABASE_ID}"
FEED_DAYS = int(os.environ.get("FEED_DAYS", "28"))
FEED_SOURCES = {"Weekly scan", "Trend sweep"}  # back-catalogue rows live in the Library tab only
CLIP_SECONDS = 7
CLIP_WIDTH = 480

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
CACHE = ROOT / "clip-cache"  # persisted between runs by actions/cache, never committed

FIELDS = {
    "Game": "game", "Type": "type", "Status": "status", "Release": "release",
    "Publisher": "publisher", "Developer": "developer", "Reference": "reference",
    "Mobile PvP angle": "angle", "Tags": "tags", "Signal": "signal", "Steam": "steam",
    "App ID": "app_id", "Found via": "found_via", "Followers": "followers",
    "Followers checked": "followers_checked", "Prev followers": "prev_followers",
    "Prev checked": "prev_checked", "Weekly gain": "weekly_gain", "Traction": "traction",
    "Starred": "starred", "Added": "added",
}


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- Notion

def http_json(url, method="GET", body=None, headers=None, retries=4):
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise


def notion(method, path, body=None, version="2025-09-03"):
    token = os.environ["NOTION_TOKEN"]
    return http_json(
        "https://api.notion.com/v1" + path, method, body,
        {"Authorization": f"Bearer {token}", "Notion-Version": version,
         "Content-Type": "application/json"},
    )


def fetch_pages():
    try:  # current API: database -> data source -> query
        ds_id = notion("GET", f"/databases/{DATABASE_ID}")["data_sources"][0]["id"]
        path, version = f"/data_sources/{ds_id}/query", "2025-09-03"
    except (urllib.error.HTTPError, KeyError, IndexError):  # older single-source API
        path, version = f"/databases/{DATABASE_ID}/query", "2022-06-28"
    pages, cursor = [], None
    while True:
        body = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
        res = notion("POST", path, body, version)
        pages += res["results"]
        if not res.get("has_more"):
            return pages
        cursor = res["next_cursor"]


def prop_value(p):
    t = p.get("type")
    v = p.get(t)
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in v or [])
    if t == "select":
        return v["name"] if v else None
    if t == "multi_select":
        return [o["name"] for o in v or []]
    if t == "date":
        return v["start"] if v else None
    if t == "formula":
        return v.get(v.get("type")) if v else None
    return v  # number, url, checkbox, created_time, last_edited_time


def normalise(page):
    props = page.get("properties", {})
    row = {key: prop_value(props[name]) if name in props else None for name, key in FIELDS.items()}
    row["notion"] = page.get("url")
    return row


# ---------------------------------------------------------------- traction

def fill_traction(row):
    """Use Notion's formula values; compute the same thing if they're missing."""
    f, pf = row.get("followers"), row.get("prev_followers")
    if row.get("weekly_gain") is None:
        gain = 0
        if f is not None and pf is not None and row.get("followers_checked") and row.get("prev_checked"):
            days = (dt.date.fromisoformat(row["followers_checked"][:10])
                    - dt.date.fromisoformat(row["prev_checked"][:10])).days
            gain = round((f - pf) * 7 / max(1, days))
        row["weekly_gain"] = gain
    if not row.get("traction"):
        g = row["weekly_gain"] or 0
        row["traction"] = ("Unknown" if f is None else "Hot" if g >= 1000
                           else "Big" if f >= 10000 else "Mid" if f >= 2000 else "Small")
    has_two_readings = pf is not None and row.get("prev_checked")
    if not has_two_readings:
        row["weekly_gain"] = None  # don't show a fake "+0/wk"
    return row


# ---------------------------------------------------------------- Steam media

def steam_media(app_id):
    url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&l=english"
    try:
        entry = http_json(url, headers={"User-Agent": "Mozilla/5.0"}).get(str(app_id), {})
    except Exception as e:  # noqa: BLE001 - one bad game must not break the build
        log(f"  appdetails failed for {app_id}: {e}")
        return {}
    if not entry.get("success"):
        return {}
    d = entry["data"]
    movies = d.get("movies") or []
    pick = (next((m for m in movies if re.search(r"gameplay", m.get("name", ""), re.I)), None)
            or next((m for m in movies if m.get("highlight")), None)
            or (movies[0] if movies else None))
    stream = poster = None
    if pick:
        stream = (pick.get("hls_h264") or pick.get("dash_h264")
                  or (pick.get("mp4") or {}).get("max") or (pick.get("webm") or {}).get("max"))
        poster = pick.get("thumbnail")
    shots = d.get("screenshots") or []
    poster = poster or (shots[0].get("path_thumbnail") if shots else None) or d.get("header_image")
    return {"stream": stream, "poster": poster}


def probe_duration(stream):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", stream],
            capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out)
    except (ValueError, subprocess.SubprocessError):
        return None


def make_clip(app_id, stream):
    """Cut a short muted loop ~25% into the trailer, past logos and title cards."""
    CACHE.mkdir(exist_ok=True)
    out = CACHE / f"{app_id}.mp4"
    if out.exists() and out.stat().st_size > 10_000:
        return out
    dur = probe_duration(stream)
    start = 10.0 if not dur else max(4.0, min(dur * 0.25, dur - CLIP_SECONDS - 1))
    tmp = CACHE / f"{app_id}.part.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.1f}", "-i", stream,
           "-t", str(CLIP_SECONDS), "-an", "-vf", f"scale={CLIP_WIDTH}:-2,fps=24",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(tmp)]
    try:
        subprocess.run(cmd, check=True, timeout=240)
        tmp.replace(out)
        return out
    except subprocess.SubprocessError as e:
        log(f"  clip failed for {app_id}: {e}")
        tmp.unlink(missing_ok=True)
        return None


# ---------------------------------------------------------------- build

def week_start(iso):
    d = dt.date.fromisoformat(iso[:10])
    return (d - dt.timedelta(days=d.weekday())).isoformat()


def build(rows, clips=True):
    today = dt.datetime.now(dt.timezone.utc).date()
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "clips").mkdir(parents=True)

    rows = [fill_traction(r) for r in rows if r.get("game")]
    feed = [r for r in rows
            if r.get("found_via") in FEED_SOURCES and r.get("added")
            and (today - dt.date.fromisoformat(r["added"][:10])).days <= FEED_DAYS]
    log(f"{len(rows)} rows, {len(feed)} in the feed")

    used = set()
    for i, r in enumerate(feed, 1):
        r["week"] = week_start(r["added"])
        if not clips or not r.get("app_id"):
            continue
        app_id = int(r["app_id"])
        log(f"[{i}/{len(feed)}] {r['game']}")
        media = steam_media(app_id)
        r["poster"] = media.get("poster")
        if media.get("stream"):
            clip = make_clip(app_id, media["stream"])
            if clip:
                shutil.copy(clip, SITE / "clips" / clip.name)
                r["clip"] = f"clips/{clip.name}"
                used.add(clip.name)
        time.sleep(1.5)  # stay well under Steam's store API rate limit

    if CACHE.exists():  # keep the cache to what the feed still shows
        for f in CACHE.glob("*.mp4"):
            if f.name not in used and clips:
                f.unlink()

    payload = {
        "built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "notion": NOTION_URL,
        "feedDays": FEED_DAYS,
        "rows": rows,
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = (ROOT / "template.html").read_text(encoding="utf-8").replace("__DATA__", data)
    (SITE / "index.html").write_text(page, encoding="utf-8")
    size = sum(f.stat().st_size for f in SITE.rglob("*") if f.is_file())
    log(f"site/ written: {len(used)} clips, {size / 1e6:.1f} MB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", help="JSON list of normalised rows (skips Notion)")
    ap.add_argument("--no-clips", action="store_true", help="skip Steam and ffmpeg")
    args = ap.parse_args()
    snapshot = ROOT / "data.json"
    if args.fixture:
        rows = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    elif os.environ.get("NOTION_TOKEN"):
        rows = [normalise(p) for p in fetch_pages()]
    elif snapshot.exists():  # no Notion access yet: build from the committed snapshot
        log("NOTION_TOKEN not set - building from data.json snapshot")
        rows = json.loads(snapshot.read_text(encoding="utf-8"))
    else:
        sys.exit("NOTION_TOKEN is not set and there is no data.json snapshot")
    build(rows, clips=not args.no_clips)


if __name__ == "__main__":
    main()
