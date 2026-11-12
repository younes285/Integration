const $ = id => document.getElementById(id);
let session;
let nextPageToken;
let files = [];
let busy = false;

function notify(message, error = false) {
  $('message').textContent = message;
  $('message').className = error ? 'error' : '';
  $('message').hidden = !message;
}

async function api(path, method = 'GET') {
  const response = await fetch(path, {
    method,
    headers: method === 'POST' ? { 'X-CSRF-Token': session.csrfToken } : {},
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'Request failed. Please try again.');
  return body;
}

function setBusy(value) {
  busy = value;
  for (const id of ['sample', 'disconnect', 'refresh', 'load-more']) $(id).disabled = value;
  $('refresh').disabled = value || !session || session.mode === 'disconnected';
}

async function updateSession() {
  session = await api('/api/session');
  const sample = session.mode === 'sample';
  const connected = session.connected;
  $('status').textContent = connected ? 'Connected' : sample ? 'Sample data' : 'Not connected';
  $('status').className = `badge ${connected ? 'connected' : sample ? 'sample' : ''}`;
  $('connection-description').textContent = connected ? 'Authorized to read your Google Drive file metadata.'
    : sample ? 'Fictional files for preview. No Google connection or OAuth flow is active.'
    : 'Securely authorize access to your file metadata.';
  $('connect').hidden = !session.configured || connected;
  $('sample').hidden = connected || sample;
  $('disconnect').hidden = !connected && !sample;
  $('disconnect').textContent = connected ? 'Disconnect & revoke' : 'Exit sample mode';
  $('setup').hidden = session.configured;
  $('redirect-uri').textContent = session.redirectUri;
  $('resource-note').textContent = sample ? 'Fictional sample data · No Google API requests' : 'Metadata only · Read-only access';
  $('token-status').textContent = connected
    ? `Tokens stay on the server. ${session.canRefresh ? 'Automatic refresh enabled.' : 'Reconnect when your token expires.'}`
    : 'Tokens stay on the server.';
  setBusy(busy);
}

function typeName(mime) {
  return ({
    'application/vnd.google-apps.document': 'Document',
    'application/vnd.google-apps.spreadsheet': 'Spreadsheet',
    'application/vnd.google-apps.presentation': 'Presentation',
    'application/vnd.google-apps.folder': 'Folder',
    'application/pdf': 'PDF',
  })[mime] || mime || 'File';
}

function renderFiles() {
  $('files').replaceChildren();
  for (const file of files) {
    const row = document.createElement('tr');
    const name = document.createElement('td');
    let link;
    try {
      const url = new URL(file.webViewLink);
      if (url.protocol === 'https:' && ['drive.google.com', 'docs.google.com'].includes(url.hostname)) link = url.href;
    } catch { /* Sample files intentionally have no external link. */ }
    if (link) {
      const anchor = document.createElement('a');
      anchor.href = link;
      anchor.target = '_blank';
      anchor.rel = 'noopener noreferrer';
      anchor.textContent = file.name;
      name.append(anchor);
    } else name.textContent = file.name;
    const type = document.createElement('td');
    type.textContent = typeName(file.mimeType);
    const modified = document.createElement('td');
    modified.textContent = file.modifiedTime ? new Date(file.modifiedTime).toLocaleDateString() : '—';
    row.append(name, type, modified);
    $('files').append(row);
  }
  $('count').textContent = files.length ? `(${files.length})` : '';
  $('empty').hidden = files.length > 0;
  $('file-container').hidden = !files.length;
  $('load-more').hidden = !nextPageToken;
  if (session?.mode !== 'disconnected' && !files.length) {
    $('empty').querySelector('h3').textContent = 'No files to show yet.';
    $('empty').querySelector('p').textContent = 'Your Drive has no matching files. Add a file in Google Drive and refresh.';
  } else if (session?.mode === 'disconnected') {
    $('empty').querySelector('h3').textContent = 'A connection opens the door.';
    $('empty').querySelector('p').textContent = 'Connect your Google account to view file metadata, or explore the demo with fictional sample files.';
  }
}

async function loadFiles(append = false) {
  if (!append) {
    files = [];
    nextPageToken = null;
    renderFiles();
  }
  const query = append && nextPageToken ? `?pageToken=${encodeURIComponent(nextPageToken)}` : '';
  const data = await api(`/api/files${query}`);
  files = append ? [...files, ...(data.files || [])] : data.files || [];
  nextPageToken = data.nextPageToken;
  renderFiles();
}

async function action(callback) {
  if (busy) return;
  setBusy(true);
  notify('');
  try {
    await callback();
  } catch (error) {
    notify(error.message, true);
    try { await updateSession(); } catch { /* Keep the original failure visible. */ }
    if (session?.mode === 'disconnected') {
      files = [];
      nextPageToken = null;
      renderFiles();
    }
  } finally {
    setBusy(false);
  }
}

$('sample').addEventListener('click', () => action(async () => {
  await api('/api/demo', 'POST');
  await updateSession();
  await loadFiles();
  notify('Sample mode is active. These files are fictional; no Google authorization or API calls were made.');
}));
$('disconnect').addEventListener('click', () => action(async () => {
  const wasLive = session.connected;
  await api('/api/disconnect', 'POST');
  files = [];
  nextPageToken = null;
  await updateSession();
  renderFiles();
  notify(wasLive ? 'Google access has been revoked and the local tokens cleared.' : 'Sample mode closed.');
}));
$('refresh').addEventListener('click', () => action(async () => { await loadFiles(); await updateSession(); }));
$('load-more').addEventListener('click', () => action(() => loadFiles(true)));

action(async () => {
  await updateSession();
  const notice = new URLSearchParams(location.search).get('notice');
  if (notice === 'consent-denied') notify('Google authorization was canceled. You can reconnect whenever you are ready.');
  if (notice === 'connected') notify('Google connected. Your Drive file metadata is now available.');
  history.replaceState(null, '', '/');
  if (session.mode !== 'disconnected') await loadFiles();
});
