#!/usr/bin/env python3
"""LLM clip search: natural language -> SQL on fixture_events -> ranked clips."""
import os, sys, json, argparse
import psycopg2
from psycopg2.extras import RealDictCursor
import anthropic

DB = os.environ.get("NEON_URL",
    "postgresql://neondb_owner:npg_gGLbRFKdme28@ep-misty-credit-aqm0oae5-pooler.c-8.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")

# Schema context injected into the prompt — Claude needs to know the columns.
SCHEMA_DOC = """
You have ONE table to query: `fixture_events` (one row per event in a rugby match).
Columns:
  - fixture_id INT, team_id INT (764 = EPERNAY = our club), player_id INT, pname TEXT
  - event_name TEXT  (values: Pass, Carries, Tackle, Tackles, Ruck, Kick for territory,
      Penalties conceded, Penalty Gain, Touchess/Takes, Missed tackle, Missed tackles,
      Score, Knock on, Lineout throw, Lineouts, Substitute, Turn overs, Forward pass,
      Linebreak, Maul, Scrum)
  - subevent_id TEXT  (numeric code for the sub-event variant, eg "56"=Positive tackle,
      "57"=Negative tackle, "21"=Yellow card, "Try", "Successful Conversion" etc.
      Don't filter on this unless the user explicitly mentions a sub-type.)
  - game_minute INT, game_second INT
  - game_moment TEXT  ('firsthalf' | 'secondhalf')
  - quarter TEXT  ('first' | 'second' | 'third' | 'fourth')
  - jersey_no TEXT, zone_id INT
  - xper NUMERIC, yper NUMERIC  (pitch coordinates 0-100)
  - videotimestamp NUMERIC  (seconds into the video, may be 0 if not annotated)
  - noruck TEXT, nolineout TEXT, metergain TEXT, kickfrom TEXT, kickland TEXT, defender TEXT

Joinable tables:
  - fixtures (id, team1_name, team2_name, home_points, away_points, game_date)
  - players (id, first_name, sir_name)

Hard rules:
  1. Output ONE single SELECT statement, nothing else (no markdown, no commentary).
  2. Always SELECT: fixture_id, event_name, subevent_id, pname, game_minute, game_second,
     videotimestamp, game_moment, xper, yper.
  3. Add `JOIN fixtures f ON f.id = fe.fixture_id` and include f.team1_name, f.team2_name, f.game_date.
  4. LIMIT 20 unless the user asks for more.
  5. ORDER BY fixture_id DESC, game_minute, game_second.
  6. If the user mentions EPERNAY or "nous"/"notre équipe", filter team_id = 764.
  7. If the user asks for "perdues" mêlées/touches: event_name = 'Scrum' or 'Lineout throw'
     with subevent_id matching 'Lost'/'Stolen' patterns — when in doubt, return without subevent filter.
"""

def ask_claude(query: str) -> str:
    """Convert natural language to SQL via Claude."""
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        system=SCHEMA_DOC,
        messages=[{"role": "user", "content": f"User query (French OK): {query}\n\nReturn only the SQL."}],
    )
    sql = msg.content[0].text.strip()
    # Strip code fences if any
    if sql.startswith("```"):
        sql = sql.split("```")[1]
        if sql.startswith("sql"): sql = sql[3:]
    return sql.strip().rstrip(";")

def fallback_sql(query: str) -> str:
    """Keyword-based fallback when no ANTHROPIC_API_KEY is set."""
    q = query.lower()
    where = ["fe.team_id = 764"]  # EPERNAY by default
    if "mêlée" in q or "melee" in q or "scrum" in q:
        where.append("fe.event_name IN ('Scrum')")
    elif "touche" in q or "lineout" in q:
        where.append("fe.event_name IN ('Lineout throw','Lineouts')")
    elif "plaqu" in q or "tackle" in q:
        if "manqu" in q or "missed" in q:
            where.append("fe.event_name IN ('Missed tackle','Missed tackles')")
        else:
            where.append("fe.event_name IN ('Tackle','Tackles')")
    elif "essai" in q or "try" in q:
        where.append("fe.event_name = 'Score' AND fe.subevent_id IN ('66','200')")
    elif "pénalité" in q or "penalite" in q or "penalty" in q:
        where.append("fe.event_name IN ('Penalties conceded','Penalty Gain')")
    elif "turnover" in q:
        where.append("fe.event_name = 'Turn overs'")
    elif "linebreak" in q or "franchissement" in q:
        where.append("fe.event_name = 'Linebreak'")
    if "1re" in q or "première" in q or "premiere" in q:
        where.append("fe.game_moment = 'firsthalf'")
    elif "2e" in q or "deuxième" in q or "deuxieme" in q:
        where.append("fe.game_moment = 'secondhalf'")
    return f"""
SELECT fe.fixture_id, fe.event_name, fe.subevent_id, fe.pname, fe.game_minute, fe.game_second,
       fe.videotimestamp, fe.game_moment, fe.xper, fe.yper,
       f.team1_name, f.team2_name, f.game_date
FROM fixture_events fe
JOIN fixtures f ON f.id = fe.fixture_id
WHERE {' AND '.join(where)}
ORDER BY fixture_id DESC, game_minute, game_second
LIMIT 20
""".strip()

def best_video(cur, fixture_id: int):
    """Pick the best playable URL for a fixture. Priority: youtube > drive > veo."""
    cur.execute("""
        SELECT platform, url, start_offset_sec, drive_file_id
        FROM fixture_videos
        WHERE fixture_id = %s
        ORDER BY CASE platform
          WHEN 'youtube' THEN 1
          WHEN 'drive'   THEN 2
          WHEN 'veo'     THEN 3
          ELSE 99 END
        LIMIT 1
    """, (fixture_id,))
    return cur.fetchone()

def clip_url(video_row, videotimestamp):
    """Build a clickable URL with deep-link to the right second."""
    if not video_row: return None
    t = int((videotimestamp or 0) + (video_row.get("start_offset_sec") or 0))
    p = video_row["platform"]
    url = video_row["url"]
    if p == "youtube":
        import re as _re
        url = _re.sub(r"[?&]t=\d+s?", "", url)
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}t={t}s"
    if p == "drive" and video_row.get("drive_file_id"):
        return f"https://drive.google.com/file/d/{video_row['drive_file_id']}/preview"
    if p == "veo":
        return f"{url.rstrip('/')}/?time={t}"
    return url

def run(query: str):
    print(f"\n🔍 Query: {query!r}")
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    sql = ask_claude(query) if has_key else fallback_sql(query)
    print(f"\n📝 SQL ({'Claude' if has_key else 'fallback'}):\n{sql}\n")

    conn = psycopg2.connect(DB)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(sql)
    rows = cur.fetchall()
    cur2 = conn.cursor(cursor_factory=RealDictCursor)
    video_cache = {}
    for r in rows:
        fid = r["fixture_id"]
        if fid not in video_cache:
            video_cache[fid] = best_video(cur2, fid)

    print(f"📦 {len(rows)} clip(s) found:\n")
    for i, r in enumerate(rows, 1):
        ts = r.get('videotimestamp') or 0
        m, s = divmod(int(ts), 60)
        link = clip_url(video_cache.get(r["fixture_id"]), ts) or "(no video on file)"
        print(f"  {i:>2}. {r['team1_name']} vs {r['team2_name']} ({r['game_date']}) — {r['event_name']} @ {m:02d}:{s:02d} — {r.get('pname') or '-'}")
        print(f"      → {link}")
    cur.close(); cur2.close()
    cur.close(); conn.close()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+", help="Question en langage naturel")
    args = ap.parse_args()
    run(" ".join(args.query))
