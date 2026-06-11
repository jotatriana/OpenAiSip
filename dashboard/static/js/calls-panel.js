class CallsPanel {
  constructor(tableBodyId) {
    this._tbody = document.getElementById(tableBodyId);
    this._calls = new Map(); // call_id → call object
  }

  handleMessage(msg) {
    if (['CALL_CREATED', 'CALL_UPDATED'].includes(msg.type)) {
      const existing = this._calls.get(msg.payload.call_id) || {};
      this._calls.set(msg.payload.call_id, { ...existing, ...msg.payload });
      this._render();
    } else if (msg.type === 'CALL_ENDED') {
      this._calls.set(msg.payload.call_id, msg.payload);
      this._render();
    } else if (msg.type === 'SNAPSHOT') {
      this._calls.clear();
      (msg.payload.active_calls || []).forEach(c => this._calls.set(c.call_id, c));
      this._render();
    }
  }

  _render() {
    const calls = Array.from(this._calls.values())
      .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
      .slice(0, 50);

    if (!calls.length) {
      this._tbody.innerHTML = '<tr><td colspan="6" class="empty">No calls</td></tr>';
      return;
    }

    this._tbody.innerHTML = calls.map(c => `
      <tr>
        <td><code>${this._esc(c.call_id.slice(0, 12))}…</code></td>
        <td>${this._formatDateTime(c.created_at)}</td>
        <td>${this._esc(this._formatUri(c.from_uri))}</td>
        <td><span class="call-state call-state--${this._esc(c.state.toLowerCase())}">${this._esc(c.state)}</span></td>
        <td>${this._esc(c.phase)}</td>
        <td>${this._formatDuration(c)}</td>
      </tr>
    `).join('');
  }

  // Escape untrusted values before interpolating into innerHTML. `from_uri`
  // originates from the caller-controlled SIP `From` header, so it must never
  // be inserted raw (stored XSS in the operator dashboard otherwise).
  _esc(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  _formatUri(uri) {
    if (!uri) return '—';
    const m = uri.match(/sip:([^@>]+)/);
    return m ? m[1] : uri;
  }

  _formatDateTime(ts) {
    if (!ts) return '—';
    const d = new Date(ts);
    const tz = 'America/New_York';
    const date = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: tz });
    const time = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: tz });
    return `${date} ${time} ET`;
  }

  _formatDuration(call) {
    if (call.duration_seconds != null) return `${Math.round(call.duration_seconds)}s`;
    if (!call.answered_at) return '—';
    const toUtcMs = ts => new Date(
      (ts.includes('+') || ts.endsWith('Z')) ? ts : ts + 'Z'
    ).getTime();
    const isLive = ['RINGING', 'ACTIVE', 'TRANSFERRING'].includes(call.state);
    // For ended calls without duration_seconds, use ended_at if available.
    // Never use Date.now() for ended calls — it grows on every refresh.
    const endMs = call.ended_at ? toUtcMs(call.ended_at) : (isLive ? Date.now() : null);
    if (endMs === null) return '—';
    const secs = Math.round((endMs - toUtcMs(call.answered_at)) / 1000);
    return secs >= 0 ? `${secs}s` : '—';
  }
}
