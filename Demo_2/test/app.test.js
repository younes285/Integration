import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { request as httpRequest } from 'node:http';
import { createApp } from '../src/app.js';
import { DRIVE_SCOPE } from '../src/google.js';

const origin = 'http://localhost:3000';
const config = { clientId: 'test-client', clientSecret: 'private-client-secret', redirectUri: `${origin}/auth/google/callback` };
const tokenResult = (extra = {}) => ({ access_token: 'private-access-token', refresh_token: 'private-refresh-token', expires_in: 3600, scope: DRIVE_SCOPE, ...extra });
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

async function fixture(t, fetchImpl = () => { throw new Error('Unexpected provider request'); }, settings = config) {
  const app = createApp(settings, { fetchImpl });
  app.listen(0, '127.0.0.1');
  await once(app, 'listening');
  t.after(() => { app.closeAllConnections(); return new Promise(resolve => app.close(resolve)); });
  let cookie = '';
  async function request(path, options = {}) {
    // Use http.request so tests can exercise the configured Host on ephemeral ports.
    const response = await new Promise((resolve, reject) => {
      const outgoing = httpRequest(`http://127.0.0.1:${app.address().port}${path}`, {
        ...options,
        headers: { Host: 'localhost:3000', Cookie: cookie, ...options.headers },
      }, incoming => {
        const chunks = [];
        incoming.on('data', chunk => chunks.push(chunk));
        incoming.on('end', () => resolve(new Response(Buffer.concat(chunks), {
          status: incoming.statusCode,
          headers: Object.fromEntries(Object.entries(incoming.headers).map(([key, value]) => [key, Array.isArray(value) ? value.join(', ') : value])),
        })));
        incoming.on('error', reject);
      });
      outgoing.on('error', reject);
      outgoing.end();
    });
    const setCookie = response.headers.get('set-cookie');
    if (setCookie) cookie = setCookie.split(';')[0];
    return response;
  }
  async function session() { return (await request('/api/session')).json(); }
  async function post(path, extraHeaders = {}) {
    const current = await session();
    return request(path, { method: 'POST', headers: { Origin: origin, 'X-CSRF-Token': current.csrfToken, ...extraHeaders } });
  }
  async function authorize() {
    const response = await request('/auth/google');
    assert.equal(response.status, 302);
    const auth = new URL(response.headers.get('location'));
    return request(`/auth/google/callback?code=test-code&state=${auth.searchParams.get('state')}`);
  }
  return { request, session, post, authorize };
}

test('serves dashboard and assets with security headers, and does not serve private files', async t => {
  const f = await fixture(t);
  for (const path of ['/', '/app.js', '/styles.css']) {
    const response = await f.request(path);
    assert.equal(response.status, 200);
    assert.match(response.headers.get('content-security-policy'), /frame-ancestors 'none'/);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.ok((await response.text()).length > 100);
  }
  assert.equal((await f.request('/.env')).status, 404);
  assert.equal((await f.request('/src/server.js')).status, 404);
  assert.equal((await f.request('/', { headers: { Host: 'attacker.example' } })).status, 400);
});

test('requires authorization to list live files and keeps secrets out of session responses', async t => {
  const f = await fixture(t);
  assert.equal((await f.request('/api/files')).status, 401);
  const response = await f.request('/api/session');
  const body = await response.text();
  assert.equal(JSON.parse(body).mode, 'disconnected');
  assert.doesNotMatch(body, /private-client-secret|private-access-token|private-refresh-token/);
  assert.match(response.headers.get('set-cookie'), /HttpOnly; SameSite=Lax/);
});

test('sample mode works without credentials or Google calls and can be exited', async t => {
  const f = await fixture(t, undefined, { ...config, clientId: '', clientSecret: '' });
  assert.equal((await f.session()).configured, false);
  assert.equal((await f.request('/auth/google')).status, 503);
  assert.equal((await f.post('/api/demo')).status, 200);
  const resources = await (await f.request('/api/files')).json();
  assert.equal(resources.mode, 'sample');
  assert.equal(resources.files.length, 4);
  assert.equal((await f.session()).connected, false);
  assert.equal((await f.post('/api/disconnect')).status, 200);
  assert.equal((await f.request('/api/files')).status, 401);
});

test('mutations reject missing CSRF tokens and foreign origins', async t => {
  const f = await fixture(t);
  assert.equal((await f.request('/api/demo', { method: 'POST', headers: { Origin: origin } })).status, 403);
  assert.equal((await f.post('/api/demo', { Origin: 'https://attacker.example' })).status, 403);
  assert.equal((await f.session()).mode, 'disconnected');
});

test('authorization requests the metadata scope and rejects missing or mismatched OAuth state', async t => {
  const f = await fixture(t);
  assert.equal((await f.request('/auth/google/callback?code=code')).status, 400);
  const response = await f.request('/auth/google');
  const auth = new URL(response.headers.get('location'));
  assert.equal(auth.origin, 'https://accounts.google.com');
  assert.equal(auth.searchParams.get('scope'), DRIVE_SCOPE);
  assert.equal(auth.searchParams.get('redirect_uri'), config.redirectUri);
  assert.equal(auth.searchParams.get('access_type'), 'offline');
  assert.equal(auth.searchParams.get('response_type'), 'code');
  assert.equal((await f.request('/auth/google/callback?state=wrong&code=code')).status, 400);
  assert.equal((await f.request(`/auth/google/callback?state=${auth.searchParams.get('state')}&code=code`)).status, 400);
});

test('consent denial returns to the dashboard and consumes state', async t => {
  const f = await fixture(t);
  const auth = new URL((await f.request('/auth/google')).headers.get('location'));
  const path = `/auth/google/callback?state=${auth.searchParams.get('state')}&error=access_denied`;
  const response = await f.request(path);
  assert.equal(response.headers.get('location'), '/?notice=consent-denied');
  assert.equal((await f.request(path)).status, 400);
});

test('exchanges the code, rotates the cookie, calls Drive with bearer token, and revokes access', async t => {
  const calls = [];
  const f = await fixture(t, async (input, options) => {
    const url = String(input);
    calls.push(url);
    if (url.endsWith('/token')) {
      assert.equal(options.body.get('code'), 'test-code');
      assert.equal(options.body.get('client_secret'), config.clientSecret);
      assert.equal(options.body.get('redirect_uri'), config.redirectUri);
      return json(tokenResult());
    }
    if (url.endsWith('/revoke')) {
      assert.equal(options.body.get('token'), 'private-refresh-token');
      return json({});
    }
    assert.equal(options.headers.Authorization, 'Bearer private-access-token');
    const drive = new URL(url);
    assert.equal(drive.searchParams.get('q'), 'trashed = false');
    assert.equal(drive.searchParams.get('pageToken'), 'next-page');
    return json({ files: [{ id: '1', name: 'Real file' }], nextPageToken: 'page-three' });
  });
  const before = await f.request('/api/session');
  const oldCookie = before.headers.get('set-cookie');
  const callback = await f.authorize();
  assert.equal(callback.headers.get('location'), '/?notice=connected');
  assert.notEqual(callback.headers.get('set-cookie'), oldCookie);
  const body = await f.session();
  assert.equal(body.connected, true);
  assert.equal(body.mode, 'google');
  assert.equal(body.canRefresh, true);
  assert.doesNotMatch(JSON.stringify(body), /private-access-token|private-refresh-token/);
  assert.equal((await f.post('/api/demo')).status, 409);
  const resources = await (await f.request('/api/files?pageToken=next-page')).json();
  assert.equal(resources.files[0].name, 'Real file');
  assert.equal(resources.nextPageToken, 'page-three');
  assert.equal(resources.mode, 'google');
  assert.equal((await f.post('/api/disconnect')).status, 200);
  assert.equal((await f.session()).connected, false);
  assert.equal((await f.request('/api/files')).status, 401);
  assert.equal(calls.length, 3);
});

test('refreshes expired tokens without exposing tokens to the browser', async t => {
  let tokenCalls = 0;
  const f = await fixture(t, async (input, options) => {
    if (String(input).endsWith('/token')) {
      tokenCalls++;
      if (tokenCalls === 1) return json(tokenResult({ expires_in: 0 }));
      assert.equal(options.body.get('grant_type'), 'refresh_token');
      assert.equal(options.body.get('refresh_token'), 'private-refresh-token');
      return json({ access_token: 'new-private-token', expires_in: 3600 });
    }
    assert.equal(options.headers.Authorization, 'Bearer new-private-token');
    return json({ files: [] });
  });
  await f.authorize();
  assert.equal((await f.request('/api/files')).status, 200);
  assert.equal(tokenCalls, 2);
  assert.equal((await f.session()).canRefresh, true);
});

test('retries Drive once after a 401 and refreshes the token', async t => {
  let driveCalls = 0;
  let tokenCalls = 0;
  const f = await fixture(t, async input => {
    if (String(input).endsWith('/token')) { tokenCalls++; return json(tokenResult()); }
    driveCalls++;
    return driveCalls === 1 ? json({}, 401) : json({ files: [] });
  });
  await f.authorize();
  assert.equal((await f.request('/api/files')).status, 200);
  assert.equal(driveCalls, 2);
  assert.equal(tokenCalls, 2);
});

test('invalid refresh clears the session and requires reconnecting', async t => {
  let tokenCalls = 0;
  const f = await fixture(t, async () => ++tokenCalls === 1
    ? json(tokenResult({ expires_in: 0 })) : json({ error: 'invalid_grant' }, 400));
  await f.authorize();
  assert.equal((await f.request('/api/files')).status, 401);
  assert.equal((await f.session()).connected, false);
});

test('rejects an authorization result that lacks the Drive scope', async t => {
  const f = await fixture(t, async () => json(tokenResult({ scope: 'openid' })));
  assert.equal((await f.authorize()).status, 403);
  assert.equal((await f.session()).connected, false);
});

test('provider error payloads do not leak into browser responses', async t => {
  const f = await fixture(t, async input => String(input).endsWith('/token')
    ? json(tokenResult()) : json({ error: { message: 'private-provider-debug-details' } }, 403));
  await f.authorize();
  const response = await f.request('/api/files');
  assert.equal(response.status, 403);
  const body = await response.text();
  assert.match(body, /Enable the Drive API/);
  assert.doesNotMatch(body, /private-provider-debug-details/);
});
