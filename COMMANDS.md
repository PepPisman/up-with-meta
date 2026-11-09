# Commands

Run everything from `C:\dota-meta-ai`. `python` below means the project's virtualenv. Activate it once per terminal:

```powershell
.venv\Scripts\Activate.ps1
```

Without it, `python` is Laragon's 3.10 and nothing works. Or call `.venv\Scripts\python.exe` directly.

## Setup

| Command | What it does |
|---|---|
| `pip install -e ".[dev]"` | Installs the project and its dependencies (pytest too) into the venv. Run again after `pyproject.toml` changes. |

## Migrations (Alembic, like `php artisan migrate`)

They use the database named in `.env` (`POSTGRES_DB`, default `dota_meta`).

| Command | What it does |
|---|---|
| `python -m alembic upgrade head` | Applies every pending migration. Safe to repeat: does nothing when already at head. |
| `python -m alembic current` | Prints which migration the database is on. Dev is at `0007 (head)`. |
| `python -m alembic history` | Lists all migrations in order. |
| `python -m alembic downgrade -1` | Undoes the last migration (`migrate:rollback`). Can delete data. |
| `python -m alembic revision -m "name"` | Creates an empty migration file in `migrations/versions/`. You write the SQL by hand. |

## Data ingest (writes to Postgres, fills the tables the API will read)

| Command | What it does | Network | Writes to |
|---|---|---|---|
| `python -m dota_meta_bot.ingest.heroes [file]` | Loads the hero list from a JSON file (default `data/heroStats.json`). Adds new heroes, updates changed ones, skips unchanged ones. A bad hero stops everything and writes nothing. | none | `heroes` |
| `python -m dota_meta_bot.ingest.snapshot --patch 7.41f` | Fetches OpenDota `/heroStats` and stores one snapshot with winrates. Skips (no network) if that patch already has a snapshot from the same source. | OpenDota, 1 call | `heroes`, `snapshots`, `hero_winrate_stats` |
| `python -m dota_meta_bot.ingest.stratz` | Fetches the latest patch and hero base stats from STRATZ. Skips the heavy call when that patch is already stored. Needs `STRATZ_API_TOKEN` in `.env` and heroes already loaded. | STRATZ, 1 small + 1 heavy call | `snapshots`, `hero_base_stats` |
| `python -m dota_meta_bot.ingest.weekly` | The weekly update. Asks STRATZ for the latest patch, saves OpenDota winrates if the last copy is 7+ days old, saves STRATZ base stats only on a new patch. First error stops the run. | STRATZ + OpenDota | `heroes`, `snapshots`, both stat tables |

Flags:

| Flag | Works on | Meaning |
|---|---|---|
| `--force` | snapshot, stratz, weekly | Ignore the skip rule and fetch again. Adds another snapshot row. |
| `--file path.json` | snapshot | Read the payload from a file instead of calling OpenDota. Stored with source `opendota_file`, so it never blocks a real fetch. |
| `--days-since-patch N` | snapshot | Stores how many days the patch had been live. The weekly command and `stratz` work this out themselves. |
| `--with-base-stats` | snapshot | Also stores OpenDota base stats (development only: they lag the live patch). |

Each command prints one result line and exits with `0` on success, `1` on failure (the error goes to stderr).

## Tests

Database tests only run when the database name ends in `_test`, so always set it for the command:

```powershell
$env:POSTGRES_DB='dota_meta_test'; python -m pytest
```

| Command | What it does |
|---|---|
| `python -m pytest` | Runs everything. Without `POSTGRES_DB=dota_meta_test` the database tests are skipped, not failed. |
| `python -m pytest tests/test_ingest_weekly.py -q` | Runs one file, quiet output. |
| `python -m pytest -k "latest" -q` | Runs only tests whose name contains "latest". |

The test fixture upgrades `dota_meta_test` to head and empties its tables before each test. It never touches `dota_meta`.

## Other

| Command | What it does |
|---|---|
| `python -m dota_meta_bot.main` | Old first experiment: fetches OpenDota `/heroStats` and prints the raw JSON. Writes nothing. Safe to ignore. |
| `scripts/throwaway_postgres.sh start\|stop\|reset\|env` | Optional temporary Postgres on port 55432. Only needed if the shared server misbehaves during tests. |
| `docker-compose.yml` | pgvector Postgres for a future WSL/CI setup. Untested, not used on Windows. |

## Typical order on a fresh database

1. `python -m alembic upgrade head`
2. `python -m dota_meta_bot.ingest.weekly`

The weekly command loads the heroes itself (its OpenDota step upserts them) before the STRATZ step needs them, so the separate `ingest.heroes` command is only for loading from a saved file.
