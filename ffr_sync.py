#!/usr/bin/env python3
"""Fetch FFR public standings and update standings table.

Walks api.www.ffr.fr/wp-json/ffr/v1/competition_home, finds the poule where
EPERNAY is, and upserts those teams as a new 'standings_ffr' row set.
"""
import os, sys, json, urllib.request
import psycopg2
from psycopg2.extras import execute_values

DB = os.environ.get("NEON_URL",
    "postgresql://neondb_owner:npg_gGLbRFKdme28@ep-misty-credit-aqm0oae5-pooler.c-8.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require")
URL = "https://api.www.ffr.fr/wp-json/ffr/v1/competition_home"
TEAM_HINT = "pernay"  # search for EPERNAY across all poules

def walk(o, path=""):
    """Yield (path, poule_dict) for every poule that contains our team."""
    if isinstance(o, dict):
        for k, v in o.items():
            yield from walk(v, f"{path}.{k}")
        # Heuristic: if this dict looks like a poule, check teams
        if "Classements" in o and "pouleId" in o:
            for c in o.get("Classements", []) or []:
                eq = c.get("Equipe") or {}
                if isinstance(eq, dict) and TEAM_HINT in (eq.get("nom") or "").lower():
                    yield path, o
                    break
    elif isinstance(o, list):
        for i, it in enumerate(o):
            yield from walk(it, f"{path}[{i}]")

def main():
    print(f"Fetching {URL}…")
    with urllib.request.urlopen(URL, timeout=30) as r:
        data = json.loads(r.read())

    poules = list(walk(data))
    if not poules:
        print("⚠ EPERNAY not found in any poule (probably no current ranking yet)")
        return
    print(f"Found {len(poules)} poule(s) with EPERNAY")
    seen_paths = set()
    rows = []
    for path, poule in poules:
        if path in seen_paths: continue
        seen_paths.add(path)
        # Try to extract competition / phase name from the path or parents
        for c in poule.get("Classements", []):
            eq = c.get("Equipe") or {}
            cl = c.get("Classement") or {}
            pos = c.get("positionIntraPoule")
            rows.append({
                "pouleId": poule.get("pouleId"),
                "position": pos,
                "team_id_ffr": (eq.get("Structure") or {}).get("id"),
                "team_name": eq.get("nom"),
                "team_emblem": (eq.get("Structure") or {}).get("embleme"),
                "pts": cl.get("pointTerrain"),
                "j": cl.get("joues"),
                "g": cl.get("gagnes"),
                "n": cl.get("nuls"),
                "p": cl.get("perdus"),
                "ga": cl.get("goalAverage"),
                "bo": cl.get("bonusOffensif"),
                "bd": cl.get("bonusDefensif"),
                "marquesPour": cl.get("pointsDeMarqueAquis"),
                "marquesContre": cl.get("pointsDeMarqueConcedes"),
                "is_us": TEAM_HINT in (eq.get("nom") or "").lower(),
            })

    print(f"{len(rows)} standings rows extracted:")
    for r in rows:
        marker = " ★" if r["is_us"] else ""
        pos = r["position"] if r["position"] is not None else "?"
        print(f"  {pos}  {r['pts']:>6}pts  {r['j']}j  {r['g']}V {r['n']}N {r['p']}D  {r['team_name']}{marker}")

    # Persist
    conn = psycopg2.connect(DB)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS standings_ffr (
          poule_id      INTEGER,
          position      INTEGER,
          team_id_ffr   TEXT,
          team_name     TEXT,
          team_emblem   TEXT,
          pts NUMERIC, j INTEGER, g INTEGER, n INTEGER, p INTEGER,
          ga NUMERIC, bo INTEGER, bd INTEGER,
          marques_pour INTEGER, marques_contre INTEGER,
          is_us BOOLEAN,
          synced_at TIMESTAMPTZ DEFAULT NOW(),
          PRIMARY KEY (poule_id, team_id_ffr)
        );
    """)
    cur.execute("DELETE FROM standings_ffr")
    execute_values(cur,
        """INSERT INTO standings_ffr(poule_id,position,team_id_ffr,team_name,team_emblem,
              pts,j,g,n,p,ga,bo,bd,marques_pour,marques_contre,is_us) VALUES %s""",
        [(r["pouleId"], r["position"], r["team_id_ffr"], r["team_name"], r["team_emblem"],
          r["pts"], r["j"], r["g"], r["n"], r["p"], r["ga"], r["bo"], r["bd"],
          r["marquesPour"], r["marquesContre"], r["is_us"]) for r in rows])
    conn.commit()
    print("✓ standings_ffr updated")
    cur.close(); conn.close()

if __name__ == "__main__":
    main()
