# Integration

Sample projects for exploring service integrations.

## Demo 2: Google as a Resource Service

[Demo_2](./Demo_2) uses Google Drive API v3 as the resource service and Google OAuth 2.0 for delegated authorization. It includes a file metadata dashboard, server-side token refresh, access revocation, and a sample-data mode that works without Google credentials.

Requires Node.js 20.12 or later. No dependency installation is needed.

```powershell
git clone https://github.com/younes285/Integration.git
cd Integration/Demo_2
npm start
```

Open **http://localhost:3000** and select **Try sample data**.

For live Google Drive access, follow the [Google Cloud and OAuth setup instructions](./Demo_2/README.md). Copy `.env.example` to `.env` and configure your own credentials locally. Local environment files and credential JSON files are excluded from Git.

Run the integration tests from `Demo_2`:

```powershell
npm test
```
