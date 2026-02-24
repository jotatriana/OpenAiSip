/**
 * WebSocket client with exponential backoff reconnect FSM.
 *
 * States: DISCONNECTED → CONNECTING → CONNECTED
 *         CONNECTED/CONNECTING → BACKING_OFF → CONNECTING  (on non-auth close)
 *         CONNECTING/CONNECTED → AUTH_FAILED              (on close code 4001, terminal)
 *         BACKING_OFF → EXHAUSTED                         (max attempts reached)
 */
class ReconnectingWS {
  static STATE = Object.freeze({
    DISCONNECTED: 'DISCONNECTED',
    CONNECTING:   'CONNECTING',
    CONNECTED:    'CONNECTED',
    BACKING_OFF:  'BACKING_OFF',
    AUTH_FAILED:  'AUTH_FAILED',
    EXHAUSTED:    'EXHAUSTED',
  });

  /**
   * @param {string} url - WebSocket URL
   * @param {string} token - Bearer token
   * @param {{ baseMs, maxMs, maxAttempts }} config - Reconnect parameters
   * @param {(msg: object) => void} onMessage - Message handler
   * @param {(state: string) => void} onStateChange - State change callback
   */
  constructor(url, token, config, onMessage, onStateChange) {
    this._url = url;
    this._token = token;
    this._cfg = config;
    this._onMessage = onMessage;
    this._onStateChange = onStateChange;
    this._ws = null;
    this._attempt = 0;
    this._backoffTimer = null;
    this._state = ReconnectingWS.STATE.DISCONNECTED;
  }

  get state() { return this._state; }

  connect() {
    if (this._state === ReconnectingWS.STATE.AUTH_FAILED) return;
    if (this._state === ReconnectingWS.STATE.EXHAUSTED) return;
    this._doConnect();
  }

  close() {
    if (this._backoffTimer) clearTimeout(this._backoffTimer);
    if (this._ws) {
      this._ws.onclose = null; // prevent reconnect
      this._ws.close();
    }
    this._setState(ReconnectingWS.STATE.DISCONNECTED);
  }

  _doConnect() {
    this._setState(ReconnectingWS.STATE.CONNECTING);
    // Pass token as query param (browsers can't set WS headers)
    const url = `${this._url}?token=${encodeURIComponent(this._token)}`;
    this._ws = new WebSocket(url);

    this._ws.onopen = () => {
      this._attempt = 0;
      this._setState(ReconnectingWS.STATE.CONNECTED);
    };

    this._ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        this._onMessage(msg);
      } catch (_) { /* ignore malformed */ }
    };

    this._ws.onclose = (evt) => {
      if (evt.code === 4001) {
        this._setState(ReconnectingWS.STATE.AUTH_FAILED);
        return;
      }
      this._scheduleReconnect();
    };

    this._ws.onerror = () => {
      // onerror always followed by onclose — let onclose handle reconnect
    };
  }

  _scheduleReconnect() {
    if (this._attempt >= this._cfg.maxAttempts) {
      this._setState(ReconnectingWS.STATE.EXHAUSTED);
      return;
    }
    this._setState(ReconnectingWS.STATE.BACKING_OFF);
    const jitter = Math.random() * 1000;
    const delay = Math.min(
      this._cfg.baseMs * Math.pow(2, this._attempt) + jitter,
      this._cfg.maxMs,
    );
    this._attempt++;
    this._backoffTimer = setTimeout(() => this._doConnect(), delay);
  }

  _setState(s) {
    if (this._state !== s) {
      this._state = s;
      this._onStateChange(s);
    }
  }
}
