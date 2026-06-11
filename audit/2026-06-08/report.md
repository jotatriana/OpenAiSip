# Security Audit Report

- **Target:** `OpenAiSip` — SIP ↔ OpenAI Realtime voice agent + operator dashboard (`/home/jtriana/pythonscripts/OpenAiSip`)
- **Date:** 2026-06-08
- **Auditor:** Claude Code — security-auditor skill
- **Standard:** OWASP Top 10 (2025)
- **Scope:** Full repository source (Python backend: `sip_bridge/`, `dashboard/`, `db/`, `core/`, `config/`, `main.py`; JS frontend: `dashboard/static/js/`). Excludes `.venv/`, `.git/`, the runtime SQLite DB, and the live `.env` (read access intentionally denied).

---

## Executive Summary

The application is, overall, **well-engineered from a security standpoint**: every dashboard route enforces bearer-token auth, all database access uses parameterized SQLAlchemy ORM (no SQL injection), the OpenAI/Svix webhook is HMAC-verified with a constant-time comparison, the LLM tool executor is hardened with per-phase allowlists and binds phone lookups to the verified caller ID, and live secrets (`.env`, `*.db`) are correctly git-ignored and never committed.

The most serious issue is a **stored XSS in the operator dashboard**: the attacker-controlled SIP `From` header is rendered into the calls table via `innerHTML` without escaping, so a malicious caller can execute JavaScript in an operator's authenticated session. Beyond that, the webhook lacks **replay protection** (no timestamp check) and dependencies are **unpinned with no lockfile** (and could not be CVE-audited here). Fixing the XSS and adding webhook timestamp validation are the headline recommendations.

### Findings by severity

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High     | 1 |
| Medium   | 2 |
| Low      | 4 |
| Info     | 2 |
| **Total** | **9** |

### Coverage by OWASP category

| ID  | Category | Findings | Status |
|-----|----------|----------|--------|
| A01 | Broken Access Control | 0 | ✅ |
| A02 | Security Misconfiguration | 3 | ⚠️ |
| A03 | Software Supply Chain Failures | 1 | ⚠️ |
| A04 | Cryptographic Failures | 1 | ⚠️ |
| A05 | Injection | 1 | ❌ |
| A06 | Insecure Design | 1 | ⚠️ |
| A07 | Authentication Failures | 2 | ⚠️ |
| A08 | Software or Data Integrity Failures | 1 | ⚠️ |
| A09 | Security Logging & Alerting Failures | 1 | ⚠️ |
| A10 | Mishandling of Exceptional Conditions | 1 | ⚠️ |

> Status key: ✅ no issues found · ⚠️ minor/uncertain · ❌ confirmed issue(s)
> Note: several findings map to more than one category; each is counted once under its primary category above.

---

## Detailed Findings

### [SEV-001] Stored XSS in operator dashboard via SIP `From` header

- **Severity:** High
- **OWASP category:** A05 – Injection (Cross-site Scripting, CWE-79)
- **Location:** `dashboard/static/js/calls-panel.js:32` (render) and `:48` (`_formatUri`); data origin `sip_bridge/webhook_handler.py:91` (`from_value = sip_headers.get("From", "")`)
- **Confidence:** Confirmed

**Description**
The calls table is built with `innerHTML` and interpolates `this._formatUri(c.from_uri)` directly, with no HTML escaping. `from_uri` is the raw SIP `From` header, which is supplied by the calling party and stored verbatim. `_formatUri` extracts the part after `sip:` (`/sip:([^@>]+)/`) but **returns the entire raw header unescaped when no `sip:` match is found** (`return m ? m[1] : uri;`). The adjacent fields `c.call_id`, `c.state`, and `c.phase` are also interpolated unescaped (lower risk — they are server-controlled).

**Evidence**
```javascript
// calls-panel.js:32
this._tbody.innerHTML = calls.map(c => `
  <tr>
    <td><code>${c.call_id.slice(0, 12)}…</code></td>
    <td>${this._formatDateTime(c.created_at)}</td>
    <td>${this._formatUri(c.from_uri)}</td>     // <-- attacker-controlled, unescaped
    ...
// calls-panel.js:48
_formatUri(uri) {
  if (!uri) return '—';
  const m = uri.match(/sip:([^@>]+)/);
  return m ? m[1] : uri;                        // <-- raw header returned on no match
}
```

**Impact**
A caller who controls their SIP `From` header (e.g. a `From` value containing `<img src=x onerror=...>` and no `sip:` URI) gets that markup rendered into the operator dashboard. Script runs in the operator's **authenticated** origin, enabling theft of the dashboard API key from storage, issuing privileged API calls (force-escalate, toggle maintenance mode, read all transcripts/PII), or pivoting further. No authentication is required from the attacker — only the ability to place/forge a call.

**Suggested fix**
Escape all dynamic values before inserting into `innerHTML`. The codebase already has the right pattern — `transcript-panel.js` and `logs-panel.js` route dynamic text through an `_esc()` helper. Apply the same here: add/import an `_esc()` and wrap `from_uri`, `state`, `phase`, and `call_id`, e.g. `<td>${this._esc(this._formatUri(c.from_uri))}</td>`. Prefer setting `textContent` on created cells over building HTML strings. Per OWASP A05 "How to Prevent," keep output encoding context-aware; consider a strict Content-Security-Policy on the dashboard as defence-in-depth. Optionally sanitize/validate the `From` header server-side in `_parse_from_header`.

---

### [SEV-002] Webhook signature lacks timestamp validation (replay)

- **Severity:** Medium
- **OWASP category:** A08 – Software/Data Integrity Failures (also A07; CWE-294 Capture-replay)
- **Location:** `sip_bridge/webhook_handler.py:220` (`_verify_signature`)
- **Confidence:** Confirmed

**Description**
The Svix-style signature is computed over `{webhook-id}.{webhook-timestamp}.{body}` and compared with `hmac.compare_digest` (good). However the `webhook-timestamp` value is never checked for freshness, and the `webhook-id` is never tracked for uniqueness. A previously observed, validly signed request can be **replayed indefinitely**.

**Evidence**
```python
webhook_ts = request.headers.get("webhook-timestamp", "")   # read…
signed_content = f"{webhook_id}.{webhook_ts}.".encode() + body
# …but webhook_ts freshness is never validated, and webhook_id is never deduplicated
```

**Impact**
An attacker (or a network observer) who captures one signed `realtime.call.incoming` webhook can resubmit it to spuriously create `Call` records and drive the accept/session flow, inflating cost and polluting CDR/transcript data.

**Suggested fix**
Reject requests whose `webhook-timestamp` is outside a small tolerance window (e.g. ±5 minutes) of server time, and optionally cache recently seen `webhook-id`s to reject duplicates — this is exactly the replay protection the Svix scheme is designed for. Per OWASP A08, reject serialized/signed data that fails integrity *and* freshness checks.

---

### [SEV-003] Unpinned dependencies + vulnerable versions installed (no lockfile)

- **Severity:** Medium
- **OWASP category:** A03 – Software Supply Chain Failures (CWE-1395 / CWE-1104)
- **Location:** `requirements.txt`; installed `.venv`
- **Confidence:** Confirmed (CVE-audited with `pip-audit` 2.10.0 on 2026-06-08)

**Description**
All dependencies use lower-bound ranges (`fastapi>=0.115.0`, `sqlalchemy[asyncio]>=2.0`, …) with **no lockfile** (no hashed `requirements.txt`, `poetry.lock`, etc.). This makes the live environment drift away from what the file implies: `pip-audit -r requirements.txt` reports **no** vulnerabilities (the ranges *resolve* to patched releases), yet the **installed `.venv` has drifted to vulnerable versions**. That discrepancy — clean declaration, vulnerable runtime — is the core supply-chain risk.

**Evidence**
`pip-audit` against the installed environment (app dependencies only; `pip`, `pillow`, and `pip-audit`'s own `pygments` excluded as non-app packages). Full output saved to `audit/2026-06-08/pip_audit_env.txt`:

| Package | Installed | Fix | Advisory | Origin | Status |
|---------|-----------|-----|----------|--------|--------|
| idna | 3.11 | 3.15 | CVE-2026-45409 | via `httpx` | ✅ upgraded → 3.18 |
| mako | 1.3.10 | 1.3.12 | CVE-2026-44307 | via `alembic` | ✅ upgraded → 1.3.12 |
| python-dotenv | 1.2.1 | 1.2.2 | CVE-2026-28684 | direct dependency | ✅ upgraded → 1.2.2 |
| pytest | 9.0.2 | 9.0.3 | CVE-2025-71176 | dev dependency | ✅ upgraded → 9.0.3 |
| starlette | 0.52.1 | 1.0.1 | PYSEC-2026-161 | via `fastapi` | ⚠️ open — needs coordinated FastAPI bump (0.52→1.0 is a major version) |

**Impact**
The running services use dependency versions with known CVEs. The HTTP-client (`idna`), Alembic (`mako`), config (`python-dotenv`), and test (`pytest`) issues are now resolved. The remaining `starlette` CVE underpins both FastAPI apps. Because nothing is pinned, deployments are non-reproducible and incident response can't determine which versions actually ran.

**Suggested fix**
1. ✅ Done — patched the low-risk versions: `idna` 3.18, `mako` 1.3.12, `python-dotenv` 1.2.2, `pytest` 9.0.3. Full test suite green (218 passing).
2. ⚠️ Remaining — bump `fastapi` + `starlette` together to a release line that includes `starlette` ≥ 1.0.1, and re-run the suite (0.52 → 1.0 is a major version; verify FastAPI compatibility before deploying).
3. Pin exact versions and commit a hashed lockfile (`pip-compile`/`uv pip compile`, or adopt Poetry/uv) so the declared and installed versions can't diverge.
4. Run `pip-audit` (or `osv-scanner`) in CI and on a schedule, and generate an SBOM (OWASP CycloneDX / Dependency-Track) per OWASP A03 guidance.

---

### [SEV-004] Webhook verification fails open when secret is empty

- **Severity:** Low
- **OWASP category:** A06 – Insecure Design / A02 (CWE-636 Failing Open)
- **Location:** `sip_bridge/webhook_handler.py:229`
- **Confidence:** Confirmed

**Description**
`_verify_signature` returns early (accepting the request) when `s.webhook_secret` is falsy: `if not s.webhook_secret: return`. `webhook_secret` is a required setting, so this requires an explicit empty value — but if ever misconfigured, the webhook endpoint becomes fully unauthenticated and anyone can inject call events.

**Evidence**
```python
s = get_settings()
if not s.webhook_secret:
    return  # Skip validation if no secret configured
```

**Impact**
A configuration slip (empty `WEBHOOK_SECRET`) silently disables authentication on a public, internet-facing endpoint rather than failing closed.

**Suggested fix**
Fail closed: if no secret is configured in a non-development environment, reject the request (HTTP 401/500) and log loudly. Gate the skip behind an explicit `ALLOW_UNSIGNED_WEBHOOKS` dev-only flag. Per A06, security controls should default to deny.

---

### [SEV-005] Non-constant-time API-key comparison (timing)

- **Severity:** Low
- **OWASP category:** A07 – Authentication Failures (CWE-208 / CWE-1254)
- **Location:** `dashboard/auth.py:16` and `dashboard/auth.py:42`
- **Confidence:** Confirmed

**Description**
The dashboard bearer-key check uses Python `!=` string comparison, which short-circuits on the first differing byte and is therefore not constant-time — unlike the webhook path, which correctly uses `hmac.compare_digest`.

**Evidence**
```python
if credentials is None or credentials.credentials != s.dashboard_api_key:   # line 16
...
if token != s.dashboard_api_key:                                            # line 42
```

**Impact**
Theoretically allows timing-based recovery of the API key. Hard to exploit over a network, but trivially avoidable and inconsistent with the webhook code.

**Suggested fix**
Use `hmac.compare_digest(provided, expected)` for both the header and WebSocket token comparisons (encode to bytes first).

---

### [SEV-006] Dashboard served over plaintext HTTP and bound to all interfaces

- **Severity:** Low
- **OWASP category:** A04 – Cryptographic Failures / A02 (CWE-319 Cleartext Transmission)
- **Location:** `main.py:58-59` (`host="0.0.0.0"`, plain `uvicorn`)
- **Confidence:** Likely (depends on deployment topology)

**Description**
Both apps bind `0.0.0.0` and run plain HTTP under uvicorn. The dashboard (port 8001) handles call transcripts and customer PII and authenticates with a static bearer key. If it is reachable without a TLS-terminating proxy, the API key and PII travel in cleartext and the dashboard is exposed network-wide.

**Impact**
Interception of the operator API key and sensitive call data on untrusted networks; broader exposure than necessary.

**Suggested fix**
Terminate TLS in front of both services (reverse proxy) and enforce HTTPS/HSTS per A04. Bind the dashboard to `127.0.0.1` (or a private interface) and reach it only through the proxy; reserve `0.0.0.0` for the SIP webhook that must be public.

---

### [SEV-007] `ngrok.yml` containing an authtoken is not git-ignored

- **Severity:** Low
- **OWASP category:** A02 – Security Misconfiguration (CWE-540 Sensitive Info in Source)
- **Location:** `ngrok.yml` (repo root); `.gitignore` (no entry)
- **Confidence:** Confirmed

**Description**
`ngrok.yml` holds an ngrok `authtoken` in plaintext. It is currently **untracked**, which is good — but it is **not** listed in `.gitignore`, so a routine `git add .` would commit the token.

**Impact**
Latent risk of leaking the ngrok authtoken into git history.

**Suggested fix**
Add `ngrok.yml` (and `*.yml` ngrok configs) to `.gitignore`, or move the token to an environment variable. Rotate the token if it has ever been shared.

---

### [SEV-008] Verbose error text surfaced and broad exception handlers

- **Severity:** Info
- **OWASP category:** A10 – Mishandling of Exceptional Conditions / A09 (CWE-209, CWE-396, CWE-703)
- **Location:** `sip_bridge/tool_executor.py:277` (`output = f"Tool error: {exc}"`); broad `except Exception:` at `dashboard/app.py:46`, `core/logger.py:45`, `sip_bridge/webhook_handler.py:246`, `dashboard/routes/logs.py:33`
- **Confidence:** Confirmed

**Description**
Raw exception strings are passed back to the model as tool output (and may be spoken or logged), and several `except Exception` blocks swallow errors broadly. These are mostly deliberate resilience choices (webhook returns 200 to stop Svix retries; logger guards), so risk is low — but raw internals leaking into model/log context is worth tightening.

**Suggested fix**
Return generic tool-failure messages to the model and log details server-side only; narrow `except Exception` to expected exception types where practical; ensure no caller PII or stack internals reach logs unencoded (A09).

---

### [SEV-009] Best-effort-only PII scrubbing in transcripts

- **Severity:** Info
- **OWASP category:** A04 – Cryptographic Failures (data protection) / A06 (design)
- **Location:** `db/repository.py:28-34` (`_CARD_RE`, `_scrub_pii`)
- **Confidence:** Confirmed (already acknowledged in code comments)

**Description**
`_scrub_pii` only redacts 16-digit card-number patterns before persisting transcript text. The code itself notes "best-effort only... a production deployment needs a certified PII service." Other PII (SSNs, full names, addresses, emails spoken aloud) is stored in plaintext in the SQLite DB.

**Impact**
Customer PII retained in cleartext at rest; regulatory exposure (PCI/GDPR) if the DB is compromised.

**Suggested fix**
Encrypt the transcript store at rest, broaden redaction (or use a dedicated PII/PCI redaction service), and confirm the `transcript_retention_days` purge meets data-minimization requirements (A04 "Data Minimization").

---

## Remediation Priorities

1. **Fix SEV-001 (stored XSS)** — escape `from_uri`/`state`/`phase`/`call_id` in `calls-panel.js` using the existing `_esc()` pattern. Highest impact, small change.
2. **Add webhook timestamp/replay validation (SEV-002)** and **fail closed on missing secret (SEV-004)** — small, high-value hardening of the public endpoint.
3. **Pin dependencies + run `pip-audit` (SEV-003)** — install `pip-audit`, run it, and pin/lock versions; add to CI.
4. **Quick wins:** constant-time key compare (SEV-005), gitignore `ngrok.yml` (SEV-007), front both services with TLS + bind dashboard to localhost (SEV-006).
5. **Hardening / roadmap:** tighten error handling (SEV-008) and strengthen PII protection at rest (SEV-009).

## Automated Scan Summary

- `scan_secrets.py` — 1 candidate, 0 confirmed (the hit is a test fixture: `tests/test_webhook_handler.py:47`). Live `.env`/`*.db` are git-ignored and never committed; only `.env.example` is tracked.
- `scan_dangerous.py` — 21 candidates. Confirmed: 1 SQL-flag was a false positive (ORM only); the `innerHTML` hits resolve to **one** real XSS (SEV-001) — the transcript/logs/selector panels correctly escape via `_esc()`/`textContent`. `Math.random()` jitter (`ws-client.js`) is non-security use. Broad-except hits → SEV-008.
- `scan_dependencies.py` — Python/pip ecosystem (`requirements.txt`). CVE audit completed with `pip-audit` 2.10.0: `-r requirements.txt` is clean, but the installed `.venv` has 5 vulnerable app-dependency versions (`starlette`, `idna`, `mako`, `python-dotenv`, `pytest` — see SEV-003). Full output: `audit/2026-06-08/pip_audit_env.txt`.

Raw scanner output is saved alongside this report: `scan_secrets.md`, `scan_dangerous.md`, `scan_dependencies.md`.

## Methodology & Limitations

Reviewed the full repository against the OWASP Top 10 (2025) using the security-auditor heuristic scanners followed by manual code review of entry points (`main.py`, `sip_bridge/app.py`, `dashboard/app.py`), authentication (`dashboard/auth.py`), the public webhook (`sip_bridge/webhook_handler.py`), the LLM tool executor (`sip_bridge/tool_executor.py`), the data layer (`db/repository.py`), all dashboard routes, and the frontend JS. Access control, injection surface, and crypto/secret handling were verified by reading the relevant code paths and confirming data provenance (trusted vs. attacker-controlled). This is a point-in-time, static, best-effort review and is not a guarantee of security. A dependency CVE scan was completed with `pip-audit` 2.10.0 (see SEV-003). **Not performed:** dynamic/runtime testing, infrastructure/deployment review, and inspection of the live `.env` (read intentionally denied).

> **Update (post-audit remediation):** SEV-001, SEV-002, SEV-004, SEV-005, and SEV-007 have been fixed in code/config (full test suite green, 218 passing). SEV-003: CVE-audited and the four low-risk dependency CVEs patched (`idna`, `mako`, `python-dotenv`, `pytest`); the `starlette` bump (coordinated with FastAPI) and version pinning/lockfile remain. SEV-006 and the Info items (SEV-008, SEV-009) remain open.
