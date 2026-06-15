# AutoAnime Handoff Plan

## Project Goal

AutoAnime is a NAS media automation app. The first production workflow is:

```txt
Mikan RSS -> anime aggregation -> per-series release choice -> PikPak offline download
-> task polling -> cloud rename -> Bangumi metadata -> NFO output -> Jellyfin library
```

The project name is intentionally general enough to support future modules:

- seasonal anime tracking
- old anime backfill
- movie downloads
- US/EU TV tracking
- old TV backfill
- multi-indexer and multi-downloader support

## Current Architecture

```txt
autoanime/
  backend/
    app/
      main.py            FastAPI app and JSON API routes
      db.py              SQLite schema, settings, logs, migrations
      scanner.py         Mikan RSS scanning, release aggregation, queueing, PikPak task processing
      parser.py          RSS title parsing: series title, group, resolution, episode
      pikpak_service.py  PikPakAPI wrapper, access/refresh token handling
      metadata.py        Bangumi metadata fetch and NFO generation
      library.py         Jellyfin-friendly path/name rendering
      config.py          data paths and default settings
    requirements.txt
  frontend/
    src/
      App.vue            Vue admin console
      api.js             Axios API client
      style.css          App-level styling
      main.js
    package.json
    vite.config.js       builds to backend/frontend_dist
  Dockerfile
  docker-compose.yml
  README.md
  PLAN.md
```

## Backend

Stack:

- Python 3.12
- FastAPI
- SQLite
- APScheduler
- httpx
- feedparser
- PikPakAPI

Data path:

```txt
APP_DATA_DIR=/data
/data/autoanime.db
/data/nfo
```

Main API routes:

```txt
GET  /api/dashboard
GET  /api/settings
PUT  /api/settings
GET  /api/series/{series_id}
PUT  /api/series/{series_id}
POST /api/scan
POST /api/tasks/process
POST /api/tasks/poll
POST /api/tasks/retry-failed
POST /api/series/{series_id}/download
POST /api/series/{series_id}/metadata
POST /api/series/{series_id}/nfo
POST /api/releases/{release_id}/download
```

Frontend hosting:

- Vite builds to `backend/frontend_dist`
- FastAPI serves `/assets/*` and SPA fallback from `backend/frontend_dist/index.html`
- If frontend is missing, FastAPI serves `backend/app/static/missing-frontend.html`

## Frontend

Chosen stack:

- Vue 3
- Vite
- Element Plus
- vuedraggable
- axios

Reasoning:

- More maintainable than Jinja for a complex admin app.
- Element Plus gives production-grade Chinese-friendly admin UI components.
- vuedraggable supports visual priority ordering for subtitle groups and resolutions.
- Still lightweight enough for NAS Docker deployment.

Current UI sections:

- Sidebar navigation
- Dashboard with metrics and feature modules
- Anime library cards
- Download queue table
- Calendar placeholder
- Settings center
- Series detail drawer
- Drag-sort subtitle group priority
- Drag-sort resolution priority

## Data Model

SQLite tables:

- `settings`: global key/value settings
- `series`: one aggregated anime/show entry
- `episodes`: per-series episode records
- `releases`: RSS releases grouped under a series
- `download_tasks`: PikPak submission and polling status
- `logs`: operational logs

Important fields:

- `series.bangumi_id`
- `series.tmdb_id`
- `series.selected_group`
- `series.selected_resolution`
- `series.auto_download`
- `series.backfill_mode`
- `download_tasks.pikpak_task_id`
- `download_tasks.pikpak_file_id`
- `download_tasks.normalized_name`

## Key Design Decisions

1. Do not use global subtitle/resolution filtering as the final rule.
   - Global priority only helps auto-select defaults.
   - Final selection is per series.

2. PikPak auth should primarily use:

```txt
access_token + refresh_token
```

   Account/password remains a fallback.

3. Jellyfin-friendly naming should be standard:

```txt
{title_cn} ({year}) [bangumi-{bangumi_id}]/Season {season:02d}/{title_cn} - S{season:02d}E{episode:02d} - {episode_title}
```

4. NFO output must be configurable.
   - Default: `/data/nfo`
   - For Jellyfin direct use, mount the rclone media directory into the container and set `nfo_output_root`, e.g. `/media/anime`.
   - NFO should live next to the media files using the same series/season directory structure.

5. Mikan RSS proxy and PikPak proxy are separate settings.
   - User said Mikan RSS mainly needs proxy; PikPak may not.

## Current Implementation State

Completed:

- Backend moved to `backend/app`
- JSON API created
- Vue + Element Plus frontend scaffolded
- Modern admin UI written in `frontend/src/App.vue`
- Drag-sort priority controls added
- Frontend dashboard now supports auto-refresh.
  - Default interval: 5 seconds.
  - User can switch between manual/auto refresh.
  - Refresh pauses while settings page is active or series drawer is open, to avoid overwriting in-progress edits.
- Dashboard now exposes active queue visibility.
  - `/api/dashboard` includes `task_counts` and `active_tasks`.
  - UI shows pending/running/submitted/failed tasks in the dashboard.
- Failed task retry is supported.
  - `POST /api/tasks/retry-failed` resets failed tasks to pending, clears attempts/errors, and starts processing.
- UI now clarifies setting effects.
  - Global settings affect future scan/queue decisions.
  - Failed token/download tasks need "重试失败".
  - "补全全部" is not fully implemented until a searchable backfill source is added.
- Existing backend workflow preserved
- Project renamed to AutoAnime
- Root Dockerfile changed to multi-stage build:
  - Node builds frontend
  - Python image serves FastAPI + built Vue SPA
- Local validation completed:
  - `npm install`
  - `npm run build`
  - `python -m compileall backend/app`
  - local uvicorn served `/` with status 200
  - local uvicorn served `/api/settings` with status 200
- PikPak offline submission now initializes captcha before `POST /drive/v1/files`.
  - If PikPak returns `Verification code is invalid`, AutoAnime refreshes captcha and retries once.
- Scan flow automatically attempts Bangumi metadata refresh for newly discovered series without `metadata_source`.
- Scan flow generates NFO after each touched series is processed.
  - If `nfo_output_root` is `/media/anime`, NFO follows the Jellyfin media folder structure.
- Series aggregation now normalizes title fingerprints.
  - Handles common Simplified/Traditional character differences.
  - Removes common release tags, punctuation, spaces, episode suffixes, and resolution labels before title fingerprinting.
- Existing duplicate series with the same `bangumi_id` are merged during DB migration and after Bangumi metadata refresh.

Validation notes:

- npm audit reported 3 high severity vulnerabilities after install.
- `npm audit fix --force` was not run because it may introduce breaking dependency upgrades.
- Vite build warned that the main JS chunk is larger than 500 kB. This is acceptable for now, but code splitting can be added later.

## Known Gaps

Functional gaps:

- Real end-to-end PikPak submission has not been tested with a real token.
- PikPak task/file ID extraction may need adjustment after seeing actual API return payload.
- Cloud rename depends on a valid `file_id`.
- Bangumi metadata is basic: manual Bangumi ID works best; automatic search is only first-result heuristic.
- Episode titles and air dates are not fully populated yet.
- TMDB is not implemented yet.
- Old anime backfill is only a planned module; current RSS alone cannot reliably backfill old episodes.
- Movie/TV modules are placeholders.
- Jellyfin refresh API is not implemented.

UI gaps:

- No authentication.
- No route-level browser URLs yet; current Vue app uses internal state.
- Series cards need richer status badges once real data exists.
- Calendar needs real Bangumi/TMDB schedule data.

## Deployment

Expected NAS path:

```sh
/volume1/docker/autoanime
```

Run:

```sh
docker compose up -d --build
```

Port:

```txt
32888:8080
```

## Upload Package Rules

Do not upload generated/heavy directories to NAS:

```txt
frontend/node_modules
backend/frontend_dist
backend/app/__pycache__
data
test-data
*.zip
```

Upload only source and deployment files:

```txt
backend/
frontend/src/
frontend/index.html
frontend/package.json
frontend/package-lock.json
frontend/vite.config.js
Dockerfile
docker-compose.yml
README.md
PLAN.md
.dockerignore
.gitignore
```

Reason:

- `frontend/node_modules` contains thousands of files and is slow to copy.
- Docker builds dependencies on the NAS during `docker compose up -d --build`.
- `data/` contains runtime state and should not be overwritten by source uploads.

When handing this project to another AI or uploading for NAS testing, create a clean archive that excludes generated/runtime paths.

For NFO/Jellyfin direct output, add a media mount:

```yaml
volumes:
  - ./data:/data
  - /volume1/Assets3/Media/pikpak-anime:/media/anime
```

Then set in UI:

```txt
NFO 输出目录: /media/anime
```

## Next Plan

1. Improve API robustness.
   - Add proper error responses.
   - Add operation status endpoints for background jobs.
   - Add API response schemas later if needed.

2. Test real Mikan RSS parsing.
   - Confirm title parser handles common groups.
   - Improve episode parsing for multi-language naming.

3. Test real PikPak token workflow.
   - Confirm access/refresh token wrapping works.
   - Inspect actual offline download response.
   - Fix task/file ID extraction if necessary.
   - Re-test after captcha initialization fix for `Verification code is invalid`.

4. Add metadata enhancements.
   - Better Bangumi search selection UI.
   - Store cover locally or support remote cover in UI.
   - Episode list from Bangumi where available.
   - NFO with richer fields.

5. Add old anime backfill module.
   - Needs a searchable source, not just RSS.
   - Likely Mikan search or other indexer adapter.
   - Current "补全全部" setting is stored, but it cannot fetch missing historical episodes by itself yet.

6. Add Jellyfin integration.
   - Configure Jellyfin URL/API key.
   - Trigger library scan after NFO generation or task completion.

7. Frontend polish.
   - Add browser routes if navigation state needs sharable URLs.
   - Add real calendar data after metadata enrichment.
   - Add better empty states and first-run setup wizard.
   - Consider dynamic imports to reduce Vite chunk size.

## Notes For Next AI

- Do not revert user/NAS-specific work.
- Keep the app NAS-friendly and Docker-friendly.
- Prefer adding modules/adapters over hardcoding Mikan/PikPak assumptions everywhere.
- Keep frontend dependency count modest.
- Update this `PLAN.md` after every meaningful architecture or workflow change.
