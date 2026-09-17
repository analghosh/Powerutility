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
| `backend` | `feat/analytics-dashboard` | 27 | **not merged**, branched off clean `dev` |
| `client`  | `feat/analytics-dashboard` | 7  | **not merged**, branched off clean `dev` |

Neither has been pushed. `git log --oneline dev..HEAD` in each repo lists everything.

**Reference documents**
- Design/spec: `docs/superpowers/specs/2026-09-17-analytics-dashboard-design.md`
- Implementation plan (34 tasks, all done): `docs/superpowers/plans/2026-09-17-analytics-dashboard.md`
- Pending deploy changes: `docs/EPMS-nginx-changes.md`

(The top-level `docs/` directory is **not** inside either git repo.)

## 3. Architecture in one page

A **dataset registry** maps 53 dataset codes onto ~7 MongoDB source groups, so ticking 12
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
| `analytics/registry.py` | 53 datasets: collection, field, unit, agg rule, series cardinality, availability, permission |
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
| Query correctness | API block 1 = 554.68; direct Mongo = 554.68 |
| Browser render | AG Grid, **33 rows in DOM for 96 blocks** → virtualization real |
| Over-budget | 998,784 cells → 413 with 3 costed suggestions |
| Capped band | 499,392 cells → 200, `capped: true`, 8 dates returned not 500k cells |
| Redis cache | miss → hit |
| Disabled dataset posted directly | 422 `ANALYTICS_DATASET_UNAVAILABLE` |
| CSV export | streamed, filter header block, nulls blank |
| XLSX inline | sheet per dataset + `Filters` sheet |
| Queued export | 202 → sweep → 22/22 chunks → ready, 183KB downloaded and opened |
| Download authz | ownership checked independently of the token |

**Tests:** backend `libs/tests` 746 pass / 10 fail; client 44 pass.
The 10 failures are **pre-existing vendor rot** (`test_supply_csv_parser.py`,
`test_grid_india_parser.py`, `test_demand_supply_entities.py`) — failing before this work.
Backend collection still aborts with exactly **6** pre-existing errors; we added none.

## 6. 🔴 Top of the queue — known defect, NOT fixed

**DC and SG are stored on opposite loss bases.** Verified on 400 rows each, zero mismatches:

| rows | `volume` is | counterpart |
|---|---|---|
| `dc_sg="DC"` | **GROSS** (before losses) | `dc_afterloss` = `volume × multiplier` |
| `dc_sg="SG"` | **NET** (after losses) | `sg_beforelosses` = `volume ÷ multiplier` |

```
DC  KUDGI blk 71 : volume 49.50  dc_afterloss    47.65   (−3.73%)
SG  NNTPP blk 41 : volume 25.31  sg_beforelosses 26.29   (+3.87%)
```

All four supply datasets read `$volume` today — `DC_PLANT`, `SG_PLANT`,
`DECLARED_CAPACITY_TOTAL`, `SG_TOTAL` — so **DC is reported gross while SG is reported net**,
with the errors running in opposite directions (~7–8% combined). DC-vs-SG is the core
availability-vs-dispatch comparison and both headers just say "MW". Nothing flags it.

**Proposed fix:** split each into explicit before/after-losses variants so the label states
the basis, e.g. `Plant wise DC (before losses)` → `$volume`,
`Plant wise DC (after losses)` → `$dc_afterloss`,
`Plant wise SG (before losses)` → `$sg_beforelosses`,
`Plant wise SG (after losses)` → `$volume`.
Contained: registry entries plus the adapter's `value` expression. PTC then only picks the
default, which is a labelling decision rather than a correctness one. `multiplier` is
per-plant (≈0.9627 in samples) and already on every row.

## 7. Other open work

- **Not tested:** mid-export scheduler restart-and-resume (unit-tested only); concurrent-load
  measurement of analytics against the bidding path; cross-user download 403 live
  (unit-tested only).
- **CSV number formatting:** raw float repr leaks, e.g. `522.9399999999999`. Left alone
  because rounding precision is a reporting decision for PTC.
- **`RouteProtection.jsx` bypass retained deliberately.** The `menus` row now ships in
  `services/auth/app/core/seeder.py`, but UAT/PROD have not reseeded, so removing the bypass
  would break the screen there. `/reports/forecast-report` carries the identical bypass for
  the same reason. Remove only after PROD reseeds.

## 8. Deploy prerequisites — will break the feature if missed

1. **`/var/lib/epms/exports` must exist and be writable by the service user.**
   Verified failure: `Cannot create the analytics export directory '/var/lib/epms/exports':
   [Errno 13] Permission denied`. Inline exports keep working, so this mis-diagnoses as
   "only large downloads break". Or set `ANALYTICS_EXPORT_DIR` in the service environment.
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

**No data source exists (6 datasets):** Weekly Forecast, Monthly Forecast, Weekly MAPE,
Monthly MAPE, TAM Traded, TAM Bidded.

**Source ambiguous:** Declared Capacity vs Plant wise DC (same rows — is the difference
aggregation level?); Final Schedule plant-level vs state-level; **DC/SG loss basis (see §6)**;
OD/UD Amount stored vs recomputed; "Traded"/"Bidded" meaning ours or the whole market.

**Data thin or odd — confirm expected:** portfolio GDAM/HPDAM have no rows at all;
HPDAM clears in ~0.6% of blocks; Rostering mostly zero (is zero real or N/A?);
`peak_demand_serve` has only 15 documents; UDM is `comp_udm` and IDF-only.

**Cross-cutting:** peak/off-peak band definitions **do not exist anywhere** in EPMS or legacy
mplive — this is the only open item blocking a visible feature (5 of 7 "Group Blocks By"
options ship disabled); which dataset list is authoritative (the docx's 46 rows include 9
duplicates); export retention (72h default); CSV decimal precision; KPI card rules when
several incompatible datasets are selected.
