# EPERNAY Rugby — Front Rugby ML

Dashboard d'analyse vidéo pour Rugby Epernay Champagne (Fédérale 3), avec recherche LLM dans 10 168 événements rugby capturés par Tisini.

## Stack
- **Backend** : FastAPI (Python 3.11)
- **DB** : Neon Postgres (schéma 2 couches Tisini miroir + back-office staff)
- **LLM** : Claude Haiku 4.5 pour conversion langage naturel → SQL
- **Sync** : Scripts idempotents Tisini + API publique FFR

## Écrans
- Tableau de bord (totaux saison, dernier match, top buteurs/plaqueurs, classement)
- Analyse vidéo (chat LLM avec embed YouTube/Drive/Veo deep-link)
- Effectif (joueurs groupés par poste)
- Stats (KPIs, taux par phase, match-par-match, leaderboards joueurs)
- Classement (FFR live + Tisini interne)

## Variables d'environnement
- `NEON_URL` : connexion Postgres
- `ANTHROPIC_API_KEY` : clé Claude
- `PORT` : assigné par Render

## Scripts
- `sync.py` : sync depuis Tisini API → Neon
- `ingest_videos.py` : import des URLs vidéo depuis `ID -Tisini.xlsx`
- `ffr_sync.py` : sync classement depuis API publique FFR

## Local dev
```bash
pip install -r requirements.txt
python server.py  # http://localhost:8770
```
