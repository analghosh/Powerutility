# Power Exchange Parameters — Test Cases
**Service:** processing (port 8003)
**Collection:** `power_exchange_parameters` (MongoDB, epms db)

---

## Setup

Get a token first from the auth service:
```
POST http://127.0.0.1:8001/api/v1/auth/login
Body: { "username": "...", "password": "..." }
```

Copy the `access_token` and set it as Bearer Token in Postman for all requests below.

Base URL for all test cases: `http://127.0.0.1:8003/api/v2/power-exchange-parameters`

**Note:** Make sure the collection is empty and the server is freshly restarted before running TC-01. The first GET call auto-seeds all 12 default records.

---

## Valid Values Reference

| Field | Accepted Values |
|---|---|
| exchange | iex, hpx, pxil |
| market | rtm, dam, gdam, hpdam |
| bid_type | single_bid, block_bid, both |
| ocf_standalone | ocf, standalone |
| allocation_percent | 0 to 100 (numeric) |

**Business rule:** When market is `rtm`, ocf_standalone must always be `standalone`.

---

## TC-01 — Fetch all records, no filters

**Method:** GET  
**URL:** `/api/v2/power-exchange-parameters`

Expected: 200, total = 12 (3 exchanges x 4 markets, auto-seeded on first call)

---

## TC-02 — Filter by exchange

**Method:** GET  
**URL:** `/api/v2/power-exchange-parameters?exchange=iex`

Expected: 200, total = 4 (all 4 markets for iex only)

---

## TC-03 — Filter by RTM market

**Method:** GET  
**URL:** `/api/v2/power-exchange-parameters?market=rtm`

Expected: 200, total = 3 (one rtm record per exchange)

---

## TC-04 — Filter by DAM (should return dam + gdam + hpdam)

**Method:** GET  
**URL:** `/api/v2/power-exchange-parameters?market=dam`

Expected: 200, total = 9 (dam, gdam, hpdam records across all 3 exchanges)

---

## TC-05 — Combine exchange + market filter

**Method:** GET  
**URL:** `/api/v2/power-exchange-parameters?exchange=iex&market=dam`

Expected: 200, total = 3 (dam + gdam + hpdam, iex only)

---

## TC-06 — Invalid exchange value

**Method:** GET  
**URL:** `/api/v2/power-exchange-parameters?exchange=xyz`

Expected: 400
```json
{ "detail": "Invalid exchange filter 'xyz'. Must be one of {'hpx', 'pxil', 'iex'}" }
```

---

## TC-07 — IDAM market not accepted (removed from valid set)

**Method:** GET  
**URL:** `/api/v2/power-exchange-parameters?market=idam`

Expected: 400
```json
{ "detail": "Invalid market filter 'idam'. Must be 'rtm' or 'dam'" }
```

---

## TC-08 — RTM + ocf should fail validation

**Method:** POST  
**URL:** `/api/v2/power-exchange-parameters`  
**Body:**
```json
{
  "exchange": "iex",
  "market": "rtm",
  "ocf_standalone": "ocf"
}
```

Expected: 400
```json
{ "detail": "ocf_standalone must be 'standalone' when market is 'rtm'" }
```

---

## TC-09 — RTM + standalone should pass

**Method:** POST  
**URL:** `/api/v2/power-exchange-parameters`  
**Body:**
```json
{
  "exchange": "iex",
  "market": "rtm",
  "ocf_standalone": "standalone",
  "bid_type": "single_bid"
}
```

Expected: 400 (duplicate, since iex/rtm is already seeded)  
If you first delete the iex/rtm record → Expected: 201

---

## TC-10 — Allocation percent out of range

**Method:** POST  
**URL:** `/api/v2/power-exchange-parameters`  
**Body:**
```json
{
  "exchange": "iex",
  "market": "dam",
  "allocation_percent": 150
}
```

Expected: 400
```json
{ "detail": "allocation_percent must be between 0 and 100" }
```

---

## TC-11 — Invalid bid_type

**Method:** POST  
**URL:** `/api/v2/power-exchange-parameters`  
**Body:**
```json
{
  "exchange": "iex",
  "market": "dam",
  "bid_type": "xyz_bid"
}
```

Expected: 400
```json
{ "detail": "Invalid bid_type 'xyz_bid'. Must be one of {'single_bid', 'block_bid', 'both'}" }
```

---

## TC-12 — Duplicate exchange + market combo

**Method:** POST  
**URL:** `/api/v2/power-exchange-parameters`  
**Body:**
```json
{
  "exchange": "iex",
  "market": "rtm",
  "ocf_standalone": "standalone"
}
```

Pre-condition: iex/rtm must already exist (from TC-01 seeding)  
Expected: 400
```json
{ "detail": "Configuration for exchange 'iex' and market 'rtm' already exists" }
```

---

## TC-13 — Get record by ID

**Method:** GET  
**URL:** `/api/v2/power-exchange-parameters/{id}`

Replace `{id}` with an actual `_id` from the TC-01 response.

Expected: 200, returns that single document

---

## TC-14 — Update a record

**Method:** PUT  
**URL:** `/api/v2/power-exchange-parameters/{id}`  
**Body:**
```json
{
  "bid_type": "block_bid",
  "allocation_percent": 75
}
```

Expected: 200, returned doc has updated `bid_type` and `allocation_percent` with new `updated_at`

---

## TC-15 — Soft delete a record

**Method:** DELETE  
**URL:** `/api/v2/power-exchange-parameters/{id}`

Expected: 204, no body returned  
Verify in MongoDB Compass: `is_deleted` field on that document should now be `true`

---

## Unit Test Results

All 15 test cases have corresponding unit tests in:  
`services/processing/app/tests/services/test_power_exchange_parameter_service.py`

Run them with:
```bash
python -m pytest services/processing/app/tests/services/test_power_exchange_parameter_service.py -v
```

Output: `15 passed in 0.94s`
