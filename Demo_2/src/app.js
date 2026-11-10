import { createServer } from 'node:http';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { DRIVE_SCOPE, GoogleResourceService, HttpError } from './google.js';
import { sampleFiles } from './sample-data.js';

const SESSION_TTL = 60 * 60 * 1000;
const nonce = () => randomBytes(32).toString('hex');
const equal = (left, right) => typeof left === 'string' && typeof right === 'string'
  && Buffer.byteLength(left) === Buffer.byteLength(right)
  && timingSafeEqual(Buffer.from(left), Buffer.from(right));
const assets = new Map([
  ['/', ['index.html', 'text/html; charset=utf-8']],
  ['/app.js', ['app.js', 'text/javascript; charset=utf-8']],
  ['/styles.css', ['styles.css', 'text/css; charset=utf-8']],
]);

export function createApp(config, { fetchImpl = fetch } = {}) {
  const sessions = new Map();
  const google = new GoogleResourceService(config, fetchImpl);
  const origin = new URL(config.redirectUri).origin;
  const configured = Boolean(config.clientId && config.clientSecret);
  const secureCookie = origin.startsWith('https:') ? '; Secure' : '';

  function sessionFor(req, res) {
    const now = Date.now();
    for (const [key, value] of sessions) if (value.expiresAt <= now) sessions.delete(key);
    const cookie = req.headers.cookie?.split(';').map(value => value.trim()).find(value => value.startsWith('demo_session='));
    let id = cookie?.slice('demo_session='.length);
    if (!sessions.has(id)) {
      if (sessions.size >= 1000) throw new HttpError(503, 'Too many demo sessions. Please try again later.');
      id = nonce();
      sessions.set(id, { csrf: nonce(), tokens: null, sample: false });
    }
    const session = sessions.get(id);
    session.expiresAt = now + SESSION_TTL;
    res.setHeader('Set-Cookie', `demo_session=${id}; Path=/; HttpOnly; SameSite=Lax; Max-Age=3600${secureCookie}`);
    return session;
  }

  function json(res, status, value) {
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(value));
  }

  function redirect(res, path) {
    res.writeHead(302, { Location: path });
    res.end();
  }

  const server = createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
    try {
      // Avoid accepting arbitrary Host headers on this local development server.
      if (req.headers.host !== new URL(origin).host) throw new HttpError(400, `Open the demo at ${origin}.`);
      const url = new URL(req.url, origin);
      if (req.method === 'GET' && assets.has(url.pathname)) {
        const [name, type] = assets.get(url.pathname);
        const content = await readFile(new URL(`../public/${name}`, import.meta.url));
        res.writeHead(200, { 'Content-Type': type });
        return res.end(content);
      }
      const session = sessionFor(req, res);
      if (req.method === 'POST') {
        if (req.headers.origin !== origin || !equal(session.csrf, req.headers['x-csrf-token'])) {
          throw new HttpError(403, 'Request verification failed. Reload the page and try again.');
        }
      }
      if (req.method === 'GET' && url.pathname === '/api/session') {
        return json(res, 200, {
          configured,
          connected: Boolean(session.tokens),
          mode: session.tokens ? 'google' : session.sample ? 'sample' : 'disconnected',
          csrfToken: session.csrf,
          scope: DRIVE_SCOPE,
          redirectUri: config.redirectUri,
          tokenExpiresAt: session.tokens?.expires_at ?? null,
          canRefresh: Boolean(session.tokens?.refresh_token),
        });
      }
      if (req.method === 'GET' && url.pathname === '/auth/google') {
        if (!configured) throw new HttpError(503, 'Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env, then restart the server.');
        session.oauth = { state: nonce(), expiresAt: Date.now() + 10 * 60 * 1000 };
        return redirect(res, google.authorizationUrl(session.oauth.state));
      }
      if (req.method === 'GET' && url.pathname === '/auth/google/callback') {
        const pending = session.oauth;
        delete session.oauth;
        if (!pending || pending.expiresAt <= Date.now() || !equal(pending.state, url.searchParams.get('state'))) {
          throw new HttpError(400, 'Invalid or expired OAuth state. Start a new Google connection from this browser.');
        }
        if (url.searchParams.has('error')) return redirect(res, '/?notice=consent-denied');
        const code = url.searchParams.get('code');
        if (!code) throw new HttpError(400, 'Google did not return an authorization code.');
        const tokens = await google.exchangeCode(code);
        if (tokens.scope && !tokens.scope.split(' ').includes(DRIVE_SCOPE)) {
          throw new HttpError(403, 'Drive metadata permission was not granted. Connect again and allow that permission.');
        }
        session.tokens = tokens;
        session.sample = false;
        // Rotate the session identifier and CSRF token after successful authorization.
        const oldCookie = req.headers.cookie?.split(';').map(value => value.trim()).find(value => value.startsWith('demo_session='));
        if (oldCookie) sessions.delete(oldCookie.slice('demo_session='.length));
        const id = nonce();
        session.csrf = nonce();
        sessions.set(id, session);
        res.setHeader('Set-Cookie', `demo_session=${id}; Path=/; HttpOnly; SameSite=Lax; Max-Age=3600${secureCookie}`);
        return redirect(res, '/?notice=connected');
      }
      if (req.method === 'POST' && url.pathname === '/api/demo') {
        if (session.tokens) throw new HttpError(409, 'Disconnect Google before switching to sample data.');
        session.sample = true;
        delete session.oauth;
        return json(res, 200, { mode: 'sample' });
      }
      if (req.method === 'GET' && url.pathname === '/api/files') {
        if (session.sample) return json(res, 200, { mode: 'sample', files: sampleFiles });
        const pageToken = url.searchParams.get('pageToken');
        if (pageToken?.length > 4096) throw new HttpError(400, 'Invalid page token.');
        const data = await google.listFiles(session, pageToken);
        return json(res, 200, { ...data, mode: 'google' });
      }
      if (req.method === 'POST' && url.pathname === '/api/disconnect') {
        await google.revoke(session.tokens);
        session.tokens = null;
        session.sample = false;
        delete session.oauth;
        session.csrf = nonce();
        return json(res, 200, { disconnected: true });
      }
      throw new HttpError(404, 'Route not found.');
    } catch (error) {
      const status = error instanceof HttpError ? error.status : 502;
      const message = error instanceof HttpError ? error.message : 'The request could not be completed. Check your connection and try again.';
      // Do not log provider payloads: they may contain credentials or personal data.
      json(res, status, { error: message });
    }
  });
  server.requestTimeout = 30000;
  server.headersTimeout = 15000;
  return server;
}
