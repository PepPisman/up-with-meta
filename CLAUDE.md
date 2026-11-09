# CLAUDE.md — dota-meta-ai

## Project

Dota 2 assistant:
1. Meta heroes + winrates per patch, from cached data.
2. "I play Luna vs these 5 heroes" -> item and pick recommendations.
3. Match outcome prediction from a draft, backtested against a naive baseline.
4. LLM answers via tools that query our DB, never from model memory. Patch notes via RAG.

Product surface: "generate item options" button, draft analysis ("I play Luna, my team is ..., enemy is ..."),
and (stretch) "evaluate my game". Fight-timing advice has no clean data: label it a heuristic, not a statistic.

## Architecture

- Modular monolith: one FastAPI app + one worker image, clean internal boundaries. Split into services
  only after scale demands it. This project is also for learning Python/FastAPI/AI/ML: favour simple over scalable.
  Counter-argument: a monolith can tangle; the boundary rules below are the only defence and are a habit, not enforced.
- Group code by job (`meta/`, `knowledge/` for items+abilities, `llm/`, `ingest/`). Modules call each other only
  through their public functions (the LLM layer calls `meta.get_winrate()`, never queries winrate tables).
  Keep ingest separate from API code: it is the likeliest first split.
- Tool functions first, LLM second: one plain SQL-backed function per question, used by both REST endpoints and
  LLM tools. The item-options button can be deterministic (popularity + counter rules); the LLM only explains.
  The same functions power the evals.
- Vectors (pgvector) are for patch-notes RAG only. Winrates and other numbers are never embedded; the LLM gets
  them from tools with provenance (patch, sample size, snapshot age, source).
- Deferred until needed: queue, event bus, per-service DBs, cache layer.

## Stack

Python, FastAPI, Postgres + pgvector, Docker, GitHub Actions, then Kubernetes + ArgoCD,
Prometheus/Grafana, MLflow.

## Rules

- Data from OpenDota/STRATZ APIs only, cached in Postgres. No scraping Dotabuff.
- Dota2ProTracker is not allowed in code: its terms forbid bots/scrapers and using its data in any database,
  application or ML model. Look at it by eye only. Read a site's terms before any automated read, even a one-off
  check (Liquipedia terms still unchecked; nothing from it is stored).
- No farming routes (no clean data).
- Cut RAG or the ML model before cutting the deploy phase.
- Verification: the AI cross-checks numbers by comparing OpenDota with STRATZ through our own tool functions
  (planned `cross_check(hero, bracket)`). Dotabuff and Dota2ProTracker are looked at by eye only, never by code.
  When there is no comparable data (STRATZ daily data has holes), the AI says so instead of guessing.
- Rare-pick distortion (MARKED, the AI must handle it): 3 picks / 3 wins shows 100% and would top any list.
  Fix: minimum-picks filter at query time (as with `pro_*`) and every winrate is returned with its `picks`, so the
  model sees the sample size. Possible upgrade later: Wilson lower bound or Bayesian shrinkage.
  Weakness: a fixed threshold is arbitrary, a hero just above it is still noisy, and a real rare-but-strong
  hero is hidden. Edge cases to teach through tool output, system prompt and evals: small sample, stale snapshot
  (show age), bracket with no data (Immortal), turbo never pooled with pub, patch just changed, OpenDota and STRATZ
  disagree (report both, never average silently), pairwise matchups are hints.

## Working rules

- Application code in `src/` is written by Claude (changed 2026-10-31, so the owner can learn Python/FastAPI by
  asking about the files). After writing or changing a file, Claude explains it only when asked, as a short TL;DR
  that maps the Python/FastAPI mechanics to Laravel. Counter-argument: reading code you did not write teaches less
  than writing it; the owner can switch back by restoring the old rule.
- Infra (Dockerfile, compose, GitHub Actions, k8s manifests, ArgoCD, monitoring) can be written directly.
- Every architectural choice gets one counter-argument or alternative.

## General rules

- Read existing files before changing them. Stay inside the current task.
- No new dependencies, folders or architectural layers without approval.
- Validate input at the API boundary (Pydantic). Secrets only in `.env`, never committed.
- Postgres only. CHECK constraints for integrity rules. Index every foreign key.
- No comments in code (any language). Names carry the intent; type hints stay.
  Explanations go in chat as a TL;DR.
- Add or update tests when behaviour changes. Run the narrowest tests that cover it.
- Commits: one concern each, `type(scope): description`, only when I ask. Show a review of the staged diff first.
- Concise replies. Documentation files only when I ask.

## Milestones

- **M1-2:** OpenDota -> Postgres, tests. Done = top-10 winrate endpoint on cached data, containerized, CI running tests.
- **M3-4:** baseline win predictor + backtest; LLM tool calling over DB; ~30 eval questions.
  Needs new tables: `items`, `abilities` (per patch), `hero_matchups`, `hero_synergies`, `hero_item_popularity`.
  Pairwise matchup winrates do not add up to a 5v5 result: show them as hints with sample sizes.
  Done = predictor vs baseline accuracy + eval score.
- **M5-6:** patch-notes RAG, k8s deploy with monitoring, MLflow, evals in CI. Done = live URL + dashboard.
- **Stretch (cut first with RAG/ML):** "evaluate my game" (match-level fetch via on-demand cached `matches` /
  `match_players`; unverified how OpenDota exposes private profiles).
- Use the `pgvector/pgvector` Postgres image in Compose from the start, so enabling the extension later is one step.

## Decisions

- Winrates stored per bracket; bot uses the user's bracket. Buckets (decided): low 1-3, mid 4-5, high 6-7.
  Immortal (8) has no data because OpenDota folds it into Divine (7): store brackets 1-7 only.
- Schema (Alembic migrations `0001`-`0004` in `migrations/versions/`): 4 tables. `snapshots` (one row per pull: patch, source,
  days_since_patch), `heroes` (static), `hero_base_stats` (per snapshot, base stats change per patch),
  `hero_winrate_stats` (WIDE, stored as fetched: one row per hero per snapshot with b1..b7, pub, turbo,
  pro pick/win columns). Stat tables FK to `snapshots`. Everything else is computed in views:
  `v_bracket_winrate` (unpivot, bot filters by bracket), `v_bucket_winrate`, `v_winrate_by_mode`.
  These three return every snapshot. Application code reads the `v_latest_*` versions instead (migrations `0006`, `0007`):
  `v_latest_winrate_snapshot` keeps the newest snapshot that has winrate rows per `source` + `patch` and flags
  `is_current` on the newest one per source; `v_latest_bracket_winrate`, `v_latest_bucket_winrate` and
  `v_latest_winrate_by_mode` join the plain views to it. Top-10 and the AI filter `is_current` for the current meta.
  Counter-argument: the DB hides old weekly rows from the app but keeps them, so a trend query needs the plain views.
  Not yet covered: base stats (`hero_base_stats`) have no latest view; `--force` can add several rows per patch.
- Snapshot growth and performance (measured 2026-11-02 on synthetic weekly data in a rolled-back transaction on
  `dota_meta_test`, 3 winrate sources): storage is not the risk (~127 winrate rows a week per source, a few MB in 10
  years). Query shape was: the `0006` views joined to the latest snapshot after unpivoting every snapshot, so a
  top-10 took 358 ms (bucket) and 747 ms (mode) at 10 years. Migration `0007` picks the latest snapshot first and
  unpivots only its rows: about 2.5 ms at 10 years and 1 ms at 5, the same as reading one snapshot directly.
  All snapshots are kept (decided); revisit pruning only if a measurement says so. Caveats: the fast plan relies on
  rows being stored in time order (true for weekly inserts; after a backfill or bulk load run ANALYZE and re-measure);
  the bucket boundaries now live in two views (`v_bucket_winrate`, `v_latest_bucket_winrate`), guarded by a test
  that compares the latest views with the plain ones. Rule: any new per-snapshot table (`hero_matchups`,
  `hero_synergies`, `hero_item_popularity`: ~16k rows per snapshot) must decide keep-all vs latest-only before it
  is built, and gets a latest view that selects the snapshot first.
- Sources (decided 2026-11-02), checked against our data and the sources' own docs:
  winrates and pick counts, brackets 1-7, pub, turbo, pro: OpenDota `/heroStats` (Immortal is NOT available there:
  `8_pick`/`8_win` are 0 for all 127 heroes). Hero base stats: STRATZ constants. Immortal and ranked-only: STRATZ
  later, once its data holes and storage terms are resolved. Draft-prediction match data: OpenDota Explorer after a
  feasibility spike, STRATZ match data as the alternative. Dotabuff and Dota2ProTracker: by eye only, never code
  (Dotabuff's terms are still unread: a guessed URL 404ed; ProTracker's terms ban bots and storing its data).
  Correction to a claim made in chat: STRATZ does not "power" ProTracker; its homepage says "Data enriched by
  Imprint & Stratz", and it shows a 7000+ MMR slice that our STRATZ Immortal numbers do not reproduce.
  Verification order when checking a number (owner's list): Dotabuff, Dota2ProTracker, STRATZ, OpenDota. The first
  two are manual, so `cross_check` can only compare STRATZ with OpenDota and tell the owner where to look by eye.
  Which stored source the AI reads is chosen per question type by data quality, not by that order.
  Source registry (`ingest/sources.py`, small, approved): one entry per source with name, kind, automated flag,
  verification rank, gate and job; a new source is one entry plus its parser; unknown source names are rejected.
- Local DB: the Windows Postgres 18 service (`postgresql-x64-18`, localhost:5432). Role `dota` (generated password
  in git-ignored `.env`), databases `dota_meta` (dev) and `dota_meta_test`. The same server also hosts Menthar's
  databases: never touch `menthar*`. No Docker on Windows: `docker-compose.yml` (image `pgvector/pgvector:pg18`,
  host port 5433, volume at `/var/lib/postgresql` because PG18 images moved the data directory) is kept for a
  possible WSL/CI setup and is untested.
  `pg_hba.conf` accepts localhost only. pgvector is NOT installed on the Windows Postgres: needed before the
  patch-notes RAG step (or run Postgres with pgvector in WSL/Docker then).
- Migrations: Alembic with hand-written raw SQL (`op.execute`), no ORM models (autogenerate ignores views).
  SQLAlchemy is used only for the engine/connection (`core/db.py`), psycopg 3 is the driver.
  Migrations are the only source of truth for the schema; never edit an applied migration, add a new one.
  Run them as a separate step (`python -m alembic upgrade head`), never at app startup.
  Counter-argument: Alembic + SQLAlchemy are heavy for four tables; dbmate/Flyway or a 30-line runner would also work.
- Database tests only run when `POSTGRES_DB` ends in `_test` (they skip otherwise, to never touch dev data); run them
  with `POSTGRES_DB=dota_meta_test` set for that command. The `migrated_database` fixture runs `upgrade head` and
  TRUNCATEs the data tables before each test; it does NOT rebuild the schema per test. Lesson (2026-10-20): the old
  fixture dropped and recreated every table around each of ~100 tests, and Windows briefly locked a file, so the shared
  Postgres hit `PANIC: could not truncate file ... Permission denied`, restarted, and recovered cleanly (dev data
  intact). The server also hosts Menthar. If it ever recurs, run tests on the throwaway cluster
  (`scripts/throwaway_postgres.sh start`, port 55432) instead of the shared server, and consider excluding the
  Postgres data directory from antivirus scanning.
- Migration `0005`: `hero_base_stats.base_mana` is nullable (STRATZ does not expose base mana; unknown is stored as
  NULL, never invented). Its downgrade deletes rows with NULL base mana before restoring NOT NULL.
- Hero loader rules: upsert on `hero_id` and skip unchanged rows (`IS DISTINCT FROM`); never delete heroes missing
  from a pull; one transaction, a bad batch writes nothing; the error names the offending hero; validation is
  a Pydantic model, so unknown `primary_attr`/`attack_type`, duplicate ids/names and empty payloads are rejected.
  Counter-argument: one bad hero blocks the other 126 (skip-and-log would hide data problems instead).
- Settings come from env vars / `.env` (`POSTGRES_*`, see `core/config.py`). Inside Compose the host becomes `db`, port 5432.
- Buckets are computed at query time as SUM(wins)/SUM(picks), never an average of bracket winrates.
- Patch is hand-stamped (API has no patch field), so the FK does not validate it: check format in ingest.
- Data sources for heroes/items/patch dates: undecided (game files preferred for ground truth). Revisit later.
- "Ranked" = sum of brackets 1-7. Unverified whether those are ranked-only: ~9% of pub picks have no rank tier.
  If exact ranked-only winrates are needed, evaluate STRATZ's ranked filter.
- `turbo_*` is a different mode: never pool with `pub_*`. `pub_*` is NOT the sum of the brackets
  (Anti-Mage 585,393 vs 636,697; 0 of 127 heroes match), so it is stored separately.
- `pro_*` samples are tiny: require a minimum-picks filter at query time.
- Separate tables: static hero info vs time-varying stats.
- LLM layer uses tools (`get_hero_info(hero)`, `get_winrate(hero, bracket)`), not stuffed context.
- Patch-note agent may summarize/flag heroes; nothing is written without human confirmation.
  Winrates always come from fresh API pulls.
- Base stats from OpenDota are NOT stored by default (`--with-base-stats` opts in, development only). Evidence
  (2026-10-20, vs Liquipedia, a community wiki): Crystal Maiden (last changed 7.41b) matched fully; Axe (7.41e)
  agility 20 vs 18; Anti-Mage (7.41f) agility 24 vs 25 and move speed 310 vs 315. OpenDota's hero constants appear to
  lag the live patch by about two patches. Three heroes is a small sample; game files are the ground truth.
  Winrates are counted from matches, so they are not affected.
- Patch label check: dota2.com/news/updates lists `7.41f` as the latest gameplay patch, and Liquipedia
  (`Version_7.41f`) gives its release date as 2026-09-15 (a second site agrees). The patch page itself shows no date, and
  the 2026-10-06 "featured post" on the news page is NOT the release date. Later "Dota 2 Update" posts (e.g. 2026-10-14)
  are client updates, not gameplay patches. Take release dates from Liquipedia or the game files, not from news-post dates.
  So the OpenDota base-stat staleness above is over five weeks old, not two.
- Dev workflow: fetch once to `data/heroStats.json`, develop against the file. No API loops.
- API call policy (changed 2026-10-31): a weekly update, never from the request path (users read Postgres only).
  One command, `python -m dota_meta_bot.ingest.weekly [--force]`, run by hand or Windows Task Scheduler now and a
  Kubernetes CronJob at M5-6 (no in-app scheduler; counter-argument: an in-app scheduler needs the app always
  running and mixes ingest with API code). Steps: (1) one small STRATZ call finds the latest patch;
  (2) OpenDota `/heroStats` (bracket picks/wins, pub, turbo, pro) is stored as a new snapshot stamped with that patch
  when the newest `opendota` snapshot of that patch is 7+ days old (so a new patch always fetches);
  (3) STRATZ base stats are fetched only when no `stratz` snapshot exists for that patch (no new patch = no heavy call).
  Weekly snapshots of one patch are separate rows; never sum across snapshots and always read the latest one.
  `--force` overrides both gates. Advisory lock stops two runs fetching at once; stop on HTTP 429; first error stops
  the run. The plain `ingest.snapshot` command keeps its patch-only gate. Open: unverified whether `/heroStats` is a
  rolling window, so a snapshot stamped with the newest patch may include matches from before it.
  Accepted weakness: data goes stale if the schedule does not run, so show snapshot age with answers.

## OpenDota `/heroStats` facts

- 127 hero objects. `id` not contiguous (max 155): key by `id`, never by position.
- `1_*`..`8_*` = rank brackets, Herald (1) to Immortal (8).
- `8_pick`/`8_win` = 0 for all heroes (no Immortal data).
- Turbo keys are plural (`turbo_picks`, `turbo_wins`); pub/pro are singular (`pub_pick`, `pro_pick`). `pro_ban` exists.
- `turn_rate` often `None`.
- `base_health` (120) identical for all heroes: placeholder, don't store. `base_mr` (25) and `cm_enabled` also constant.
  `base_mana` has 3 distinct values (earlier note saying it was constant was wrong).
- `*_trend` arrays: 7 entries (likely rolling 7 days; unverified).
- Explorer (`/api/explorer?sql=...`, checked 2026-11-02): live, exposes `public_matches` (`radiant_team`, `dire_team`
  hero id arrays, `radiant_win`, `duration`, `avg_rank_tier`, `game_mode`, `lobby_type`). It is a sample (a maintainer
  said in 2017 only matches with 3+ players showing MMR and balanced modes are eligible; unverified for today). The
  newest rows are unfinished matches (`duration` 0, teams `[0,0,0,0,0]`). A 24-hour `count(*)` hit "statement
  timeout", so pull small paged queries. Free tier: 60 calls/min, 3000/day.
- Rate limit 60/min without key. ~0.4s warm / ~0.6s cold. Use `timeout=(3, 10)`.

## Next

- [x] Anti-Mage: sum `1_pick`..`7_pick` vs `pub_pick`. Result: pub is larger (see Decisions).
- [x] Table schemas designed, written as migrations, tested against a throwaway Postgres (see Decisions).
- [x] Migrations applied to `dota_meta` (head `0004`), 36 tests pass against `dota_meta_test`.
- [x] Snapshot ingest with the fetch gate: `python -m dota_meta_bot.ingest.snapshot --patch X` (95 tests green,
  verified end to end with one live API call into `dota_meta_test`, repeat skipped with the network blocked).
- [x] First real snapshot in `dota_meta` (2026-10-20): patch `7.41f`, source `opendota`, days_since_patch 35, 127 heroes,
  127 winrate rows, 0 base-stat rows. Label checked against dota2.com; release date 2026-09-15 per Liquipedia
  (`days_since_patch` was first stored as 14 from a wrong date and corrected to 35 on 2026-10-20).
- [ ] Sanity check to add to ingest: total wins / total picks is 0.5000 per mode in a real pull (it was, today).
- [ ] At home: load base stats from the game files (`npc_heroes.txt`) as a snapshot with source `gamefiles`;
  base-stat queries prefer `gamefiles`. Then compare against OpenDota to measure how far it lags.
- [x] Superseded by the weekly update (see API call policy): re-fetching on a new patch is now `ingest.weekly`.
- [x] Heroes loader (`ingest/heroes.py`): 127 heroes loaded twice with no rewrites, broken file changes nothing.
- [ ] Next loader: `snapshots` row (patch passed as a CLI argument, format-checked) + `hero_base_stats`, then `hero_winrate_stats`.
- Learning path (one new skill each): [x] Postgres + hero loader;
  [ ] FastAPI top-10 endpoint; [ ] tests for it; [ ] one LLM tool function + one tool-calling round trip;
  [ ] items/matchups tables + item-options button.
- [x] Rare-pick distortion: explained, fix and weakness written under Rules (marked). Implementation is step 4 below.
- Ordered steps (approved 2026-10-31):
  1. [x] CLAUDE.md rules and policy.
  2. [x] Age gate in `ingest_snapshot` (`min_age_days`, `now`) + `ingest/weekly.py` + tests.
  3. [x] Latest-snapshot views (migration `0006`) so top-10 never mixes weekly snapshots.
  3b. [x] Benchmarked the views; rewrote them to filter first (migration `0007`, see Snapshot growth).
  3c. [x] Applied `0006` + `0007` to `dota_meta` by the owner (2026-11-02): head `0007`, `v_latest_winrate_snapshot` shows
      `7.41f` / `opendota` / current. No weekly run yet (the only stored OpenDota snapshot is from 2026-10-20).
  3d. [x] Source registry (`ingest/sources.py`) + `weekly.py` refactor + tests. Deviation: `get_source` rejects unknown
      names, but the low-level `ingest_snapshot` still accepts any source string (dev file loads use `opendota_file`).
      `automated` is derived (a source with a job is automated), and the list order is the run order.
  3e. [ ] Later, M3-4, before the predictor: Explorer feasibility spike (~1,000 finished matches in small pages;
      measure rows per call, share with all 10 heroes, rank spread, calls per 100k matches). No tables until then.
  4. [ ] `meta/` top-10 function with the minimum-picks filter, returning picks, patch, snapshot age, source.
  5. [ ] FastAPI app: `GET /heroes/top?bracket=..&min_picks=..`, Pydantic response model, tests with `TestClient`.
  6. [ ] Dockerfile + CI.
  7. [ ] LLM tools (`get_winrate`, `cross_check`) + ~30 evals for the edge cases above.
  8. [ ] Last: admin area with CRUD to adjust values. Edits must be flagged as a `manual` source with an audit row.
     Counter-argument: hand-editing stats breaks "numbers come from data"; prefer overrides stored separately.
- [~] STRATZ evaluation done 2026-10-20 (17 of 20 calls, responses in git-ignored `data/stratz/`; GraphQL at
  `https://api.stratz.com/graphql`, header `Authorization: Bearer <STRATZ_API_TOKEN>` plus `User-Agent: STRATZ_API`;
  introspection works; the token was pasted in chat, so rotate it on stratz.com/api). Results:
  1. Immortal separate from Divine: YES (`RankBracket` enum has `IMMORTAL`; `bracketBasicIds` groups `DIVINE_IMMORTAL`).
  2. Daily results: YES (`heroStats.winDay`) but with holes: days present were Sep 11-17 and Oct 1-10; Sep 18-30 and everything
     after Oct 10 missing. `winGameVersion` (per patch) is stale: last data is 7.40 (Dec 2025). Patch-complete sums are NOT possible yet.
  3. Raw counts: YES (`winCount`, `matchCount`).
  4. Ranked-only filter: YES (`gameModeIds: [ALL_PICK_RANKED]`); `TURBO` is separate.
  5. Cost: one `winDay` call returns all 127 heroes x 17 days (137 KB) for one bracket + mode, so a full pull is about 10-20 calls.
     Live limits (response headers): 8/s, 150/min, 1,500/h, 15,000/day (the web page says 20/250/2,000/10,000).
  6. Constants fresh: YES. `constants.hero(id)` at gameVersionId 190 (7.41f) gives Anti-Mage agi 25 / ms 315, Axe agi 18, Crystal Maiden
     unchanged: all match Liquipedia where OpenDota was stale. Caveat: STRATZ `startingDamageMin/Max` and `startingArmor` already
     include attribute bonuses (Anti-Mage 54-58 = 29-33 + 25 agi; armor 6.17 = 2 + 25/6), so subtract them to store "base" values.
     `constants.gameVersions` lists 189 patches with release dates (7.41f = 2026-09-15, independently confirms our date).
  7. Terms on storing data: UNRESOLVED. The API page and knowledge base are silent and I could not load a terms page. Ask STRATZ before
     storing their winrates long term.
  8. Stability: no GraphQL errors on valid queries, but `patchNotes` returned 0 rows for 7.41f, 7.41e and 7.41, so it is not a RAG source.
  Validation of OUR data: Puck, Divine, ranked all pick, 13 days since 7.41f = 49.4% (17.5k matches) vs our OpenDota bracket 7 = 49.34%.
  Sanity: STRATZ totals are exactly 50.000% (1,190,565 / 2,381,130 matches). STRATZ Immortal for Puck was 49.2-49.5%, which does not
  reproduce Dota2ProTracker's 51.8% (7000+ MMR, different population; not usable as a source).
  Gate result: Immortal, counts yes; terms unresolved, so DO NOT store STRATZ winrates yet. Safe to use now: STRATZ constants (public game
  data from Valve files) for base stats and patch dates, pending the same terms answer. Also untested but present: `heroStats.matchUp`,
  `heroVsHeroMatchup`, `itemFullPurchase`, `laneOutcome` (feature 2 data).
- [x] STRATZ base stats loaded (2026-10-20): `python -m dota_meta_bot.ingest.stratz [--force]` stored 127 heroes for 7.41f,
  source `stratz`, days_since_patch 35 (patch name and release date come from STRATZ `gameVersions`, so no hand-typed label).
  Storage permission: I could not verify STRATZ's terms; the project owner stated on 2026-10-20 that storing is allowed.
  Conversion rules (checked against all 127 heroes vs OpenDota and against Liquipedia): damage minus the primary
  attribute base for str/agi/int heroes, universal heroes unchanged (STRATZ already gives base damage); armor minus
  agility/6; hp/mp regen, speeds, vision copied; `turn_rate` filled for all 127 (OpenDota had 82 null); `base_mana`
  NULL. Versus OpenDota, raw stats differ for about a fifth of heroes (agility 18, strength 12, intelligence 13,
  move speed 12) and health regen for 66, which matches OpenDota's staleness. Hero names differ for two heroes
  (Outworld Devourer, Ring Master), so the `heroes` table stays OpenDota-owned and STRATZ runs never touch it.
  The gate costs one small call per run (latest game version); the heavy hero call is skipped when stored.
  The token was pasted in chat: rotate it on stratz.com/api.
- [ ] Base-stat reads must prefer source `gamefiles`, then `stratz`; `opendota` rows would not exist (we skip them).
- [ ] STRATZ winrates are NOT loaded: daily data has holes (Sep 18-30 and after Oct 10 missing) and the per-patch
  endpoint is stale (7.40). Revisit; Immortal (`b8_pick`/`b8_win`) would need a new migration and view changes.
- [ ] Add an ingest check: compute `days_since_patch` from a verified release date (STRATZ `constants.gameVersions` or the game
  files) instead of typing it by hand.

## Open decisions

- Predictor first or LLM layer first (decide by end of M2).
- Cloud provider (defer to M4).
- ORM models: none for now (raw SQL for queries). Revisit if the app layer needs them.
- Postgres schema per module (`meta.`, `knowledge.`) so a later service split moves a schema, not scattered tables.
  Deferred: every name and view would need qualifying. Counter-argument: a split is hypothetical and cheap to
  retrofit with a rename migration.

## Layout

- `src/dota_meta_bot/main.py` — `/heroStats` fetch + timing
- `src/dota_meta_bot/core/` — shared infrastructure only: `config.py` (settings), `db.py` (engine, `check_connection`)
- `src/dota_meta_bot/ingest/heroes.py` — `parse_heroes` (pure, Pydantic `HeroRow`), `upsert_heroes`, `load_heroes`;
  run with `python -m dota_meta_bot.ingest.heroes [file]` (defaults to `data/heroStats.json`, git-ignored)
- `src/dota_meta_bot/ingest/opendota.py` — `fetch_hero_stats()` (timeout `(3, 10)`; 429 -> `RateLimited`, other failures
  -> `OpenDotaError`); `ingest/snapshot.py` — row models (`BaseStatsRow`, `WinrateRow`), `validate_patch`, the gate
  (`latest_snapshot_at`), advisory lock (`LOCK_KEY`), `ingest_snapshot(patch, fetch=..., force=, source=)`, CLI
  (`--patch`, `--force`, `--file`, `--days-since-patch`, `--with-base-stats`). `fetch` is injected so tests never touch the network.
  `--file` loads use source `opendota_file` so a dev file load never blocks a real fetch of the same patch.
  Weakness: the API call happens inside the DB transaction that holds the lock (max ~13 s); accepted for a single-user tool.
- Planned module packages next to `core/` and `ingest/`, one per job, each owning its tables and exposing public
  functions: `meta/` (winrates, top-10), `knowledge/` (items, abilities), `llm/`.
  Modules never query another module's tables; `core/` never imports a module.
- `scripts/throwaway_postgres.sh start|stop|reset|env` — optional temporary Postgres 18 on port 55432 (no longer needed)
- `src/dota_meta_bot/ingest/stratz.py` — `run_query` (GraphQL, bearer token, `User-Agent: STRATZ_API`, 429 ->
  `StratzRateLimited`, errors inside HTTP 200 -> `StratzError`, the token never appears in messages),
  `fetch_latest_game_version`, `fetch_hero_constants`, `to_base_stats_payload` (pure conversion), `ingest_stratz_base_stats`,
  CLI. It reuses `ingest_snapshot(..., include_base_stats=True, include_winrates=False, update_heroes=False)`.
- `src/dota_meta_bot/ingest/sources.py` — the source registry: `SourceSpec` (name, kind, verification rank, optional job),
  `SOURCES` (opendota, stratz automated; dotabuff, dota2protracker reference-only), `get_source`, `automated_sources`,
  `verification_order`, `WEEKLY_MIN_AGE_DAYS`.
- `src/dota_meta_bot/ingest/weekly.py` — `run_weekly` (one STRATZ patch check, then each automated source in order:
  OpenDota snapshot with the 7-day gate, then STRATZ base stats with the patch-only gate), CLI
  `python -m dota_meta_bot.ingest.weekly [--force]`.
- `migrations/` + `alembic.ini` — Alembic (raw SQL), `migrations/versions/0001`..`0007`
- `tests/` — `test_config.py`, `test_ingest_heroes.py`, `test_ingest_opendota.py`, `test_ingest_snapshot.py`,
  `test_ingest_stratz.py`, `test_ingest_sources.py`, `test_ingest_weekly.py`, `test_migrations.py`; `conftest.py` holds `migrated_database` (upgrade + TRUNCATE);
  `fakes.py` has the fake HTTP response; `fixtures/heroStats_sample.json` (3 OpenDota heroes) and
  `fixtures/stratz_constants_sample.json` (4 STRATZ heroes: agility, strength, intelligence, universal)
- `COMMANDS.md` — every command (migrations, ingest, tests) with what it does, what it writes and whether it uses the network
- `pyproject.toml` — dependencies (`pip install -e ".[dev]"`), pytest config
- `docker-compose.yml`, `.env.example` — local pgvector Postgres 18 (copy `.env.example` to `.env`, set a password)
