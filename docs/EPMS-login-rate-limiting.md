# Login rate limiting

Closes the VAPT gap "no login rate limiting or account lockout anywhere". Built
2026-08-19; now on `fix/vapt-remediation`.

## What it does

Two independent limiters on `POST /api/v1/auth/login`, evaluated **before** the password
is hashed.

| Key | Threshold | On trip |
|---|---|---|
| **Account** — the submitted username, normalised | 6 failures / 15 min | lock 5 min, doubling per strike, 60 min cap |
| **IP** — the resolved client address | 50 failures / 15 min | lock 5 min, 15 min cap |

Account tier also runs a delay ladder in front of the lock:

| Failure | Next attempt allowed after |
|---|---|
| 1–2 | immediately |
| 3 | 2s |
| 4 | 5s |
| 5 | 15s |
| 6 | **locked** |

Strike escalation on the account tier — every 6 failures is one more strike:

| Cumulative failures | Strike | Lock |
|---|---|---|
| 6 | 1 | 5 min |
| 12 | 2 | 10 min |
| 18 | 3 | 20 min |
| 24 | 4 | 40 min |
| 30 | 5 | 60 min (cap) |

Failure count expires after 15 min of no failures. Strike count expires after
`LOGIN_MAX_LOCKOUT_MINUTES`. A correct password clears the account's window, gate and
lock — but **not** the strike count, and **not** the IP counter.

## Design decisions worth knowing

**Locks auto-expire; no admin unlock exists.** A hard lockout would let an
unauthenticated attacker take the platform down with six bad passwords against each of
740 accounts, with a human as the only recovery path. Auto-expiry makes that attack
self-healing. It is also why the first lock is 5 minutes rather than the 30 MFA uses: DAM
bid submission closes at 11:00, and a trader locked out at 10:55 is a financial event, not
a helpdesk ticket.

**Keyed on the submitted username whether or not it exists.** Throttling only real
accounts would turn the limiter into a username-existence oracle — spray, watch which
names slow down, harvest the valid list. Unknown names walk the identical ladder and get
the identical message.

**The delay is a stored expiry, never a `sleep()`.** Sleeping in the handler would hold a
worker slot per attacker and rebuild the denial of service this exists to prevent.

**Evaluated before password verification.** Hashing is the expensive half. Unmetered
`/login` is a CPU-exhaustion path against the one service every other service calls to
validate every request, so a throttled attempt must never reach the hash.

**More lenient than MFA on purpose.** MFA allows 5 and locks for 30 minutes doubling. A
TOTP code is six digits — a 10⁶ space that rate limiting is the only thing guarding — and
it is read off a device in the user's hand, so typos are rare. A password comes from a far
larger space and is typed from memory.

**Fails open.** If Redis is unreachable the limiters fall back to per-worker in-process
state, logging each degradation. A cache outage must not become a platform outage, and an
attacker cannot gain attempts by taking Redis down — that is strictly harder than
exhausting the limit.

## nginx — required change

Per-IP throttling and every audit `client_ip` depend on nginx passing the real client
address. **Without this, the app sees only the proxy.**

Add to the EPMS server block (and any `location` that overrides `proxy_set_header`, since
those directives do not inherit once a nested block sets any of them):

```nginx
location / {
    proxy_pass http://127.0.0.1:8001;

    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $remote_addr;   # NOT $proxy_add_x_forwarded_for
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

**Why `$remote_addr` and not the idiomatic `$proxy_add_x_forwarded_for`:** that variable
*appends* to the header the client sent. A client sending
`X-Forwarded-For: 1.2.3.4` arrives as `1.2.3.4, <real client>`, and anything reading the
leftmost entry gets the address the attacker chose — evading the IP limiter and poisoning
the audit trail. EPMS is a single proxy hop, so overwriting is both correct and
unspoofable.

Defence in depth is already in the code: `get_client_ip` prefers `X-Real-IP` (which nginx
always overwrites) over `X-Forwarded-For`, and honours neither unless the socket peer is
listed in `TRUSTED_PROXIES`. Setting both headers as above is correct under either
reading.

If a WAF or load balancer is ever put in front of nginx, this becomes a multi-hop chain
and the rule changes — take the rightmost untrusted entry instead. Revisit then.

### Verify on the box

```bash
# 1. Is nginx forwarding the address at all?
grep -rn "X-Real-IP\|X-Forwarded-For" /etc/nginx/

# 2. Reload and confirm the app agrees. Log in with a wrong password from a remote
#    machine, then check the auth log:
grep "Per-IP login throttling is inactive" /var/log/epms/auth.log
```

That log line is the interlock reporting that per-IP throttling has **switched itself
off** because the address resolved to a proxy or loopback. Seeing it means nginx is not
forwarding the header, or `TRUSTED_PROXIES` does not list the proxy. Per-account
throttling is unaffected and still does the primary job — the interlock exists precisely
so a misconfigured proxy degrades one tier instead of locking out all of PTC from a single
shared bucket.

## Rollout

1. Deploy with `LOGIN_RATE_LIMIT_MODE=warn`. Counters run and log what *would* have been
   refused; nothing is blocked.
2. Watch `login_rate_limited` audit events for a day. Any legitimate account appearing
   repeatedly means the thresholds are wrong for real usage.
3. Flip to `enforce`. Counters were already warm, so nothing restarts from zero.
4. `off` is the panic switch — no code change or redeploy needed.

## Operations

State lives in Redis db `REDIS_BLACKLIST_DB` (1), keys `login:acct:*` and `login:ip:*`.

```bash
# Who is currently locked out?
redis-cli -n 1 --scan --pattern 'login:acct:lock:*'

# Release one account immediately
redis-cli -n 1 del login:acct:fail:<username> login:acct:lock:<username> \
                   login:acct:delay:<username> login:acct:strikes:<username>
```

Usernames are lower-cased and trimmed before keying.

## Not covered

- `/verify-login` — the MFA step has its own limiter (`mfa_rate_limiter`, 5 / 15 min /
  30 min doubling).
- `/change-password` and `/admin/reset-password` — credential surfaces, but both require a
  valid token first, so they are not anonymous brute-force paths. Worth adding later.
- `/refresh` — needs a valid refresh token; not a guessing surface.
- Distributed spraying from many source addresses against many accounts stays below both
  tiers by construction. Detecting that needs alerting on the aggregate `login_failed`
  rate, not a per-key limiter.

## Files

| File | Role |
|---|---|
| `services/auth/app/services/rate_limit_engine.py` | shared window / lockout / delay engine |
| `services/auth/app/services/login_rate_limiter.py` | the two-tier login policy |
| `services/auth/app/services/mfa_rate_limiter.py` | MFA config on the same engine |
| `services/auth/app/controllers/auth_controller.py` | check before hashing, record outcome |
| `libs/middleware/utils.py` | `get_client_ip`, `get_client_ip_or_none`, `get_trusted_proxies` |
| `tests/auth/test_login_rate_limiter.py` | 44 tests, both storage backends |
