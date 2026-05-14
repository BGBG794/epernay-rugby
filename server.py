#!/usr/bin/env python3
"""FastAPI backend for clip search + static UI."""
import os, re, json
# Load .env if present
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    for line in open(_env_path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor

DB = os.environ.get("NEON_URL",
    "postgresql://neondb_owner:npg_gGLbRFKdme28@ep-misty-credit-aqm0oae5-pooler.c-8.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")

app = FastAPI(title="SCUF Clip Search")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def fallback_sql(query: str) -> tuple[str, str]:
    """Keyword -> SQL. Returns (sql, intent_label)."""
    q = query.lower()
    where = ["fe.team_id = 764"]
    intent = "événements EPERNAY"
    if "mêlée" in q or "melee" in q or "scrum" in q:
        where.append("fe.event_name = 'Scrum'"); intent = "mêlées"
    elif "touche" in q or "lineout" in q:
        where.append("fe.event_name IN ('Lineout throw','Lineouts')"); intent = "touches"
    elif "plaqu" in q or "tackle" in q:
        if "manqu" in q or "missed" in q:
            where.append("fe.event_name IN ('Missed tackle','Missed tackles')"); intent = "plaquages manqués"
        else:
            where.append("fe.event_name IN ('Tackle','Tackles')"); intent = "plaquages"
    elif "essai" in q or "try" in q:
        where.append("fe.event_name = 'Score' AND fe.subevent_id IN ('66','200')"); intent = "essais"
    elif "pénalité" in q or "penalite" in q or "penalty" in q:
        where.append("fe.event_name IN ('Penalties conceded','Penalty Gain')"); intent = "pénalités"
    elif "turnover" in q:
        where.append("fe.event_name = 'Turn overs'"); intent = "turnovers"
    elif "linebreak" in q or "franchissement" in q:
        where.append("fe.event_name = 'Linebreak'"); intent = "franchissements"
    elif "kick" in q or "coup de pied" in q or "tape" in q:
        where.append("fe.event_name = 'Kick for territory'"); intent = "kicks territoriaux"
    elif "ruck" in q:
        where.append("fe.event_name = 'Ruck'"); intent = "rucks"
    elif "knock" in q or "en avant" in q:
        where.append("fe.event_name = 'Knock on'"); intent = "en-avants"
    if "1re" in q or "première" in q or "premiere" in q or "first half" in q:
        where.append("fe.game_moment = 'firsthalf'"); intent += " (1re mi-temps)"
    elif "2e" in q or "deuxième" in q or "deuxieme" in q or "second half" in q:
        where.append("fe.game_moment = 'secondhalf'"); intent += " (2e mi-temps)"
    # Restrict to fixtures that have a playable video, prioritize YouTube
    sql = f"""
WITH best_v AS (
  SELECT DISTINCT ON (fixture_id) fixture_id, platform
  FROM fixture_videos
  ORDER BY fixture_id,
    CASE platform WHEN 'youtube' THEN 1 WHEN 'drive' THEN 2 WHEN 'veo' THEN 3 ELSE 99 END
)
SELECT fe.id, fe.fixture_id, fe.event_name, fe.subevent_id, fe.pname,
       fe.game_minute, fe.game_second, fe.videotimestamp, fe.game_moment,
       fe.xper, fe.yper,
       f.team1_name, f.team2_name, f.game_date, bv.platform
FROM fixture_events fe
JOIN fixtures f ON f.id = fe.fixture_id
JOIN best_v bv ON bv.fixture_id = fe.fixture_id
WHERE {' AND '.join(where)}
  AND fe.videotimestamp > 0
ORDER BY
  CASE bv.platform WHEN 'youtube' THEN 1 WHEN 'drive' THEN 2 WHEN 'veo' THEN 3 ELSE 99 END,
  fe.fixture_id DESC, fe.videotimestamp
LIMIT 24
""".strip()
    return sql, intent

def ask_claude(query: str):
    try:
        import anthropic
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    schema = """ONE table to query: `fixture_events` (one row per rugby match event).
Cols: fixture_id, team_id (764=EPERNAY), player_id, pname, event_name (Pass/Carries/Tackle/Tackles/Ruck/Kick for territory/Penalties conceded/Penalty Gain/Missed tackle/Missed tackles/Score/Knock on/Lineout throw/Lineouts/Turn overs/Linebreak/Maul/Scrum/Forward pass), subevent_id (TEXT codes; for Score: 66=Try, 60=Successful Conversion, 44=Successful Penalty, 42=Missed Conversion, 61=Missed Penalty, 200=Penalty Try), game_minute, game_second, game_moment (firsthalf/secondhalf), quarter, jersey_no, xper, yper (pitch 0-100), videotimestamp.
You MUST wrap the query with a CTE that limits to fixtures with playable video, sorted with YouTube first:
WITH best_v AS (SELECT DISTINCT ON (fixture_id) fixture_id, platform FROM fixture_videos ORDER BY fixture_id, CASE platform WHEN 'youtube' THEN 1 WHEN 'drive' THEN 2 WHEN 'veo' THEN 3 ELSE 99 END)
Then SELECT fe.id, fe.fixture_id, fe.event_name, fe.subevent_id, fe.pname, fe.game_minute, fe.game_second, fe.videotimestamp, fe.game_moment, fe.xper, fe.yper, f.team1_name, f.team2_name, f.game_date, bv.platform
FROM fixture_events fe JOIN fixtures f ON f.id=fe.fixture_id JOIN best_v bv ON bv.fixture_id=fe.fixture_id
WHERE <user filters> AND fe.videotimestamp > 0
ORDER BY CASE bv.platform WHEN 'youtube' THEN 1 WHEN 'drive' THEN 2 WHEN 'veo' THEN 3 ELSE 99 END, fe.fixture_id DESC, fe.videotimestamp
LIMIT 24.
Default team_id=764 (EPERNAY) unless user mentions opponent. Return ONLY the SQL, no markdown."""
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        system=schema,
        messages=[{"role":"user","content":f"Query (French OK): {query}\nSQL only."}],
    )
    sql = msg.content[0].text.strip()
    if sql.startswith("```"):
        sql = sql.split("```")[1]
        if sql.startswith("sql"): sql = sql[3:]
    return sql.strip().rstrip(";")

def best_video(cur, fixture_id):
    cur.execute("""
        SELECT platform, url, start_offset_sec, drive_file_id
        FROM fixture_videos WHERE fixture_id=%s
        ORDER BY CASE platform WHEN 'youtube' THEN 1 WHEN 'drive' THEN 2 WHEN 'veo' THEN 3 ELSE 99 END
        LIMIT 1
    """, (fixture_id,))
    return cur.fetchone()

PRE_ROLL = 3  # seconds to back off so the action is anticipated

def build_url(v, vts):
    if not v: return None, None
    t_raw = int((vts or 0) + (v.get("start_offset_sec") or 0))
    t = max(0, t_raw - PRE_ROLL)
    p = v["platform"]; url = v["url"]
    if p == "youtube":
        clean = re.sub(r"[?&]t=\d+s?", "", url)
        sep = "&" if "?" in clean else "?"
        return f"{clean}{sep}t={t}s", p
    if p == "drive" and v.get("drive_file_id"):
        return f"https://drive.google.com/file/d/{v['drive_file_id']}/preview", p
    if p == "veo":
        return f"{url.rstrip('/')}/?time={t}", p
    return url, p

@app.get("/api/clips")
def clips(q: str = Query(..., min_length=1)):
    use_claude = bool(os.environ.get("ANTHROPIC_API_KEY"))
    sql = None; intent = "événements"
    if use_claude:
        try: sql = ask_claude(q)
        except Exception as e: sql = None
    if not sql:
        sql, intent = fallback_sql(q)
    conn = psycopg2.connect(DB)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(sql)
    rows = cur.fetchall()
    cur2 = conn.cursor(cursor_factory=RealDictCursor)
    video_cache = {}
    out = []
    for r in rows:
        fid = r["fixture_id"]
        if fid not in video_cache: video_cache[fid] = best_video(cur2, fid)
        url, platform = build_url(video_cache[fid], r["videotimestamp"])
        ts = int(r["videotimestamp"] or 0)
        ts_start = max(0, ts - PRE_ROLL)  # where to start playback (3s before)
        out.append({
            "id": r["id"],
            "fixture_id": fid,
            "match": f"{r['team1_name']} vs {r['team2_name']}",
            "date": str(r["game_date"]),
            "event": r["event_name"],
            "subevent_id": r["subevent_id"],
            "player": (r["pname"] or "").strip() or None,
            "game_minute": r["game_minute"],
            "game_second": r["game_second"],
            "game_moment": r["game_moment"],
            "video_seconds": ts,
            "video_start_sec": ts_start,
            "video_time": f"{ts // 60:02d}:{ts % 60:02d}",
            "clip_url": url,
            "platform": platform,
            "xper": float(r["xper"]) if r["xper"] is not None else None,
            "yper": float(r["yper"]) if r["yper"] is not None else None,
        })
    cur.close(); cur2.close(); conn.close()
    return JSONResponse({
        "query": q, "intent": intent,
        "engine": "claude" if (use_claude and sql) else "fallback",
        "count": len(out),
        "clips": out
    })

@app.get("/api/stats")
def stats(team_id: int = 764):
    """Season stats aggregates."""
    conn = psycopg2.connect(DB)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    out = {}

    # KPIs
    cur.execute("""
        WITH ours AS (
          SELECT id, game_date, team1_id, team2_id, team1_name, team2_name,
                 home_points, away_points,
                 CASE WHEN team1_id=%s THEN home_points ELSE away_points END AS pts_for,
                 CASE WHEN team1_id=%s THEN away_points ELSE home_points END AS pts_against
          FROM fixtures
          WHERE (team1_id=%s OR team2_id=%s) AND home_points IS NOT NULL
        )
        SELECT count(*) AS matches,
               count(*) FILTER (WHERE pts_for > pts_against) AS wins,
               count(*) FILTER (WHERE pts_for = pts_against) AS draws,
               count(*) FILTER (WHERE pts_for < pts_against) AS losses,
               sum(pts_for)::int AS pts_for,
               sum(pts_against)::int AS pts_against,
               round(avg(pts_for)::numeric, 1) AS avg_pts_for,
               round(avg(pts_against)::numeric, 1) AS avg_pts_against
        FROM ours
    """, (team_id, team_id, team_id, team_id))
    out["kpis"] = cur.fetchone()

    # Event distribution
    cur.execute("""
        SELECT event_name, count(*) AS n
        FROM fixture_events
        WHERE team_id = %s
        GROUP BY event_name
        ORDER BY n DESC
    """, (team_id,))
    out["event_distribution"] = cur.fetchall()

    # Success rates (computed from event_subevents)
    cur.execute("""
        SELECT
          (SELECT sum(total) FROM event_subevents WHERE team_id=%s AND event_name='Lineout' AND sub_event_name='Won') AS lineout_won,
          (SELECT sum(total) FROM event_subevents WHERE team_id=%s AND event_name='Lineout' AND sub_event_name='Stolen') AS lineout_stolen,
          (SELECT sum(total) FROM event_subevents WHERE team_id=%s AND event_name='Tackle' AND sub_event_name='Positive') AS tackle_pos,
          (SELECT sum(total) FROM event_subevents WHERE team_id=%s AND event_name='Tackle' AND sub_event_name='Negative') AS tackle_neg,
          (SELECT sum(total) FROM event_subevents WHERE team_id=%s AND event_name='Score' AND sub_event_name='Try') AS tries,
          (SELECT sum(total) FROM event_subevents WHERE team_id=%s AND event_name='Score' AND sub_event_name='Successful Conversion') AS conv_ok,
          (SELECT sum(total) FROM event_subevents WHERE team_id=%s AND event_name='Score' AND sub_event_name='Missed Conversion') AS conv_miss,
          (SELECT sum(total) FROM event_subevents WHERE team_id=%s AND event_name='Score' AND sub_event_name='Successful Penalty') AS pen_ok,
          (SELECT sum(total) FROM event_subevents WHERE team_id=%s AND event_name='Score' AND sub_event_name='Missed Penalty') AS pen_miss
    """, (team_id,) * 9)
    out["rates"] = cur.fetchone()

    # Match-by-match
    cur.execute("""
        SELECT f.id, f.game_date, f.team1_name, f.team2_name,
               CASE WHEN f.team1_id=%s THEN 'home' ELSE 'away' END AS venue,
               CASE WHEN f.team1_id=%s THEN f.team2_name ELSE f.team1_name END AS opponent,
               CASE WHEN f.team1_id=%s THEN f.home_points ELSE f.away_points END AS pts_for,
               CASE WHEN f.team1_id=%s THEN f.away_points ELSE f.home_points END AS pts_against,
               (SELECT count(*) FROM fixture_events fe WHERE fe.fixture_id=f.id AND fe.team_id=%s AND fe.event_name='Score' AND fe.subevent_id IN ('66','200')) AS tries,
               (SELECT count(*) FROM fixture_events fe WHERE fe.fixture_id=f.id AND fe.team_id=%s AND fe.event_name IN ('Tackle','Tackles')) AS tackles,
               (SELECT count(*) FROM fixture_events fe WHERE fe.fixture_id=f.id AND fe.team_id=%s AND fe.event_name='Carries') AS carries,
               (SELECT count(*) FROM fixture_events fe WHERE fe.fixture_id=f.id AND fe.team_id=%s AND fe.event_name='Penalties conceded') AS pen_conc,
               (SELECT count(*) FROM fixture_events fe WHERE fe.fixture_id=f.id AND fe.team_id=%s AND fe.event_name='Turn overs') AS turnovers,
               (SELECT platform FROM fixture_videos fv WHERE fv.fixture_id=f.id
                ORDER BY CASE platform WHEN 'youtube' THEN 1 WHEN 'drive' THEN 2 WHEN 'veo' THEN 3 ELSE 99 END LIMIT 1) AS video
        FROM fixtures f
        WHERE (f.team1_id=%s OR f.team2_id=%s) AND f.home_points IS NOT NULL
        ORDER BY f.game_date DESC
    """, (team_id,) * 9 + (team_id, team_id))
    out["matches"] = cur.fetchall()

    # Leaderboards
    metrics = {
        "tries":     ("Score", "fe.subevent_id IN ('66','200')"),
        "tackles":   ("Tackle,Tackles", None),
        "carries":   ("Carries", None),
        "turnovers": ("Turn overs", None),
        "kicks":     ("Kick for territory", None),
        "knockons":  ("Knock on", None),
    }
    out["leaderboards"] = {}
    for key, (names, extra) in metrics.items():
        nlist = "(" + ",".join(f"'{n}'" for n in names.split(",")) + ")"
        where = f"fe.team_id=%s AND fe.event_name IN {nlist}"
        if extra: where += " AND " + extra
        cur.execute(f"""
            SELECT p.id, p.first_name, p.sir_name, p.current_position,
                   tp.current_jersey_no AS jersey, count(*) AS n
            FROM fixture_events fe
            JOIN players p ON p.id=fe.player_id
            LEFT JOIN team_players tp ON tp.player_id=p.id AND tp.team_id=%s
            WHERE {where}
            GROUP BY p.id, p.first_name, p.sir_name, p.current_position, tp.current_jersey_no
            HAVING count(*) >= 1
            ORDER BY n DESC LIMIT 12
        """, (team_id, team_id))
        out["leaderboards"][key] = cur.fetchall()

    cur.close(); conn.close()
    def cast(o):
        if isinstance(o, dict): return {k: cast(v) for k, v in o.items()}
        if isinstance(o, list): return [cast(x) for x in o]
        if hasattr(o, "isoformat"): return o.isoformat()
        try:
            from decimal import Decimal
            if isinstance(o, Decimal): return float(o)
        except: pass
        return o
    return cast(out)

@app.get("/api/dashboard")
def dashboard():
    """Aggregated home dashboard data for EPERNAY."""
    conn = psycopg2.connect(DB)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    out = {}

    # Last match
    cur.execute("""
        SELECT f.id, f.team1_name, f.team2_name, f.team1_id, f.team2_id, f.game_date,
               f.home_points, f.away_points, f.venue,
               (SELECT platform FROM fixture_videos fv WHERE fv.fixture_id = f.id
                ORDER BY CASE platform WHEN 'youtube' THEN 1 WHEN 'drive' THEN 2 WHEN 'veo' THEN 3 ELSE 99 END LIMIT 1) AS video_platform
        FROM fixtures f
        WHERE (f.team1_id = 764 OR f.team2_id = 764) AND f.home_points IS NOT NULL
        ORDER BY f.game_date DESC NULLS LAST LIMIT 1
    """)
    out["last_match"] = cur.fetchone()

    # Standings excerpt (FFR if available)
    cur.execute("""
        SELECT position, team_name, pts, j, g, n, p, is_us
        FROM standings_ffr ORDER BY pts DESC NULLS LAST
    """)
    out["standings"] = cur.fetchall()

    # Top scorers (tries)
    cur.execute("""
        SELECT p.id, p.first_name, p.sir_name, p.current_position,
               tp.current_jersey_no AS jersey,
               count(*) AS tries
        FROM fixture_events fe
        JOIN players p ON p.id = fe.player_id
        LEFT JOIN team_players tp ON tp.player_id = p.id AND tp.team_id = 764
        WHERE fe.team_id = 764 AND fe.event_name = 'Score' AND fe.subevent_id IN ('66','200')
        GROUP BY p.id, p.first_name, p.sir_name, p.current_position, tp.current_jersey_no
        ORDER BY tries DESC LIMIT 5
    """)
    out["top_scorers"] = cur.fetchall()

    # Top tacklers
    cur.execute("""
        SELECT p.id, p.first_name, p.sir_name, p.current_position,
               tp.current_jersey_no AS jersey,
               count(*) AS tackles
        FROM fixture_events fe
        JOIN players p ON p.id = fe.player_id
        LEFT JOIN team_players tp ON tp.player_id = p.id AND tp.team_id = 764
        WHERE fe.team_id = 764 AND fe.event_name IN ('Tackle','Tackles')
        GROUP BY p.id, p.first_name, p.sir_name, p.current_position, tp.current_jersey_no
        ORDER BY tackles DESC LIMIT 5
    """)
    out["top_tacklers"] = cur.fetchall()

    # Season totals
    cur.execute("""
        SELECT
          (SELECT count(*) FROM fixtures WHERE (team1_id=764 OR team2_id=764) AND home_points IS NOT NULL) AS matches_played,
          (SELECT count(*) FROM fixture_events WHERE team_id=764 AND event_name='Score' AND subevent_id IN ('66','200')) AS total_tries,
          (SELECT sum(CASE WHEN team1_id=764 THEN home_points ELSE away_points END)
             FROM fixtures WHERE (team1_id=764 OR team2_id=764) AND home_points IS NOT NULL) AS pts_scored,
          (SELECT count(*) FROM team_players tp WHERE tp.team_id=764
             AND (SELECT count(*) FROM lineups l WHERE l.player_id = tp.player_id) >= 1) AS active_players
    """)
    out["totals"] = cur.fetchone()

    cur.close(); conn.close()
    # Cast Decimal to float
    def cast(o):
        if isinstance(o, dict): return {k: cast(v) for k, v in o.items()}
        if isinstance(o, list): return [cast(x) for x in o]
        if hasattr(o, "isoformat"): return o.isoformat()
        try:
            from decimal import Decimal
            if isinstance(o, Decimal): return float(o)
        except: pass
        return o
    return cast(out)

@app.get("/api/players")
def players(team_id: int = 764):
    conn = psycopg2.connect(DB)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT p.id, p.first_name, p.sir_name, p.current_position, p.dob,
               tp.current_jersey_no AS jersey,
               (SELECT count(*) FROM lineups l WHERE l.player_id = p.id) AS matches,
               (SELECT count(*) FROM lineups l WHERE l.player_id = p.id AND l.player_type='first11') AS starts,
               (SELECT count(*) FROM fixture_events fe WHERE fe.player_id = p.id) AS events,
               (SELECT count(*) FROM fixture_events fe WHERE fe.player_id = p.id AND fe.event_name='Score' AND fe.subevent_id IN ('66','200')) AS tries,
               (SELECT count(*) FROM fixture_events fe WHERE fe.player_id = p.id AND fe.event_name IN ('Tackle','Tackles')) AS tackles,
               pp.nickname, pp.photo_url, pp.status
        FROM team_players tp
        JOIN players p ON p.id = tp.player_id
        LEFT JOIN player_profile pp ON pp.player_id = p.id
        WHERE tp.team_id = %s
        ORDER BY events DESC, starts DESC, matches DESC
    """, (team_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    out = []
    for r in rows:
        fn = (r["first_name"] or "").strip()
        sn = (r["sir_name"] or "").strip()
        out.append({
            "id": r["id"],
            "name": f"{fn} {sn}".strip(),
            "first_name": fn, "sir_name": sn,
            "position": r["current_position"],
            "jersey": r["jersey"] or 0,
            "matches": r["matches"], "starts": r["starts"], "events": r["events"],
            "tries": r["tries"], "tackles": r["tackles"],
            "nickname": r["nickname"], "photo_url": r["photo_url"],
            "status": r["status"] or "Apte",
        })
    return out

@app.get("/api/standings")
def standings():
    conn = psycopg2.connect(DB)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT poule_id, position, team_name, team_emblem, pts, j, g, n, p, ga, bo, bd,
               marques_pour, marques_contre, is_us, synced_at
        FROM standings_ffr ORDER BY pts DESC NULLS LAST
    """)
    ffr_rows = cur.fetchall()
    cur.execute("""
        SELECT competition_id, season_id, team_id, team_name,
               p AS j, w AS g, d AS n, l AS p_lost, gf, ga, gd, pts,
               synced_at
        FROM standings ORDER BY competition_id, season_id, pts DESC
    """)
    tisini_rows = cur.fetchall()
    cur.close(); conn.close()
    def cast(rows):
        out = []
        for r in rows:
            d = dict(r)
            for k, v in list(d.items()):
                if hasattr(v, "isoformat"): d[k] = v.isoformat()
                elif isinstance(v, (int, str, bool, type(None))): pass
                else: d[k] = float(v) if v is not None else None
            out.append(d)
        return out
    return {"ffr": cast(ffr_rows), "tisini": cast(tisini_rows)}

@app.post("/api/standings/sync")
def standings_sync():
    import subprocess
    p = subprocess.run(["python3", os.path.join(os.path.dirname(__file__), "ffr_sync.py")],
                       capture_output=True, text=True, timeout=30)
    return {"ok": p.returncode == 0, "stdout": p.stdout, "stderr": p.stderr}

@app.get("/api/fixtures")
def fixtures():
    conn = psycopg2.connect(DB)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT f.id, f.team1_name||' vs '||f.team2_name AS match, f.game_date,
               f.home_points, f.away_points,
               (SELECT platform FROM fixture_videos fv WHERE fv.fixture_id=f.id
                ORDER BY CASE platform WHEN 'youtube' THEN 1 WHEN 'drive' THEN 2 WHEN 'veo' THEN 3 ELSE 99 END LIMIT 1) AS video_platform
        FROM fixtures f WHERE f.team1_id=764 OR f.team2_id=764
        ORDER BY f.game_date DESC
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()
    return [{"id": r["id"], "match": r["match"], "date": str(r["game_date"]),
             "score": f"{r['home_points']}-{r['away_points']}" if r["home_points"] is not None else None,
             "video": r["video_platform"]} for r in rows]

app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8770)), reload=False)
