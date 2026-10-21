import http from 'node:http';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import pino from 'pino';
import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys';

const PORT = Number(process.env.PORT || 3000);
const API_TOKEN = (process.env.WHATSAPP_SERVICE_TOKEN || '').trim();
const TELEGRAM_TOKEN = (process.env.BOT_TOKEN || '').trim();
const ADMIN_ID = (process.env.ADMIN_ID || '').trim();
const AUTH_DIR = process.env.AUTH_DIR || '/data/auth_info';

if (!API_TOKEN) throw new Error('WHATSAPP_SERVICE_TOKEN is required');
if (!TELEGRAM_TOKEN || !/^\d+$/.test(ADMIN_ID)) {
  throw new Error('BOT_TOKEN and numeric ADMIN_ID are required for inbound-message notifications');
}

const accounts = new Map();
const recipientLastSent = new Map();
const accountLastSent = new Map();
const logger = pino({ level: 'silent' });
const RECIPIENT_COOLDOWN_MS = Math.max(60_000, Number(process.env.RECIPIENT_COOLDOWN_MS || 60_000));
const ACCOUNT_SEND_GAP_MS = Math.max(2_000, Number(process.env.ACCOUNT_SEND_GAP_MS || 2_000));

function safeTokenMatch(candidate) {
  const a = Buffer.from(String(candidate || ''));
  const b = Buffer.from(API_TOKEN);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
function validAccountId(value) {
  return /^[a-zA-Z0-9_-]{1,32}$/.test(String(value || ''));
}
function accountPath(id) {
  return path.join(AUTH_DIR, id);
}
async function telegramNotify(text) {
  const response = await fetch(`https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ chat_id: ADMIN_ID, text, disable_web_page_preview: true }),
  });
  if (!response.ok) throw new Error(`Telegram notification failed: ${response.status}`);
}
function accountSummary(id, account) {
  return {
    id,
    connected: Boolean(account.connected),
    status: account.status,
    registered: Boolean(account.registered),
  };
}

async function connectAccount(id) {
  if (!validAccountId(id)) throw new Error('Invalid accountId');
  const existing = accounts.get(id);
  if (existing?.connecting || existing?.sock) return existing;
  const account = existing || {
    sock: null, connected: false, connecting: false,
    registered: false, status: 'starting', saveCreds: null,
  };
  account.connecting = true;
  accounts.set(id, account);
  try {
    const dir = accountPath(id);
    fs.mkdirSync(dir, { recursive: true });
    const { state, saveCreds } = await useMultiFileAuthState(dir);
    account.registered = Boolean(state.creds.registered);
    account.saveCreds = saveCreds;
    const sock = makeWASocket({
      auth: state,
      logger,
      printQRInTerminal: false,
      markOnlineOnConnect: false,
      syncFullHistory: false,
    });
    account.sock = sock;
    account.status = 'connecting';
    sock.ev.on('creds.update', async () => {
      account.registered = Boolean(state.creds.registered);
      await saveCreds();
    });
    sock.ev.on('connection.update', ({ connection, lastDisconnect }) => {
      account.connected = connection === 'open';
      if (connection === 'open') {
        account.status = 'connected';
        console.log(`WhatsApp account ${id} connected`);
      } else if (connection === 'close') {
        account.connected = false;
        const code = lastDisconnect?.error?.output?.statusCode;
        account.status = code === DisconnectReason.loggedOut ? 'logged_out' : 'disconnected';
        account.sock = null;
        console.log(`WhatsApp account ${id} closed: ${account.status}`);
        if (code !== DisconnectReason.loggedOut) {
          setTimeout(() => connectAccount(id).catch(err =>
            console.error(`Reconnect failed for ${id}:`, err.message)), 3000);
        }
      }
    });
    sock.ev.on('messages.upsert', async ({ messages, type }) => {
      if (type !== 'notify') return;
      for (const item of messages || []) {
        if (!item.message || item.key?.fromMe) continue;
        const jid = item.key?.remoteJid || '';
        if (!jid.endsWith('@s.whatsapp.net')) continue;
        const messageText = item.message.conversation
          || item.message.extendedTextMessage?.text
          || item.message.imageMessage?.caption
          || item.message.videoMessage?.caption;
        if (!messageText) continue;
        const sender = jid.replace('@s.whatsapp.net', '');
        try {
          await telegramNotify(`📩 WhatsApp message received\nAccount: ${id}\nFrom: +${sender}\n\n${messageText}`);
        } catch (err) {
          console.error('Could not forward incoming message to Telegram:', err.message);
        }
      }
    });
    return account;
  } finally {
    account.connecting = false;
  }
}

async function waitForSocket(account, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (account?.sock) return account;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('WhatsApp session is still starting. Try again shortly.');
}

async function readJson(req) {
  let raw = '';
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > 100_000) throw new Error('Request too large');
  }
  return raw ? JSON.parse(raw) : {};
}
function sendJson(res, status, data) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(data));
}
function chooseAccount(accountId) {
  if (accountId) return accounts.get(String(accountId));
  if (accounts.size === 1) return accounts.values().next().value;
  return null;
}

const server = http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    return sendJson(res, 200, { ok: true, service: 'whatsapp' });
  }
  if (!safeTokenMatch((req.headers.authorization || '').replace(/^Bearer\s+/i, ''))) {
    return sendJson(res, 401, { ok: false, message: 'Unauthorized' });
  }
  try {
    if (req.method === 'GET' && req.url === '/accounts') {
      return sendJson(res, 200, {
        ok: true,
        accounts: [...accounts.entries()].map(([id, account]) => accountSummary(id, account)),
      });
    }
    if (req.method === 'GET' && req.url.startsWith('/status')) {
      const accountId = new URL(req.url, 'http://localhost').searchParams.get('accountId') || '';
      const account = chooseAccount(accountId);
      if (!account) return sendJson(res, 409, { ok: false, message: 'Specify accountId when multiple accounts exist.' });
      return sendJson(res, 200, { ok: true, ...accountSummary(accountId || 'main', account) });
    }
    if (req.method === 'POST' && req.url === '/pairing-code') {
      const body = await readJson(req);
      const accountId = String(body.accountId || 'main');
      const phone = String(body.phone || '').replace(/\D/g, '');
      if (!validAccountId(accountId)) return sendJson(res, 400, { ok: false, message: 'Invalid accountId. Use 1-32 letters, digits, _ or -.' });
      if (!/^\d{8,15}$/.test(phone)) return sendJson(res, 400, { ok: false, message: 'Use a phone number with country code, digits only.' });
      let account = accounts.get(accountId);
      if (!account?.sock) account = await connectAccount(accountId);
      await waitForSocket(account);
      if (account.registered) return sendJson(res, 409, { ok: false, message: 'This account ID already has a linked WhatsApp session.' });
      const code = await account.sock.requestPairingCode(phone);
      return sendJson(res, 200, { ok: true, accountId, code });
    }
    if (req.method === 'POST' && req.url === '/send') {
      const body = await readJson(req);
      const accountId = String(body.accountId || '');
      const phone = String(body.phone || '').replace(/\D/g, '');
      const message = String(body.message || '').trim();
      if (!/^\d{8,15}$/.test(phone) || !message || message.length > 4000) {
        return sendJson(res, 400, { ok: false, message: 'Invalid phone or message.' });
      }
      const account = chooseAccount(accountId);
      if (!account) return sendJson(res, 409, { ok: false, message: 'Specify a valid accountId; multiple accounts may be connected.' });
      if (!account.connected || !account.sock) return sendJson(res, 503, { ok: false, message: 'Selected WhatsApp account is not connected.' });
      const now = Date.now();
      const recipientKey = `${accountId}:${phone}`;
      const lastRecipientSent = recipientLastSent.get(recipientKey) || 0;
      const lastAccountSent = accountLastSent.get(accountId) || 0;
      if (now - lastRecipientSent < RECIPIENT_COOLDOWN_MS) {
        return sendJson(res, 429, { ok: false, message: 'This recipient is on a temporary cooldown for this account.' });
      }
      if (now - lastAccountSent < ACCOUNT_SEND_GAP_MS) {
        return sendJson(res, 429, { ok: false, message: 'This account is sending too quickly. Wait and retry.' });
      }
      await account.sock.sendMessage(`${phone}@s.whatsapp.net`, { text: message });
      recipientLastSent.set(recipientKey, Date.now());
      accountLastSent.set(accountId, Date.now());
      return sendJson(res, 200, { ok: true, accountId: accountId || 'main' });
    }
    return sendJson(res, 404, { ok: false, message: 'Not found' });
  } catch (err) {
    console.error('Request failed:', err.message);
    return sendJson(res, 500, { ok: false, message: 'Request failed. Check service logs.' });
  }
});

async function startSavedAccounts() {
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  // Migrate the original single-session folder into the reserved "main" account
  // only when a legacy credentials file exists and main has not been created.
  const legacyCreds = path.join(AUTH_DIR, 'creds.json');
  const mainDir = accountPath('main');
  if (fs.existsSync(legacyCreds) && !fs.existsSync(path.join(mainDir, 'creds.json'))) {
    fs.mkdirSync(mainDir, { recursive: true });
    for (const name of fs.readdirSync(AUTH_DIR)) {
      if (name === 'main' || name === 'creds.json' || name.endsWith('.json')) {
        const src = path.join(AUTH_DIR, name);
        if (fs.statSync(src).isFile()) fs.renameSync(src, path.join(mainDir, name));
      }
    }
  }
  for (const name of fs.readdirSync(AUTH_DIR)) {
    const dir = path.join(AUTH_DIR, name);
    if (!validAccountId(name) || !fs.statSync(dir).isDirectory()) continue;
    if (fs.existsSync(path.join(dir, 'creds.json'))) {
      connectAccount(name).catch(err => console.error(`Initial connection failed for ${name}:`, err.message));
    }
  }
}

server.listen(PORT, '0.0.0.0', () => {
  console.log(`WhatsApp multi-account service listening on port ${PORT}`);
  startSavedAccounts().catch(err => console.error('Could not load saved accounts:', err.message));
});
