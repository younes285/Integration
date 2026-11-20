# Google SSO + MCP + Jira

Sign in with Google, then use Jira through an MCP server that knows who you are.

- **Google SSO** proves your identity.
- **Roles** decide what you may do: admins can create and edit tasks, everyone else is read-only.
- **Each user connects their own Jira account**, so Jira applies your permissions and records changes as you.
- A **web app** is the front end, and every action goes through the MCP server.

```
Browser ─► webapp.py (:3000) ─► server.py (:8000/mcp) ─► Jira Cloud
```

## Launch

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Create your config and fill it in:
   ```
   copy .env.example .env
   ```
   - **Google** (required): create an OAuth client (Web application) with redirect URI `http://localhost:8000/auth/callback`, then set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
   - **Admins:** set `ADMIN_EMAILS` to the emails that should be admins.
   - **Jira:** either set `ATLASSIAN_CLIENT_ID` and `ATLASSIAN_CLIENT_SECRET` (each user connects their own account; callback URL `http://localhost:8000/jira/callback`), or set `JIRA_BASE_URL`, `JIRA_EMAIL` and `JIRA_API_TOKEN` to use one shared service account.
3. Start the MCP server:
   ```
   python server.py
   ```
4. In a second terminal, start the web app:
   ```
   python webapp.py
   ```
5. Open **http://localhost:3000** (use `localhost`, not `127.0.0.1`) and click **Continue with Google**.

Run the tests with `python -m pytest`.
