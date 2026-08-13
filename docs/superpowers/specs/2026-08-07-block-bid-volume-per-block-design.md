# Block Bid Volume per 15-minute block (DAM)

Date: 2026-08-07
Status: approved, ready to implement

## Problem

On `dam/dam-single-bid`, the `Block Bid Volume (MW) →` row reads `0` for every one of the
96 blocks, even when a block bid has been submitted to the exchange and is visible on
`dam/block-bid-approval`.

Root cause is a key mismatch, not missing data. Block bids are stored in
`block_bid_current_data` with `time_block` as the raw span string the trader entered:

```python
# services/processing/app/v2/services/block_bid_services.py:79-86
time_block = f"{request['from'][i]}-{request['to'][i]}"   # e.g. "07:00-11:00"
```

The table aggregation groups by that span string
(`block_bid_current_data_repository.py:267-272`), but the service then looks the result up
with a **15-minute** key:

```python
# services/processing/app/v2/services/new_dam_bidding_services.py:1417-1418
time_block_str = get_fifteen_full_time_block_id(actual_block_no)   # "07:00-07:15"
totalBlockBid  = float(approved_block_bid_quantities.get(time_block_str, 0))
```

`"07:00-11:00" != "07:00-07:15"`, so a multi-block bid contributes to no block at all.
Only a bid that happens to span exactly one 15-minute slot (`"19:00-19:15"`) ever matches.

The per-row quantity maths is already correct — `number_of_bids × quantum` exists at
`block_bid_current_data_repository.py:253-264`. The only missing piece is expanding a
span into the blocks it covers.

## Solution

Expand each bid's `from`/`to` range into the 15-minute block labels it covers, and
accumulate. One function changes:
`BlockBidCurrentDataRepository.get_approved_block_bid_quantities_by_time_block`.

The return contract stays `Dict[str, float]` keyed by 15-minute `"HH:MM-HH:MM"` labels, so
`new_dam_bidding_services.py:1417` and every client file stay byte-identical. Only the
dict's contents change.

### Block numbering

The day is 96 blocks of 15 minutes. Block `n` covers `(n-1)×15` to `n×15` minutes after
midnight. A range is **start-inclusive, end-exclusive**:

```
start_block = from_minutes // 15 + 1
end_block   = to_minutes   // 15
```

| stored span     | blocks filled | note                       |
| --------------- | ------------- | -------------------------- |
| `07:00-11:00`   | 29 – 44       | 16 blocks = 4 h            |
| `19:00-19:15`   | 77            | single block               |
| `00:00-00:15`   | 1             | first block                |
| `23:45-00:00`   | 96            | `00:00` as end = midnight  |
| `00:00-24:00`   | 1 – 96        | whole day                  |
| `22:00-02:00`   | 89 – 96, 1 – 8 | wraps midnight            |

Labels are emitted in exactly the format the consuming service builds via
`get_fifteen_full_time_block_id` (`new_dam_bidding_services.py:1061-1068`), including its
special case that block 96 ends `"00:00"` rather than `"24:00"`.

### Aggregation change

`$group` moves from `"$time_block"` to `{from, to, time_block}`, and `$project` carries
`from` / `to` through. The Python layer then expands each group into its block labels and
accumulates with `+=`, so **overlapping bids sum**: if `07:00-11:00` (-30) and
`10:00-11:00` (-15) are both submitted, blocks 41–44 read -45 and blocks 29–40 read -30.

### Fallback

Rows missing `from` / `to` fall back to splitting `time_block` on `-`, mirroring what the
client already does at `BlockBidApprovePage.jsx:423-425`. Rows that parse to nothing usable
are skipped with a warning rather than throwing — this endpoint feeds a whole page.

## Decisions

| decision            | value                                                        |
| ------------------- | ------------------------------------------------------------ |
| fix site            | backend repository, single function                          |
| scope               | DAM only, `totalBlockBid` only                               |
| statuses            | `Submitted` / `submitted` — unchanged from today             |
| market filter       | `market_type == "DAM"` — unchanged from today                |
| range convention    | start-inclusive, end-exclusive                               |
| value per row       | `number_of_bids × quantum` — unchanged from today            |
| overlapping bids    | sum                                                          |
| revisions           | keep summing every matched row — unchanged from today        |
| `Reserved For →`    | out of scope, separate ticket                                |

### Why DAM only is sufficient

`dam/dam-single-bid` calls `API_ENDPOINTS.dayAhead.singleBidTable` unconditionally
(`useDAMSingleBid.js:556`), which resolves to the DAM base `/api/v2/dam-bidding/…`. The
`gdamDayAhead` / `hpdamDayAhead` endpoint groups exist in `apiEndpoints.js:439,468` but are
only reachable through `getDayAheadMarketApis()`, which the feature uses for
`approvalRequestList`, `submitForApproval`, `bidCreation` and `submitForPreExchange` — never
`singleBidTable` or `singleBidGraph`. So `/gdam-bidding/single-bid-table` and
`/hpdam-bidding/single-bid-table` are called by nothing in the client, and their identical
copies of this bug are unreachable code paths today. The `GDAM Forecasted Price` and
`HPDAM Forecasted Price` rows on the page come from the same single DAM payload.

## Known consequence: NOC headroom tightens

`useDAMSingleBid.js:3414-3433` subtracts `totalBlockBid` from sell and buy NOC:

```js
const availableSellNOC = sellNOC - totalBlockBid - total_reserved_bid;
const blockBidExceedsSellNOC = (totalBlockBid + total_reserved_bid) > sellNOC;
```

Today it subtracts `0`, so available NOC is overstated. After this fix, blocks covered by a
submitted block bid lose that much headroom, and single bids that pass validation today may
be capped or rejected. That is the behaviour the vendor clearly intended, but it is a
visible change on the bidding screen — not merely a new row of numbers. Flag it to whoever
runs UAT.

## Out of scope (raise separately)

1. **`Reserved For →` row** — `get_approved_reserved_block_bid_quantities_by_time_block`
   (`:311`) has the identical span-lookup bug. It also has **no `market_type` filter**
   (`:324-328`), matching every market on the date, so expanding its ranges without adding
   that filter would let RTM/GRTM reserved volume eat DAM's NOC headroom. Two behaviour
   changes, its own blast radius, its own UAT pass.
2. **Chart `Block Bid Qty` series** — `block_bid_approved_volume` is a hardcoded
   `{"$literal": "0"}` in the graph pipeline (`new_dam_bidding_services.py:880-887`). The
   graph endpoint never reads block bids at all. Separate pipeline, separate job.
3. **GDAM / HPDAM block bids** — `useBlockBidPage.js:1302` takes `market_type` per row, so
   traders can raise GDAM/HPDAM block bids, and UAT data contains them. They will not appear
   in this row. In local UAT this is moot (none are `Submitted`; all lapsed), but whether the
   day-ahead family should roll up into one row is a domain call for PTC.
4. **Revision de-duplication** — no `revision_no` de-dup in the aggregation, so if several
   `Submitted` rows share a `(trade_session, time_block)` lineage they all count. Unconfirmed
   on real data; deliberately left unchanged here.

## Verification

Unit tests on the expansion helpers (no Mongo needed): `07:00-11:00 → 29-44`, single block,
block 96, full day, midnight wrap, `from`/`to` missing, zero-length range, malformed input.

Then the real check: start the local stack, confirm a `Submitted` DAM block bid spanning
multiple blocks exists on the target date, and read the actual `Block Bid Volume (MW) →` row
in the browser on `dam/dam-single-bid`. Green tests are not evidence the page renders.
