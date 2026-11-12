'use strict';
const $ = (id) => document.getElementById(id);
let csrfToken;
let googleReady;

function report(error) {
  $('error').textContent = error.message || String(error);
  $('error').hidden = false;
}

async function api(path, options = {}) {
  const response = await fetch(path, {credentials: 'same-origin', ...options});
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) {
      $('dashboard').hidden = true;
      $('admin').hidden = true;
      $('login').hidden = false;
    }
    const detail = typeof data.detail === 'string' ? data.detail : 'Request rejected';
    throw new Error(`${response.status}: ${detail}`);
  }
  return data;
}

async function getCSRF() {
  csrfToken = (await api('/api/csrf')).csrf_token;
}

async function post(path, body) {
  // Obtain a current token, including after login rotates its session binding.
  await getCSRF();
  return api(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken},
    body: JSON.stringify(body || {})});
}

async function showUser(user) {
  $('login').hidden = true;
  $('dashboard').hidden = false;
  $('name').textContent = user.name;
  $('email').textContent = user.email;
  $('role').textContent = user.role;
  $('picture').hidden = true;
  if (user.picture_url) {
    const url = new URL(user.picture_url);
    if (url.protocol === 'https:') {
      $('picture').src = url.href;
      $('picture').hidden = false;
    }
  }
  $('admin').hidden = user.role !== 'admin';
  if (user.role === 'admin') await loadUsers();
}

async function loadUsers() {
  const users = await api('/api/admin/users');
  $('users').replaceChildren();
  for (const user of users) {
    const row = document.createElement('tr');
    for (const value of [user.id, user.name, user.email, user.role]) {
      const cell = document.createElement('td');
      cell.textContent = String(value);
      row.append(cell);
    }
    $('users').append(row);
  }
}

function loadGoogle(clientId) {
  return new Promise((resolve, reject) => {
    if (clientId.startsWith('your-client-id')) {
      reject(new Error('Setup needed: replace GOOGLE_CLIENT_ID in .env with your Google web Client ID, then restart the server.'));
      return;
    }
    const script = document.createElement('script');
    script.src = 'https://accounts.google.com/gsi/client';
    script.async = true;
    script.onerror = () => reject(new Error('Google Sign-In could not load. Check your network or browser settings.'));
    script.onload = () => {
      google.accounts.id.initialize({client_id: clientId, auto_select: false, callback: async ({credential}) => {
        $('error').hidden = true;
        $('status').textContent = 'Verifying your Google identity…';
        try { await showUser(await post('/auth/google', {credential})); }
        catch (error) { report(error); }
        finally { $('status').textContent = ''; }
      }});
      google.accounts.id.renderButton($('google-button'), {type: 'icon', theme: 'outline', size: 'large', shape: 'circle'});
      resolve();
    };
    document.head.append(script);
  });
}

async function demonstration(path, target) {
  $('error').hidden = true;
  try {
    $(target).hidden = false;
    await api(path);
    $(target).textContent = path.includes('/admin/') ? '200: Admin access granted.' : '200: Protected endpoint accessed successfully.';
    // Reflect CLI role changes on the next authenticated request.
    await showUser(await api('/api/me'));
  } catch (error) {
    $(target).hidden = false;
    $(target).textContent = error.message;
    if (error.message.startsWith('401')) report(new Error('Your session expired. Sign in again.'));
    if (error.message.startsWith('403')) $('admin').hidden = true;
  }
}

$('protected').addEventListener('click', () => demonstration('/api/dashboard', 'protected-result'));
$('test-admin').addEventListener('click', () => demonstration('/api/admin/dashboard', 'admin-result'));
$('refresh-users').addEventListener('click', () => loadUsers().catch(report));
$('picture').addEventListener('error', () => { $('picture').hidden = true; });
$('logout').addEventListener('click', async () => {
  $('error').hidden = true;
  try {
    await post('/auth/logout');
    if (window.google) google.accounts.id.disableAutoSelect();
    $('dashboard').hidden = true;
    $('admin').hidden = true;
    $('login').hidden = false;
    $('users').replaceChildren();
    $('protected-result').hidden = true;
    $('admin-result').hidden = true;
  } catch (error) { report(error); }
});

(async () => {
  try {
    const config = await api('/config');
    // Prepare sign-in even for authenticated users so expired sessions can recover.
    googleReady = loadGoogle(config.google_client_id).catch(report);
    try { await showUser(await api('/api/me')); }
    catch (error) {
      if (!error.message.startsWith('401')) throw error;
      $('login').hidden = false;
    }
  } catch (error) { report(error); }
  finally { $('status').textContent = ''; }
})();
