#!/usr/bin/env python3
"""Read ID-Tisini.xlsx Game ID sheet and load into fixture_videos."""
import re, os
import openpyxl
import psycopg2
from psycopg2.extras import execute_values

DB = os.environ.get("NEON_URL",
    "postgresql://neondb_owner:npg_gGLbRFKdme28@ep-misty-credit-aqm0oae5-pooler.c-8.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")

XLSX = "/Users/baptistegaultier/Downloads/ID -Tisini.xlsx"

def classify(url: str) -> str:
    if not url: return None
    u = url.lower()
    if "veo.co" in u: return "veo"
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "drive.google.com" in u: return "drive"
    if "wetransfer" in u or "we.tl" in u: return "wetransfer"
    if "grosfichiers" in u: return "grosfichiers"
    if "docs.google.com/spreadsheets" in u: return "sheet"
    if "ffr.fr" in u: return "ffr"
    if "facebook.com" in u: return "facebook"
    if "scrummage.co.ke" in u: return "blog"
    return "other"

def extract_t_seconds(url: str):
    """Pull ?t=192s or &t=348s or ?t=192 from a youtube URL."""
    if not url: return None
    m = re.search(r"[?&]t=(\d+)s?", url)
    return int(m.group(1)) if m else None

def parse_start_offset(video_info: str):
    """Parse 'Start 96:36' or 'start at 144,46' or '144.46' -> seconds."""
    if not video_info: return None
    txt = video_info.strip().lower()
    # mm:ss
    m = re.search(r"(\d{1,3}):(\d{2})", txt)
    if m: return int(m.group(1)) * 60 + int(m.group(2))
    # mm,ss or mm.ss (decimal minutes)
    m = re.search(r"(\d{1,3})[,.](\d{1,2})", txt)
    if m: return int(m.group(1)) * 60 + int(m.group(2))
    return None

def extract_drive_id(url: str):
    if not url: return None
    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    return m.group(1) if m else None

def main():
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb["Game ID"]
    rows = list(ws.iter_rows(values_only=True))

    out = []
    for r in rows[1:]:
        if len(r) < 8 or r[3] is None: continue
        # Cols: 0=Covered 1=Opponent 2=Date 3=Game ID 4=Jersey 5=Video Info(notes) 6=Video Link 7=Line up 8=Seen
        covered, opponent, gid, video_info, video_link, lineup_col = r[0], r[1], r[3], r[5], r[6], r[7]
        fixture_id = int(gid)
        start_offset = parse_start_offset(video_info)  # parses "Start 96:36" or "144,46"
        # Real URL columns are video_link (col 6) and the "Line up" col (col 7) which often holds the Drive MP4.
        for col_kind, url in (("link", video_link), ("download", lineup_col)):
            if not isinstance(url, str): continue
            url = url.strip()
            if url.startswith("www"): url = "https://" + url
            if not url.startswith("http"): continue
            platform = classify(url)
            t_sec = extract_t_seconds(url)
            drive_id = extract_drive_id(url) if platform == "drive" else None
            out.append((fixture_id, platform, col_kind, url, start_offset, t_sec, drive_id, str(covered or "").strip(), str(opponent or "").strip()))

    print(f"Parsed {len(out)} video links across {len(set(r[0] for r in out))} fixtures")

    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS fixture_videos (
          id              SERIAL PRIMARY KEY,
          fixture_id      INTEGER,
          platform        TEXT,
          source          TEXT,           -- 'primary' (Video Info col) | 'secondary' (Video Link col)
          url             TEXT,
          start_offset_sec INTEGER,        -- offset where match starts in the video (from "Start 96:36")
          embed_t_sec     INTEGER,         -- t= param in the URL (youtube)
          drive_file_id   TEXT,
          covered_team    TEXT,
          opponent        TEXT,
          synced_at       TIMESTAMPTZ DEFAULT NOW()
        );
        TRUNCATE fixture_videos RESTART IDENTITY;
        CREATE INDEX IF NOT EXISTS idx_fv_fixture ON fixture_videos(fixture_id);
    """)
    execute_values(cur,
        "INSERT INTO fixture_videos(fixture_id,platform,source,url,start_offset_sec,embed_t_sec,drive_file_id,covered_team,opponent) VALUES %s",
        out)
    conn.commit()

    cur.execute("""
        SELECT platform, count(*) FROM fixture_videos GROUP BY 1 ORDER BY 2 DESC;
    """)
    print("\nBy platform:")
    for p, n in cur.fetchall(): print(f"  {p}: {n}")

    cur.execute("""
        SELECT fv.platform, count(DISTINCT fv.fixture_id) 
        FROM fixture_videos fv 
        JOIN fixtures f ON f.id=fv.fixture_id
        GROUP BY 1 ORDER BY 2 DESC;
    """)
    print("\nMatched to actual fixtures in Tisini:")
    for p, n in cur.fetchall(): print(f"  {p}: {n} fixtures")

    cur.close(); conn.close()

if __name__ == "__main__":
    main()
