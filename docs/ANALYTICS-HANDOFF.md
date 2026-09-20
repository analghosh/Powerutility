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

A **dataset registry** maps 48 dataset codes onto 5 MongoDB source groups, so ticking 12
datasets issues ~4 aggregations rather than 12. A **cell budget**, computed from the filters
alone before any database call, decides whether a request renders inline, streams, or is
refused with a 413 carrying priced narrowings (it used to queue — see §6g).

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
| `analytics/registry.py` | 48 datasets: collection, field, unit, agg rule, series cardinality, availability, permission |
| `analytics/block_bands.py` | block grouping; PTC's 5 named bands seeded **inactive** |
| `analytics/budget.py` | cell arithmetic and band classification — pure, no I/O |
| `analytics/grouping.py` | the trading-day key, block bucket `$switch`, accumulators, and the identity **columns** each Group Date By puts in the file (§6g) |
| `analytics/adapters/` | one adapter per source group (demand, supply, exchange, trade, peak) |
| `analytics/derived.py` | OD/UD Quantum = drawal − schedule |
| `analytics/planner.py` / `executor.py` | plan + concurrent execution + per-group failure capture |
| `analytics/pivot.py` / `models.py` | canonical response, request validation |
| `repository/analytics_repository.py` | runs pipelines; **raises** on failure (unlike `GenericRepository.aggregate`, which swallows and returns `[]`) |
| `repository/analytics_job_repository.py` | export job documents |
| `services/analytics_service.py` | budget → permissions → cache → plan → execute → pivot |
| `services/analytics_export_service.py` | CSV stream / XLSX inline / **413** decision; writes the history row |
| `helpers/analytics_layout.py` | **the one table both writers emit** — identity columns + one column per series |
| `helpers/analytics_csv.py`, `analytics_xlsx.py` | writers (CSV is a generator; XLSX is write-only mode, one `Data` sheet) |
| `api/router/analytics_routes.py`, `controller/analytics_controller.py` | HTTP |

Scheduler: `services/scheduler/app/processors/analytics_export_processor.py`.
Migration: `migrations/analytics_indexes_2026_09_17.py`.

**Client files** — `client/src/features/analytics/` (hooks, components,
`utils/readEstimate.js`) and `client/src/pages/reports/AnalyticsDashboard.jsx`
(composition only). `FilterPanel` has a `layout="wide"` grid variant (ranges · data
sets · format picker + file name) and the original `"sidebar"` variant.
`ExportsPanel` takes `onExecute(job)` — each row shows its file name, a *View
configuration* disclosure and an *Execute* button that loads those filters back into
the panel (§6g). `BudgetDialog mode="narrow-only"` shows the priced narrowings and
nothing else, since there is no longer a server-side alternative to offer.
`utils/orientation.js` is unreferenced — the File layout toggles it served were
removed with the single-table layout.

## 4. API

```
GET  /api/v2/analytics/datasets                  registry + availability
GET  /api/v2/analytics/plants?dataset=CODE       dataset-aware plant list
GET  /api/v2/analytics/block-bands               configured bands
POST /api/v2/analytics/query                     200 {data} | 413 {estimate+suggestions}
POST /api/v2/analytics/export                    200 stream | 200 file | 413 {estimate+suggestions}
GET  /api/v2/analytics/exports                   this user's export history
# Below: still served, no longer reachable from the dashboard (§6g). They exist for
# the queued rows already in Mongo; the screen never creates new ones.
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
All configurable via `ANALYTICS_CELLS_*`. `date_buckets` is **one per trading day**
whatever the Group Date By — the pill names a column, not a bucket (§6g).

**Export modes (2026-09-20):** CSV ≤500k streams; XLSX ≤150k builds inline; anything
larger is **refused with 413** carrying priced narrowings. Nothing queues from the
dashboard. The queue below it (`_enqueue`, the scheduler worker, the retry route)
still owns the rows already in Mongo: **MongoDB is the queue; RabbitMQ is only a
doorbell** — `USE_RABBITMQ_JOBS=false` locally, so the every-minute Mongo sweep is
the primary path, not a fallback.

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

> Superseded by §6g (2026-09-20): the roll-up datasets were removed, so
> `analytics/coverage.py`, `meta.period_coverage`, `meta.partial_periods` and the
> `# Partial periods` header line are all gone. The ISO-week counting fix below is
> also moot — every grouping counts one bucket per trading day now. Kept as the
> record of why they existed. The "Open for PTC" question at the end was answered
> by removing the feature.

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
- ~~`FINAL_SCHEDULE_PLANT` (serve_dam_scheduling) → "DAM Schedule — by plant"~~ **removed
  2026-09-18 (user decision): one plant-wise schedule, `SG_PLANT` from `supply_serve`.**
  `serve_dam_scheduling` is the bid-construction snapshot `schedule_api` uploads — no
  revisions, non-plant lines (`IEX_IMPORT`…), 4 Feb → 3 Aug in UAT, never overlapping the
  supply data (4 Aug →) so the two could not even be compared. The scheduling adapter and
  its tests went with it; 5 source groups remain. 52 datasets.
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

## 6e. Capture cut-off, saved defaults, named files (2026-09-18)

Four changes, all driven through the real UI before being called done
(`tools/e2e/analytics_journey.mjs`, 13 steps, all passing).

**The ℹ "no rows yet" marker is gone.** `DatasetMultiSelect` put a warning glyph and a
tooltip on any dataset the registry flags `availability: "empty"` — four rows, the
GDAM/HPDAM Portfolio Traded/Bidded pair. It is a claim about storage made before any
query has run, and PTC read it as a claim about their data. The rows stay selectable
and the after-the-fact "No data in this range" note, which reports what a query
actually returned, is untouched.

**Declared Capacity and Final Schedule: no cut-off, and none needed.** PTC define
these as the figure captured at 01:00 the next morning from the last revision
received by then. A cut-off stage was built for it and then **removed the same day** —
it could only ever delete a row, never improve one, and there is provably nothing for
it to catch:

* The stored row **is** the last received revision. `supply_serve` keeps one document
  per key and rewrites it in place (zero duplicate `(scheduled_at, time_block,
  plant_id, dc_sg)` in either collection; `dc_sg` has a literal `unique=True` index
  plus an upsert). The existing revision collapse already returns it.
* **The ingest window shuts at midnight.** `supply_processor.py:440` routes a delivery
  date into `dc_sg` → `supply_serve` only while it is "today or tomorrow". After that
  a revision goes down the `historical_data` branch straight to
  `supply_serve_historical` with `system_revision_number = 1` hardcoded
  (`supply_processor.py:553`) — which **loses** the collapse to the archived copy
  carrying the real number (2144, 2147…). So it cannot change the answer.
* Measured over all 63,648 rows: latest grid stamp **+14.05 h**, last write **+23.94 h**
  (= 23:56 on the delivery day, exactly where the code says the window shuts). Nothing
  is within an hour of 01:00.

So the value is frozen ~1 hour **before** PTC's capture time, and the requirement is
met by the collapse alone. `CACHE_VERSION` stays **v6** — no dataset changed meaning.
`test_nothing_filters_between_the_union_and_the_revision_collapse` is the regression
guard. Read the supply adapter's module docstring before reintroducing anything here.

⚠️ Archival is **not** the freeze: `process_daily_archival_and_cleanup` deliberately
re-mirrors, *"so the historical table remains a true mirror of the serve table"*
(`supply_serve_repository.py:321`). The freeze is the ingest window, plus
`cleanup_cutoff = today - 3 days` deleting the serve row.

**Two per-plant datasets: restored, relabelled, and no plant picker (2026-09-18).**
Three changes in sequence, all user decisions:

1. `DC_PLANT` / `SG_PLANT` were briefly hidden, then **restored** once it was
   established that "Plant wise DC / Plant wise SG" were the OLD LABELS of these same
   two codes, not a separate pair. Verified from the stored job rows: a label reading
   `Plant wise DC + Plant wise SG` carries `datasets: ['DC_PLANT','SG_PLANT']`.
   `registry.HIDDEN_CODES` is now `()`; the mechanism stays as the cheap way to
   withhold a dataset (catalogue policy, never enforced in the planner, so a queued
   export still replays).
2. **Labels restored** to `Plant wise DC` / `Plant wise SG` — what PTC call them and
   what the pre-relabel job rows read. `drilldown` is unchanged, so they still sort
   after the state-level totals, which remain the default. `CACHE_VERSION` → **v7**:
   values did not move, but `series[].label` is in the cached payload and the writers
   print it, so a past-date query cached under v6 would emit the old label into files
   for the full 24 h TTL.
3. **No plant picker — every plant, always.** `PlantSelector` is no longer rendered
   and the client no longer calls `GET /analytics/plants` at all.

🔴 **The trap in (3), and why the resolution sits where it does.** An empty `plants`
list used to be a 422; it now means *all*. But `context_for` prices `plant_count` from
`len(req.plants)`, so left empty it would price **34 series as 1** and wave through a
request far over the inline cap — silent, because the response looks fine until the
file is enormous. So `AnalyticsService.with_plants` resolves the full list **before
anything is priced, keyed or stored**, exactly as `with_band` stamps its ranges:

* the budget sees the real series count (verified: 25 series returned, priced 25, not 1);
* the stored job row records the plants the file was built from, so a replay months
  later reproduces that file rather than gaining a newly commissioned plant.

The list is one cached distinct scan per source group (900 s TTL, read once then
cached — pinned by test). A failed lookup leaves `plants` empty rather than raising:
no plant filter is the same set of rows, and losing the query is worse than
under-pricing one request. The model's "plants must be selected" validator is gone;
three tests that pinned the old refusal now pin the new behaviour.

**Renamed "Analytics Dashboard" → "Reports Dashboard"** (user, 2026-09-18). Header,
sidebar, the not-granted hint and the Masters → Parameters help text. The **route,
files and `features/analytics/` folder keep their old names on purpose** — the path is
in RouteProtection's bypass list and in every bookmark.
🔴 **The menu NAME mints the permission.** `_get_resource_name_from_menu` falls back to
`name.lower().replace(" ", "_")`, so the row now mints `reports_dashboard:read` where
it minted `analytics_dashboard:read`. `AnalyticsService.PAGE_GRANTS` accepts **both** —
do not drop the old spelling until every deployment has been reseeded, or every role
granted under the old name loses all 52 datasets and gets a 200 with an empty file.

**The filter selection is saved per user, in MongoDB.** `analytics_user_preferences`,
one row per `user_id` (unique index), holding `datasets`, `plants` and `format`;
`GET`/`PUT`/`DELETE /api/v2/analytics/preferences`, owner-only, no user id in any
signature. The screen applies it **once**, when the catalogue and the preference have
both arrived — a default that reapplied itself would fight the user for the form — and
thereafter only when they press Load. The header carries *My default · Save current ·
Load · Clear*. Codes the registry has since lost, or a grant since revoked, are dropped
on read and reported rather than replayed into a disabled row. A preference is never
authority: `/query` re-checks the registry and the grant on every request.

**The user names the file.** A "File name" box sits under the format picker and rides
the request as `file_name`, so it applies to the instant download and to a queued job
alike; blank keeps the generated `analytics_<from>_to_<to>`. A finished export can also
be renamed from My Exports (`PATCH /export/{id}/filename`), which moves
`result.filename` and `filters.file_name` only — the bytes stay where the worker put
them, because the reaper and the disk-budget sweep both find files by `result.path`.
An empty rename restores the generated name. `request_hash` now includes the name, or
asking for the same data under a second name would be deduped onto the first job and
its first file name.

`analytics_csv.safe_file_stem` is the one sanitiser: it drops a supplied extension
(`report.exe` must not become `report.exe.csv`), turns everything outside
`[A-Za-z0-9_.-]` into `_` — which is what defeats traversal, since a `..` with no
separator left is just two dots — strips leading dots (hidden file) and trailing dots
and spaces (Windows renames those silently), and truncates to 120. A name that
sanitises away to nothing falls back to the generated one: the file is what the user
asked for, and its name is not worth losing it over.

🔴 **Defect found by driving the UI (pre-existing, affects every browser download):**
processing's CORS middleware set `allow_headers` but not `expose_headers`, and
`allow_headers` governs the *request*. A response header is unreadable to JavaScript
unless it is exposed — so `Content-Disposition` was invisible to the browser, `curl`
saw it throughout, and `useAnalyticsExports` silently saved every export under its own
`analytics-<from>-to-<to>` guess. Fixed in `services/processing/app/main.py`. PROD is
path-routed and therefore same-origin, so it never bit there; any deployment that puts
the API on its own host would have. The client fallback now also honours the typed name
(`features/analytics/utils/fileName.js`, 11 tests) so a proxy that strips the header
degrades to the right name instead of the wrong one.

**Tests:** backend `libs/tests` **861 pass / 10 fail** — the same 10 pre-existing
vendor-rot failures (`test_supply_csv_parser.py`, `test_grid_india_parser.py`,
`test_demand_supply_entities.py`), unchanged. Client `node --test` **113 pass**.
Browser journey **13/13**.

🔴 **Correction to step B4 — restarting auth does NOT create the menu row.**
`DatabaseSeeder.seed_menus` bails on the first line if *any* menu exists
(`if existing_menus and not force_reload: return True`, `seeder.py:190`), and there is
no add-missing path. UAT and PROD both have menus, so a restart seeds nothing. The
only other branch, `force_reload=True`, **deletes every menu first** — which would
destroy every `menu_roles` grant on the box. Neither is usable.
Insert the row directly instead, then grant it (step B5):

```javascript
db.menus.insertOne({
  name: "Reports Dashboard",            // mints reports_dashboard:read — see §6e
  icon: "📉",
  parent_id: <the _id of the "Reports" menu>,
  path: "/reports/analytics-dashboard", // unchanged; RouteProtection bypasses it
  order: 2,
  is_active: true
})
```

Verify the row's field names against an existing child of Reports before inserting —
copy that document's shape rather than this snippet's.

## 6f. Excel is the default export format (2026-09-19)

> Superseded in part by §6g (2026-09-20): the workbook is one `Data` sheet, not one
> sheet per dataset, and nothing queues. Excel-first is unchanged and still current.

The Generate As toggle now lists **Excel first and opens on it**; CSV is the second
button. PTC read this screen in Excel — one sheet per dataset with a `Block | <date>`
grid is what they open — and the CSV long form is the machine-readable half that was
never the common case. Verified in the browser
(`tools/e2e/analytics_journey.mjs`, **18 steps, all passing**), including a step that
clears the saved default and the per-device localStorage copy and asserts the screen
comes back on Excel.

Default flipped in five places, because "the default" is not one constant:

| Where | Was | Is |
|---|---|---|
| `analytics_export_service.DEFAULT_FORMAT` | `csv` | `xlsx` |
| `models.ExportRequest.format` | `csv` | `xlsx` |
| `models.PreferenceRequest.format` | `csv` | `xlsx` |
| `analytics_preference_repository.FORMATS[0]` | `csv` | `xlsx` |
| client `FORMATS[0]` / `readStoredFormat` fallback | `csv` | `xlsx` |

`FORMATS[0]` in the preference repository is load-bearing: `validate`, `sanitize` and
`empty_preference` all take the default from it, so the tuple order **is** the default.
The client mirror is the same shape — `FORMATS[0]` is both the first button and what
`readStoredFormat` falls back to, so the button order and the default cannot drift.

Three things this deliberately did **not** change:

* **A saved preference still wins.** A user who saved CSV as their default keeps
  opening on CSV. The new default is what a user with no saved preference gets.
* **The `or "csv"` fallbacks in `analytics_routes` and the export processor stay.**
  Those read the format of a job that was already written, to name a file that is
  already on disk. A legacy row with no `format` key is a CSV file; relabelling it
  `.xlsx` would hand the user a workbook that is not one.
* **`extensionFor` still coerces to `csv`.** It is fed `formatOfJob`, which reads the
  stored filename — a file not ending `.xlsx` really is a CSV.

**The cost, and it is real.** XLSX rides the lower of the two in-request ceilings
(`ANALYTICS_CELLS_INLINE`, 150k) where CSV rides `ANALYTICS_CELLS_INLINE_CAPPED`
(500k) — see the module docstring on `analytics_export_service` for why the two
numbers differ. So a range between those two ceilings used to stream instantly and
now **queues** and lands in My Exports. That is the intended trade (a 150k-cell
workbook holds a web worker for about a minute with nothing to show), but it means
more jobs through the scheduler sweep, and the disk budget and the 72h retention
matter more than they did. If PTC complain that exports that used to be instant now
queue, the lever is `ANALYTICS_CELLS_INLINE`, not the default.

> **Since §6g that range is refused, not queued** — the lever is the same setting,
> and it now decides what is possible at all rather than what waits.

**Tests:** backend `libs/tests` **872 pass / 10 fail** — the same 10 pre-existing
vendor-rot failures (`test_supply_csv_parser.py`, `test_grid_india_parser.py`,
`test_demand_supply_entities.py`), confirmed unchanged by re-running them on a
stashed tree. Analytics alone: **761 pass**. Client `node --test` **114 pass**
(one new case: the download fallback names an `.xlsx` when given no format).
Browser journey **18/18**.

## 6g. One table, grouping-as-a-column, immediate download (2026-09-20)

The largest change since the feature was built, and it reverses three of its
original decisions. PTC drove it after asking a question the code could not answer:
**"why does Group Date By make no difference to the sheet?"**

Three separate defects were behind that one question:

1. **Block-wise and Date-wise were the same file.** Both formatted `%Y-%m-%d` and
   left the block axis to Group Blocks By, so they were byte-identical — two pills,
   one behaviour. They are now the single **Block & Date wise**.
2. **Hourly was inert at Individual Blocks.** A block *is* a quarter hour, so adding
   an hour to the group key merely scattered 96 values across 24 sparse columns.
   Measured: 24 date columns × 96 block rows holding 96 non-null cells on a
   diagonal.
3. **A ticked weekly dataset silently rewrote the whole request.** `forced_grouping`
   applied a roll-up's `fixed_grouping` to every dataset in the file, so a user
   asking for block-wise Declared Capacity got ISO-week columns — with the
   Block-wise pill still lit and the Filters sheet still printing `BLOCK_WISE`.
   Reproduced live on the user's own range before the fix:

   ```
   asked for             : BLOCK_WISE
   meta.date_grouping    = WEEK_WISE
   meta.grouping_forced_by = ['WEEKLY_FORECAST']
   axes.dates            = ['2026-W37']
   ```

### What the screen does now

**Group Date By is a label, not an aggregation.** Every dashboard query groups on the
trading **day** whatever the pill says; the pill decides which identity columns the
file carries. A week-wise file holds the same block values a block-wise one does,
with a week number beside them.

| Pill | Columns, coarse to fine |
|---|---|
| Block & Date wise | `Date · Time block` |
| Hourly | `Date · Hourly · Time block` |
| Week-wise | `Week · Date · Time block` |
| Month-wise | `Month · Date · Time block` |
| Year-wise | `Year · Date · Time block` |

**One sheet, one row per (day, block), one column per series.** The workbook's
sheet-per-dataset layout is gone: two datasets a user wants to compare now sit on the
same row instead of on two sheets. A series is a dataset *plus its entity*, so one
ticked Plant wise DC becomes 34 columns and one ticked DAM ACP becomes three. The CSV
emits the identical table; its old flat `dataset,label,unit,date,block,value` form is
gone.

```
Week      Date         Time block   Actual Demand (MW)   Weekly Forecast · Day Ahead (MW)
W38 2026  14 Sep 2026           1              4821.30                            4798.20
W38 2026  14 Sep 2026           2              4805.70                            4798.20
```

Other changes in the same batch:

* **No queue.** A file is generated inside the request and downloaded, or refused
  with **413** plus priced narrowings. My Exports is now a **history of
  configurations** — file name, a *View configuration* disclosure, and *Execute*,
  which loads the filters back so the user can press Apply & Generate. Download,
  cancel, retry and also-as are gone.
* **Button reads "Apply & Generate"**; the spinner shows elapsed seconds. Real
  progress is not available for a single request and a percentage would be invented.
* **File layout (orientation toggles) removed** — one canonical layout now.
* **Custom block band removed from the UI.** The server still understands it so
  stored rows replay; the panel does not offer it.
* **From/To block inputs appear only for Individual Blocks.** A named band shows its
  own blocks read-only, and the Time block column then reads `Peak`, not a code.
* **The Filters sheet reports what actually ran**, read from `meta`, not from the
  request. That is the line that used to lie.
* **Dates are real Excel dates** (`dd mmm yyyy`), not text — they were unsortable.

### The roll-ups were removed (Weekly/Monthly Forecast and MAPE)

They first got their own pipeline, so they could stop hijacking the request. Then
PTC looked at what they actually were and removed them outright the same day.

They read the **same stored fields** as the datasets beside them and differed only
in aggregation period:

```python
FORECAST_DAF     value="$forecast"  agg="avg"  match={"type":"DAF"}          # per day
WEEKLY_FORECAST  value="$forecast"  agg="avg"  fixed_grouping="WEEK_WISE"    # per week
```

Nothing is stored as an average anywhere — the average was computed at read time —
so a user who wants the weekly picture averages the daily columns, which are still
on the picker as **Day Ahead / Intra Day Forecast** and **Day Ahead / Intra Day
MAPE**. The catalogue went 52 → 48 datasets.

What went with them:

| Removed | Why it existed |
|---|---|
| `Dataset.fixed_grouping` | only the four roll-ups set it — it *was* the hijack mechanism |
| `grouping.date_key_expr`, `DATE_FORMATS` | bucketing by a period other than the day |
| `grouping.period_key` / `period_day_map` / `days_in_range` | mapping a period back onto its days |
| `planner._split_by_rollup` / `_rollup_expander`, `GroupPlan["post"]` | the separate pipeline and the broadcast |
| `adapters.base.ROLLUP_GROUPING_KEY` | the filter key that selected a period |
| `analytics/coverage.py` + its tests | labelling partially-covered periods |
| `analytics_service.rollup_families`, `_rollup_coverage` | reporting which families were ticked |
| `meta.rollup_families` / `period_coverage` / `partial_periods` | the response fields |
| `Rolled-up columns` / `Partial periods` in the Filters sheet | explaining the repeated columns |
| the client's `Weekly`/`Monthly` badge in the picker | naming the roll-up family |

That also closes the partial-period caveat by deleting it: there is no longer a
period that can be partially covered.

**One deliberate piece of dead code left in place.** `per_forecast_type` had exactly
these four users, so `DemandAdapter.entity = "type"`, `_wants_entity`, the
`series_cardinality` branch and `pivot.ENTITY_LABELS` are now inert. They are kept
because `dedupe_key` includes `type` and because removing them touches the grouping
behaviour of **every** demand dataset for no functional gain. `test_analytics_
adapters_simple.py` pins that no demand dataset is `per_forecast_type` today, so a
future one has to clear the trap deliberately.

**Compatibility.** Saved preferences were already safe — `analytics_preference_
repository.sanitize` drops codes the registry no longer knows. Stored export rows
were not: 13 rows in local Mongo name `WEEKLY_FORECAST` or `MONTHLY_MAPE`, and
replaying one would 422. The client's Execute therefore filters the stored dataset
list against the live catalogue and tells the user what it left out.

### Budget arithmetic changed with it

`date_bucket_count` is now **one bucket per trading day, whatever the grouping** — the
`grouping` argument is accepted and ignored. Two suggestions changed as a result:
`coarser_dates` ("switch to Week-wise") was **removed** because following it now
changes the cell count by nothing, and `fewer_plants` became `fewer_datasets` since
there is no plant picker to narrow. `shorter_range` is new. The practical effect:
a week-wise or month-wise request that used to price at a handful of buckets now
prices at its real size, so some selections that were accepted before are refused.
That is the estimate becoming honest, not a regression.

### Compatibility

`BLOCK_WISE` and `DATE_WISE` still arrive from stored export rows and saved user
preferences. They are canonicalised forward to `BLOCK_DATE_WISE` in
`grouping.canonical`, at the request model's validator and in the client's
`canonicalDateGrouping` — nothing on disk was migrated. The query cache version was
bumped **v7 → v8**: a v7 entry cached under `WEEK_WISE` holds week buckets the new
writers would read as days.

### What this leaves behind

`services/scheduler/app/processors/analytics_export_processor.py` and the four job
routes (`/export/{id}`, `/download`, `/cancel`, `/retry`) are no longer reachable from
the dashboard. They are intact and still tested, because the rows already in Mongo
need them. Delete them once those rows have aged out — not before.

**New file:** `services/processing/app/v2/helpers/analytics_layout.py` — the one table,
shared by both writers. **Deleted:** `client/src/features/analytics/components/DisplayOptions.jsx`.

**Tests:** backend analytics **748 pass**, full `libs/tests` **859 pass / 10 fail** —
the same 10 pre-existing vendor-rot failures (`test_supply_csv_parser.py`,
`test_grid_india_parser.py`, `test_demand_supply_entities.py`). Client `node --test`
**114 pass**. New browser journey `tools/e2e/analytics_layout_journey.mjs`
**18/18 steps**, which drives the real UI and reads the real downloaded workbook —
including a step proving an older history row that names a removed dataset still
Executes. While the roll-ups still existed they were recomputed straight from
`demand_serve` ∪ `demand_serve_historical` (deduped as the pipeline does) and
compared with the API cell by cell: **800 cells, 0 mismatches** — which is the
evidence that the daily columns they were removed in favour of carry the same data.

## 7. REMAINING WORK — the actual to-do list

Ordered by priority. Items marked ✅ are **already done** — do not redo them.

### Already verified — do not repeat

- ✅ **Browser verification was performed**, and is now repeatable — see item 2 below.
  Two committed journeys: `tools/e2e/analytics_journey.mjs` (18 steps) and
  `tools/e2e/analytics_layout_journey.mjs` (17 steps, §6g), both driving a real
  Chromium session as `localdev` and reading the real downloaded workbook.
  The 403s seen in the console are Highcharts CDN scripts, pre-existing on every
  EPMS page and unrelated to this screen, which draws no charts.
- ✅ Queued export driven end to end: 202 → Mongo sweep → 22/22 chunks → downloaded 183KB
  XLSX and opened it. **Historical** — the dashboard stopped queueing on 2026-09-20
  (§6g); this proves the worker, which still serves the rows already in Mongo.
- ✅ Budget 413, capped band, Redis cache hit, disabled-dataset 422, CSV stream, XLSX inline.
- ✅ **The four roll-ups checked against raw Mongo**: 800 cells recomputed from
  `demand_serve` ∪ `demand_serve_historical` and compared with the API, 0 mismatches.

### P1 — correctness

1. ~~Fix the DC/SG loss basis~~ — **closed by PTC decision**: both stay on `volume`. See §6.
   **The one PTC answer that matters now is B2/A8 on the question page** — whether
   PPCLGPP's own generation sits inside the state's load and schedule. `actual` (includes
   it) and `drawal` (excludes it) are both shipped; Final Schedule = Σ SG (includes it).

### P2 — test coverage gaps

2. ✅ **Repeatable e2e journeys exist** — `tools/e2e/analytics_journey.mjs` (18 steps)
   and `tools/e2e/analytics_layout_journey.mjs` (17 steps). Both are Node `.mjs` in
   the house style. ⚠️ They live in the **workspace root** `tools/`, which is not a
   git repository — they are not version-controlled with either repo. Move them into
   `backend/tools/e2e/` if they should ship.
   ⚠️ Note `playwright` was pip-installed straight into `backend/.venv` during earlier
   verification and is **not in any requirements file**; the journeys use the Node
   Playwright instead, which is the existing convention.
3. **Mid-export restart-and-resume is unit-tested only.** Start a large export, kill the
   scheduler mid-run, restart, and confirm from the job document that it resumed at the right
   chunk rather than restarting. The chunk receipts in `parts[]` exist for this.
   *Lower priority since §6g*: the dashboard no longer creates queued jobs, so this
   path only runs for rows already in Mongo.
4. **Cross-user download 403 is unit-tested only.** Needs a second real account to prove the
   authorization boundary live. Same caveat as item 3.
5. **Concurrent-load measurement never run.** Fire N simultaneous analytics queries while
   timing an authenticated bidding call, and record the degradation. Analytics has its own
   Motor client (`processing_analytics`) specifically so it cannot starve the bidding pool —
   but that is designed, not measured.

### P3 — polish / decisions

5b. **Decide when the queue comes out.** `analytics_export_processor.py` and the four
   job routes are unreachable from the dashboard but still serve the queued rows in
   Mongo. Once those age out (72h retention, 87-day row retention), delete the worker,
   the routes, `_enqueue`, and `utils/orientation.js` on the client, which the removed
   File layout toggles were the only consumer of.

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
2. **Indexes — automatic since 2026-09-18, no manual step.** The processing lifespan
   runs `migrations.analytics_indexes_2026_09_17.apply` at start-up, next to the five
   other `ensure_*_indexes` blocks already there. Idempotent, `background=True`, each
   index caught individually, so a restart costs nothing and one bad index cannot block
   boot. Confirm after the restart in step 5:
   `grep "Analytics indexes ensured" <processing log>` → should read **13**.
   Still runnable by hand if you want them in place before the deploy:
   `./.venv/bin/python -m migrations.analytics_indexes_2026_09_17` from the processing
   checkout. Without them `demand_serve` COLLSCANs on both sides — silent, just slow.
3. **nginx** — item 10 in `docs/EPMS-nginx-changes.md`; `nginx -t && systemctl reload nginx`.
   Non-breaking; can ride with items 1/2/6/7/8 in one reload.
4. **Insert the `menus` row BY HAND, then restart the services.** 🔴 A restart does
   *not* create it — see the correction above §7: `seed_menus` returns immediately if
   any menu exists, and `force_reload=True` would delete every menu on the box.

   🪤 **`parent_id` is a STRING, not an ObjectId** — verified against the seeded rows
   (`DSM Overview.parent_id = '68c6e887df51823617d696a6'`, a str). An ObjectId here
   builds no tree and the item never renders. `menu_roles.menu_id` is a string too.
   The statement below resolves the parent itself, so neither can be got wrong:

   ```javascript
   // auth_db — must return 0, or you are about to create a duplicate sidebar entry
   db.menus.countDocuments({ path: "/reports/analytics-dashboard" })

   db.menus.insertOne({
     name: "Reports Dashboard",                 // the NAME mints reports_dashboard:read (§6e)
     icon: "📉",
     parent_id: db.menus.findOne({ path: "/reports", is_deleted: false })._id.toString(),
     path: "/reports/analytics-dashboard",      // unchanged — RouteProtection bypasses it
     order: 2,
     is_active: true,
     is_deleted: false,
     created_at: new Date(),
     updated_at: new Date()
   })
   ```

   Field shape verified field-by-field against a working row: same 10 keys, same types.
   Check `order` against the other children of Reports on the box if the position in the
   sidebar matters.

   Then restart auth, then processing (which seeds the five `analytics_band_*`
   parameters and ensures the analytics indexes), then scheduler.
5. **Grant the menu to roles** — Admin → Roles → menu assignment → tick *Reports →
   Reports Dashboard* (`read`) for every role that should see the screen. The grant
   runs `_sync_role_permissions`, which mints `reports_dashboard:read` from the row's
   NAME (§6e); `PAGE_GRANTS` also still accepts `analytics_dashboard:*`, so a box seeded
   under the old name keeps working. Users pick the grant up on their **next login**
   (it lives in the token).
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
- **Plant universes differ**: `supply_serve` 34 vs `plant_master` 35 (and the removed
  `serve_dam_scheduling` had 53). The plant selector reads the dataset's own collection.
- **HPDAM is real but sparse** — clears in ~0.6% of blocks, always at the ₹20,000 cap.
  Uncleared blocks are `null`.
- **Market-wide "Traded" IS `exchange_serve.acv`** — the same field as ACV, not a second dataset.
- **`*_bid_capture` is NOT a dashboard source** (~542k docs per date); it is the internal
  pre-submission bid ladder.
- **The archival leaves duplicates** — the same document sits in both `exchange_serve` and
  `exchange_serve_historical`, so `$unionWith` double-counts without dedup. Handled.
- `supply_serve` carries revisions and needs latest-per-key collapsing.

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

# backend — analytics only
cd backend && ./.venv/bin/python -m pytest libs/tests -k analytics -q

# client
cd client && node --test $(find src -name "*.test.js" | grep -v node_modules)
cd client && npm run build      # npm run lint is DEAD, do not trust it

# browser journeys (stack must be up; client on :3005)
cd ~/Desktop/infiscopelabs && node tools/e2e/analytics_journey.mjs
cd ~/Desktop/infiscopelabs && node tools/e2e/analytics_layout_journey.mjs
#   E2E_HEADED=1 to watch it; artifacts land in /tmp/epms-analytics-*-e2e

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
