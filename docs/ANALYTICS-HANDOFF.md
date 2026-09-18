# EPMS Analytics Dashboard — Handoff

**Written 2026-09-17.** Everything below is verified against the live local stack and real
PTC UAT data, not inferred. Read this file first; it is self-contained.

---

## 1. What this is

A new screen, **Power System Data Analytics Dashboard**, built to PTC's
`Power_System_Data_Analytics_Dashboard_Developer_Explanation_Final_v2.docx` plus their HTML
prototype. The user picks a date range, a block range and multiple datasets, chooses CSV
or Excel, and gets a **file** — instantly for small selections, via the server queue for
large ones.

**The screen is file-first (user decision, 2026-09-17 afternoon).** There is NO data table
in the browser: a 239-column result is a spreadsheet, not something anyone reads on
screen. The filter panel spans the full width; **My Exports** beneath it is the only
content area. The query engine, budget and pivot are unchanged — only the render surface
was removed (`AnalyticsTable.jsx` deleted; `orientation.js` kept, it drives the file
layout).

**v1 scope, by explicit user decision:** filters + download. (Originally filters +
table + download; the table was removed the same day.)
**Not built:** KPI cards, Chart View, Summary tab. The query engine is designed so those are
additive — they read the same canonical response — but they are out of scope for v1.

It sits **under Reports** in the sidebar (`/reports/analytics-dashboard`, user decision
2026-09-17 evening — it was briefly its own "Analytics" root). Nothing already delivered
was changed or removed.

## 2. Where the work is

| repo | branch | commits | state |
|---|---|---|---|
| `backend` | `feat/analytics-dashboard` | 16 (squashed from 53; original history at local `backup/analytics-53-commits`) | **PR #326 open on `dev`, not merged**; rebased on `origin/dev` 14 Sep |
| `client`  | `feat/analytics-dashboard` | 10 (squashed from 17; original at local `backup/analytics-17-commits`) | **PR #192 open on `dev`, not merged**; rebased on `origin/dev` 14 Sep |

Both pushed. `git log --oneline origin/dev..HEAD` in each repo lists everything.

**Reference documents**
- Design/spec: `docs/superpowers/specs/2026-09-17-analytics-dashboard-design.md`
- Implementation plan (34 tasks, all done): `docs/superpowers/plans/2026-09-17-analytics-dashboard.md`
- Pending deploy changes: `docs/EPMS-nginx-changes.md`

(The top-level `docs/` directory is **not** inside either git repo.)

## 3. Architecture in one page

A **dataset registry** maps 52 dataset codes onto ~7 MongoDB source groups, so ticking 12
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
| `analytics/registry.py` | 52 datasets: collection, field, unit, agg rule, series cardinality, availability, permission |
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

**Client files** — `client/src/features/analytics/` (hooks, components, `utils/orientation.js`,
`utils/readEstimate.js`) and `client/src/pages/analytics/AnalyticsDashboard.jsx` (~230 lines,
composition only). `FilterPanel` has a `layout="wide"` grid variant (ranges · data sets ·
file layout + format picker) and the original `"sidebar"` variant. `ExportsPanel` takes
`onAlsoAs(job, fmt)` — every Ready row offers the other format, rebuilt from the job's
stored filters on demand rather than generated up front. `BudgetDialog mode="export-only"`
hides the narrow/preview branches (nothing to narrow FOR without a table).

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

Request bodies take `block_band` (`block_grouping` accepted as an alias) and, for
`CUSTOM`, `custom_blocks`; `block_band_ranges` is server-stamped. Response payloads are
nested under `data` (the client reads `response.data.data`).
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
| ~~Browser render~~ | table removed 2026-09-17 — screen is file-first |
| **Full job lifecycle in the browser** | Also as → 202 · Cancel (confirm) → 200 → Cancelled + Try again · Try again → 200 on the **same job id** → Ready · Download → 200, valid XLSX. DB confirms 8/8 ready, no orphan. |
| **File-first flow** | small selection → `POST /export` 200, CSV downloaded instantly (542.68 in row 1); large by-plant selection → 202, queued, no dialog; "Also as CSV" on a Ready XLSX → 202 → a CSV job with identical filters reaches Ready |
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

**Tests:** backend `libs/tests` 817 pass / 10 fail; client 54 pass.
The 10 failures are **pre-existing vendor rot** (`test_supply_csv_parser.py`,
`test_grid_india_parser.py`, `test_demand_supply_entities.py`) — failing before this work.
Backend collection still aborts with exactly **6** pre-existing errors; we added none.

## 6. ✅ PTC dataset answers applied (2026-09-17, commit abf046c)

| PTC answer | what changed |
|---|---|
| ~~Actual Demand = `drawal`~~ → **superseded 2026-09-18: Actual Demand = `actual`** | `actual` is what every existing EPMS chart labels "Actual Demand" (they read `comp_act_demand`, which IS `actual` wherever an actual exists). `demand_processor.py:816` computes it as **drawal + SG of plant PPCLGPP** (own generation; verified `actual − drawal` = PPCLGPP's SG). `DRAWAL` is back as its own dataset ("Drawal", the raw feed value DSM settles on) and **`OD_UD_QUANTUM` depends on `DRAWAL`**, not on Actual Demand. Live 29 Aug blk 41: Actual 330.13 · Drawal 318.53 · DSM Schedule 430.38 · OD/UD −111.85 · Final Schedule 445.38. `CACHE_VERSION` v6. |
| **Demand (UDM) = actual + rostering** | Kept `$comp_udm` — it IS exactly that where `actual` exists (1,864 rows, 0 mismatches) and also covers 1,542 rows where actual has not landed. |
| **DC/SG stay on `volume`**, no loss variants | No change to values. Recorded in a registry comment that `volume` is GROSS on DC rows and NET on SG rows — PTC's convention, not a defect. |
| **Final Schedule — keep both** plant and state level | Already shipped. |
| **State-level is the default; per-plant is a drill-down** (user decision, later same day) | Codes unchanged. `DECLARED_CAPACITY_TOTAL` → "Declared Capacity", `SG_TOTAL` → "Scheduled Generation", `FINAL_SCHEDULE_STATE` → "Final Schedule" lead their groups. `DC_PLANT` / `SG_PLANT` / `FINAL_SCHEDULE_PLANT` are relabelled "— by plant", flagged `drilldown: true`, sorted after the totals and badged "By plant". A 5-month Declared Capacity + SG request now renders inline with no plant selector and no 413. Verified: state total 475.33 = latest-revision sum over 25 plants in Mongo. |
| **Weekly/Monthly Forecast & MAPE = `DAF \| IDF`** | **4** live datasets (`WEEKLY_FORECAST`, `WEEKLY_MAPE`, `MONTHLY_FORECAST`, `MONTHLY_MAPE`), each `series: per_forecast_type` → **one tick yields a "Day Ahead" and an "Intra Day" column**. Originally shipped as 8 split `_DAF`/`_IDF` datasets on a mis-reading of the sheet; collapsed the same day on the user's question. DAF and IDF share the same `(date, block)` key, so they are columns of one table, not two tables. |

**Weekly/monthly MAPE is the MEAN of the per-block `mape`** — identical to how daily MAPE is
defined in `libs/helpers/demand_calculations.py` (per block:
`|(actual+rostering−forecast)/(actual+rostering)|×100`, verified 600 rows / 0 mismatches).
It is deliberately NOT recomputed from summed totals, which gives a different figure
(7.24% vs 7.78% on a real week) that nobody could reconcile with the daily number.
Verified live: API weekly MAPE 7.784 = hand mean-of-blocks 7.784.

**`per_forecast_type`** is a new series kind: the demand adapter declares `type` as its
entity, and the base only keys `$group` on it when such a dataset is selected — an
ordinary Actual Demand query is NOT split by type (pinned by test; the DAF/IDF rows carry
identical `drawal`, so a split would be a silent duplicate column). The pivot emits
`entity_label` ("Day Ahead"/"Intra Day") beside the stable `entity` key; the CSV/XLSX
writers prefer the label. Verified live: weekly MAPE DAF 7.784 (matches hand calc), IDF 4.499.

**`fixed_grouping`** is a new `Dataset` field. Roll-up datasets carry `WEEK_WISE`/`MONTH_WISE`
and the service forces that grouping for the whole request in `_as_request()` (the single
choke point, so budget, cache key and planner all see it). `meta.grouping_forced_by` records
which dataset imposed it. Mixing weekly with monthly is refused with 422.

**`CACHE_VERSION` bumped to v2.** The cache key is built from dataset codes, so redefining
what a code means kept serving the OLD payload for the TTL — verified live (`cache=hit`,
stale 554.68). Bump it whenever a dataset's meaning changes.

Absent datasets are now **2** (TAM only), down from 6.

## 6b. Partial weeks, indexes, sentinel zeros (2026-09-17 evening)

**Partial roll-up periods.** A 3-day range rolled up weekly returns a bucket labelled with
the WHOLE week. The engine's average was honest; the label was not. Now: `meta.period_coverage`
(per period: `days_covered / days_in_period / partial / from / to`), `meta.partial_periods`,
and a CSV header line `# Partial periods: 2026-W35 (5 of 7 days); 2026-W36 (4 of 7 days)`.
`analytics/coverage.py`, pure date arithmetic, 12 tests. **The budget also miscounted weeks**
— `ceil(days/7)` said Sat 29 → Mon 31 Aug was 1 week (it is 2, an UNDERestimate); it now
counts distinct ISO weeks, matching the engine. `CACHE_VERSION` → v3 (response shape changed).
**Open for PTC:** a weekly roll-up returns 96 rows per week (a weekly-average daily profile),
not one number per week — the docx says "aggregate week-wise"; confirm which they want.

**Indexes.** `explain` on every source group for a 5-month range showed `demand_serve`
(18 datasets, the most-used group) was COLLSCAN on BOTH serve and historical — its only
index led with `type`, which the analytics `$match` deliberately does not filter on.
`supply_serve_historical` and both peak collections had no index at all. Migration now
creates 12 indexes; every group IXSCANs on both sides. Measured after: 1 day/1 dataset
0.32s cold / 0.05s warm; 1 month/10 datasets 0.19 / 0.06; 5 months/24 market series
0.71 / 0.10; 5 months DC+SG totals 0.69 / 0.06 (the supply revision-collapse is the only
real query-side cost). Nothing to optimise at local volumes; the indexes are insurance
for production growth.

**Sentinel zeros.** `FREQUENCY_FORECAST` was 0.0 on 100% of recent rows — not dead:
`demand_processor.py:991` writes 0.0 when the feed carried no value, and 320 real 49–49.5 Hz
values exist in historical. New registry field `sentinel_null` (per dataset) maps that
stored 0 to None BEFORE the accumulator, so a column of "0 Hz" cannot masquerade as a
forecast. Genuine zeros elsewhere (OD/UD) are untouched. Full population audit of all 45
live datasets: every `agg` rule is right for its unit; HPDAM's 94–99% zeros are real
(clears ~0.6% of blocks); `FINAL_SCHEDULE_PLANT` has no rows after 3 Aug locally (data
currency, not a defect).

## 6c. Final Schedule → supply_serve, and the halving bug it exposed (2026-09-17, late)

PTC's sheet places **Final Schedule under `supply_serve`**. We had it on `demand_serve.schedule`.
Investigated before changing: the two differ by exactly **15.00 MW on every block — plant
`PPCLGPP`** (`src=dc_sg_rollover`), plus `demand_serve.schedule` excludes the negative `OA_PX`
exchange line and is byte-identical across days in the UAT extract (an uploaded CSV
column, `demand_data_processing.py:168`). `supply_serve` SG is EPMS's own versioned,
published schedule — the docx's "after applicable revisions and cut-off". My first
explanation (losses) was WRONG: schedule is lower not higher, and the ratio wanders.

Decision (user): **Final Schedule = ΣSG from `supply_serve`.** Codes unchanged:
- `SG_TOTAL` → label "Final Schedule" (it already IS that number)
- `SG_PLANT` → "Final Schedule — by plant"
- `FINAL_SCHEDULE_STATE` (demand_serve) → **"DSM Schedule"** — kept because OD/UD Quantum
  must subtract the schedule at the drawal point (the sheet's own `drawal − schedule`)
- `FINAL_SCHEDULE_PLANT` (serve_dam_scheduling) → "DAM Schedule — by plant"
**Open for PTC:** should PPCLGPP and OA_PX be in the Final Schedule? Only they can say.

**Halving bug found while verifying** (commit `c0d2370`): `demand_serve` has a DAF and an
IDF row per block; `schedule`, `net_dsm_amount`, `dsm_rate`, `forecasted_frequency` are
written ONLY on IDF, DAF carries `0`. Unpinned, the two averaged and **DSM Schedule read
267.72 for a stored 535.44 — and OD/UD Quantum and Amount were wrong too**. Pinned to
`type: IDF` (same rule the forecast datasets already use). `drawal`/`frequency`/`comp_udm`
are identical on both rows and stay unpinned. `CACHE_VERSION` → v4. Verified live:
DSM Schedule 535.44, OD/UD −1.48 (= 533.96 − 535.44), Final Schedule 550.44.

## 6d. Bands from Masters → Parameters, Entity column gone, Reports move (2026-09-17, night)

**Group Blocks By is now parametrised.** The five named bands (Night Off-Peak, Day
Off-Peak, Day Peak, Evening Peak, Peak) each read their block ranges from one row in
`parameters` — `analytics_band_night_off_peak` … `analytics_band_peak` — edited on
**Masters → Parameters** like any other key. Value is text: `1-24, 89-96` (inclusive
blocks 1-96; `;` or whitespace also separate; a single `5` is block 5). Empty = band stays
disabled; the pill's tooltip names the parameter. An unparsable value disables the band
with the parse error as its tooltip rather than failing the screen. Processing seeds the
five keys empty at start-up (`ensure_block_band_parameters`, next to the bid-price
defaults), so PTC finds them in the list.

Flow: `AnalyticsService.with_band()` is the one place a definition enters a request — it
reads the band (30 s per-process cache, `bands(fresh=True)` for the `/block-bands` call)
and stamps `block_band_ranges` onto the request. Everything downstream (cache key,
planner, adapters, the stored job row, the CSV header) sees the ranges and never touches
`parameters` again; a queued/regenerated export therefore reproduces the file it made
even if PTC edits the band later. An inactive band → 422 `ANALYTICS_INVALID_REQUEST`
naming the parameter. Verified live end-to-end: set `analytics_band_peak = 65-72, 81-88`
through the real parameters API → pill lit up → UI-generated CSV `PEAK 488.5625` = hand
mean of the 32 stored blocks.

🔴 **Defect found by driving the UI (pre-existing):** the filter panel sent `block_grouping`
but the request model only read `block_band`, so **every Custom or named-band selection
was silently served as INDIVIDUAL** — the CSV header even said so. Fixed on both sides:
the client now sends `block_band` (+ `custom_blocks = [[from, to]]` for Custom) and the
model accepts `block_grouping` as an alias. Verified: Custom blocks 1-8 → `1-8 518.28`.

**Entity column removed from CSV and XLSX** (user: empty on most rows). CSV columns are
now `dataset,label,unit,date,block,value`, the entity folded into the label —
`Declared Capacity — by plant (ABCREPL_BHDL2)`, `DAM ACP (IEX)`, `Weekly MAPE (Day
Ahead)`. XLSX: a scalar sheet is `Block | <dates>`; a per-entity sheet gets a leading
column named for what it holds — `Plant` / `Exchange` / `Forecast` (pivot now emits
`series_kind`). `CACHE_VERSION` → v5.

**Reports move:** page file `client/src/pages/reports/AnalyticsDashboard.jsx`, route and
menu child `/reports/analytics-dashboard` under Reports; the "Analytics" root menu is
gone from the seeder and from the local DB (the child row was re-parented, its 17
`menu_roles` grants kept). `RouteProtection`/`Sidebar` bypass paths updated. The menu
name is unchanged, so it still mints `analytics_dashboard:read`.

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
   **The one PTC answer that matters now is B2/A8 on the question page** — whether
   PPCLGPP's own generation sits inside the state's load and schedule. `actual` (includes
   it) and `drawal` (excludes it) are both shipped; Final Schedule = Σ SG (includes it).

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

9. The 18 questions in §13. **Peak/off-peak band definitions** (⑭) no longer block
   anything in code — PTC types them into Masters → Parameters (§6d) and the pills light
   up. Everything else affects labelling or correctness confirmation, not function.

### Before any deployment

10. The go-live checklist in §8 — indexes, nginx, reseed, **menu grant** (without it the
    screen is admin-only), scheduler flags. Each breaks the feature in a way that
    mis-diagnoses as something else.

## 8. Go-live checklist — everything between "merged" and "working for PTC users"

Verified 2026-09-17 evening against the real PROD topology (single Ubuntu EC2, four
systemd units as user `epms`, each service its OWN checkout under
`/var/www/applications/server/production/<svc>/`, nginx path-routed `/processing/` →
:8003, client static under `/var/www/applications/client/dist`).

### A. Code — done on the branch, ships with the merge

| # | what | why it mattered |
|---|---|---|
| A1 | 🔴 **Page grant unlocks the datasets** (`analytics_service.permissions_for`) | The registry names `analytics:demand` / `:dsm` / `:forecast` / `:market` / `:schedule` / `:supply`. The menu-RBAC never mints those: a role holding the "Analytics Dashboard" menu gets `analytics_dashboard:read` (+ `analytics:read` from the parent). **Every non-super-admin role — 740 of 818 UAT accounts are `user` — got `permitted: false` on all 52 datasets and a 200 with an empty file.** Local testing never saw it because `localdev` is `admin`. Now any page grant expands to `analytics:*` (PTC's page model: access to the page = full rights). Proven live with a `user`-role account: before 0/52 permitted, after the menu grant 52/52, export returns 481 rows. |
| A2 | 🔴 Client honours `permitted` | It ignored the field entirely, so denied rows looked tickable and Generate produced an empty file. Now: disabled, "Not granted" badge, reason text; group "All" skips them. |
| A3 | 🟠 `openpyxl==3.1.2` added to `services/scheduler/requirements.txt` | The scheduler imports `analytics_xlsx` (openpyxl) but its requirements never listed it; locally one shared venv hid it. On PROD's per-service venv the sweep job would `ImportError` at registration — **every queued export dead, CSV included**. |
| A4 | 🟡 nginx snippet corrected (`docs/EPMS-nginx-changes.md` §10, `docs/nginx/application-proposed.conf` [10]) | First version was `location /api/v2/analytics/export` with no `proxy_pass` — on the path-routed vhost it would never have matched. |

### B. Deploy steps — in this order

1. **Merge** PR #326 (backend) and PR #192 (client) into `dev`, then promote as usual.
2. **Indexes** (once, against the shared Mongo, before the first user hits the screen):
   `./.venv/bin/python -m migrations.analytics_indexes_2026_09_17` from the processing
   checkout. Idempotent. Without it `demand_serve` is a COLLSCAN on both sides.
3. **nginx** — item 10 in `docs/EPMS-nginx-changes.md`; `nginx -t && systemctl reload nginx`.
   Non-breaking; can ride with items 1/2/6/7/8 in one reload.
4. **Restart auth first** so the seeder creates the `Analytics Dashboard` `menus` row
   under Reports (it adds rows it does not find; existing rows untouched). Then
   processing (which also seeds the five `analytics_band_*` parameters), then scheduler.
5. **Grant the menu to roles** — Admin → Roles → menu assignment → tick *Reports →
   Analytics Dashboard* (`read`) for every role that should see the screen. The grant
   runs `_sync_role_permissions`, which is what mints `analytics_dashboard:read`; users
   pick it up on their **next login** (it lives in the token).
   ⚠️ **Read the trap in §9 before doing this on PROD**: that sync REBUILDS the role's
   permission list from its menu rows and dropped 58 `:write`/`:delete` strings from the
   `user` role locally.
6. **Scheduler flags** — decide `USE_RABBITMQ_JOBS`. It defaults **true**. With it on,
   the doorbell is live and the branch's `BaseConsumer._cleanup` fix means the consumer
   now *reconnects* after a RabbitMQ blip instead of dying. **Before restarting the
   scheduler**, check for a backlog the dead consumer left behind — it will be drained
   and every message executed on startup:
   `rabbitmqctl list_queues name messages consumers`. Purge anything stale with
   `rabbitmqctl purge_queue <name>`. With it off, the every-minute sweep alone runs
   exports (≤60 s added latency, nothing else lost).
7. **Export directory** — optional. Default is `<checkout>/exports` per service; the
   scheduler writes there and stores the ABSOLUTE path in the job row, processing serves
   from that path, so the two different checkouts are fine on one box as one user. Set
   `ANALYTICS_EXPORT_DIR` in **both** `.env` files only if PTC wants files off the code
   volume (`mkdir -p … && chown epms:epms … && chmod 750 …`).
7b. **Band definitions** — Masters → Parameters → fill `analytics_band_*` with PTC's
   block ranges (§6d). Until then the five pills stay disabled with a tooltip saying so.
8. **Verify** (in this order, each proves the previous step):
   - `GET /processing/api/v2/analytics/datasets` as a non-admin → `permitted: true`
   - small CSV as that user → 200 with rows, not a header-only file
   - the TTFB curl in the nginx doc → first byte well before total
   - a large by-plant XLSX → 202 → Ready within ~2 min → Download 200
   - `grep "Analytics export" /var/log/epms/scheduler.log` shows the sweep ticking

### C. Not needed (checked, so nobody re-checks)

- No new `VITE_*` variable — analytics endpoints hang off the existing processing base.
- No `client_max_body_size` change — the request body is a small JSON filter.
- No Redis change — the cache uses the existing client; a Redis outage degrades to
  `cache=skipped`, it does not fail the query.
- No systemd change — `--root-path /processing` is already there and the routes build no
  absolute URLs.
- No `RouteProtection` change — the bypass stays until PROD has reseeded (§7 item 7).

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
- **Granting a menu through the admin UI rewrites the role's whole permission list.**
  `MenuRoleRepository._sync_role_permissions` rebuilds `roles.permissions` from the
  role's `menu_roles` rows and nothing else. Locally the `user` role went **92 → 34**
  strings on one Analytics grant — every `:write`/`:delete` it held (`dam_single_bid:write`,
  `forecast:write`, `supply:write`, …) vanished because its menu rows carry `["read"]`.
  Vendor behaviour, pre-existing, latent on the `dev` lineage (no route reads those
  strings there) — but a live outage the day `fix/vapt-remediation`'s guards merge. Before
  granting on PROD: `db.roles.find({}, {name:1, permissions:1})` → keep a copy → grant →
  diff. Restore with `$set` if anything but the two `analytics*` strings changed.
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

1. `BaseConsumer._cleanup()` was called in a `finally:` but **defined nowhere**. Precisely:
   `start_consuming()` blocks, so a consumer that connected cleanly at boot works — until
   the first broker disconnect or error, when the `finally` raises `AttributeError` and
   the thread dies **instead of reconnecting**. On PROD that means job triggers published
   after any RabbitMQ blip queue up unconsumed until the next scheduler restart; cron jobs
   are unaffected, so the symptom is subtle. (An earlier revision of this note said "died
   on its first iteration" — too strong.) See §8 B6 for the backlog check this implies.
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
    **Follow-up:** the lock read as "broken" because the only explanation was a hover
    tooltip on a disabled checkbox. Now every roll-up row carries a Weekly / Monthly tag,
    and a notice appears at the top of the group naming the locked axis with an inline
    "Clear the monthly selection" link. Verified: tick Monthly → notice + Weekly rows
    disabled → click Clear → Weekly unlocks, notice gone.
11. **Every download from My Exports was a 403** ("This download link is not valid for
    this export"). The route requires `?token=<download_token>`; the server exposes it on
    every job row; the client never sent it. Masked during verification because curl
    passed the token by hand. Fixed in client `1f30a8d` — `utils/downloadToken.js`, with
    a `node --test` case pinning that the token travels as a query param.
12. **The budget dialog never opened on 413** (client `085cf53`). The hook read the estimate
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
**answered: `volume`**; Actual Demand — PTC said `drawal`, **user decided 2026-09-18:
`actual`** (matches every existing chart), `drawal` shipped beside it as "Drawal"; is
PTC's "Load Data" row one of these or a third figure? ~~UDM~~ **answered:
actual+rostering = `comp_udm`**; OD/UD Amount stored vs recomputed; "Traded"/"Bidded"
meaning ours or the whole market.

**Data thin or odd — confirm expected:** portfolio GDAM/HPDAM have no rows at all;
HPDAM clears in ~0.6% of blocks; Rostering mostly zero (is zero real or N/A?);
`peak_demand_serve` has only 15 documents; UDM is `comp_udm` and IDF-only.

**Cross-cutting:** peak/off-peak band definitions **do not exist anywhere** in EPMS or legacy
mplive — PTC now enters them in Masters → Parameters (§6d), so this is data entry, not a
code question; which dataset list is authoritative (the docx's 46 rows include 9
duplicates); export retention (72h default); CSV decimal precision; KPI card rules when
several incompatible datasets are selected.
