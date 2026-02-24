class HealthPanel {
  constructor(containerId) {
    this._el = document.getElementById(containerId);
  }

  handleMessage(msg) {
    if (msg.type === 'HEALTH_UPDATE') {
      this._render(msg.payload);
    } else if (msg.type === 'SNAPSHOT') {
      this._render(msg.payload.channel_health);
    }
  }

  _render(h) {
    if (!h) return;
    const sipClass = `sip-state--${h.sip_registration_state.toLowerCase()}`;
    this._el.innerHTML = `
      <div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:0.75rem">
        <span style="font-size:0.75rem;color:var(--text-secondary)">SIP Registration:</span>
        <span class="sip-state ${sipClass}">${h.sip_registration_state}</span>
      </div>
      <div class="health-grid">
        <div class="health-metric">
          <div class="health-metric__value" style="color:var(--status-active)">${h.active_call_count}</div>
          <div class="health-metric__label">Active Calls</div>
        </div>
        <div class="health-metric">
          <div class="health-metric__value" style="color:var(--accent)">${h.ws_session_count}</div>
          <div class="health-metric__label">WS Sessions</div>
        </div>
        <div class="health-metric">
          <div class="health-metric__value" style="color:var(--text-primary)">${h.total_calls_today}</div>
          <div class="health-metric__label">Calls Today</div>
        </div>
        <div class="health-metric">
          <div class="health-metric__value" style="color:${h.total_calls_failed > 0 ? 'var(--status-error)' : 'var(--text-primary)'}">${h.total_calls_failed}</div>
          <div class="health-metric__label">Failed</div>
        </div>
        <div class="health-metric">
          <div class="health-metric__value" style="font-size:1.1rem;color:var(--text-primary)">${Math.round(h.avg_call_setup_latency_ms)}ms</div>
          <div class="health-metric__label">Avg Setup</div>
        </div>
        <div class="health-metric">
          <div class="health-metric__value" style="font-size:1.1rem;color:${h.openai_ws_errors_1h > 5 ? 'var(--status-warn)' : 'var(--text-primary)'}">${h.openai_ws_errors_1h}</div>
          <div class="health-metric__label">WS Errors/1h</div>
        </div>
      </div>
    `;
  }
}
