# Integration

Sample projects for exploring Google identity and resource-service integrations.

| Task | Project | Stack | Setup |
| --- | --- | --- | --- |
| Task 2 | Google Identity Provider with role-based access control | Python / FastAPI / SQLite | [task2/README.md](./task2/README.md) |
| Task 3 | Google Drive as a Resource Service | Node.js / OAuth 2.0 | [task3/README.md](./task3/README.md) |

## Task 2: Google Identity Provider with RBAC

[task2](./task2) authenticates users through Google Identity Services and manages local sessions and user/admin permissions with FastAPI and SQLite. Follow its [setup instructions](./task2/README.md) from the `task2` directory. It runs at **http://localhost:8000** and needs its own local configuration.

Task 2 was copied unchanged from [komayl-dbouk/integration-tasks](https://github.com/komayl-dbouk/integration-tasks/tree/ee9f94a2e36a13e2d2c5f8466ebc1f0f1e2c6d68/task2), commit `ee9f94a2e36a13e2d2c5f8466ebc1f0f1e2c6d68`. The original code and documentation are preserved.

## Task 3: Google as a Resource Service

[task3](./task3), previously named `Demo_2`, uses Google Drive API v3 as the resource service and Google OAuth 2.0 for delegated authorization. It includes a file metadata dashboard, server-side token refresh, access revocation, and a sample-data mode that works without Google credentials.

Requires Node.js 20.12 or later. No dependency installation is needed.

```powershell
git clone https://github.com/younes285/Integration.git
cd Integration/task3
npm start
```

Open **http://localhost:3000** and select **Try sample data**.

For live Google Drive access, follow the [Google Cloud and OAuth setup instructions](./task3/README.md). Copy `.env.example` to `.env` and configure your own credentials locally. Local environment files and credential JSON files are excluded from Git.

Run the integration tests from `task3`:

```powershell
npm test
```
