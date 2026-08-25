# EPMS — VAPT findings, verified against the code

**Reference document. Written 2026-08-13/14.**

Source report: `~/Downloads/PTC-INDIA_Appsec_V1_Report.pdf` — *Application Security Report*,
InfoHash Private Limited, prepared for PTC India Limited.
Target: `epm.ptcindia.com`. Assessed **23 May 2026**, published **8 June 2026**.
19 findings: 6 High, 6 Medium, 7 Low, 0 Critical.

Verified against: backend `feat/notifications` @ `8b54924` with spot-checks on the vendor
`UAT` branch, client @ `987c577`. No network requests were made to PTC infrastructure.
The remediation has since moved to `fix/vapt-remediation` in both repos; see §3.1 for the
current status and hashes.

Published triage page (same content, prettier):
<https://claude.ai/code/artifact/c700859a-e24d-4b60-bbdb-f73733e3f697>

---

## 1. Headline

- **18 of 19 findings describe a real defect.** None was fully refuted.
- **Zero have been fixed.** Last vendor commit on any branch is 26 June 2026; the assessment
  was 23 May. Nothing in this report has been actioned.
- **9 findings are rated too low** by the report, 2 too high.
- **10 further issues exist that the test never covered** — see §6.

### The single most important sentence

> **Findings 1–6 are not six problems. They are one problem, sampled six times.**

The processing service authenticates every request and authorizes none of them. There are
**72 state-changing routes across 45 route files**, and the only thing in front of them is
"the caller holds a valid JWT." The pentester hit five. Patching those five leaves ~67
identical holes, including bidding, scheduling and cron orchestration.

---

## 2. Authentication vs authorization — the distinction everything hinges on

These are two different checks, and EPMS only does the first.

| | Question | Does EPMS do it? |
|---|---|---|
| **Authentication** | Are you logged in? | ✅ Yes, on every request |
| **Authorization** | Are you *allowed to do this*? | ❌ No, nowhere in processing |

Proof authentication works — `services/processing/app/main.py`:

```python
if not auth_header:
    return JSONResponse(status_code=401, content={"detail": "Authorization header required"})
```

**Mental model:** a building with a locked front door and no locks on any interior door.
A stranger gets nothing. Any badge-holder reaches every room, including the vault.

So the accurate phrasing is **"authenticated but not authorized"**, *not* "the APIs are
unauthenticated". The exceptions — where there is genuinely no front door — are the
scheduler service and the ingestion cron routes (§5).

Why it still matters: ~816 accounts exist. Every one is a badge into every room. MFA does not
work (F19) and there is no login rate limiting, so one phished password reaches everything.

---

## 3. Verdicts — all 19

`CONFIRMED` = defect exists as described. `PARTIAL` = defect is real, report's mechanism or
severity is wrong. `NOT IN CODE` = infrastructure, not settleable from the repo.

| ID | Verdict | Report → Verified | What the check changed |
|---|---|---|---|
| F1 | CONFIRMED | High → High | A check *does* exist; it reads a hardcoded allow-list. The report's implied fix changes nothing. |
| F2 | CONFIRMED | High → **Critical** | GET returns *decrypted* exchange API keys. Two extra mutating routes the report missed. |
| F3 | CONFIRMED | High → **FIXED** | Delete performed no referential check; orphaned plant records. Authorization closed by `06f465f`; the referential half closed by `bb85d84` — delete now returns **409** naming the count of plants still assigned. Verified live: group `B` (12 plants) refused, unreferenced group deleted 204. |
| F4 | CONFIRMED | High → High | Scope understated — ungated bulk `/upload` and full `/download_csv` unlisted. |
| F5 | CONFIRMED | High → **ACCEPTED (closed)** | Defect 1 (may you approve at all) closed by F6. Defect 2 (self-approval) **accepted by the client 2026-08-18** as a deliberate business trade. See §4.4. |
| F6 | CONFIRMED | High → **Critical** | Quantified: 72 write routes, 3 dead authz libraries, 1 of 46 routers guarded. |
| F7 | CONFIRMED | Medium → **High** | The refresh token survives too, so the window is 7 days, not 30 minutes. |
| F8 | CONFIRMED | Medium → Medium | Worse mechanism than described; but `is_active=false` does take effect immediately. |
| F9 | ⚠️ partial — bounded | Idle sign-out after 30 minutes, which now **revokes both tokens server-side** (client `e60ac55` + `5f3f607`, backend `d6b8324`), and the refresh-token ceiling is cut from **7 days to 24 hours** (`JWT_REFRESH_TOKEN_EXPIRE_DAYS`). Residual, stated plainly: there is no server-side inactivity tracking, so if the browser never runs the timer — tab closed, crash, laptop slept — nothing is revoked and a token copied beforehand stays usable until that 24-hour ceiling. A session-idle key gated on a new token claim is designed and held in reserve if PTC's auditor requires server-side enforcement |
| F10 | CONFIRMED | Medium → **Med–High** | Renaming the seeded admin re-seeds a fresh super-admin on restart. (Severity revised down from High — see §4.5.) |
| F11 | PARTIAL | Medium → **ACCEPTED (closed)** | Not an exposed dashboard — the deployment root, holding the JWT signing key. **Accepted by the client 2026-08-19** on the basis that Jenkins requires a login. Recorded risk: that boundary has failed before (CVE-2024-23897 was an unauthenticated file read used in the wild to pull `secrets/` and `.env` off controllers), and a symmetric `JWT_SECRET_KEY` there means forged super-admin tokens without touching EPMS at all. Cheap mitigation offered and not taken: an nginx IP allowlist on `/jenkins/`. |
| F12 | CONFIRMED | Medium → **Low** | Corrected 2026-08-24: `ptcindia.com` publishes **no DMARC record at all**, so nothing is inherited. Still Low — the app sends no mail. PTC's DNS team owns it. |
| F13 | NOT IN CODE | Low → Low | Banner real; "EOL" is not — Ubuntu backports into a frozen version string. |
| F14 | PARTIAL | Low → **High** | Not a soft delete. No delete at all — the coroutine is never awaited. |
| F15 | PARTIAL | Low → Low | The escalation warning is provably false here; the real risk on that endpoint is elsewhere. |
| F16 | CONFIRMED | Low → Low | **Subresource Integrity, not CORS** — an earlier revision of this table mislabelled it. Seven unpinned third-party tags in `client/index.html`. Untouched. |
| F17 | PARTIAL | Low → **FIXED (API) / open (vhost)** | Headline false for the API. `86265e9` splits the policy: API responses now get `default-src 'none'` (no script-src at all), docs pages get a Swagger-shaped policy — **`unsafe-eval` removed from both**. Verified in a real browser: Swagger UI renders all 281 operations with no CSP violation from its own code. ⚠️ The static SPA vhost is nginx and still needs its own CSP — see §F17 note. |
| F18 | PARTIAL | Low → **Info** | Not a vulnerability. Its only weight is as a map for F6. |
| F19 | CONFIRMED | Low → **High** | Two independent breaks. The screen tells admins MFA is on for users who have none. |

---

### 3.1 Status — 2026-08-24

Verified against the code at the head of `fix/vapt-remediation`, not against this
document's prose. **Nothing below is merged**: backend PR #294 and client PR #147 are the
only place any of it exists, so none of it protects the running platform yet.

Commit hashes elsewhere in this document were rewritten when the work was rebased onto
`fix/vapt-remediation`; the hashes here are the current ones.

| ID | Status | Where |
|---|---|---|
| F1 | ✅ fixed | `27c5f44` — the hardcoded page→roles dict is gone; real permissions are checked |
| F2 | ✅ fixed | `247e6d5` (disclosure) + `06f465f` (the write routes). **Rotate the IEX credentials regardless** |
| F3 | ✅ fixed | `06f465f` (authorization) + `bb85d84` (409 when plants still reference the group) |
| F4 | ✅ fixed | `06f465f`, including the unlisted bulk upload and CSV export |
| F5 | ⚖️ accepted | Client decision 2026-08-18. No code change, none planned — §4.4 |
| F6 | ✅ fixed | `06f465f` + the page-permission model. 113 of 113 write routes guarded, `AUTHZ_MODE` defaults to enforce |
| F7 | ✅ fixed | `296a924` (revocation on reset/role change/deactivation) + `d6b8324` (the refresh retires the token it replaces) |
| F8 | ✅ fixed | `296a924` + `fd53d33`; revocation stays immediate under local validation |
| F9 | ⚠️ partial — bounded | Idle sign-out after 30 minutes with **both tokens revoked server-side**, plus a refresh ceiling cut from 7 days to 24 hours. No server-side inactivity tracking — see §3.1 |
| F10 | ✅ fixed | `3d6998c` drops `username` from the update model. Residuals: the repository's `_PRIVILEGED_FIELDS` filter (vendor) still omits `username`, the seeder re-mints an admin whenever the name is free, and `ADMIN_INITIAL_PASSWORD` defaults to `""` with only a log warning |
| F11 | ⚖️ accepted | Client decision 2026-08-19. Rotate everything Jenkins holds |
| F12 | ⬜ open | Not a repo change. `ptcindia.com` publishes no DMARC record; PTC's DNS team must publish one |
| F13 | ⬜ open | `server_tokens off` is written and waiting in `docs/nginx/application-proposed.conf`; needs a deploy |
| F14 | ✅ fixed | `3d6998c` — the missing `await` |
| F15 | ✅ fixed | `5ddac4a` — `profile` is a typed model, so unknown keys are dropped instead of stored |
| F16 | ⬜ open | Subresource Integrity, **not** CORS. `client/index.html` still loads seven unpinned third-party tags. Fix by deleting them — but `ForecastChart.jsx:14` reads `window.Highcharts`, so that component moves to the npm import first |
| F17 | ✅ API / ⬜ vhost | `86265e9` split the policy and dropped `unsafe-eval`; `00370b2` gave the scheduler's own HTML pages a document policy. The SPA vhost still needs its CSP — nginx |
| F18 | ℹ️ informational | Not a vulnerability. Closed by reply |
| F19 | ✅ fixed | `335527f` + client `00b6cb0`/`6323fe4`. **Ships switched off** (`d89d41b`, `1b7b13f`): set `AUTHENTICATOR_MFA_ENABLED=true` and `ENCRYPTION_KEY` in the deployment env or nobody can enrol |

Two regressions this programme introduced were found and closed on 2026-08-24: the
scheduler's operator UI was blanked by the new API CSP (`00370b2`) and its own login page
returned 401 (`2e1c73e`). Both were verified in a browser before and after.


## 4. Fix order

Ordered by verified severity and by what unblocks what. Effort covers the code change only.

**Status, 2026-08-16** — three items shipped on backend `feat/notifications` (since rebased
onto `fix/vapt-remediation`). The vendor
has still fixed nothing; last vendor commit on any branch is 26 June 2026.

| Commit | Closes | |
|---|---|---|
| `247e6d5` | F2 — disclosure half only | `get_all()` returns `API_KEY_MASK` + `has_api_key`; `update()` drops the mask so an unchanged edit keeps the stored key; `create()` 400s on it. **The write routes are still unguarded — that half is F6; see §4.6 for what is actually reachable through them.** |
| `3d6998c` | F14, F10 | `await` on `delete_user`; `username` gone from `UserUpdateRequest` (extra field, so old clients still get 200, not 422) |
| `3a960de` | — | `superadmin` gets the `*` wildcard; token refresh stops minting a permission-less session. Both vendor bugs, both harmless until something checked permissions |
| `06f465f` | **F6**, F2 (second half), F3, F4, and the unguarded `submit-to-exchange` | every processing route now requires a permission; 285 routes, 279 guarded, 108 of 108 writes. Provisional matrix, `AUTHZ_MODE=warn` kill switch. **See `EPMS-F6-authorization.md`** |

Verified live against the local stack with curl, Mongo and Playwright, and covered by
`libs/tests/test_exchange_credential_exposure.py` and
`libs/tests/test_user_management_routes.py` (the latter fails 5/6 at `b2f9d19`).
Full-suite delta 425 → 440 passed, no new failures or errors.

⚠️ The mask is dots, not asterisks, and must stay that way: the client filters the
api_key field through `/[^A-Za-z0-9._-]/g` and validates against the same set, so an
asterisk mask sanitises to `""` on load and the Edit Credential form then refuses every
save with "API Key is required". A test pins this.

Known UX consequence: the "Reveal API Key" and "View credential details" password
prompts now reveal the mask. That prompt was always theatre — the plaintext had already
been delivered to the browser — so the screen no longer promises what it cannot do. To
change a key an operator now types the new one.

### P0 — now

| ID | Issue | Effort |
|---|---|---|
| F2 | Exchange API keys returned decrypted to any logged-in user; `server_url` rewritable | S + M |
| F6 | Processing performs authentication but no authorization — 72 write routes | L |
| F11 | ~~Jenkins on the public app vhost~~ — **accepted, not fixed** (client decision, 2026-08-19) | — |
| — | Scheduler ships with authentication commented out *(not in report)* | S |
| — | Ingestion `/cron/trigger-all` is anonymous *(not in report)* | S |
| — | `block-bid/submit-to-exchange` unguarded — places real IEX orders *(not in report)* | S |

### P1 — this month

| ID | Issue | Effort |
|---|---|---|
| F14 | User deletion is a silent no-op — one missing `await` | S |
| F5 | ~~A maker can approve their own request~~ — **accepted, not fixed** (client decision, 2026-08-18) | — |
| F19 | MFA checkbox writes a field MFA never reads; enrolment screen unreachable | M |
| F10 | Renaming the seeded admin mints a second super-admin on the next restart | S |
| F7 | Admin password reset revokes nothing; the 7-day refresh token survives | M |
| F1 | Upload authorization resolves against a hardcoded dict granting `operator` | M |
| F4 | Master plant CRUD unguarded — plus unlisted bulk upload and full CSV export | S |
| F3 | ~~Price group CRUD unguarded; delete orphans referencing plants~~ — **fixed** (`06f465f`, `bb85d84`) | — |

### P2 — next quarter

F9 (no server-side session; the 30-minute idle cap added 2026-08-21 is enforced in the browser, not on the server) ·
F16 (Subresource Integrity — delete the CDN tags; `ForecastChart.jsx:14` reads `window.Highcharts`, so it moves to the npm import first) ·
F17 — **API half fixed**; the static nginx vhost still needs a CSP. The production bundle was checked and does **not** need `unsafe-eval`: the only three `Function("return this")` hits in `client/dist` are
try/catch-guarded `globalThis` polyfills, unreachable in a browser where `self` exists. Suggested vhost
policy, to go in alongside the `X-Real-IP` change:

```nginx
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;
```

`style-src 'unsafe-inline'` stays because the charting libraries set inline styles at runtime.
(F8 was closed by `296a924` + `fd53d33`.)

### P3 — document or close

F15 (typed profile model) · F13 (`server_tokens off`) · F12 (verify org-domain DMARC first) ·
F18 (not a vulnerability)

### 4.1 Ship the cheap tail first

These are all **S**, independent, and **none depends on the permissions programme**:

- ✅ the missing `await` on delete (one word) — `3d6998c`
- ✅ drop `username` from `UserUpdateRequest` (one field) — `3d6998c`
- ✅ mask `api_key` in `IexApiDetailService.get_all()` — `247e6d5`
- ⚖️ the self-approval guard (one line, ×6 handlers) — **decided 2026-08-18: accepted, not
  built.** See §4.4
- ✅ restore scheduler authentication middleware — `9699fec`
- ✅ add authentication to ingestion `/cron/*` — `27c5f44`

Hours, not weeks. Cheapest risk reduction available, and it de-risks the programme by
shrinking what stays broken if the catalog work slips.

---

## 4.2 F1 — the upload check exists, it just reads a hardcoded list

`services/ingestion/app/services/auth_client.py`:

```python
async def check_page_access(self, user_roles, page_name, token) -> bool:
    """Check if user has access to a specific page based on roles."""
    page_permissions = {
        "Data Ingestion":        ["admin", "operator"],
        "Provider Management":   ["admin", "config_manager"],
        "System Administration": ["admin"],
        "User Management":       ["admin"],
        "Reports":               ["admin", "data_analyst", "manager"],
    }
    required_roles = page_permissions.get(page_name, ["admin"])
    return any(role in user_roles for role in required_roles)
```

**Analogy:** a security guard who *does* check — against a laminated card taped to his desk,
never the employee database. The card says *Data Ingestion — admin, operator*.

Four consequences:

1. **Operator can upload.** It's on the card. (The pentester's finding.)
2. **Revoking access does nothing.** Remove "Data Ingestion" from operator in Role Management
   — the menu disappears, the upload still works.
3. **New roles are permanently locked out.** Grant a new role everything; it still gets 403.
   Only a source edit and redeploy can add it.
4. **`superadmin` is denied.** The list is `["admin","operator"]`; a `superadmin` role matches
   neither.

Two further details: the `token` argument is accepted and **never used** — no DB read, no
service call, despite the comment saying "Check page access via auth service". And a **second
copy of the dict** twenty lines below (`get_user_accessible_pages`) has *different* contents
(`data_analyst` included), so the "what can I see" list and the enforcement disagree.

**Fix implication:** the report says "implement server-side authorization checks". One
already exists on every route. Adding another changes nothing — the check's *data source* is
wrong. Replace it with the real permission list (already in the token), delete both dicts,
split read from write. Blocked on a `data_ingestion:*` permission existing at all.

---

## 4.3 F3 / F4 — price groups and master plants

FastAPI gives two places to attach a guard. Both are empty:

```python
# 1 — on the router, guards every route in the file
router = APIRouter(prefix="/master_plants", tags=["Master Plants V2"])   # no dependencies=[...]

# 2 — when included into the app
v2_app.include_router(master_plants_routes.router)                       # no dependencies=[...]
```

Each route then attaches exactly two things, neither of which is a lock:

```python
user_context: Dict[str, Any] = Depends(get_user_context),   # reads WHO you are — never refuses
audit_logger: AuditLogger    = Depends(get_audit_logger)    # writes a log line
```

**Why these records are not trivia.** A plant record holds `VC_price` (variable cost),
`FC_price`, `PX_price`, `ramping`, `volume`, `dispatch_type`, `min_up_time`, `price_group`.
These are the inputs to merit order and therefore to **what EPMS bids into the market**.
Change `VC_price` and EPMS bids differently on the next cycle — correctly, using numbers
someone swapped underneath it. Price groups are the bands every plant references, so editing
one band moves every plant pointing at it.

The change *is* logged — as `action: master_plant_update, success: true`. Indistinguishable
from routine maintenance. No approval step, no alert, no before/after diff.

**Missed by the report:** `POST /master_plants/upload` (bulk file import, rewrites plant data
wholesale — strictly worse than the single-record create they did list) and
`GET /master_plants/download_csv` (full portfolio cost structure in one file).

Also: price-group `DELETE` has no referential check — deleting a group orphans every plant
pointing at it. Data-integrity bug independent of the security issue.

---

## 4.4 F5 — maker-checker, and why the permission fix will not close it

F5 bundles **two independent defects**:

| | Control | Question |
|---|---|---|
| 1 | Authorization (RBAC) | May you approve *at all*? |
| 2 | Separation of duties | May you approve *your own* request? |

Defect 1 is closed by the F6 programme. **Defect 2 is not, and cannot be** — RBAC grants
capabilities to a person; it has no vocabulary for "not on a record you created."

> ### ⚖️ Status: ACCEPTED — closed 2026-08-18
>
> The client reviewed this and decided self-approval is acceptable for EPMS. **No code change
> was made, and none is planned.** F5 is closed as an accepted business trade, not as a fixed
> defect.
>
> The distinction that matters to an auditor: before this decision, self-approval was an
> **accident** — nobody wrote the check, as the code below proves. It is now a **documented
> decision**. That is the entire substance of what changed; the behaviour is identical.
>
> What remains true and should be stated plainly to anyone reviewing EPMS's controls:
> a single account can create a bid, approve it, and submit it to the exchange, and every
> step is recorded in the audit log as legitimate. The audit trail shows **who** acted at each
> step, so a reviewer reading the log can still see that the maker and the checker were the
> same person — but nothing surfaces it, and nobody is prompted to look.
>
> If that ever needs revisiting, the fix has not moved: one comparison in `approve()`, and both
> batch paths funnel through it. A lighter option that preserves the decision while making it
> visible is to stamp `self_approved: true` on the record and surface it in a review report.

After a perfect F6 rollout: Alice (trader) tries to approve → 403 ✅. Bob (checker) creates a
bid and approves it himself → 200 ❌.

The code proves it was an omission. Same file, same class, ~170 lines apart:

```python
# stage_edit() — EDITING an approval
if approval["requested_by"] != user_ctx.get("user_id"):
    raise PermissionError("Only the requester can edit this approval")

# approve() — APPROVING it
if not approval:                     raise ValueError(...)
if approval["status"] != "PENDING":  return {"message": "Request already processed"}
# ...straight to writing APPROVED. No identity comparison at all.
```

`requested_by` *is* read in `approve()` — only to copy into `created_by` on the upsert.

Same shape on `block-bid/update-status` and `submit-pre-exchange-data` for all four markets.

**Fix:** one comparison in `approve()`; both batch paths funnel through it, so it covers
`/approve/{id}`, `/approve-batch` and `/approve-all`.

```python
if str(approval.get("requested_by")) == str(approver_ctx.get("user_id")):
    raise PermissionError("Maker cannot approve own request")
```

### Is self-approval necessarily a bug? — open PTC decision

**No, not necessarily.** SoD is a business control, not a law of computing. If PTC decides
one person may submit and approve, the code matches policy and there is no defect. Three
qualifications:

1. **It's PTC's call**, not the vendor's and not ours. Our digest of the handover pack records
   a maker–checker workflow as a documented Phase-I BRD deliverable — pull the exact wording
   before concluding it's fine.
2. **Today it is not a decision, it's an accident** (the guard exists on edit, absent on
   approve). "We assessed and accepted this" and "we didn't notice" produce very different
   audit findings from identical code.
3. **It probably isn't one switch.** Self-approving an OCF limit value ≠ self-approving a
   block bid that reaches IEX. Ask per workflow.

Businesses usually relax maker-checker for **staffing**, not philosophy — hard gate closures,
one trader on shift. The usual resolution is not "allow it silently" but: allow it, mark the
record (`self_approved: true`), and surface those rows in a review report. Same insertion
point, barely more work than the block.

Detection already works: `approved_by`, `approved_username`, `approved_at` and audit
documents are all written. You can reconstruct a self-approval. You cannot prevent one.

**Also note:** in live UAT data **no role holds an approve or write permission on any bid
screen** — every role is `:read`-only, including one literally named `checker` (0 users,
duplicate of `operator`). Three layers, three misses: permission model, code, role data.

---

## 4.5 F10 — renaming the admin mints a second one

**Two different things share a similar name.** This is the whole confusion:

| | What it is | Who changes it |
|---|---|---|
| `ADMIN_USERNAME` | A line in the server's `.env`. Say `superadmin`. | Nobody. It just sits there. |
| `username` | A field on a user record in MongoDB. | Anyone calling `PUT /users/{id}` |

**You never touch `ADMIN_USERNAME`.** The seeder runs one check at every startup:

```python
admin_user = await self.user_repository.get_user_by_username(settings.ADMIN_USERNAME)
if admin_user:
    return True                       # found — do nothing
else:
    created_user = await self.user_repository.create_user(
        username=settings.ADMIN_USERNAME,
        password=settings.ADMIN_INITIAL_PASSWORD,
        roles=["admin"], is_active=True, is_verified=True)
```

1. Someone calls `PUT /users/{admin_id}` with `{"username": "bob"}`. `.env` untouched.
2. Service restarts (deploy, crash, `systemctl restart`).
3. Seeder asks "is there a user called `superadmin`?" → finds only `bob` → **no**.
4. Creates one. Admin role, active, password from `.env`.

Two admins where there was one.

**A unique index does not help.** There *is* one on `username` (vendor baseline,
`create_index("username", unique=True)`, created *before* the failing `email` one so it
genuinely exists). It's irrelevant — the rename **freed** the old name, so there is no
collision to catch.

The real defect: **identity is keyed on a mutable field.** Same shape elsewhere — bid records
store maker identity as a username string and re-resolve it by live lookup, so a rename
orphans that history and a later account taking the freed name inherits it.

| Rename to… | Outcome |
|---|---|
| an **existing** username | Blocked by the unique index → `DuplicateKeyError`, uncaught → 500 not 409. Cosmetic. |
| a **new** username | Succeeds. Seeder recreates the admin at next restart. **The dangerous path.** |

**Severity revised down to Medium–High** (was High). `PUT /users/{id}` is super-admin-only
today, and a super-admin can already create admins directly — so this is not an escalation
route. It is (a) a **persistence backdoor**: rename the admin and every restart regenerates an
admin with a password from `.env`, looking exactly like the legitimate seeded account, and
(b) an **operational footgun**: a bulk rename catching the admin account produces a live
account with a default password nobody realises exists.

**Fix:** delete `username` from `UserUpdateRequest`. The UI never offers renaming. Longer
term, key the seeder on a stable marker (`is_seed_admin` flag or fixed `_id`), not a name.

---

## 4.6 F2's second half — what the unguarded write routes actually reach

Demonstrated 2026-08-16 against the local stack with a freshly created account holding
role `user` — the role 741 of the 816 UAT accounts hold — whose JWT carries **only
`:read` permissions. Every document was restored afterwards.**

```
GET  /api/v2/iex-api-details/        -> 200
PUT  /api/v2/iex-api-details/{id}    -> 200  {"status":"success"}
     body: {"server_url":"http://attacker.invalid/iex",
             "participant_id":"ATTACKER0001","portfolio_id":"ATTACKERPF01"}
```

The record changed, and `participant_id`/`portfolio_id` propagated into **5
`exchange_config` documents, all `is_active: true`** — the documents
`dam_transaction_service._get_api_client()` reads to build every IEX request
(`LoginUserId`, `ParticipantId`, `PortfolioId`, `BearerToken`).

⚠️ **Correction to the report's framing, and to earlier drafts of this doc.**
`server_url` is **dead storage**. Across `services/` and `libs/` it appears only in the
two Pydantic models in `iex_api_detail_controller.py` (lines 17 and 39) — nothing reads
it. The outbound host is `baseUrl` in `exchange_config`, which is read in six places in
`dam_transaction_service.py` and **written by no route at all**. So "repoint `server_url`
at an attacker host and redirect bids" **does not work** — the field it names is inert
and the field that matters is not writable through the API.

What the same request *does* reach is worse in a different way, because it is silent:

- **Trading identity corruption.** `LoginUserId` / `ParticipantId` / `PortfolioId` /
  `BidAreaId` flow from this route into the active exchange config via
  `IexApiDetailService.update()` → `update_exchange_environment()`. Bids then go to IEX
  under the wrong participant — rejected outright, or filed against the wrong portfolio.
- **Availability.** `status: "Inactive"`, or a `create` with `status: "Active"`, triggers
  `deactivate_other_environment()` and can leave no active credential at all.
- **`PUT /api/v2/exchange-config/{id}/switch`** — flips the platform between UAT and
  PROD. Same router set, same lack of a guard. Code-verified; not fired.
- **`DELETE /api/v2/iex-api-details/{id}`** — soft-deletes a credential.

Severity is unchanged; the mechanism in the report is wrong. Rotate the exchange
credentials regardless — the keys were readable by every account for the life of the
deployment.

---

## 5. Two services accept unauthenticated requests

**Not the same bug as F6.** F6 = authenticated, not authorized (needs a login). This =
**not authenticated at all** (needs nothing).

| Service | Authentication | Authorization |
|---|---|---|
| auth (8001) | Yes | Yes — one real guard, 4 routers |
| processing (8003) | Yes | **None** ← F6, 72 write routes |
| ingestion (8002) | Per-route only; `/cron/*` forgotten | Hardcoded dict (F1) |
| scheduler (8004) | **None** | **None** |

`services/scheduler/app/main.py:222-242` — the whole middleware block commented out:

```python
# Add Auth Middleware
# AuthMiddleware removed to allow internal trigger without authentication
# app.add_middleware(
#     AuthMiddleware, ...
```

Routes carry no auth of their own — the only `Depends` on them is an **audit logger**.
Exposed:

```
POST /api/v1/scheduler/bid-trade-live-sync/trigger-dam
POST /api/v1/scheduler/bid-trade-live-sync/trigger-rtm
POST /api/v1/scheduler/rtm-scheduling/trigger
POST /api/v1/scheduler/demand-trigger/trigger
GET  /job-monitoring/{running,statistics,dashboard,trace/{id},audit-logs/search,...}
```

Ingestion: no `AuthMiddleware` on the service at all, and `main.py:254-294` defines
`/cron/status`, `/cron/trigger/{job_id}` and `/cron/trigger-all` with **no dependencies**.
(`trigger_cron_job` and `trigger_all_cron_jobs` are each also defined **twice** in that file.)

### Why the pentester missed them

The four trigger endpoints are `include_in_schema=False` — absent from `/docs` and
`openapi.json`. The frontend never calls the scheduler, so they aren't in the JS bundle
either (which is where InfoHash sourced their endpoint list for F18). A black-box test had
nothing to find them with. That is obscurity, not access control.

### "But it doesn't return data" — it does, and it wouldn't matter if it didn't

`GET /job-monitoring/audit-logs/search` returns the audit log itself, with a caller-controlled
`limit` and no ceiling (`?limit=999999` accepted). Same for `/dashboard`, `/statistics`,
`/all-jobs`, `/trace/{id}`, `/execution-details/{id}`. Even `trigger-dam` returns
`{"status": "success", "data": result}`.

In that same handler:

```python
user_id="system",  # TODO: Replace with actual user_id from authentication
```

Someone knew. Every anonymous access is filed under `"system"`, so the audit trail cannot
distinguish an intruder from a cron job.

And for the triggers, the response body was never the attack surface — the **side effect** is.
`trigger-dam` runs `processor.execute_dam_live_sync()`, which calls the exchange and writes to
the database. Confidentiality is one of three properties; integrity (overwrite scheduling data
mid-window) and availability (unauthenticated, unthrottled, single EC2 box, hammering the
exchange under PTC's participant credentials) are the other two.

> ⚠️ **An empty response is unusually weak evidence in this codebase** — the processing service
> silently returns `[]` for every query under the Motor event-loop misbind, with errors
> swallowed into HTTP 200s. Empty-and-successful is a known failure signature here.

### 🔑 The one command that decides severity

Whether these are internet-reachable depends on the nginx vhost, which exists **nowhere in
either repo**. The vendor's handover documentation describes a single public vhost
path-routing all four services — `/auth/`→8001, `/ingestion/`→8002, `/processing/`→8003,
**`/scheduler/`→8004**.

```sh
grep -A3 'location /scheduler' /etc/nginx/sites-available/*
```

If `/scheduler/` is proxied, this jumps **above F6** — F6 needs a stolen credential, this
needs nothing. If internal-only, it drops to defence-in-depth and F6 stays top.
**Highest-value thing anyone can run today.**

---

## 5.1 `submit-to-exchange` — the endpoint the report never tested

`services/processing/app/v2/api/router/block_bid_routes.py:318`. Router declared bare
(`APIRouter(prefix="/block-bid", tags=["Block Bid V2"])`), route dependencies are
`get_user_context` + `get_audit_logger` only.

Full chain available to one ordinary login:

```
POST /api/v2/block-bid/submit-for-approval   → create the bid
POST /api/v2/block-bid/update-status         → approve your own bid   (F5)
POST /api/v2/block-bid/submit-to-exchange    → send it to IEX for real
```

Docstring: *"Submit all Approved block bids for schedule_date to IEX exchange… posts to
exchange API."* Every step writes an audit record marking it a successful legitimate action.

**Genuine constraints** (timing/sanity, not access control): a valid login is required;
exchange gate windows apply; ≤60 bids enforced; IEX applies its own limits.

Combined with F2 — corrupt the trading identity IEX sees, or deactivate the live
environment and cut connectivity entirely. (Not "repoint `server_url` at an attacker
host": see §4.6 — that field is never read.)

**The platform's protection against an employee or compromised account placing unauthorized
power-market orders is the React UI hiding a button.**

---

## 6. Issues the test did not cover

Ours, not InfoHash's. Add to remediation scope and to any retest.

| Issue | Why it matters | Sev |
|---|---|---|
| Scheduler authentication commented out | §5 | High |
| Ingestion `/cron/trigger-all` anonymous | §5 | High |
| `block-bid/submit-to-exchange` unguarded | §5.1 — places real IEX orders | High |
| **No login rate limiting or account lockout anywhere** | Unbounded credential stuffing. Compounds F19 — no second factor to fall back on. (`RATE_LIMIT_*` in ingestion config is for outbound scraping, not auth.) | High |
| bcrypt runs synchronously on the event loop | A login rush freezes auth; every other service fails closed and 401s. Measured `/health` 1–3 ms → **14,101 ms** at 60 concurrent logins. Fix written, uncommitted. | High |
| Token blacklist fails open | Redis unreachable → returns "not blacklisted", un-revoking every logged-out token. Single-box, no HA. | High |
| JWT expiry stamped with naive local time | Encoded as if UTC, validated against real UTC. MFA temp token nominally 5 min becomes **5h35m** on an IST host — that token stands between a password and a full session. | Medium |
| Auth middleware excludes four `/*-bidding/public` prefixes | **Latent, not live** — a duplicate inline middleware (`main.py:552`) registers later, runs first, and enforces auth. Delete the apparent duplicate and four bidding routers go anonymous. Checked specifically; not currently exploitable. | Medium |
| No infrastructure-as-code anywhere | nginx vhost, 4 systemd units, PM2 config and all 5 real Jenkins pipelines exist only on the box and in a PDF. Nothing reviewable or reproducible. | Medium |
| `update_user` writes twice per request | Unawaited call followed by an awaited one. Duplicate audit trails; sits in the same function as the MFA toggle. | Low |
| Vendor domain in auth CORS defaults | `api-dev.infiscopelabs.com` ships in the default allow-list with credentials enabled. Unknown whether PROD overrides via env. | Low |

**Checked and clean** (so nobody re-raises them): CORS is env-driven with explicit origins and
a wildcard+credentials guard at `processing/app/core/config.py:215`. Not a finding.

---

## 7. ⚠️ Sequencing — this will cause an outage if ignored

**The permission vocabulary cannot express what the fixes need.**

- Role permissions are synthesised from menu assignments using a fixed action list:
  `read`, `write`, `create`, `delete` (`services/auth/app/models/menu_role.py:11`).
- There is **no `approve`**. There is **no `update`**. There is **no `data_ingestion`**
  resource at all.
- On live UAT data every role — including one named `checker` — holds only `:read`.

Enabling authorization guards today returns **403 to every non-super-admin on every write**.
That is an outage, not a fix.

**Required order:**

1. Extend the permission catalog and action vocabulary
2. Seed the new permissions onto the roles that should hold them
3. Verify against real users in UAT
4. Then enable guards — at `include_router` level, so new routers inherit protection by
   default rather than needing 45 individual patches

Steps 1–3 are the bulk of the work. Treat the catalog work as **task one of F6**, not a
follow-up.

### A related trap

`PUT /users/{id}` is super-admin-only today **purely because** the guard asks for
`users:update` while the catalog only defines `users:write` — a naming mismatch nobody can
satisfy. It looks like a bug and invites a tidy-up. Aligning those two names would silently
open the endpoint to every role holding the Users write flag. **Fix the writable `username`
field first, then reconcile the names.**

---

## 8. What the permission programme does and does not close

| | Closed by F6 rollout | Needs its own change |
|---|---|---|
| F1 upload | ✅ (with `data_ingestion:*` added to catalog) | — |
| F2 exchange API | ✅ the CRUD half | ❌ masking the decrypted key in `get_all()` |
| F3 price groups | ✅ | — |
| F4 master plants | ✅ incl. bulk upload + CSV export | — |
| F5 approvals | ✅ "anyone can approve" | ❌ **self-approval — one line, ×6 handlers** |
| submit-to-exchange | ✅ | — |
| F7 / F8 / F9 sessions | — | ❌ `token_version`; stop overwriting DB roles with token claims |
| F10 username | — | ❌ drop the field |
| F14 delete no-op | — | ❌ add `await` |
| F19 MFA | — | ❌ field name + reachable enrolment |
| Scheduler / ingestion cron | — | ❌ **authentication, not authorization — different layer** |

---

## 9. Open questions

### For the infrastructure owner (production box access)

1. **Is `/scheduler/` proxied publicly?** `grep -A3 'location /scheduler' /etc/nginx/sites-available/*`
   — decides whether §5 outranks F6.
2. Are the ingestion `/cron/*` paths reachable through nginx?
3. Does PROD override `CORS_ORIGINS` and `ENABLE_V1_API` via env? (`ENABLE_V1_API` defaults
   `False`; if PROD sets it `True`, the entire `/api/v1` surface of processing is excluded
   from `AuthMiddleware` and the inline middleware only guards `/api/v2/`.)
4. What is the production host timezone? (`timedatectl`) — decides the MFA temp-token issue.
5. ~~Does `ptcindia.com` publish a DMARC record with an `sp=` tag?~~ — **answered 2026-08-24:
   no `v=DMARC1` record exists on the domain at all.** Nothing to inherit; PTC's DNS team
   must publish one (`p=none` with `rua=`, then tighten).

### For PTC (business decisions, in writing)

1. **Should the person who submits a request be permitted to approve it?** Per workflow —
   limit values, block bids, single bids. If yes, should those approvals be flagged for review?
2. Which roles should hold write and approve permissions on each screen? (Required input for
   §7 step 2 — nothing can proceed without it.)
3. Confirm the BRD's maker-checker wording before anything is relaxed.

### Credential rotation — raise regardless

- Exchange API keys (F2 — assume known)
- Everything Jenkins holds (F11): SSH deploy key, `JWT_SECRET_KEY`, Mongo URI, RabbitMQ
  password, Redis password, `ADMIN_INITIAL_PASSWORD`. Rotating the JWT key logs everyone out
  — schedule it.
- RabbitMQ admin password leaked in cleartext in the handover PDF.

---

## 10. Method

Each finding traced route → controller → service → repository. Nine findings rated High or
above were handed to independent reviewers instructed to **refute** them; all nine survived,
with corrections to line numbers and supporting detail. Four claims were re-checked by hand,
of which **one was refuted** — an assertion that the `/*-bidding/public` routes are
unauthenticated — and is recorded above as latent rather than live.
