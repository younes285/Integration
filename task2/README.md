# Google Identity Provider Integration with RBAC

An educational integration project: Google authenticates people, and FastAPI independently manages local sessions and permissions. The frontend is HTML, CSS and vanilla JavaScript; SQLite stores users and hashed session tokens. There are exactly two application roles: `user` and `admin`.

## Project structure

The current workspace is the project root (it can be renamed `google-identity-rbac`).

```text
google-identity-rbac/
├── app/
│   ├── __init__.py
│   ├── main.py              # app factory, lifespan, public routes, static files
│   ├── config.py            # validated environment settings
│   ├── database.py          # SQLAlchemy engine and request-scoped sessions
│   ├── models.py            # users and sessions
│   ├── schemas.py           # input and safe output contracts
│   ├── security.py          # Google verification, cookies, signed CSRF
│   ├── dependencies.py      # session, user and admin checks
│   ├── cli.py               # local-only admin bootstrap
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── users.py
│   │   └── admin.py
│   └── static/
│       ├── index.html
│       ├── app.js
│       └── style.css
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_rbac.py
│   ├── test_sessions.py
│   └── test_configuration_cli.py
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Install and run

Requires Python 3.12 or newer and internet access to Google for real sign-in. Run all commands from the project root. No client secret is needed for this Google Identity Services callback flow.

Windows PowerShell (activation is optional; these commands avoid execution-policy issues):

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Linux/macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Open `.env`. Replace `CSRF_SECRET_KEY` with the generated random value and `GOOGLE_CLIENT_ID` with your actual web Client ID. The server refuses a missing/placeholder CSRF secret. Keep `.env` private. The Client ID itself is public and is intentionally returned by `/config`.

Start on Windows:

```powershell
.\.venv\Scripts\python -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

Start on Linux/macOS:

```sh
.venv/bin/python -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

Visit **http://localhost:8000**. Use that exact hostname and port, matching `APP_ORIGIN` and the Google authorized origin. `127.0.0.1` is the listen address, while `localhost` is the configured browser hostname. SQLite tables are created automatically at startup; `users.db` is relative to the working directory. API documentation is available at `/docs`; state-changing requests there still require Origin and CSRF headers.

## Google Cloud configuration

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create/select a project.
2. Open Google Auth Platform. Configure Branding (application name and contact details) and Audience (internal or external as appropriate).
3. If your audience is in Testing and Google requires test users, add the Google accounts used in your presentation.
4. Under Clients, create an OAuth Client ID with application type **Web application**.
5. Add **http://localhost:8000** under Authorized JavaScript origins. An origin has no path. This project uses the popup JavaScript callback; it does not use a redirect callback endpoint.
6. Copy the Client ID ending in `.apps.googleusercontent.com` into `.env`, and restart FastAPI.
7. Open the application and use the official Google-rendered button. Allow time for configuration changes to propagate if Google reports an origin mismatch.

No Gmail API, mailbox permission, Firebase, password login or Google client secret is required. Google Identity Services loads from `https://accounts.google.com/gsi/client`.

References: [Google setup guide](https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid), [server-side ID token verification](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token), [JavaScript API](https://developers.google.com/identity/gsi/web/reference/js-reference).

## Architecture and complete authentication flow

```text
Browser ── official GIS SDK ──> Google Identity Provider
Browser <── signed Google ID token ── Google
Browser ── credential + CSRF header ──> FastAPI POST /auth/google
FastAPI ── Google signature/claim verification ──> trusted identity
FastAPI ── lookup Google sub, create/retrieve user ──> SQLite
Browser <── opaque HttpOnly session cookie ── FastAPI
Browser ── session cookie ──> protected API
FastAPI ── hash lookup, expiry/revocation, current DB role ──> allow/deny
```

1. The browser loads the same-origin static application, fetches the public Client ID, and calls `/api/me` to recover an existing session.
2. Without a valid session, it displays Google Sign-In. Google handles account authentication and returns an ID token to the JavaScript callback.
3. JavaScript obtains a CSRF token from `/api/csrf`, then sends the credential in JSON to `/auth/google`, with `X-CSRF-Token`. The browser supplies its Origin header.
4. FastAPI first verifies Origin and CSRF. It passes the credential and configured audience to `google.oauth2.id_token.verify_oauth2_token()`. That library checks the signature using Google's public certificates and validates time, audience and issuer. Application checks additionally require the expected issuer/audience, unexpired token, nonempty `sub`, email, and boolean verified-email claim.
5. FastAPI identifies the account by **Google `sub`**, never by email. A new account gets `user`; an existing account retains its database role and updates its current profile information. No client role is accepted.
6. FastAPI generates 32 random bytes as a URL-safe opaque token. SQLite receives only its SHA-256 hash. The raw value goes only into an HttpOnly cookie. A previous session presented by this browser is revoked, and CSRF protection rotates to bind to the new session.
7. JavaScript displays the returned profile and role, without retaining the Google credential or persisting any authentication tokens in localStorage.

Google's ID token is used only for authentication at login. The application's cookie is a separate credential. Refreshing the page preserves login until the local session expires or is revoked.

## Authorization and administrator bootstrap

Every protected route uses `get_current_user()`. Its session dependency hashes the browser cookie and looks up an unexpired, unrevoked SQLite session. It then loads the current user. `require_admin()` rejects users whose **current database role** is not `admin`. Both admin endpoints use this dependency. Hiding frontend controls is only a convenience.

After the intended administrator has signed in once, run locally using the same `.env`, working directory and database as the server:

```powershell
.\.venv\Scripts\python -m app.cli list-users
.\.venv\Scripts\python -m app.cli promote --id 1
# Alternative: select by Google subject instead of database ID
.\.venv\Scripts\python -m app.cli promote --sub "google-subject-from-list"
```

Linux/macOS uses `.venv/bin/python -m app.cli` with the same arguments. Verify the selected ID/subject before promotion. The CLI requires local database access; protect the machine, database and configuration accordingly. There is no public role-changing endpoint or automatic email/domain promotion.

A role change applies on the next API request, even with the existing session. Click **Test admin access** to refresh the UI's role and open admin features after promotion, or refresh the page.

## API contract

| Method | Route | Access / result |
| --- | --- | --- |
| GET | `/` | Static application |
| GET | `/health` | `{"status":"ok"}` |
| GET | `/config` | Public Google Client ID only |
| GET | `/api/csrf` | New signed token in JSON and matching HttpOnly cookie |
| POST | `/auth/google` | Origin + CSRF; JSON `{"credential":"GOOGLE_ID_TOKEN"}` |
| GET | `/api/me` | Session; ID, name, email, role, picture URL |
| GET | `/api/dashboard` | Any authenticated role |
| POST | `/auth/logout` | Session + Origin + CSRF; revoke and clear cookies |
| GET | `/api/admin/dashboard` | Admin only; message and user count |
| GET | `/api/admin/users` | Admin only; safe user profiles and roles |

Successful calls return 200. A missing, invalid, expired or revoked session returns 401. An authenticated user with insufficient privileges receives 403. Missing/untrusted Origin or failed CSRF also returns 403. Unexpected request fields such as `role` return 422. Session hashes, raw session values and Google tokens are never returned in JSON.

## Security decisions

**CSRF:** All state-changing routes require an exact trusted Origin and an explicit `X-CSRF-Token` header. `/api/csrf` issues a random token with a timestamp and HMAC-SHA256 signature, and sets an identical HttpOnly cookie. The signature covers the token and either an anonymous marker or the hash of the current session cookie. Requests must match the cookie/header, have a valid signature and be within one hour. This protects login as well as logout. Successful login rotates the token to the new session, so a pre-login token cannot authorize actions under the new session. After logout both cookies are removed.

An attacker on another origin cannot read the JSON token due to the same-origin policy, cannot create a valid HMAC token, and cannot send an accepted Origin. A cross-origin form also cannot supply the required custom header. No wildcard CORS is enabled. `/api/csrf` issues a fresh token on every call; the frontend fetches one immediately before each POST. Multiple tabs may occasionally need to retry if one rotates the CSRF cookie between another tab's fetch and POST; rejection remains safe.

**Session cookies:** HttpOnly, SameSite=Lax, Path=/, no Domain; one-hour configurable absolute lifetime with no sliding renewal. `SESSION_COOKIE_SECURE=false` is permitted only on local HTTP origins. For deployment configure HTTPS, its exact origin, and `SESSION_COOKIE_SECURE=true`. The server validates expiry independently of browser cookie expiry. Revoked records remain for inspection; this small demo has no scheduled cleanup job.

**Storage and rendering:** SQLAlchemy parameterizes database operations; unique Google subjects and session hashes, foreign keys and a role check constraint are enforced. SQLite timestamps are stored as UTC without timezone offsets. UI profile and table values use `textContent`; image URLs require HTTPS. Responses use no-store, a GIS-compatible content security policy, trusted-host validation and other basic security headers. Passwords and Google ID tokens are never stored. Application code does not log credentials.

**Scope:** This is a local university demonstration, not a complete production identity platform. Internet-facing operation should additionally provide deployment-specific TLS/proxy configuration, request body limits, rate limiting, operational monitoring and database lifecycle management. CSRF cannot defend against malicious code already executing on the trusted origin. Separate devices can hold separate sessions; logging out revokes the current browser's session, not all devices. Logging out of this app does not sign out of Google.

## Concepts for your oral presentation

| Concept | Meaning in this project |
| --- | --- |
| Identity Provider | Google authenticates the person and provides signed identity claims. |
| OpenID Connect (OIDC) | An identity layer built on OAuth 2.0; Google issues an ID token describing the authenticated subject. |
| OAuth 2.0 | A framework for delegated access to resources. This app needs identity, not a Gmail access grant. |
| ID token | A signed identity assertion for this configured client. It is checked once to establish a local session. |
| JWT | A token format with encoded header, claims and signature. Decoding claims alone does not establish trust. |
| Signature verification | The Google library checks cryptographic authenticity with Google's published keys, followed by issuer/audience/time checks. |
| Session cookie | A browser-held random application credential mapped to server-side session state. HttpOnly prevents JavaScript from reading it. |
| RBAC | The application's database role determines which routes are permitted. Google never assigns `admin`. |
| 401 vs 403 | 401 means no valid authentication; 403 means access is forbidden (including authorization or CSRF rejection). |

## Automated tests

```powershell
.\.venv\Scripts\python -m pytest -q
```

```sh
.venv/bin/python -m pytest -q
```

Each test uses an isolated temporary SQLite database. Only the Google verification call is mocked; tests exercise the real FastAPI routes, application claim validation, sessions, cookies, CSRF signatures, database constraints and role dependencies. They cover all requested authentication/session/RBAC scenarios, subject-based identity, wrong issuer and unverified email, explicit audience forwarding, session rotation, CSRF session binding and live role changes. They do not disable auth dependencies or bypass CSRF.

Implementation verification: **42 tests passed** on Python 3.12.10. JavaScript passed `node --check app/static/app.js`. The installed Starlette version emits one deprecation warning about its HTTPX TestClient adapter; tests still pass, and the project retains the requested HTTPX stack. A real Uvicorn HTTP smoke check also passed for health, HTML, JavaScript, CSS, public config and CSRF initialization. Browser layout and real Google sign-in have not been verified.

Google signature failures, expiration and audience errors are simulated at the external library boundary; claim checks are also tested with returned invalid claims. These tests do not prove Google's real network, certificate or browser behavior. Real sign-in requires your Client ID and a manual browser check. No actual Google credentials are bundled.

## Presentation and manual verification

1. Start the server and open http://localhost:8000. Show the architecture strip and official Google button.
2. Sign in with Google account A. Show its verified name, email and optional image; the role starts as `user`.
3. Click **Test user endpoint** and show 200. Refresh to demonstrate that the session survives.
4. Click **Test admin access** and show 403. Manually navigating to `/api/admin/users` also yields 403; frontend edits cannot change that.
5. In another terminal, run `list-users`, then explicitly `promote --id <A's ID>`.
6. Click **Test admin access** again. Show 200 and the registered users table. No new Google login is needed, because FastAPI reloads the database role.
7. Sign out. Navigate directly to `/api/me` or `/api/dashboard` and show 401.
8. Optionally set `SESSION_LIFETIME_SECONDS=60`, restart, sign in, wait over a minute, then test a protected resource to show expiration and the sign-in screen.

Remaining manual checks: real Google account selection, authorized-origin configuration, popup/FedCM behavior in your browser, Google-hosted avatar display, and HTTPS Secure-cookie behavior if deployed. Keep the browser console and Network tab open during the demo to inspect status codes; avoid displaying or copying credential request bodies or cookie values.
#   G o o g l e P r o v i d e r  
 