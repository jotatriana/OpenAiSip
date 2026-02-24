class TokensPanel {
  constructor(containerId) {
    this._el = document.getElementById(containerId);
    this._global = null;
  }

  handleMessage(msg) {
    if (msg.type === 'TOKEN_USAGE') {
      this._global = msg.payload.global;
      this._render();
    } else if (msg.type === 'SNAPSHOT') {
      this._global = msg.payload.global_tokens;
      this._render();
    }
  }

  _render() {
    const g = this._global;
    if (!g) {
      this._el.innerHTML = '<p class="empty">No token data yet</p>';
      return;
    }

    const total = g.total_tokens || 1; // avoid div-by-zero
    const inputPct = Math.round((g.input_tokens / total) * 100);
    const outputPct = Math.round((g.output_tokens / total) * 100);

    this._el.innerHTML = `
      <div class="token-summary">
        <div class="token-stat">
          <div class="token-stat__value">${this._fmt(g.total_tokens)}</div>
          <div class="token-stat__label">Total</div>
        </div>
        <div class="token-stat">
          <div class="token-stat__value">${this._fmt(g.input_tokens)}</div>
          <div class="token-stat__label">Input</div>
        </div>
        <div class="token-stat">
          <div class="token-stat__value">${this._fmt(g.output_tokens)}</div>
          <div class="token-stat__label">Output</div>
        </div>
      </div>
      <div style="margin-bottom:0.5rem">
        <div style="display:flex;justify-content:space-between;font-size:0.7rem;color:var(--text-secondary);margin-bottom:0.2rem">
          <span>Input tokens (${inputPct}%)</span>
          <span>${this._fmt(g.input_audio_tokens)} audio / ${this._fmt(g.input_text_tokens)} text</span>
        </div>
        <div class="token-bar"><div class="token-bar__fill token-bar__fill--input" style="width:${inputPct}%"></div></div>
      </div>
      <div>
        <div style="display:flex;justify-content:space-between;font-size:0.7rem;color:var(--text-secondary);margin-bottom:0.2rem">
          <span>Output tokens (${outputPct}%)</span>
          <span>${this._fmt(g.output_audio_tokens)} audio / ${this._fmt(g.output_text_tokens)} text</span>
        </div>
        <div class="token-bar"><div class="token-bar__fill token-bar__fill--output" style="width:${outputPct}%"></div></div>
      </div>
      <div style="font-size:0.7rem;color:var(--text-secondary);margin-top:0.75rem">
        ${g.response_count} responses · Last updated ${this._relTime(g.last_updated)}
      </div>
    `;
  }

  _fmt(n) {
    if (n == null) return '0';
    return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
  }

  _relTime(iso) {
    if (!iso) return '—';
    const secs = Math.round((Date.now() - new Date(iso)) / 1000);
    if (secs < 5) return 'just now';
    if (secs < 60) return `${secs}s ago`;
    return `${Math.round(secs / 60)}m ago`;
  }
}
