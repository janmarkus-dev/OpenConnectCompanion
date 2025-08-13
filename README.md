# OpenConnectCompanion
A blazing fast, open source, local, self hostable, widely compatible software, which synchronizes, analyzes and visualises data from Garmin smartwatches and bicycle computers written in python.
OpenConnectCompanion will try to mirror the feature set of the Garmin Connect software, while maintaining open-sourceness, reducing bloat and ramaining useable if garmin ever ceases to exist.

> OpenConnectCompanion is an independent open-source project and is not affiliated with or endorsed by Garmin Ltd. or its subsidiaries.

---

## Quick start (Docker)

Run the app locally in Docker. Data is stored in a Docker volume mounted at `/data` inside the container.

```powershell
docker compose up --build -d
Start-Process http://localhost:8000
```

Environment variables (defaults already set in compose):

- DATA_DIR=/data
- UPLOAD_FOLDER=/data/uploads
- DATABASE_URL=sqlite:////data/occ.db

To allow auto-import from devices, also mount your device or a host folder containing `.fit` files to one of: `/media`, `/mnt`, or `/data/mnt` inside the container.

## Local development (without Docker)

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:FLASK_APP = "app:create_app"
$env:DATABASE_URL = "sqlite:///occ.db"
$env:DATA_DIR = (Resolve-Path ".\data").Path
$env:UPLOAD_FOLDER = (Join-Path $env:DATA_DIR "uploads")
python -c "from app import create_app; from app import db; app = create_app();\nwith app.app_context(): db.create_all()"
python app.py
```

Open http://127.0.0.1:5000.

Troubleshooting: If you see "permission denied /data" when running locally (not in Docker), set DATA_DIR to a writable path as above. The app now defaults to .\data when not running inside a container.

## Features in this MVP

- Data lake: copies each FIT file into `/data/imports`, stores SHA256 for dedupe, keeps parsed JSON next to metadata rows in SQLite
- Background job (every 5 minutes) scanning common mount roots for Garmin FIT files and importing new ones
- Manual upload page for `.fit` files
- Activities list with summary metrics, per-activity page with zoomable charts (Plotly) and a basic route map (Leaflet + OSM tiles)
- REST API endpoints under `/api` to serve data to the frontend

## Project structure

```
app/
	__init__.py          # Flask app factory, scheduler setup
	blueprints/
		web.py             # UI routes
		api.py             # REST API
	models/
		models.py          # SQLAlchemy models (Activity, FileAsset, HealthMetric)
	services/
		device_scanner.py  # Scans for mounted devices
		importer.py        # Dedup, copy, parse, persist
		fit_parser.py      # Parses FIT to normalized JSON
	templates/           # Bootstrap 5 templates
	static/              # Static assets (unused for now)
```

## Privacy & performance

- All processing is local; no external APIs are used.
- SQLite indices on key fields help with multi-year datasets. Parsed JSON lives in `file_assets.parsed_json` for fast reads for charts.

## Extensibility

New analytics modules can be added as new services and blueprints. Store additional metrics in new tables or augment the `parsed_json` envelope, and expose data via new API endpoints.
