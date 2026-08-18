# F6: Processing-service authorization

## The finding

The processing service authenticated every request and authorized none of them: 72
write routes across 45 routers, not one of them checking what the caller was allowed to
do. A `viewer` account holding 51 read-only permissions was measured creating a real
block bid, approving its own bid, and reaching the exchange submission call — never
once receiving a 403.

## The guard

`libs/authz/route_guard.py` closes this at the router level, applied once per router at
`include_router` in `services/processing/app/main.py` — so every route a router will
ever have is covered, including routes added later, with no per-route annotation to
miss.

The action a request needs is derived from the HTTP method and path, not declared by
hand:

- `GET` / `HEAD` / `OPTIONS` → `read`
- `POST` / `PUT` / `PATCH` → `write`, unless the path matches an approval marker
  (`/approve`, `/reject`, `/update-status`, `/submit-to-exchange`,
  `/submit-for-pre-exchange`, `/submit-pre-exchange-data`), in which case → `approve`
- `DELETE` → `delete` (a small allowlist of "cancel working state" paths map to `write`
  instead)

`/submit-for-approval` — the maker's action — is deliberately not an approval marker;
`/approve` is not a substring of it. This is what keeps the maker (submits a bid for
approval) and the checker (approves or rejects it) as two distinct authorities: a role
can hold `write` without holding `approve`.

`admin` and `superadmin` bypass the permission check entirely (`SUPER_ROLES`). This is
deliberate, not an oversight — `roles.permissions` in the live database is a snapshot
that only updates on grant/revoke, and gating the platform owners on a matrix that can
lag its source of truth risks locking them out of their own installation.

`AUTHZ_MODE` controls enforcement as a config value, not a deploy:

| mode | behaviour |
|---|---|
| `enforce` (default) | 403 on a missing permission |
| `warn` | allow the request, log at WARNING with the permission that would have been required |
| `off` | no check at all — the kill switch |

`AUTHZ_MODE` governs **this guard only** — the processing service's
`libs/authz/route_guard.py`. It is not a platform-wide kill switch. Two other checks
ignore it entirely:

- `global_authz_guard`, mounted on the auth service's user, role, menu and menu-role
  routers, has no mode switch at all and always enforces;
- the ingestion service's `check_page_access` calls `is_allowed(...)` directly, likewise
  with no mode switch.

Anything those two decide is live the moment the permission data changes, with no warn
phase in front of it. That is why the administrative resources are handled in the
permission model (above) rather than left to the rollout.

## The permission model

Permissions are no longer decided by a hand-authored classification of which menus are
"masters" or "admin" or "operational" screens. As of 2026-08-18 they follow PTC's page
model (`libs/authz/page_model.py`):

> If a role has been granted a page, it may do anything on that page.

Concretely: a `menu_roles` row means only "this role has this page" — it no longer
carries its own verb list. The verbs come from the role:

- an ordinary role gets `read`, `write` and `delete` on every page it holds;
- a role flagged `roles.is_read_only: true` is capped at `read` alone on every page it
  holds, regardless of what the (now-vestigial) grant row says.

`approve` is never produced by a page grant, by any role, under any flag. Holding a bid
screen lets a role create and edit a bid; approving it and submitting it to the exchange
remain reachable only through the `SUPER_ROLES` bypass above. Whether some non-admin
role should ever hold `approve` is a business decision for PTC, not something this model
decides on its own — see the rollout notes below.

### The one exception: administrative resources

`page_model.ADMIN_RESOURCES` — `users`, `roles`, `admin` — receive `read` and nothing
else from a page grant, for every role, read-only or not.

The reason is that the auth service mounts `global_authz_guard` on its own user, role,
menu and menu-role routers (`services/auth/app/main.py`). That guard derives its
requirement from path and method, so `DELETE /api/v1/users/{id}` requires exactly
`users:delete` and is satisfied by plain string membership. Minting that string from a
menu grant therefore hands user administration to every role that holds the Users page —
measured before the exception existed, `user` (740 live accounts), `operator`, `checker`
and `test123` all held `users:delete` and `admin:delete`, and an account with the default
`user` role was able to soft-delete another user with a single call.

Withholding these costs real administrators nothing: `admin` and `superadmin` resolve to
`["*"]` at login, so they never depend on page-derived strings. A non-super role that
genuinely needs user administration has to be granted it explicitly rather than acquiring
it from a menu assignment.

The Role Management screen exposes `is_read_only` as a checkbox (`RoleCreateRequest` /
`RoleUpdateRequest` in `services/auth/app/api/v1/routes/role_management.py`). Setting it
through `PUT /api/v1/roles/{id}` re-syncs `roles.permissions` unconditionally in the same
request — not only when the flag changes — via
`MenuRoleRepository._sync_role_permissions`, so a role can never be left asserting a
`is_read_only` flag that its stored permission strings don't reflect. The sync reports
success or failure explicitly; a failure surfaces as a 500 telling the admin to save
again, rather than silently leaving stale permissions in place.

## Rebuilding permissions in bulk: `tools/resync_role_permissions.py`

This replaces the retired `tools/seed_authz_permissions.py` hand-authored matrix.
Rather than classify every menu, it reads the pages each role actually holds
(`menu_roles` rows with `is_active: true` — inactive/revoked grants are excluded on
purpose, since under this model an inactive row would otherwise become a write) and
derives `roles.permissions` straight from `page_model.permission_strings()`.

```
python tools/resync_role_permissions.py --dry-run              # print the diff, change nothing
python tools/resync_role_permissions.py --apply                # write, after a timestamped backup
python tools/resync_role_permissions.py --apply --normalise-grants   # also flatten menu_roles rows to ["read"]
python tools/resync_role_permissions.py --revert FILE          # restore a backup exactly
```

`--dry-run` prints, per role, the counts and then the permission strings being added and
removed. Ordinary strings are summarised after twelve; anything on `users`, `roles`,
`admin`, `iex_api_detail` or `power_exchange` is always listed in full. Read that list,
not the counts — `operator 92 -> 159 permissions (53 write)` is what hid a `users:delete`
grant through seven reviews.

Every `--apply` writes a timestamped JSON backup of `menu_roles`, `roles` (permissions
*and* `is_read_only`, since the flag decides the verbs) and `permissions_catalog` before
it touches anything, and prints the backup's path.
`--revert` replays that file document by document — an unwanted rollout is undone
without a deploy, and it refuses to run against a database whose role `_id`s don't
match the backup (a UAT backup applied to production would otherwise match nothing, or
worse, match the wrong documents).

Two collections are written because the runtime reads one and the UI edits the other:
`roles.permissions` is the flattened list the auth service puts in the JWT;
`menu_roles.permissions` is what the Roles & Permissions screen edits and is only a
historical artifact of the old model now (`--normalise-grants` flattens it to `["read"]`
so it stops asserting verbs nothing reads).

### When a role change takes effect

This differs by service, and the difference matters when planning a rollout or a rollback:

- **Processing service** — permissions are read from the JWT, which is minted at login
  and at every `/validate` call. A role change reaches an already-signed-in session only
  when it next obtains a token: up to 30 minutes.
- **Auth service's own admin routes** — immediately, on sessions that are already signed
  in. `global_authz_guard` resolves the caller through
  `compute_effective_permissions` (`libs/core/authz/principal.py`), which loads
  `db.roles` live and unions those permissions with the token's. A permission removed
  from a role is gone on the caller's next request; one added is live just as fast.

So a `--revert` closes an over-grant on the auth service's user/role/menu routes at once,
but the processing service keeps honouring the old token until it expires.

## Rollout notes for production

These are the order the change has to reach production in, not implementation steps:

1. Merge `08d27ff` first, on its own. It fixes token refresh minting a permission-less
   session and gives `superadmin` the wildcard. Without both, switching the guard on
   throws every user out of their session every 30 minutes and locks out the six live
   superadmins.
2. Deploy with `AUTHZ_MODE=warn`. Nothing is blocked on the processing service; every
   would-be denial logs the exact permission it wanted. Note the scope caveat above —
   this does not put the auth or ingestion checks into warn mode.
3. **Set `is_read_only` on every role that must stay read-only**, before anything is
   resynced. `viewer` is the one the seeder ships with the flag; any role PTC has added
   since — an auditor, a management-view account, a vendor login — has to be flagged by
   hand through the Role Management screen or
   `PUT /api/v1/roles/{id}`. A role that should be read-only and is not flagged becomes
   a full read/write/delete role the moment the resync runs.
4. **Re-grant pages with PTC.** `user` (740 live accounts) currently holds Users, Roles &
   Permissions, Audit Log and DAM bid approval. The administrative pages among those are
   capped at `read` by the model, but the operational ones become live write rights, so
   the grant list has to be the one PTC wants *before* the permissions are rebuilt from
   it — not corrected afterwards.
5. Run `tools/resync_role_permissions.py --dry-run` against the production database and
   have PTC read the diff. It prints the permission strings being added and removed per
   role, never truncating anything on `users`, `roles`, `admin`, `iex_api_detail` or
   `power_exchange`. This is the go/no-go: a count alone is not enough to approve.
6. Soak the warn log through a full business cycle including a bid window.
7. Run `tools/resync_role_permissions.py --apply`. Keep the backup path it prints.
8. Flip `AUTHZ_MODE=enforce`. The kill switch stays `AUTHZ_MODE=off`, a config change and
   a restart rather than a deploy — for the processing service. To undo a permission
   change everywhere, including the auth and ingestion checks that have no mode switch,
   use `--revert` with the backup from step 7.

## Verified locally (2026-08-18)

Local `auth_db` was migrated to this model and the original probe re-run end to end
(`.superpowers/sdd/2026-08-18-page-model-authorization/task-7-report.md` has the full
transcript). Summary:

- A `viewer` account (51 read-only permissions, `is_read_only: true`): `GET
  /api/v2/block-bid/approval-list` → 200, and all three writes it previously reached
  (`submit-for-approval`, `update-status`, `dam-bidding/bid-creation`) → 403.
- A `user` account: `submit-for-approval` (the maker action) → 200; `update-status` (the
  checker action) → 403, correctly still denied — the maker/checker split holds under
  the new model.
- `approve` is granted to no role by the resync tool, confirmed against every role in
  the local database, not only the two probed above.
