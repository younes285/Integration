export const DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive.metadata.readonly';

export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export class GoogleResourceService {
  constructor(config, fetchImpl = fetch) {
    this.config = config;
    this.fetch = fetchImpl;
  }

  authorizationUrl(state) {
    const url = new URL('https://accounts.google.com/o/oauth2/v2/auth');
    url.search = new URLSearchParams({
      client_id: this.config.clientId,
      redirect_uri: this.config.redirectUri,
      response_type: 'code',
      scope: DRIVE_SCOPE,
      access_type: 'offline',
      prompt: 'consent',
      state,
    }).toString();
    return url.toString();
  }

  async tokenRequest(parameters) {
    const response = await this.fetch('https://oauth2.googleapis.com/token', {
      method: 'POST',
      body: new URLSearchParams({
        client_id: this.config.clientId,
        client_secret: this.config.clientSecret,
        ...parameters,
      }),
      signal: AbortSignal.timeout(15000),
    });
    const data = await response.json();
    if (!response.ok || !data.access_token) {
      throw new HttpError(401, 'Google authorization failed. Connect to Google again.');
    }
    return { ...data, expires_at: Date.now() + Number(data.expires_in ?? 3600) * 1000 };
  }

  exchangeCode(code) {
    return this.tokenRequest({ code, grant_type: 'authorization_code', redirect_uri: this.config.redirectUri });
  }

  async refresh(session) {
    if (!session.tokens?.refresh_token) {
      session.tokens = null;
      throw new HttpError(401, 'Your Google access expired. Connect to Google again.');
    }
    try {
      const refreshed = await this.tokenRequest({
        refresh_token: session.tokens.refresh_token,
        grant_type: 'refresh_token',
      });
      session.tokens = { ...session.tokens, ...refreshed };
    } catch (error) {
      if (error.status === 401) session.tokens = null;
      throw error;
    }
  }

  async listFiles(session, pageToken) {
    if (!session.tokens) throw new HttpError(401, 'Connect to Google before requesting files.');
    if (session.tokens.expires_at <= Date.now() + 30000) await this.refresh(session);
    const url = new URL('https://www.googleapis.com/drive/v3/files');
    url.search = new URLSearchParams({
      pageSize: '20',
      fields: 'nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,size)',
      q: 'trashed = false',
      orderBy: 'modifiedTime desc',
      ...(pageToken ? { pageToken } : {}),
    }).toString();
    const request = () => this.fetch(url, {
      headers: { Authorization: `Bearer ${session.tokens.access_token}` },
      signal: AbortSignal.timeout(15000),
    });
    let response = await request();
    if (response.status === 401) {
      await this.refresh(session);
      response = await request();
    }
    if (response.status === 401) {
      session.tokens = null;
      throw new HttpError(401, 'Google access was rejected. Connect to Google again.');
    }
    if (response.status === 403) {
      throw new HttpError(403, 'Google denied access. Enable the Drive API, check the metadata scope and account policy, and check API quotas.');
    }
    if (response.status === 429) throw new HttpError(429, 'Google rate limit reached. Try again later.');
    if (!response.ok) throw new HttpError(502, 'Google Drive is unavailable. Try again shortly.');
    return response.json();
  }

  async revoke(tokens) {
    if (!tokens) return;
    const response = await this.fetch('https://oauth2.googleapis.com/revoke', {
      method: 'POST',
      body: new URLSearchParams({ token: tokens.refresh_token || tokens.access_token }),
      signal: AbortSignal.timeout(15000),
    });
    // A token Google already invalidated needs no further revocation.
    if (!response.ok && response.status !== 400) {
      throw new HttpError(502, 'Google could not revoke access. Please retry disconnecting.');
    }
  }
}
