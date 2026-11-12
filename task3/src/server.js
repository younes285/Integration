import { existsSync } from 'node:fs';
import { createApp } from './app.js';

const envFile = new URL('../.env', import.meta.url);
if (existsSync(envFile)) process.loadEnvFile(envFile);
const port = Number(process.env.PORT || 3000);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('PORT must be between 1 and 65535.');
const redirectUri = process.env.GOOGLE_REDIRECT_URI || `http://localhost:${port}/auth/google/callback`;
const redirect = new URL(redirectUri);
if (redirect.origin !== `http://localhost:${port}` || redirect.pathname !== '/auth/google/callback' || redirect.search || redirect.hash) {
  throw new Error('This local demo requires GOOGLE_REDIRECT_URI=http://localhost:PORT/auth/google/callback (matching PORT).');
}
const server = createApp({
  clientId: process.env.GOOGLE_CLIENT_ID?.trim() || '',
  clientSecret: process.env.GOOGLE_CLIENT_SECRET?.trim() || '',
  redirectUri,
});
server.listen(port, '127.0.0.1', () => {
  console.log(`Google resource service demo: http://localhost:${port}`);
  console.log('Sample data is available immediately. Configure .env to connect to Google Drive.');
});
server.on('error', error => {
  console.error(error.code === 'EADDRINUSE' ? `Port ${port} is in use. Set PORT and GOOGLE_REDIRECT_URI in .env to another port.` : 'Could not start the server.');
  process.exitCode = 1;
});
