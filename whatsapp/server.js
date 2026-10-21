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

let sock;
let connected = false;
let connecting = false;
let lastStatus = 'starting';

function safeTokenMatch(candidate) {
  const a = Buffer.from(String(candidate || ''));
  const b = Buffer.from(API_TOKEN);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

async function telegramNotify(text) {
  const response = await fetch(`https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ chat_id: ADMIN_ID, text, disable_web_page_preview: true }),
  });
  if (!response.ok) throw new Error(`Telegram notification failed: ${response.status}`);
}

async function connectWhatsApp() {
  if (connecting) return;
  connecting = true;
  try {
    fs.mkdirSync(path.dirname(AUTH_DIR), { recursive: true });
    fs.mkdirSync(AUTH_DIR, { recursive: true });
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
    sock = makeWASocket({
      auth: state,
      logger: pino({ level: 'silent' }),
      printQRInTerminal: false,
      markOnlineOnConnect: false,
      syncFullHistory: false,
    });

    sock.ev.on('creds.update', saveCreds);
    sock.ev.on('connection.update', async ({ connection, lastDisconnect }) => {
      connected = connection === 'open';
      if (connection === 'open') {
        lastStatus = 'connected';
        console.log('WhatsApp connected');
      } else if (connection === 'close') {
        connected = false;
        const code = lastDisconnect?.error?.output?.statusCode;
        lastStatus = code === DisconnectReason.loggedOut ? 'logged_out' : 'disconnected';
        console.log('WhatsApp connection closed:', lastStatus);
        if (code !== DisconnectReason.loggedOut) {
          setTimeout(() => connectWhatsApp().catch(err => console.error('Reconnect failed:', err.message)), 3000);
        }
      }
    });

    sock.ev.on('messages.upsert', async ({ messages, type }) => {
      if (type !== 'notify') return;
      for (const item of messages || []) {
        if (!item.message || item.key?.fromMe) continue;
        const jid = item.key?.remoteJid || '';
        if (!jid.endsWith('@s.whatsapp.net')) continue; // Ignore groups and status updates.
        const text = item.message.conversation
          || item.message.extendedTextMessage?.text
          || item.message.imageMessage?.caption
          || item.message.videoMessage?.caption;
        if (!text) continue;
        const sender = jid.replace('@s.whatsapp.net', '');
        try {
          await telegramNotify(`📩 WhatsApp message from +${sender}:\n\n${text}`);
        } catch (err) {
          console.error('Could not forward incoming message to Telegram:', err.message);
        }
      }
    });
  } finally {
    connecting = false;
  }
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

const server = http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    return sendJson(res, 200, { ok: true, service: 'whatsapp' });
  }
  if (!safeTokenMatch((req.headers.authorization || '').replace(/^Bearer\s+/i, ''))) {
    return sendJson(res, 401, { ok: false, message: 'Unauthorized' });
  }
  try {
    if (req.method === 'GET' && req.url === '/status') {
      return sendJson(res, 200, { ok: true, connected, status: lastStatus });
    }

    if (req.method === 'POST' && req.url === '/pairing-code') {
      const body = await readJson(req);
      const phone = String(body.phone || '').replace(/\D/g, '');
      if (!/^\d{8,15}$/.test(phone)) {
        return sendJson(res, 400, { ok: false, message: 'Use a phone number with country code, digits only.' });
      }
      if (!sock) await connectWhatsApp();
      if (sock?.authState?.creds?.registered) {
        return sendJson(res, 409, { ok: false, message: 'A WhatsApp account is already linked to this session.' });
      }
      const code = await sock.requestPairingCode(phone);
      return sendJson(res, 200, { ok: true, code });
    }

    if (req.method === 'POST' && req.url === '/send') {
      const body = await readJson(req);
      const phone = String(body.phone || '').replace(/\D/g, '');
      const message = String(body.message || '').trim();
      if (!/^\d{8,15}$/.test(phone) || !message || message.length > 4000) {
        return sendJson(res, 400, { ok: false, message: 'Invalid phone or message.' });
      }
      if (!connected || !sock) {
        return sendJson(res, 503, { ok: false, message: 'WhatsApp is not connected.' });
      }
      const jid = `${phone}@s.whatsapp.net`;
      await sock.sendMessage(jid, { text: message });
      return sendJson(res, 200, { ok: true });
    }

    return sendJson(res, 404, { ok: false, message: 'Not found' });
  } catch (err) {
    console.error('Request failed:', err.message);
    return sendJson(res, 500, { ok: false, message: 'Request failed. Check service logs.' });
  }
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`WhatsApp service listening on port ${PORT}`);
  connectWhatsApp().catch(err => console.error('Initial WhatsApp connection failed:', err.message));
});
