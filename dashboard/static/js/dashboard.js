/**
 * Main dashboard controller.
 * - Loads config from /api/config
 * - Initialises all panels
 * - Connects WebSocket with reconnect backoff
 * - Manages dark/light theme
 */

let API_KEY = localStorage.getItem('dashboard_api_key') || '';

function clearApiKey() {
  localStorage.removeItem('dashboard_api_key');
  location.reload();
}

function showLoginOverlay(errorMsg) {
  const overlay = document.getElementById('login-overlay');
  overlay.classList.add('visible');
  if (errorMsg) {
    document.getElementById('login-error').textContent = errorMsg;
  }
  document.getElementById('login-key-input').focus();
}

function hideLoginOverlay() {
  document.getElementById('login-overlay').classList.remove('visible');
}

document.getElementById('login-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const key = document.getElementById('login-key-input').value.trim();
  if (!key) {
    document.getElementById('login-error').textContent = 'API key cannot be empty.';
    return;
  }
  localStorage.setItem('dashboard_api_key', key);
  API_KEY = key;
  hideLoginOverlay();
  init();
});

// ── Theme management ──────────────────────────────────────────────────────────
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('theme', theme);
  document.getElementById('theme-toggle').textContent = theme === 'dark' ? '☀ Light' : '☾ Dark';
}

function initTheme() {
  const saved = localStorage.getItem('theme');
  const preferred = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  applyTheme(saved || preferred);
}

document.getElementById('theme-toggle').addEventListener('click', () => {
  const current = document.documentElement.getAttribute('data-theme') || 'light';
  applyTheme(current === 'dark' ? 'light' : 'dark');
});

// ── Connection status UI ──────────────────────────────────────────────────────
const connBadge = document.getElementById('conn-badge');

function setConnState(state) {
  connBadge.className = `conn-badge conn-badge--${state.toLowerCase().replace('_','-')}`;
  const labels = {
    CONNECTED:    'Connected',
    CONNECTING:   'Connecting…',
    BACKING_OFF:  'Reconnecting…',
    DISCONNECTED: 'Disconnected',
    AUTH_FAILED:  'Auth Failed',
    EXHAUSTED:    'Disconnected',
  };
  connBadge.innerHTML = `<span class="conn-badge__dot"></span>${labels[state] || state}`;

  if (state === 'AUTH_FAILED') {
    document.getElementById('auth-banner').classList.add('visible');
  }
}

// ── Panels ────────────────────────────────────────────────────────────────────
const callsPanel      = new CallsPanel('calls-tbody');
const tokensPanel     = new TokensPanel('tokens-body');
const healthPanel     = new HealthPanel('health-body');
const logsPanel       = new LogsPanel('log-stream');
const transcriptPanel = new TranscriptPanel('tx-transcript', 'tx-events', 'tx-call-select');

document.getElementById('log-level-filter').addEventListener('change', (e) => {
  logsPanel.setLevelFilter(e.target.value);
});

function handleMessage(msg) {
  callsPanel.handleMessage(msg);
  tokensPanel.handleMessage(msg);
  healthPanel.handleMessage(msg);
  logsPanel.handleMessage(msg);
  transcriptPanel.handleMessage(msg);
}

// ── WebSocket initialisation ──────────────────────────────────────────────────
let _wsClient = null;

async function init() {
  initTheme();

  if (!API_KEY) {
    showLoginOverlay();
    return;
  }

  // Load reconnect config from server
  let cfg = { baseMs: 500, maxMs: 30000, maxAttempts: 15 };
  try {
    const res = await fetch('/api/config', {
      headers: { Authorization: `Bearer ${API_KEY}` },
    });
    if (res.ok) {
      const data = await res.json();
      cfg = {
        baseMs:      data.ws_reconnect_base_ms,
        maxMs:       data.ws_reconnect_max_ms,
        maxAttempts: data.ws_reconnect_max_attempts,
      };
    }
  } catch (_) { /* use defaults */ }

  // Derive WebSocket URL from current page origin
  const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
  const wsUrl = `${wsProto}://${location.host}/ws/events`;

  if (_wsClient) _wsClient.close();
  _wsClient = new ReconnectingWS(wsUrl, API_KEY, cfg, handleMessage, (state) => {
    setConnState(state);
    if (state === 'AUTH_FAILED') {
      localStorage.removeItem('dashboard_api_key');
      API_KEY = '';
      showLoginOverlay('Invalid API key. Please try again.');
    }
  });
  setConnState('DISCONNECTED');
  _wsClient.connect();
}

init();
