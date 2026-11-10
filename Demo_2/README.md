# Demo 2 — Google as a Resource Service

A runnable Node.js integration demo that uses **Google Drive API v3 as the resource service** (OAuth resource server). Google OAuth handles authorization; this project is the client. The client requests read-only file metadata, keeps tokens on the backend, and lists files through a small dashboard.

## Run locally

Requires Node.js 20.12 or later. There are no third-party dependencies and no install step.

```powershell
cd Demo_2
npm start
```

Open **http://localhost:3000** and select **Try sample data**. Sample mode uses fictional files and does not call Google or simulate a successful OAuth connection.

## Connect a real Google account

1. Create or select a project in [Google Cloud Console](https://console.cloud.google.com/).
2. In **APIs & Services → Library**, enable **Google Drive API** for that project.
3. Configure **Google Auth Platform** branding and audience. For an external app in Testing, add your Google account to the test users. An internal audience is appropriate only for an eligible Workspace organization.
4. In **Data Access**, add `https://www.googleapis.com/auth/drive.metadata.readonly`.
5. Create an OAuth client with application type **Web application**. Add this exact **Authorized redirect URI**:

   ```text
   http://localhost:3000/auth/google/callback
   ```

   No authorized JavaScript origin is needed for this server-side flow.

6. Copy the example configuration:

   ```powershell
   Copy-Item .env.example .env
   ```

7. Edit `.env` locally and enter your OAuth client ID and client secret:

   ```dotenv
   PORT=3000
   GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your-client-secret
   GOOGLE_REDIRECT_URI=http://localhost:3000/auth/google/callback
   ```

8. Restart `npm start`, open the dashboard, select **Connect Google**, and grant the file metadata permission. The dashboard lists up to 20 files per page, sorted by modification date. Use **Load more files** for pagination.

The metadata scope can view information about Drive files but cannot read or modify their contents. Google classifies it as a restricted scope; use test users for this local demo. Public deployment may require Google's verification and, depending on how restricted data is handled, a security assessment. External Testing projects requesting Drive scopes generally have refresh tokens that expire after seven days. See the official references below.

## How it works

```text
Browser → Demo backend → Google authorization & consent
                          ↓ authorization code + state
Browser → /auth/google/callback
            ↓ backend exchanges code with Google token endpoint
         Server-side access token + refresh token
            ↓ Authorization: Bearer <access_token>
         Google Drive API → File metadata → Browser
```

- The Google account holder is the resource owner.
- The Node.js application is a confidential OAuth client.
- Google OAuth is the authorization server.
- Google Drive API is the resource server/service.
- This is delegated user authorization, not a Google service-account demo or a general sign-in implementation.

Tokens are stored only in memory on the server and never returned to frontend JavaScript. A random, HttpOnly, SameSite cookie identifies the session. The callback validates a single-use, ten-minute OAuth state. POST endpoints validate both Origin and a session CSRF token. The session ID rotates after authorization. The backend refreshes expiring tokens and retries a Drive request once after a 401.

**Disconnect & revoke** asks Google to revoke the grant, then clears local tokens. **Exit sample mode** only clears the fictional preview. Closing the tab does not revoke Google access; restarting the server forgets its tokens but does not revoke the Google grant. You can also remove the app through [Google Account connections](https://myaccount.google.com/connections).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Dashboard |
| GET | `/api/session` | Connection metadata and CSRF token; no OAuth tokens |
| GET | `/auth/google` | Start Google authorization |
| GET | `/auth/google/callback` | Validate state and exchange authorization code |
| GET | `/api/files?pageToken=...` | Get live Drive metadata or explicitly selected sample data |
| POST | `/api/demo` | Enter sample mode; Google must be disconnected |
| POST | `/api/disconnect` | Revoke Google access and clear session connection |

POST endpoints require the dashboard origin and `X-CSRF-Token` from `/api/session`.

## Verify

```powershell
npm test
```

Tests use a real local HTTP server and stub only the Google network calls. They cover authorization state, consent denial, token exchange, cookie rotation, bearer requests, refresh, pagination, revocation, CSRF protection, scope rejection, error handling, and sample mode without credentials. Live consent and Drive access require your own Google OAuth credentials and are not exercised by automated tests.

## Project layout

```text
Demo_2/
  .env.example          Local configuration template
  package.json          Run, watch, and test commands
  public/               Dashboard HTML, CSS, and JavaScript
  src/app.js            HTTP routing, sessions, and OAuth callback
  src/google.js         Google authorization, token, Drive, and revocation calls
  src/sample-data.js    Explicitly fictional preview data
  src/server.js         Load .env and start the local server
  test/app.test.js      Integration tests with stubbed Google calls
```

This is a local demonstration: it binds to `127.0.0.1`, accepts the configured localhost origin, uses an in-memory session store with a one-hour idle lifetime and a 1,000-session limit, and loses connections on restart. Before deploying, use HTTPS with secure cookies, a persistent session/token store with protected credentials, and appropriate operational controls. `.env` is ignored by Git; do not commit Google secrets.

If port 3000 is occupied, change both `PORT` and the port in `GOOGLE_REDIRECT_URI`, and update the Google client's authorized redirect URI to match. A `redirect_uri_mismatch` means those values differ. A 403 may indicate the Drive API is disabled, metadata permission was not granted, a Workspace policy blocks the app, or a quota was exceeded. Use the same browser and `localhost` address throughout the authorization flow.

## Official references

- [Google OAuth 2.0 web-server flow, refresh, and revocation](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Google OAuth token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)
- [Drive API setup](https://developers.google.com/workspace/drive/api/quickstart/nodejs)
- [Drive scopes and verification requirements](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)
- [Drive files.list reference](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list)
 
