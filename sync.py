#!/usr/bin/env python3
"""Sync Tisini API -> Neon. Idempotent (UPSERT)."""
import os, sys, json, urllib.request, urllib.error
import psycopg2
from psycopg2.extras import execute_values

API   = "https://api.tisini.africa"
KEY   = os.environ.get("TISINI_KEY", "DXixJNmEYFOLpjj10ZtZzfph4mvxarC1NUyn6F0LhHE")
DB    = os.environ.get("NEON_URL",   "postgresql://neondb_owner:npg_gGLbRFKdme28@ep-misty-credit-aqm0oae5-pooler.c-8.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")
OUR_CLUB_NAME = "EPERNAY"

# Rugby scoring: Try=5, Successful Conversion=2, Successful Penalty=3, Drop Goal=3
TRY_PTS, CONV_PTS, PEN_PTS, DROP_PTS = 5, 2, 3, 3

def get(path):
    req = urllib.request.Request(API + path, headers={"x-api-key": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def safe_int(v):
    try: return int(v) if v not in (None, "") else None
    except: return None

def safe_num(v):
    try: return float(v) if v not in (None, "") else None
    except: return None

def date_or_none(v):
    if not v: return None
    return v.split("T")[0]

def compute_points(stats_side):
    """stats_side = list of {event_name, total, sub_events:[{sub_event_name,total}]} for one team."""
    pts = 0
    for e in stats_side or []:
        if e.get("event_name") != "Score":
            continue
        for se in e.get("sub_events") or []:
            n, t = se.get("sub_event_name",""), se.get("total",0) or 0
            if n == "Try":                    pts += t * TRY_PTS
            elif n == "Successful Conversion": pts += t * CONV_PTS
            elif n == "Successful Penalty":    pts += t * PEN_PTS
            elif n in ("Drop", "Drop Goal"):   pts += t * DROP_PTS
    return pts

def main():
    conn = psycopg2.connect(DB)
    conn.autocommit = False
    cur = conn.cursor()

    # ─── COMPETITIONS ───
    comps = get("/competitions")
    execute_values(cur,
        "INSERT INTO competitions(id,name,status) VALUES %s "
        "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, status=EXCLUDED.status, synced_at=NOW()",
        [(c["id"], c["name"], c.get("status")) for c in comps])
    print(f"✓ {len(comps)} competitions")

    season_ids = []
    for c in comps:
        seasons = get(f"/competitions/{c['id']}/seasons")
        execute_values(cur,
            "INSERT INTO seasons(id,competition_id,name,date_from,date_to,status) VALUES %s "
            "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,date_from=EXCLUDED.date_from,date_to=EXCLUDED.date_to,status=EXCLUDED.status,synced_at=NOW()",
            [(s["id"], s["tournament"], s["name"], s["date_from"], s["date_to"], s.get("status")) for s in seasons])
        print(f"✓ comp {c['id']}: {len(seasons)} seasons")

        for s in seasons:
            season_ids.append((c["id"], s["id"]))

            # ─── TEAMS ───
            teams = get(f"/competitions/{c['id']}/seasons/{s['id']}/teams")
            execute_values(cur,
                "INSERT INTO teams(id,name,logo) VALUES %s "
                "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, logo=EXCLUDED.logo, synced_at=NOW()",
                [(t["team_id"], t["team_name"], t.get("team_logo")) for t in teams])
            cur.execute("UPDATE teams SET is_our_club=(name=%s)", (OUR_CLUB_NAME,))
            print(f"  ✓ {len(teams)} teams (our club flagged where name={OUR_CLUB_NAME!r})")

            # ─── PLAYERS PER TEAM ───
            total_p = 0
            for t in teams:
                roster = get(f"/competitions/{c['id']}/seasons/{s['id']}/teams/{t['team_id']}/players")
                if not roster: continue
                # dedupe by player_id (rosters can list same player twice)
                p_seen = {r["player_id"]: r for r in roster}
                execute_values(cur,
                    "INSERT INTO players(id,first_name,sir_name,other_name,dob,current_position,nationality,passportphoto) VALUES %s "
                    "ON CONFLICT (id) DO UPDATE SET first_name=EXCLUDED.first_name,sir_name=EXCLUDED.sir_name,other_name=EXCLUDED.other_name,dob=EXCLUDED.dob,current_position=EXCLUDED.current_position,nationality=EXCLUDED.nationality,passportphoto=EXCLUDED.passportphoto,synced_at=NOW()",
                    [(r["player_id"], r["player"]["first_name"], r["player"]["sir_name"], r["player"].get("other_name"),
                      date_or_none(r["player"].get("dob")), r["player"].get("current_position"),
                      r["player"].get("nationality"), r["player"].get("passportphoto")) for r in p_seen.values()])
                # team_players — dedupe by team_player id
                tp_seen = {r["id"]: r for r in roster}
                execute_values(cur,
                    "INSERT INTO team_players(id,team_id,player_id,current_jersey_no) VALUES %s "
                    "ON CONFLICT (id) DO UPDATE SET team_id=EXCLUDED.team_id,player_id=EXCLUDED.player_id,current_jersey_no=EXCLUDED.current_jersey_no,synced_at=NOW()",
                    [(r["id"], r["team_id"], r["player_id"], safe_int(r.get("current_jersey_no"))) for r in tp_seen.values()])
                total_p += len(tp_seen)
            print(f"  ✓ {total_p} team_players synced")

            # ─── FIXTURES ───
            fixtures = get(f"/competitions/{c['id']}/seasons/{s['id']}/fixtures")
            for f in fixtures:
                cur.execute("""
                    INSERT INTO fixtures(id,competition_id,season_id,team1_id,team2_id,team1_name,team2_name,
                                         home_score,away_score,matchday,game_status,game_moment,game_date,venue)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (id) DO UPDATE SET
                      competition_id=EXCLUDED.competition_id, season_id=EXCLUDED.season_id,
                      team1_id=EXCLUDED.team1_id, team2_id=EXCLUDED.team2_id,
                      team1_name=EXCLUDED.team1_name, team2_name=EXCLUDED.team2_name,
                      home_score=EXCLUDED.home_score, away_score=EXCLUDED.away_score,
                      matchday=EXCLUDED.matchday, game_status=EXCLUDED.game_status,
                      game_moment=EXCLUDED.game_moment, game_date=EXCLUDED.game_date,
                      venue=EXCLUDED.venue, synced_at=NOW()
                """, (f["id"], int(f["league"]), int(f["series"]), f["team1_id"], f["team2_id"],
                      f["team1_name"], f["team2_name"], safe_int(f["home_score"]), safe_int(f["away_score"]),
                      f.get("matchday"), f.get("game_status"), f.get("game_moment"),
                      f.get("game_date"), f.get("venue")))
            print(f"  ✓ {len(fixtures)} fixtures")

            # ─── FIXTURE DETAILS (stats per side + points calc) ───
            for f in fixtures:
                fid = f["id"]
                try:
                    det = get(f"/competitions/{c['id']}/seasons/{s['id']}/fixtures/{fid}/details")
                except Exception as e:
                    print(f"    ⚠ details {fid}: {e}"); continue
                stats = det.get("stats", {})
                home_pts = compute_points(stats.get("home"))
                away_pts = compute_points(stats.get("away"))
                cur.execute("UPDATE fixtures SET home_points=%s, away_points=%s, fixture_type=%s WHERE id=%s",
                            (home_pts, away_pts, det.get("fixture",{}).get("fixture_type"), fid))

                # event_subevents (aggregated)
                cur.execute("DELETE FROM event_subevents WHERE fixture_id=%s", (fid,))
                rows = []
                for side in ("home","away"):
                    side_team_id = f["team1_id"] if side == "home" else f["team2_id"]
                    for e in stats.get(side, []) or []:
                        if not e.get("sub_events"):
                            rows.append((fid, side_team_id, e["event_id"], e["event_name"], "_total", "_total", e["total"], side))
                        for se in e.get("sub_events", []):
                            rows.append((fid, side_team_id, e["event_id"], e["event_name"],
                                         str(se["sub_event_id"]), se["sub_event_name"], se["total"], side))
                if rows:
                    execute_values(cur,
                        "INSERT INTO event_subevents(fixture_id,team_id,event_id,event_name,sub_event_id,sub_event_name,total,side) VALUES %s",
                        rows)

                # ─── LINEUPS ───
                try:
                    lu = get(f"/competitions/{c['id']}/seasons/{s['id']}/fixtures/{fid}/lineups")
                except Exception as e:
                    print(f"    ⚠ lineups {fid}: {e}"); continue
                lu_rows = []
                for side in ("home","away"):
                    for p in lu.get(side, []) or []:
                        lu_rows.append((p["id"], p["fixture_id"], safe_int(p.get("teamid")),
                                        p.get("team_player_id"), p.get("player"),
                                        safe_int(p.get("jersey_no")), p.get("player_type"),
                                        safe_int(p.get("lineupposition")),
                                        safe_int(p.get("red")), safe_int(p.get("gk"))))
                if lu_rows:
                    # Skip lineups for players not yet in players table
                    cur.execute("SELECT id FROM players")
                    known_players = {row[0] for row in cur.fetchall()}
                    lu_rows = [r for r in lu_rows if r[4] in known_players]
                    execute_values(cur,
                        "INSERT INTO lineups(id,fixture_id,team_id,team_player_id,player_id,jersey_no,player_type,lineupposition,red,gk) VALUES %s "
                        "ON CONFLICT (id) DO UPDATE SET fixture_id=EXCLUDED.fixture_id,team_id=EXCLUDED.team_id,team_player_id=EXCLUDED.team_player_id,player_id=EXCLUDED.player_id,jersey_no=EXCLUDED.jersey_no,player_type=EXCLUDED.player_type,lineupposition=EXCLUDED.lineupposition,red=EXCLUDED.red,gk=EXCLUDED.gk,synced_at=NOW()",
                        lu_rows)

                # ─── RAW EVENTS (LE CŒUR) ───
                try:
                    re_data = get(f"/competitions/{c['id']}/seasons/{s['id']}/fixtures/{fid}/raw-events")
                except Exception as e:
                    print(f"    ⚠ raw {fid}: {e}"); continue

                # Update videourl on fixture
                vurl = (re_data.get("fixture") or {}).get("videourl")
                if vurl:
                    cur.execute("UPDATE fixtures SET videourl=%s WHERE id=%s", (vurl, fid))

                events = re_data.get("events", []) or []
                # filter to known players, but keep events with no player too
                cur.execute("SELECT id FROM players")
                known = {row[0] for row in cur.fetchall()}
                ev_rows = []
                for e in events:
                    pid = e.get("player_id")
                    if pid and pid not in known:
                        pid = None  # detach to avoid FK
                    ev_rows.append((
                        e["id"], fid, safe_int(e.get("team")), pid, safe_int(e.get("teamplayer_id")),
                        e.get("event_id"), e.get("event_name"),
                        e.get("subevent_id"), None,        # subevent_name not in raw events payload
                        e.get("subsubevent_id"),
                        safe_int(e.get("game_minute")), safe_int(e.get("game_second")),
                        e.get("game_moment"), e.get("quarter"),
                        e.get("jersey_no"), e.get("pname"),
                        safe_int(e.get("zone_id")), safe_num(e.get("xper")), safe_num(e.get("yper")),
                        safe_num(e.get("videotimestamp")),
                        e.get("noruck"), e.get("nolineout"), e.get("metergain"),
                        e.get("kickfrom"), e.get("kickland"), e.get("defender"),
                        e.get("subplayer_id"), e.get("time")
                    ))
                if ev_rows:
                    execute_values(cur,
                        """INSERT INTO fixture_events(id,fixture_id,team_id,player_id,teamplayer_id,event_id,event_name,
                              subevent_id,subevent_name,subsubevent_id,game_minute,game_second,game_moment,quarter,
                              jersey_no,pname,zone_id,xper,yper,videotimestamp,noruck,nolineout,metergain,
                              kickfrom,kickland,defender,subplayer_id,event_time) VALUES %s
                           ON CONFLICT (id) DO NOTHING""",
                        ev_rows)
                print(f"    ✓ fixture {fid}: {len(events)} events, score {home_pts}-{away_pts}")

            # ─── STANDINGS ───
            try:
                st = get(f"/competitions/{c['id']}/seasons/{s['id']}/standings")
                cur.execute("DELETE FROM standings WHERE competition_id=%s AND season_id=%s", (c["id"], s["id"]))
                # dedupe by team_id (API can return same team twice)
                st_seen = {}
                for r in st.get("standings", []):
                    st_seen[r["id"]] = r
                rows = [(c["id"], s["id"], r["id"], r["team_name"],
                         r["P"], r["W"], r["D"], r["L"], r["GF"], r["GA"], r["GD"], r["Pts"]) for r in st_seen.values()]
                if rows:
                    execute_values(cur,
                        "INSERT INTO standings(competition_id,season_id,team_id,team_name,P,W,D,L,GF,GA,GD,Pts) VALUES %s",
                        rows)
                conn.commit()
                print(f"  ✓ {len(rows)} standings rows")
            except Exception as e:
                print(f"  ⚠ standings: {e}")

    conn.commit()
    cur.close()
    conn.close()
    print("\n✅ Sync complete.")

if __name__ == "__main__":
    main()
