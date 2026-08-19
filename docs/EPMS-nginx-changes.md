# Nginx changes pending for UAT / PROD

**One file:** `/etc/nginx/sites-available/application` on `py-optima.ptcindia.com`
(13.232.58.80). Single vhost, path-routed, TLS 1.2/1.3.

This is the running list. Nothing here is deployed. Each item says what it closes, why it
cannot be fixed in application code, and how to verify it afterwards.

| # | Change | Closes | Risk if wrong |
|---|---|---|---|
| 1 | Forward the real client IP | login rate limiting + every audit `client_ip` | none — fails safe |
| 2 | CSP on the SPA vhost | VAPT F17 (remaining half) | **page breaks visibly** |
| 3 | `server_tokens off` | VAPT F13 | none |
| 4 | HSTS on the SPA responses | hardening | low |
| 5 | SSE proxy settings | notifications reliability | stream drops |
| 6 | Restrict `/jenkins/` | VAPT F11 — **declined by client** | locks out remote CI access |

---

## 1. Forward the real client IP  *(required)*

Per-IP login throttling and every audit `client_ip` depend on this. Add the four headers to
**each** proxied API location — `proxy_set_header` does **not** inherit into a block that
sets any of its own, so putting them only at `server` level silently does nothing.

```nginx
location /auth/ {
    proxy_pass http://127.0.0.1:8001/;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $remote_addr;   # NOT $proxy_add_x_forwarded_for
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Repeat in `/ingestion/`, `/processing/`, `/scheduler/`.

**Why `$remote_addr` and not `$proxy_add_x_forwarded_for`:** that variable *appends* to the
header the client sent. A client sending `X-Forwarded-For: 1.2.3.4` arrives as
`1.2.3.4, <real client>`, and anything reading the leftmost entry gets the address the
attacker chose — evading the IP limiter and poisoning the audit trail. EPMS is a single
proxy hop, so overwriting is correct and unspoofable. If a WAF or load balancer is ever put
in front, this becomes multi-hop and the rule changes to rightmost-untrusted.

Also set in the auth service `.env`
(`/var/www/applications/server/production/auth/.env`):

```
TRUSTED_PROXIES=127.0.0.1
```

Forwarded headers are honoured **only** when the socket peer is listed there, so an
untrusted client cannot assert its own address.

**Verify:**

```bash
grep "Per-IP login throttling is inactive" /var/log/epms/auth.log
```

Present after a failed remote login = still not forwarding. Absent = working. Safe to
deploy the app before this change: the interlock switches the per-IP tier off rather than
bucketing the whole internet together, and per-account throttling is unaffected.

---

## 2. CSP on the SPA vhost  *(closes VAPT F17's remaining half)*

The API half is fixed in code (`911f22f`). This half cannot be: nginx `/` proxies to PM2 on
:3000, so `index.html` never passes through `SecurityHeadersMiddleware`. CSP is enforced by
the browser against the document it loaded, so it has to be set here.

```nginx
location / {
    proxy_pass http://127.0.0.1:3000;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;
}
```

- **No `unsafe-eval`.** Verified against the real `client/dist` bundle: the only three
  `Function("return this")` hits across 39 JS files are try/catch-guarded `globalThis`
  polyfills, unreachable in a browser where `self` exists.
- **`style-src 'unsafe-inline'` stays.** AG Grid and Highcharts set inline styles at
  runtime; removing it breaks the charts.
- **`always`** matters, or the header is dropped on error responses.
- If the SPA is ever pointed at an API on another origin, `connect-src 'self'` must be
  widened to name it, or every XHR is blocked.

⚠️ **This is the one change that can break the page visibly.** Deploy it with a browser open
on the console and exercise the charts and AG Grid grids specifically — they are the
components most likely to trip `style-src`. To stage it, ship
`Content-Security-Policy-Report-Only` first, watch for violations, then switch the header
name.

---

## 3. `server_tokens off;`  *(closes VAPT F13)*

```nginx
http {
    server_tokens off;
}
```

Removes the version from the `Server` header and error pages. The report's "EOL version"
claim is false — Ubuntu backports fixes into a frozen version string — but suppressing the
banner is free and closes the finding.

---

## 4. HSTS on the SPA responses  *(hardening)*

The services set `Strict-Transport-Security` in middleware, so API responses carry it. The
SPA is served by PM2 and does not. Add it beside the CSP in `location /`:

```nginx
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

Only after confirming every subdomain is HTTPS-ready — `includeSubDomains` is sticky for a
year and cannot be walked back quickly.

---

## 5. SSE proxy settings for notifications  *(verify — likely already fine)*

The stream endpoint (`/api/v2/notifications/stream`) already sends
`X-Accel-Buffering: no`, so nginx will not buffer it, and it heartbeats every 25 s against
nginx's 60 s default `proxy_read_timeout`. Nothing is required.

Worth confirming on the box, because streaming breaks subtly if it is wrong:

```nginx
location /processing/ {
    proxy_http_version 1.1;          # 1.0 upstream breaks chunked streaming
    proxy_set_header Connection "";  # do not forward a "close"
    proxy_read_timeout 300s;         # comfortably above HEARTBEAT_SECONDS = 25
}
```

**Unverified against the live vhost** — I could not read it. If notifications already work
in UAT, this section is a no-op.

---

## 6. Restrict `/jenkins/`  *(VAPT F11 — offered and declined 2026-08-19)*

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
touching EPMS at all. Three lines, no Jenkins config change.

---

## After any change

```bash
sudo nginx -t && sudo systemctl reload nginx
```

`nginx -t` first, always: a syntax error plus a restart takes the whole platform down,
where a reload on a valid config is seamless.
