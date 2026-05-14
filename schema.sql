-- ═══════════════════════════════════════════════════════════════
-- COUCHE TISINI (miroir API, repopulée par sync, idempotent)
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS competitions (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  status       INTEGER,
  synced_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS seasons (
  id             INTEGER PRIMARY KEY,
  competition_id INTEGER REFERENCES competitions(id),
  name           TEXT,
  date_from      DATE,
  date_to        DATE,
  status         INTEGER,
  synced_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS teams (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  logo          TEXT,
  is_our_club   BOOLEAN DEFAULT FALSE,
  synced_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS players (
  id               INTEGER PRIMARY KEY,
  first_name       TEXT,
  sir_name         TEXT,
  other_name       TEXT,
  dob              DATE,
  current_position TEXT,
  nationality      TEXT,
  passportphoto    TEXT,
  synced_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS team_players (
  id                  INTEGER PRIMARY KEY,
  team_id             INTEGER REFERENCES teams(id),
  player_id           INTEGER REFERENCES players(id),
  current_jersey_no   INTEGER,
  synced_at           TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_team_players_team ON team_players(team_id);

CREATE TABLE IF NOT EXISTS fixtures (
  id              INTEGER PRIMARY KEY,
  competition_id  INTEGER,
  season_id       INTEGER,
  team1_id        INTEGER REFERENCES teams(id),
  team2_id        INTEGER REFERENCES teams(id),
  team1_name      TEXT,
  team2_name      TEXT,
  home_score      INTEGER,           -- raw from API (souvent 0)
  away_score      INTEGER,
  home_points     INTEGER,           -- calculé depuis events Score
  away_points     INTEGER,
  matchday        TEXT,
  game_status     TEXT,
  game_moment     TEXT,
  game_date       DATE,
  fixture_type    TEXT,
  venue           TEXT,
  videourl        TEXT,
  synced_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fixtures_season ON fixtures(season_id);
CREATE INDEX IF NOT EXISTS idx_fixtures_date ON fixtures(game_date DESC);

CREATE TABLE IF NOT EXISTS lineups (
  id              INTEGER PRIMARY KEY,
  fixture_id      INTEGER REFERENCES fixtures(id) ON DELETE CASCADE,
  team_id         INTEGER REFERENCES teams(id),
  team_player_id  INTEGER,
  player_id       INTEGER REFERENCES players(id),
  jersey_no       INTEGER,
  player_type     TEXT,              -- 'first11' | 'sub'
  lineupposition  INTEGER,
  red             INTEGER,
  gk              INTEGER,
  synced_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lineups_fixture ON lineups(fixture_id);
CREATE INDEX IF NOT EXISTS idx_lineups_player ON lineups(player_id);

-- LA TABLE CLÉ POUR LE LLM VIDEO SEARCH
CREATE TABLE IF NOT EXISTS fixture_events (
  id              BIGINT PRIMARY KEY,        -- event id de l'API
  fixture_id      INTEGER REFERENCES fixtures(id) ON DELETE CASCADE,
  team_id         INTEGER REFERENCES teams(id),
  player_id       INTEGER REFERENCES players(id),
  teamplayer_id   INTEGER,
  event_id        INTEGER,
  event_name      TEXT,
  subevent_id     TEXT,
  subevent_name   TEXT,
  subsubevent_id  TEXT,
  game_minute     INTEGER,
  game_second     INTEGER,
  game_moment     TEXT,
  quarter         TEXT,
  jersey_no       TEXT,
  pname           TEXT,
  zone_id         INTEGER,
  xper            NUMERIC,
  yper            NUMERIC,
  videotimestamp  NUMERIC,
  noruck          TEXT,
  nolineout       TEXT,
  metergain       TEXT,
  kickfrom        TEXT,
  kickland        TEXT,
  defender        TEXT,
  subplayer_id    TEXT,
  event_time      TIMESTAMPTZ,
  synced_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_events_fixture ON fixture_events(fixture_id);
CREATE INDEX IF NOT EXISTS idx_events_player  ON fixture_events(player_id);
CREATE INDEX IF NOT EXISTS idx_events_name    ON fixture_events(event_name);
CREATE INDEX IF NOT EXISTS idx_events_team    ON fixture_events(team_id);

CREATE TABLE IF NOT EXISTS event_subevents (
  fixture_id      INTEGER REFERENCES fixtures(id) ON DELETE CASCADE,
  team_id         INTEGER REFERENCES teams(id),
  event_id        INTEGER,
  event_name      TEXT,
  sub_event_id    TEXT,
  sub_event_name  TEXT,
  total           INTEGER,
  side            TEXT,             -- 'home' | 'away'
  PRIMARY KEY (fixture_id, team_id, event_id, sub_event_id)
);

CREATE TABLE IF NOT EXISTS standings (
  competition_id  INTEGER,
  season_id       INTEGER,
  team_id         INTEGER REFERENCES teams(id),
  team_name       TEXT,
  P INTEGER, W INTEGER, D INTEGER, L INTEGER,
  GF INTEGER, GA INTEGER, GD INTEGER, Pts INTEGER,
  synced_at       TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (competition_id, season_id, team_id)
);

-- ═══════════════════════════════════════════════════════════════
-- COUCHE BACK-OFFICE (saisie staff, jamais écrasée par sync)
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS player_profile (
  player_id      INTEGER PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
  nickname       TEXT,
  photo_url      TEXT,
  license_ffr    TEXT,
  height_cm      INTEGER,
  weight_kg      INTEGER,
  status         TEXT,            -- 'Apte' | 'Blessé' | 'Suspendu' | 'Réserve'
  staff_notes    TEXT,
  updated_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS announcements (
  id           SERIAL PRIMARY KEY,
  title        TEXT NOT NULL,
  body         TEXT,
  audience     TEXT,
  author       TEXT,
  priority     TEXT CHECK (priority IN ('high','mid','low')),
  published_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS training_sessions (
  id           SERIAL PRIMARY KEY,
  date         DATE NOT NULL,
  start_time   TIME,
  type         TEXT,            -- 'Récup' | 'Muscu' | 'Terrain' | 'Analyse' | 'Match'
  label        TEXT,
  location     TEXT,
  audience     TEXT
);

CREATE TABLE IF NOT EXISTS injuries (
  id            SERIAL PRIMARY KEY,
  player_id     INTEGER REFERENCES players(id) ON DELETE CASCADE,
  label         TEXT NOT NULL,
  severity      TEXT CHECK (severity IN ('mineure','moyenne','grave','surveillance')),
  return_eta    TEXT,
  start_date    DATE DEFAULT CURRENT_DATE,
  end_date      DATE
);
