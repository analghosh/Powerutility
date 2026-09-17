# EPMS Analytics Dashboard — Handoff

**Written 2026-09-17.** Everything below is verified against the live local stack and real
PTC UAT data, not inferred. Read this file first; it is self-contained.

---

## 1. What this is

A new screen, **Power System Data Analytics Dashboard**, built to PTC's
`Power_System_Data_Analytics_Dashboard_Developer_Explanation_Final_v2.docx` plus their HTML
prototype. The user picks a date range, a block range and multiple datasets, gets a pivoted
block-wise table, and downloads CSV or Excel.

**v1 scope, by explicit user decision:** filters + table + download only.
**Not built:** KPI cards, Chart View, Summary tab. The query engine is designed so those are
additive — they read the same canonical response — but they are out of scope for v1.

It sits **alongside** the existing reports. Nothing already delivered was changed or removed.

## 2. Where the work is

| repo | branch | commits | state |
|---|---|---|---|
| `backend` | `feat/analytics-dashboard` | 32 | **not merged**, branched off clean `dev` |
| `client`  | `feat/analytics-dashboard` | 11 | **not merged**, branched off clean `dev` |

Neither has been pushed. `git log --oneline dev..HEAD` in each repo lists everything.

**Reference documents**
- Design/spec: `docs/superpowers/specs/2026-09-17-analytics-dashboard-design.md`
- Implementation plan (34 tasks, all done): `docs/superpowers/plans/2026-09-17-analytics-dashboard.md`
- Pending deploy changes: `docs/EPMS-nginx-changes.md`

(The top-level `docs/` directory is **not** inside either git repo.)

## 3. Architecture in one page

A **dataset registry** maps 56 dataset codes onto ~7 MongoDB source groups, so ticking 12
datasets issues ~4 aggregations rather than 12. A **cell budget**, computed from the filters
alone before any database call, decides whether a request renders inline, streams, or queues
as a background job.

```
filters + ticked datasets
  → registry lookup            (registry.py)
  → collapse to source groups  (planner.py)
  → per group, ONE pipeline:
        $match  (dates, blocks, discriminators)
        $unionWith  serve ∪ serve_historical
        [$sort+$group to collapse revisions — supply only]
        $group  _id = {dateKey, blockBucket, entity}
  → asyncio.gather, semaphore=4, maxTimeMS=20s   (executor.py)
  → resolve derived datasets                     (derived.py)
  → pivot once                                   (pivot.py)
```

**Backend files** — all under `services/processing/app/v2/`:

| file | responsibility |
|---|---|
| `analytics/registry.py` | 56 datasets: collection, field, unit, agg rule, series cardinality, availability, permission |
| `analytics/block_bands.py` | block grouping; PTC's 5 named bands seeded **inactive** |
| `analytics/budget.py` | cell arithmetic and band classification — pure, no I/O |
| `analytics/grouping.py` | `$dateToString` keys, block bucket `$switch`, accumulators |
| `analytics/adapters/` | one adapter per source group (demand, supply, scheduling, exchange, trade, peak) |
| `analytics/derived.py` | OD/UD Quantum = drawal − schedule |
| `analytics/planner.py` / `executor.py` | plan + concurrent execution + per-group failure capture |
| `analytics/pivot.py` / `models.py` | canonical response, request validation |
| `repository/analytics_repository.py` | runs pipelines; **raises** on failure (unlike `GenericRepository.aggregate`, which swallows and returns `[]`) |
| `repository/analytics_job_repository.py` | export job documents |
| `services/analytics_service.py` | budget → permissions → cache → plan → execute → pivot |
| `services/analytics_export_service.py` | CSV stream / XLSX inline / queue decision |
| `helpers/analytics_csv.py`, `analytics_xlsx.py` | writers (CSV is a generator; XLSX is write-only mode) |
| `api/router/analytics_routes.py`, `controller/analytics_controller.py` | HTTP |

Scheduler: `services/scheduler/app/processors/analytics_export_processor.py`.
Migration: `migrations/analytics_indexes_2026_09_17.py`.

**Client files** — `client/src/features/analytics/` (hooks, components, `utils/orientation.js`)
and `client/src/pages/analytics/AnalyticsDashboard.jsx`.

## 4. API

```
GET  /api/v2/analytics/datasets                  registry + availability
GET  /api/v2/analytics/plants?dataset=CODE       dataset-aware plant list
GET  /api/v2/analytics/block-bands               configured bands
POST /api/v2/analytics/query                     200 {data} | 413 {estimate+suggestions}
POST /api/v2/analytics/export                    200 stream | 200 file | 202 {job_id}
GET  /api/v2/analytics/exports                   this user's jobs
GET  /api/v2/analytics/export/{id}               status + progress + download_token
GET  /api/v2/analytics/export/{id}/download?token=…
POST /api/v2/analytics/export/{id}/cancel
POST /api/v2/analytics/export/{id}/retry     re-queue a failed/cancelled job IN PLACE
```

Response payloads are nested under `data` (the client reads `response.data.data`).
`series[].values` is a numeric matrix indexed `[block][date]`. **`null` means missing and must
never render as `0`** — a stated PTC requirement.

**Cell budget bands** (`cells = date_buckets × block_buckets × Σ series_cardinality`):
≤150k inline · ≤500k inline-capped · >500k async-only (**413**) · >10M refused.
All configurable via `ANALYTICS_CELLS_*`.

**Export modes:** CSV ≤500k streams; XLSX ≤150k builds inline; anything larger queues.
**MongoDB is the queue; RabbitMQ is only a doorbell** — `USE_RABBITMQ_JOBS=false` locally, so
the every-minute Mongo sweep is the primary path, not a fallback.

## 5. Verified working (live, 2026-09-17)

| check | evidence |
|---|---|
| Query correctness | API block 1 = **542.68** (`drawal`); direct Mongo `drawal` = 542.68 |
| Browser render | AG Grid, **33 rows in DOM for 96 blocks** → virtualization real |
| Over-budget | 998,784 cells → 413 with 3 costed suggestions |
| Capped band | 499,392 cells → 200, `capped: true`, 8 dates returned not 500k cells |
| Redis cache | miss → hit |
| Disabled dataset posted directly | 422 `ANALYTICS_DATASET_UNAVAILABLE` |
| CSV export | streamed, filter header block, nulls blank |
| XLSX inline | sheet per dataset + `Filters` sheet |
| Queued export | 202 → sweep → 22/22 chunks → ready, 183KB downloaded and opened |
| Download authz | ownership checked independently of the token |
| **Large range in the browser** | 5 months × Plant wise DC+SG × 34 plants → Apply → **413** → budget dialog opens (9,98,784 values, 3 costed suggestions) |
| **Suggestion round-trip** | click "Switch to Week-wise" → 200 → dialog closes → pill activates → grid renders **68 per-plant columns**, 33 DOM rows |
| Exports panel persistence | earlier queued jobs (Ready + Failed) still listed in a fresh browser session, with the `Errno 13` message surfaced to the user |

**Tests:** backend `libs/tests` 781 pass / 10 fail; client 49 pass.
The 10 failures are **pre-existing vendor rot** (`test_supply_csv_parser.py`,
`test_grid_india_parser.py`, `test_demand_supply_entities.py`) — failing before this work.
Backend collection still aborts with exactly **6** pre-existing errors; we added none.

## 6. ✅ PTC dataset answers applied (2026-09-17, commit abf046c)

| PTC answer | what changed |
|---|---|
| **Actual Demand = `drawal`** (not `actual`; they are never equal, drawal ≈ 0.9676 × actual) | `ACTUAL_DEMAND` now reads `$drawal`. The duplicate `DRAWAL` dataset was removed; `OD_UD_QUANTUM` depends on `ACTUAL_DEMAND`. |
| **Demand (UDM) = actual + rostering** | Kept `$comp_udm` — it IS exactly that where `actual` exists (1,864 rows, 0 mismatches) and also covers 1,542 rows where actual has not landed. |
| **DC/SG stay on `volume`**, no loss variants | No change to values. Recorded in a registry comment that `volume` is GROSS on DC rows and NET on SG rows — PTC's convention, not a defect. |
| **Final Schedule — keep both** plant and state level | Already shipped. |
| **State-level is the default; per-plant is a drill-down** (user decision, later same day) | Codes unchanged. `DECLARED_CAPACITY_TOTAL` → "Declared Capacity", `SG_TOTAL` → "Scheduled Generation", `FINAL_SCHEDULE_STATE` → "Final Schedule" lead their groups. `DC_PLANT` / `SG_PLANT` / `FINAL_SCHEDULE_PLANT` are relabelled "— by plant", flagged `drilldown: true`, sorted after the totals and badged "By plant". A 5-month Declared Capacity + SG request now renders inline with no plant selector and no 413. Verified: state total 475.33 = latest-revision sum over 25 plants in Mongo. |
| **Weekly/Monthly Forecast & MAPE = `DAF \| IDF`** | 8 new live datasets (`WEEKLY_/MONTHLY_` × `FORECAST_/MAPE_` × `DAF/IDF`). Plain roll-ups of the per-block values with `agg avg`. |

**Weekly/monthly MAPE is the MEAN of the per-block `mape`** — identical to how daily MAPE is
defined in `libs/helpers/demand_calculations.py` (per block:
`|(actual+rostering−forecast)/(actual+rostering)|×100`, verified 600 rows / 0 mismatches).
It is deliberately NOT recomputed from summed totals, which gives a different figure
(7.24% vs 7.78% on a real week) that nobody could reconcile with the daily number.
Verified live: API weekly MAPE 7.784 = hand mean-of-blocks 7.784.

**`fixed_grouping`** is a new `Dataset` field. Roll-up datasets carry `WEEK_WISE`/`MONTH_WISE`
and the service forces that grouping for the whole request in `_as_request()` (the single
choke point, so budget, cache key and planner all see it). `meta.grouping_forced_by` records
which dataset imposed it. Mixing weekly with monthly is refused with 422.

**`CACHE_VERSION` bumped to v2.** The cache key is built from dataset codes, so redefining
what a code means kept serving the OLD payload for the TTL — verified live (`cache=hit`,
stale 554.68). Bump it whenever a dataset's meaning changes.

Absent datasets are now **2** (TAM only), down from 6.

## 7. REMAINING WORK — the actual to-do list

Ordered by priority. Items marked ✅ are **already done** — do not redo them.

### Already verified — do not repeat

- ✅ **Browser verification was performed.** A real Chromium session logged in as `localdev`,
  loaded `/analytics/dashboard`, set the date range, ticked Actual Demand, clicked
  Apply & Generate, and confirmed the AG Grid rendered `554.68, 544.6, 527.54…` matching a
  direct Mongo read. 33 rows in the DOM for a 96-block result, proving virtualization.
  Screenshot captured. Zero analytics console errors (the 403s seen are Highcharts CDN
  scripts, pre-existing on every EPMS page).
- ✅ Queued export driven end to end: 202 → Mongo sweep → 22/22 chunks → downloaded 183KB
  XLSX and opened it.
- ✅ Budget 413, capped band, Redis cache hit, disabled-dataset 422, CSV stream, XLSX inline.
- ✅ **Large-range path in the real browser**: 413 → budget dialog → Week-wise suggestion →
  200 → 68-column grid. This run found and fixed the dialog-never-opens defect (§11 item 7).

### P1 — correctness

1. ~~Fix the DC/SG loss basis~~ — **closed by PTC decision**: both stay on `volume`. See §6.

### P2 — test coverage gaps

2. **Commit a repeatable analytics e2e journey.** The browser run above was **ad-hoc and was
   not saved**. The house convention is **Node `.mjs` scripts in `tools/e2e/`** — see the
   existing `tools/e2e/harness.mjs` and `tools/e2e/mfa_journey.mjs`. Write
   `tools/e2e/analytics_journey.mjs` in that style, covering: login → dashboard → set filters
   → Apply → assert grid values → CSV download → queue an export → poll to ready.
   ⚠️ Note `playwright` was pip-installed straight into `backend/.venv` during verification
   and is **not in any requirements file**. Either pin it in `requirements-dev.txt` or drop
   it and use the Node harness, which is the existing convention.
3. **Mid-export restart-and-resume is unit-tested only.** Start a large export, kill the
   scheduler mid-run, restart, and confirm from the job document that it resumed at the right
   chunk rather than restarting. The chunk receipts in `parts[]` exist for this.
4. **Cross-user download 403 is unit-tested only.** Needs a second real account to prove the
   authorization boundary live.
5. **Concurrent-load measurement never run.** Fire N simultaneous analytics queries while
   timing an authenticated bidding call, and record the degradation. Analytics has its own
   Motor client (`processing_analytics`) specifically so it cannot starve the bidding pool —
   but that is designed, not measured.

### P3 — polish / decisions

6. **CSV number formatting** — raw float repr leaks, e.g. `522.9399999999999`. Deliberately
   left alone: rounding precision is a reporting decision for PTC (question ⑰).
7. **`RouteProtection.jsx` bypass retained deliberately.** The `menus` row now ships in
   `services/auth/app/core/seeder.py`, but UAT/PROD have not reseeded, so removing the bypass
   would break the screen there. `/reports/forecast-report` carries the identical bypass for
   the same reason. Remove only after PROD reseeds.

### P4 — explicitly out of v1 scope (user decision, not oversight)

8. KPI cards, Chart View, Summary tab. The engine already returns everything they need; KPI
   cards additionally depend on PTC answering question ⑱ (which dataset the cards read when
   several incompatible ones are selected).

### Blocked on PTC, not on code

9. The 18 questions in §13. Only one blocks a visible feature: **peak/off-peak band
   definitions** (⑭), without which 5 of the 7 "Group Blocks By" options stay disabled.
   Everything else affects labelling or correctness confirmation, not function.

### Before any deployment

10. The four prerequisites in §8 — export directory, nginx buffering, index migration,
    auth reseed. Each breaks the feature in a way that mis-diagnoses as something else.

## 8. Deploy prerequisites — will break the feature if missed

1. ~~`/var/lib/epms/exports` must exist~~ — **no longer required.** `ANALYTICS_EXPORT_DIR`
   now defaults to `<repo>/exports`, which any service user can create. Setting it to a
   dedicated volume in PROD is recommended housekeeping, not a prerequisite.

2. **nginx `proxy_buffering off`** on `/api/v2/analytics/export`, else CSV streaming is
   pointless and large exports risk a proxy timeout.
3. Run `./.venv/bin/python -m migrations.analytics_indexes_2026_09_17` — `exchange_serve`
   and `exchange_serve_historical` had **no indexes at all**.
4. Reseed auth so the Analytics `menus` row exists, or the screen bounces users to `/login`.

Items 1 and 2 are recorded in `docs/EPMS-nginx-changes.md`.

## 9. Traps that will otherwise cost you hours

- **v2 code lives in `app/v2/`, not `app/api/v2/`.** Both `app/api/v2/router.py` and
  `app/v2/v2.py` are dead registration paths. Register routers in `app/main.py` only.
- **FastAPI here defers `include_router` into `_IncludedRouter`.** Inspecting `app.routes`
  before startup shows nothing. Verify via TestClient/OpenAPI instead.
- **A new screen needs a `menus` row + `menu_roles` grants**, or `RouteProtection` sends the
  user to `/login` regardless of how correct the frontend route is.
- **Sub-minute cron is not expressible.** `_add_job_to_scheduler` rejects anything but 5
  fields and never passes `second=`, so `*/30 * * * *` is thirty **minutes**. The sweep uses
  `* * * * *`.
- **A new scheduler job must be registered in THREE places** or it runs once at boot and
  never again: `_get_job_function`, `get_default_jobs`, `_register_fallback_jobs`.
- **Error responses use `detail`, success responses use `data`.** FastAPI's `HTTPException`
  puts its payload under `detail`; our 2xx routes nest under `data`. Any client code that
  reads an error body must look in `detail` first — the budget dialog was silently dead
  because it did not.
- **`GenericRepository.aggregate` swallows exceptions and returns `[]`.** Analytics must
  distinguish "no data" from "query failed", so it uses its own wrapper that raises.
- **Client has no React Query and no Tailwind.** Both appear in the tree but are inert. Use
  plain `useState`/`apiClient` hooks and Bootstrap + `newstyle.css`.
- **Class names `main-dashbooard` and `selction-time-row` are misspelled in the CSS** and must
  be copied verbatim.
- **Every pre-existing AG Grid usage sets `domLayout="autoHeight"`, which disables
  virtualization.** Ours deliberately does not. The conflicting `@ag-grid-community/*` v32
  packages were removed; only v34 remains.
- **`backend/.gitignore` used to hide all `*.md`/`*.txt`/`*.json`.** Scoped on this branch —
  if you branch from `dev` instead, new docs and fixtures will vanish silently.
- **Login as `localdev` / `Local@Dev12345`.** Logging in as `admin` gives "Uh Oh! Something
  went wrong" — `admin` is MFA-enrolled and dev has no MFA step.

## 10. Storage facts (verified, do not re-derive)

- `demand_serve.udm` is **zero in every document**; real values are in `comp_udm`, IDF rows only.
- `supply_serve.max_mw_dc` is **null everywhere**. DC is `volume`.
- `demand_serve.rostering` **is** populated (544 non-zero of 3,406).
- **OD/UD needs no join** — `drawal` and `schedule` are on the same document.
  OD/UD Amount is already stored as `net_dsm_amount`; the CERC band maths in
  `dsm_calculation.py` is not re-run.
- **Plant universes differ per dataset**: `supply_serve` 34, `serve_dam_scheduling` 53,
  `plant_master` 35. The plant selector is dataset-aware because of this.
- **HPDAM is real but sparse** — clears in ~0.6% of blocks, always at the ₹20,000 cap.
  Uncleared blocks are `null`.
- **Market-wide "Traded" IS `exchange_serve.acv`** — the same field as ACV, not a second dataset.
- **`*_bid_capture` is NOT a dashboard source** (~542k docs per date); it is the internal
  pre-submission bid ladder.
- **The archival leaves duplicates** — the same document sits in both `exchange_serve` and
  `exchange_serve_historical`, so `$unionWith` double-counts without dedup. Handled.
- `serve_dam_scheduling` has **no historical twin** and no duplicate revisions.
  `supply_serve` **does** carry revisions and needs latest-per-key collapsing.

## 11. Defects found and fixed along the way (all committed)

1. `BaseConsumer._cleanup()` was called in a `finally:` but **defined nowhere** — every
   consumer thread died on its first loop iteration. If PROD matches, RabbitMQ-triggered jobs
   have never run there; cron jobs are unaffected, so the symptom is subtle.
2. `ConsumerManager.start()` **ignored `USE_RABBITMQ_JOBS`/`_AUDIT`**.
3. **`HOURLY_WISE` grouping returned 1 bucket instead of 24** — `scheduled_at` is stamped at
   midnight, so `%H` labelled every row identically. Hour now derives from `time_block`.
   Only surfaced against real MongoDB.
4. **Archival double-count** (see §10).
5. `JobPublisher` used blocking pika in a sync function → added `publish_job_trigger_async`.
6. `.gitignore` blanket globs scoped.
7. **Export default dir was root-owned** (`/var/lib/epms/exports`) — first queued export
   failed `Errno 13`. Default is now repo-relative; PROD override optional.
8. **"Try again" on a failed export spawned a sibling job**, leaving the failed row in the
   list forever. Added `POST /export/{id}/retry` + `AnalyticsJobRepository.requeue()`: the
   SAME job goes back to `queued` with its run state wiped. Regenerate stays for `expired`
   (its file is gone, so a new job is honest). Verified in the browser: rows 3 → 3,
   Failed 1 → 0.
9. **CSV/Excel buttons looked decorative** — disabled with no explanation until a dataset
   was picked. Now a labelled "Download as" group with a hint saying why it is disabled.
10. **Group "All" on Demand & DSM produced "Analytics query failed" with raw dataset
    codes.** The server correctly refused mixing weekly and monthly roll-ups (different
    date axes), but the UI let the user build the combination first, then showed a red
    error listing `MONTHLY_MAPE_IDF` etc. Fixed at the source: `/datasets` now exposes
    `fixed_grouping`; the multi-select locks the OTHER axis's roll-ups (disabled, tooltip
    says why) once one is ticked, and group-select resolves the axis inside the batch so
    "All" ticks 17 not 21. The server message now uses labels, not codes, and carries the
    codes in a structured `groups` field. Verified: All → 17 selected → Apply → 200.
11. **The budget dialog never opened on 413** (client `085cf53`). The hook read the estimate
   from `response.data.data`, but FastAPI's `HTTPException` puts the payload under
   `detail` — `{ detail: { code, message, estimate } }`. curl could not catch this; only
   driving the real UI did. `readEstimate` now lives in `utils/readEstimate.js` with a
   `node --test` case pinning the exact real 413 shape.

## 12. Commands

```bash
# stack (login localdev / Local@Dev12345)
cd ~/Desktop/infiscopelabs && ./start.sh          # ./stop.sh to halt

# backend tests — NEVER bare pytest, the tests/ tree aborts collection
cd backend && ./.venv/bin/python -m pytest libs/tests/ -q

# client
cd client && node --test src/features/analytics/utils/orientation.test.js
cd client && npm run build      # npm run lint is DEAD, do not trust it

# indexes
cd backend && ./.venv/bin/python -m migrations.analytics_indexes_2026_09_17
```

## 13. The 18 open questions for PTC

Full text with reasoning is in §14 of the spec and in the session transcript. Headlines:

**No data source exists (2 datasets):** TAM Traded, TAM Bidded. ~~Weekly/Monthly Forecast
and MAPE~~ — **answered**: built as roll-ups of the per-block DAF/IDF values (§6).

**Source ambiguous:** Declared Capacity vs Plant wise DC (same rows — is the difference
aggregation level?); ~~Final Schedule level~~ **answered: keep both**; ~~DC/SG loss basis~~
**answered: `volume`**; ~~Actual Demand~~ **answered: `drawal`**; ~~UDM~~ **answered:
actual+rostering = `comp_udm`**; OD/UD Amount stored vs recomputed; "Traded"/"Bidded"
meaning ours or the whole market.

**Data thin or odd — confirm expected:** portfolio GDAM/HPDAM have no rows at all;
HPDAM clears in ~0.6% of blocks; Rostering mostly zero (is zero real or N/A?);
`peak_demand_serve` has only 15 documents; UDM is `comp_udm` and IDF-only.

**Cross-cutting:** peak/off-peak band definitions **do not exist anywhere** in EPMS or legacy
mplive — this is the only open item blocking a visible feature (5 of 7 "Group Blocks By"
options ship disabled); which dataset list is authoritative (the docx's 46 rows include 9
duplicates); export retention (72h default); CSV decimal precision; KPI card rules when
several incompatible datasets are selected.
