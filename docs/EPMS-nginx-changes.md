# Nginx changes pending for UAT / PROD

**One file:** `/etc/nginx/sites-available/application`. Single vhost, path-routed,
TLS 1.2/1.3. A ready-to-paste replacement is at
[`docs/nginx/application-proposed.conf`](nginx/application-proposed.conf); this document
explains each change, why it cannot be fixed in application code, and how to verify it.

**Updated 2026-08-21** against the *actual* test vhost (`epm.ptcindia.com`), which had
never been read before. Three items in the earlier revision of this document were written
against assumptions that turned out to be wrong, and two new items were found. Those are
marked ⚠️ **CORRECTED** and 🆕 below.

| # | Change | Closes | Risk if wrong |
|---|---|---|---|
| 1 | `X-Forwarded-For` → `$remote_addr` | login rate limiting + audit `client_ip` spoofing | none — fails safe |
| 2 | 🆕 Strip `X-Internal-Token` on all proxied locations | remote wildcard-permission bypass | none |
| 3 | 🆕 Lock `/scheduler/` to loopback | anonymous job control + audit-log read | none if `SCHEDULER_SERVICE_URL` is loopback |
| 4 | CSP on the SPA `location /` | VAPT F17 (remaining half) | **page breaks visibly** |
| 5 | Repeat HSTS inside `location /` | prevents a regression caused by #4 | silently loses HSTS |
| 6 | `server_tokens off` | VAPT F13 | none |
| 7 | SSE proxy settings | notifications | stream drops |
| 8 | Cipher list | hardening | old-client compatibility |
| 9 | Restrict `/jenkins/` | VAPT F11 — **declined by client** | locks out remote CI access |

Prerequisites that are **not** nginx changes but must land alongside:

- `TRUSTED_PROXIES=127.0.0.1` in the **auth** and **ingestion** service `.env` files.
- Before #3: confirm `SCHEDULER_SERVICE_URL` on the box is loopback (command in §3).
- Confirm a separate file redirects `:80` → `:443`. This vhost listens on 443 only.

---

## 1. Forward the real client IP  *(required)* — ⚠️ **CORRECTED**

The live vhost **already sets `X-Real-IP $remote_addr`** on all four API locations, which
is the header `get_client_ip` prefers. So the per-IP login limiter is *already working* —
the earlier revision of this document was wrong to describe this item as untouched.

What is still wrong is the second header:

```nginx
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;   # current — spoofable
proxy_set_header X-Forwarded-For $remote_addr;                 # change to this
```

`$proxy_add_x_forwarded_for` **appends** to the header the client sent. A client sending
`X-Forwarded-For: 1.2.3.4` arrives at the app as `1.2.3.4, <real client>`. Anything reading
the leftmost entry gets the address the attacker chose. Two call sites do exactly that and
do **not** go through `get_client_ip`:

- `libs/helpers/idf_forecast_audit.py:33` — `forwarded.split(",")[0]`, written straight into
  the forecast audit record's `client_ip`.
- `libs/core/authz/guard.py:165` — folded into the client fingerprint that its rate limiter
  keys on, so an attacker can rotate the fingerprint at will.

EPMS is a single proxy hop, so overwriting is correct and unspoofable. If a WAF or load
balancer is ever put in front, this becomes multi-hop and the rule changes to
rightmost-untrusted.

Repeat in all four API locations — `proxy_set_header` does **not** inherit into a block that
sets any of its own, so setting it once at `server` level silently does nothing.

Also set in the auth and ingestion `.env`:

```
TRUSTED_PROXIES=127.0.0.1
```

Forwarded headers are honoured **only** when the socket peer is listed there.

**Verify:**

```bash
grep "Per-IP login throttling is inactive" /var/log/epms/auth.log
```

Absent after a failed remote login = working.

---

## 2. 🆕 Strip `X-Internal-Token` at the edge  *(required)*

`libs/authz/service_auth.py:22` defines `X-Internal-Token`. `AuthMiddleware` accepts it in
place of a login and returns:

```python
{"user_id": "internal-service", "roles": ["service"], "permissions": ["*"], ...}
```

`permissions: ["*"]` — every authorization guard added by the F6 programme passes. One
header, presented by anyone, is full platform access. The comparison is constant-time and
the secret is not currently known to be leaked, but the secret lives in the service `.env`
files on the same box as Jenkins, which the client has accepted as an unrestricted public
path (§9). The two risks compound.

Internal callers reach each other on `127.0.0.1` and never traverse nginx, so clearing the
header at the edge costs nothing:

```nginx
proxy_set_header X-Internal-Token "";
```

nginx omits a header set to the empty string. Add it to all four API locations and the SSE
location. After this, the header is unusable from the internet regardless of whether the
secret ever leaks.

**Verify:** from off-box, `curl -H 'X-Internal-Token: anything' https://epm.ptcindia.com/processing/api/v2/...`
must still return 401, and the auth log must not record `Rejected X-Internal-Token with a
wrong internal token` (absence proves the header never arrived).

---

## 3. 🆕 Lock `/scheduler/` to loopback  *(required)*

`docs/EPMS-VAPT-triage.md` §5 ends with "the one command that decides severity" — whether
`/scheduler/` is proxied publicly. **The vhost answers it: it is.**

The scheduler ships with its `AuthMiddleware` commented out
(`services/scheduler/app/main.py`, "AuthMiddleware removed to allow internal trigger
without authentication"). No authentication, no authorization, on any route. Publicly
reachable today with no credential of any kind:

```
POST /scheduler/api/v1/scheduler/bid-trade-live-sync/trigger-dam
POST /scheduler/api/v1/scheduler/bid-trade-live-sync/trigger-rtm
POST /scheduler/api/v1/scheduler/rtm-scheduling/trigger
POST /scheduler/api/v1/scheduler/demand-trigger/trigger
GET  /scheduler/job-monitoring/audit-logs/search?limit=999999
GET  /scheduler/job-monitoring/{running,statistics,dashboard,trace/{id},...}
```

`trigger-dam` runs `processor.execute_dam_live_sync()` — it calls the exchange and writes
the database under PTC's participant credentials. The response body was never the attack
surface; the side effect is. Every anonymous access is filed in the audit trail as
`user_id="system"`, so an intruder is indistinguishable from a cron job.

This ranks **above F6**: F6 needs a stolen credential, this needs nothing.

Nothing in the browser uses the path — `client/.env.remote` defines no scheduler base URL,
and processing reaches the scheduler at `SCHEDULER_SERVICE_URL`, which defaults to
`http://localhost:8004` and bypasses nginx entirely.

```nginx
location /scheduler/ {
    allow 127.0.0.1;
    allow ::1;
    deny all;
    # ...existing proxy_pass
}
```

**Before applying**, confirm the loopback assumption on the box:

```bash
grep SCHEDULER_SERVICE_URL /var/www/applications/server/production/processing/.env
```

Loopback or unset → safe. If it names the public hostname, change it to
`http://localhost:8004` in the same maintenance window, or the DAM/RTM live-sync proxy
routes will start returning 403.

The application-side fix (uncommenting `AuthMiddleware`, with internal callers switched to
`X-Internal-Token`) exists on `fix/vapt-remediation` and is **not merged**. This nginx
change is what closes the hole on the currently deployed code, and remains correct
defence-in-depth afterwards.

---

## 4. CSP on the SPA vhost  *(closes VAPT F17's remaining half)* — ⚠️ **CORRECTED**

Two corrections to the earlier revision.

**The SPA is served as static files, not proxied to PM2 on :3000.** The vhost has
`root /var/www/applications/client/dist;` and `try_files $uri $uri/ /index.html;`. The
header still has to be set here — nginx serves `index.html` off disk and it never passes
through `SecurityHeadersMiddleware` — but the directive goes beside `try_files`, not beside
a `proxy_pass`.

**The proposed policy would have broken every chart on the platform.** `client/dist/index.html`
loads scripts from third-party CDNs:

```html
<script src="https://code.highcharts.com/highcharts.js"></script>
<script src="https://code.highcharts.com/modules/{arearange,accessibility,exporting,full-screen}.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dragula@3.7.3/dist/dragula.min.js"></script>
<link href="https://cdn.jsdelivr.net/npm/dragula@3.7.3/dist/dragula.min.css" rel="stylesheet">
```

and `client/src` adds three more origins at runtime:

| Origin | Used by | CSP directive |
|---|---|---|
| `unpkg.com` (Leaflet 1.4.0) | `WindyChart.jsx:60-61` | `script-src`, `style-src` |
| `api.windy.com` (`libBoot.js`) | `WindyChart.jsx:62` | `script-src`, `connect-src` |
| `api.open-meteo.com` | `CurrentWeather.jsx`, `WeatherForecastChart.jsx` | `connect-src` |
| `fonts.googleapis.com` / `gstatic` | `@import` in `styles.css` / `styles.scss` | `style-src`, `font-src` |

Both weather components and the Windy map are live on the Demand Forecast page
(`src/pages/DemandForecast.jsx:223-244`).

The working policy is in `docs/nginx/application-proposed.conf`. `'unsafe-inline'` is
deliberately kept **out of `script-src`** — including it would defeat the purpose of the
header. `style-src 'unsafe-inline'` stays, because AG Grid and Highcharts set inline styles
at runtime.

⚠️ **This is the one change that can break the page visibly.** Ship it as
`Content-Security-Policy-Report-Only` first — that is how the proposed config is written —
and only rename the header after a pass over Demand Forecast, a Highcharts screen and an
AG Grid screen with the browser console open and no violations logged.

### Worth fixing in the client, not in nginx

Four of those five CDN origins should not be there at all:

- **Highcharts is already an npm dependency** (`package.json:48`, `highcharts@^12.3.0`) and
  every chart imports it properly (`import Highcharts from "highcharts"`). The five
  `code.highcharts.com` tags load a *second*, unpinned copy into a global the bundle never
  reads. The `exporting` and `accessibility` modules attach to that unused global, so they
  have never done anything.
- **dragula is not imported anywhere in `src/`** — the tags are dead weight.
- **Leaflet is also an npm dependency** (`package.json:54`) yet `WindyChart` fetches it from
  unpkg at runtime.

Deleting the seven tags from `client/index.html` and importing Leaflet from the bundle is a
no-functionality-change edit that lets `script-src` and `style-src` drop to `'self'` plus
`api.windy.com`. Until then, the production trading UI executes unpinned, unverified
third-party JavaScript from three CDNs on every page load, with no Subresource Integrity —
whoever controls `code.highcharts.com` controls the bidding screens. That is a real
supply-chain exposure and it is not in the VAPT.

---

## 5. HSTS inside `location /`  *(required if #4 ships)* — ⚠️ **CORRECTED**

The earlier revision listed adding HSTS as a separate hardening item. It is **already
present at `server` level** on the live vhost, so the SPA does get it today.

The item is now the opposite: **`add_header` does not inherit into a location that declares
any `add_header` of its own.** The moment `location /` gains a CSP header (#4), the
server-level HSTS is dropped for every SPA response. Repeat it inside the block:

```nginx
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

This is the same inheritance rule as `proxy_set_header`, and it fails silently in both
directions.

Cosmetic side effect: API responses carry HSTS twice, once from nginx's server-level
directive and once from `SecurityHeadersMiddleware`. Harmless in every browser; if you want
it clean, drop the line from the middleware rather than from nginx.

**Verify:** `curl -sI https://epm.ptcindia.com/ | grep -i strict-transport` — must return a
line after the CSP change, not before it only.

---

## 6. `server_tokens off;`  *(closes VAPT F13)*

Valid in `http`, `server` or `location` context — putting it in this server block avoids
touching `nginx.conf`. Removes the version from the `Server` header and error pages. The
report's "EOL version" claim is false — Ubuntu backports fixes into a frozen version string
— but suppressing the banner is free and closes the finding.

**Verify:** `curl -sI https://epm.ptcindia.com/ | grep -i ^server` → `Server: nginx`.

---

## 7. SSE proxy settings for notifications  *(needed when `feat/notifications` deploys)*

The stream endpoint sends `X-Accel-Buffering: no`
(`services/processing/app/v2/api/router/notification_routes.py:389-398`), so nginx will not
buffer it, and it heartbeats every 25 s against nginx's 60 s default `proxy_read_timeout`.
The defaults therefore do work — but they work because of a header the app happens to send,
which is a fragile thing to rely on.

The browser opens `https://epm.ptcindia.com/processing/api/v2/notifications/stream`
(`client/src/config/apiEndpoints.js:412`). Give it its own exact-match location so the long
read timeout does not apply to ordinary processing requests:

```nginx
location = /processing/api/v2/notifications/stream {
    proxy_pass http://127.0.0.1:8003/api/v2/notifications/stream;
    proxy_http_version 1.1;          # 1.0 upstream breaks chunked streaming
    proxy_set_header Connection "";  # do not forward a "close"
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;         # comfortably above HEARTBEAT_SECONDS = 25
    # ...the four IP headers and the X-Internal-Token strip
}
```

An exact-match location wins over the `/processing/` prefix regardless of file order.

Not an nginx change, but the other half of shipping SSE: the systemd units need
`--timeout-graceful-shutdown`, or SIGTERM hangs for up to 30 minutes on open streams.

---

## 8. Cipher list  *(hardening)*

`ssl_ciphers HIGH:!aNULL:!MD5` permits CBC/SHA-1 suites and does not require forward
secrecy. Replace with the Mozilla "intermediate" list (in the proposed config) and set
`ssl_prefer_server_ciphers off` (correct for TLS 1.3 and modern clients).

**`ssl_session_tickets off` was proposed and dropped (2026-08-21, user decision).** The
argument for it was real but modest: nginx generates a session-ticket key at startup and
never rotates it for the life of the process, so anyone who later compromises the box can
decrypt previously captured sessions — giving back exactly the forward secrecy the ECDHE
suites above provide. On a single-box deployment where a compromise also yields the private
key, that is a narrow win for an extra moving part. Not a finding; do not re-add it without
a reason.

Low risk — the list still covers every browser from roughly 2015 onward. Also note
`listen 443 ssl http2` is deprecated on nginx ≥ 1.25 in favour of a separate `http2 on;`
directive; it still works, and only emits a warning. `nginx -v` on the box decides whether
to bother.

---

## 9. Restrict `/jenkins/`  *(VAPT F11 — offered and declined 2026-08-19)*

Recorded for completeness. The client accepted F11 on the basis that Jenkins requires a
login.

```nginx
location /jenkins/ {
    allow <PTC office range>;
    allow <vendor VPN range>;
    deny all;
    # ...existing proxy_pass
}
```

Rationale kept on file: Jenkins' login has not always been the boundary — CVE-2024-23897
was an unauthenticated arbitrary-file-read used in the wild to pull `secrets/` and `.env`
off controllers — and what sits behind it here is the SSH deploy key, `JWT_SECRET_KEY` and
every service `.env`. A symmetric signing key means forged super-admin tokens without
touching EPMS at all. Three lines, no Jenkins config change. With §2 in place, one of the
things an attacker would find in those `.env` files — `INTERNAL_SERVICE_TOKEN` — is no
longer remotely usable.

---

## Optional / lower priority

- **Swagger.** `/docs`, `/redoc` and `/openapi.json` are gated by `DEBUG or ENABLE_SWAGGER`
  in all four services. Confirm with
  `grep -E 'DEBUG|ENABLE_SWAGGER' /var/www/applications/server/production/*/.env`. If they
  are enabled in UAT that is fine; PROD should have them off. An nginx `deny` is
  belt-and-braces only.
- **`/health`.** Reachable unauthenticated through `/processing/health` etc. The
  amplification (30 anonymous requests took real API calls from 8 ms to 1,700 ms) is fixed
  in code by `0d78a4b`, which is **not merged**. Restricting `/health` to loopback at nginx
  would close it on the deployed code, but check first whether Jenkins' curl gate goes
  through nginx or straight to `127.0.0.1:800x` — if the former, this breaks the pipeline.
- **`limit_req` on `/auth/`.** Application-level login throttling now runs before bcrypt,
  so this is defence-in-depth rather than a fix.

---

## After any change

```bash
sudo nginx -t && sudo systemctl reload nginx
```

`nginx -t` first, always: a syntax error plus a restart takes the whole platform down,
where a reload on a valid config is seamless.

Ordering note: items 1, 2, 3, 6, 7 and 8 are non-breaking and can go in one reload. Item 4
(and therefore 5) should be a second, separate reload so that if a chart breaks there is
only one candidate cause.
