class CallsPanel {
  constructor(tableBodyId) {
    this._tbody = document.getElementById(tableBodyId);
    this._calls = new Map(); // call_id → call object
  }

  handleMessage(msg) {
    if (['CALL_CREATED', 'CALL_UPDATED'].includes(msg.type)) {
      this._calls.set(msg.payload.call_id, msg.payload);
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
      this._tbody.innerHTML = '<tr><td colspan="5" class="empty">No calls</td></tr>';
      return;
    }

    this._tbody.innerHTML = calls.map(c => `
      <tr>
        <td><code>${c.call_id.slice(0, 12)}…</code></td>
        <td>${this._formatUri(c.from_uri)}</td>
        <td><span class="call-state call-state--${c.state.toLowerCase()}">${c.state}</span></td>
        <td>${c.phase}</td>
        <td>${this._formatDuration(c)}</td>
      </tr>
    `).join('');
  }

  _formatUri(uri) {
    if (!uri) return '—';
    const m = uri.match(/sip:([^@>]+)/);
    return m ? m[1] : uri;
  }

  _formatDuration(call) {
    if (call.duration_seconds != null) return `${Math.round(call.duration_seconds)}s`;
    if (!call.answered_at) return '—';
    const secs = Math.round((Date.now() - new Date(call.answered_at)) / 1000);
    return `${secs}s`;
  }
}
