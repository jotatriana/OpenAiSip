class TranscriptPanel {
  /**
   * Shows live transcript turns and call event timeline for a selected call.
   *
   * @param {string} transcriptBodyId - Container for transcript turns
   * @param {string} eventsBodyId     - Container for call event timeline
   * @param {string} selectorId       - <select> element for picking active call
   */
  constructor(transcriptBodyId, eventsBodyId, selectorId) {
    this._transcriptEl = document.getElementById(transcriptBodyId);
    this._eventsEl     = document.getElementById(eventsBodyId);
    this._selectorEl   = document.getElementById(selectorId);

    // call_id → Map<turn_index, turn> (deduplicates on reconnect)
    this._transcripts = new Map();
    // call_id → [event, ...]
    this._events      = new Map();
    // call_id → call metadata (for stable label including created_at)
    this._callMeta    = new Map();

    this._selectedCallId = null;
    this._autoScroll = true;
    this._renderTimer = null;

    this._selectorEl.addEventListener('change', () => {
      this._selectedCallId = this._selectorEl.value || null;
      this._renderNow();
    });

    this._transcriptEl.addEventListener('scroll', () => {
      const { scrollTop, scrollHeight, clientHeight } = this._transcriptEl;
      this._autoScroll = scrollHeight - scrollTop - clientHeight < 40;
    });
  }

  handleMessage(msg) {
    switch (msg.type) {
      case 'SNAPSHOT': {
        const transcripts = msg.payload.active_call_transcripts || {};
        const events      = msg.payload.active_call_events      || {};
        for (const [cid, turns] of Object.entries(transcripts)) {
          // Merge into the existing map rather than replacing it.
          // TRANSCRIPT_TURN events may have arrived before the snapshot (the snapshot
          // is built from the DB at a point in time, so very recent turns that were
          // published to the bus but not yet committed to DB won't be in it).
          // Merging by turn_index preserves those early turns without duplication.
          let map = this._transcripts.get(cid);
          if (!map) { map = new Map(); this._transcripts.set(cid, map); }
          turns.forEach(t => map.set(t.turn_index, t));
        }
        for (const [cid, evts] of Object.entries(events)) {
          // Merge snapshot events with any live events that arrived before the snapshot.
          // Snapshot events have a DB `id`; live CALL_EVENT bus events don't.
          // Preserve live-only events so they aren't lost when the snapshot lands.
          const existing = this._events.get(cid) || [];
          const snapshotDbIds = new Set(evts.filter(e => e.id != null).map(e => String(e.id)));
          const liveOnly = existing.filter(e => e.id == null);
          this._events.set(cid, [...evts, ...liveOnly]);
        }
        (msg.payload.active_calls || []).forEach(c => this._callMeta.set(c.call_id, c));
        this._syncSelector(msg.payload.active_calls || []);
        this._scheduleRender();
        break;
      }
      case 'CALL_CREATED': {
        const cid = msg.payload.call_id;
        if (!this._transcripts.has(cid)) this._transcripts.set(cid, new Map());
        if (!this._events.has(cid))      this._events.set(cid, []);
        this._callMeta.set(cid, msg.payload);
        this._addSelectorOption(cid, msg.payload, true);  // prepend — new call goes to top
        // Auto-select the new call so the operator sees the live transcript immediately
        // without having to search through ended calls in the dropdown.
        this._selectorEl.value = cid;
        this._selectedCallId = cid;
        this._scheduleRender();
        break;
      }
      case 'CALL_ENDED':
      case 'CALL_UPDATED': {
        // Keep data; update selector label if needed
        const cid = msg.payload.call_id;
        this._updateSelectorOption(cid, msg.payload);
        this._scheduleRender();
        break;
      }
      case 'TRANSCRIPT_TURN': {
        const cid = msg.payload.call_id;
        if (!this._transcripts.has(cid)) this._transcripts.set(cid, new Map());
        this._transcripts.get(cid).set(msg.payload.turn_index, msg.payload);
        if (cid === this._selectedCallId) this._scheduleRender();
        break;
      }
      case 'CALL_EVENT': {
        const cid = msg.payload.call_id;
        if (!this._events.has(cid)) this._events.set(cid, []);
        this._events.get(cid).push(msg.payload);
        if (cid === this._selectedCallId) this._scheduleRender();
        break;
      }
    }
  }

  // ── Private ──────────────────────────────────────────────────────────────────

  _scheduleRender() {
    if (this._renderTimer) return;
    this._renderTimer = setTimeout(() => {
      this._renderTimer = null;
      this._renderNow();
    }, 150);
  }

  _renderNow() {
    this._renderTranscript();
    this._renderEvents();
  }

  _renderTranscript() {
    const cid = this._selectedCallId;
    if (!cid || !this._transcripts.has(cid)) {
      this._transcriptEl.innerHTML = '<p class="empty">Select a call to view transcript</p>';
      return;
    }
    const turns = Array.from(this._transcripts.get(cid).values())
      .sort((a, b) => a.turn_index - b.turn_index);

    if (!turns.length) {
      this._transcriptEl.innerHTML = '<p class="empty">No transcript yet</p>';
      return;
    }

    this._transcriptEl.innerHTML = turns.map(t => {
      const roleClass = t.role === 'assistant' ? 'tx-turn--assistant' : 'tx-turn--caller';
      const label     = t.role === 'assistant' ? 'Agent' : 'Caller';
      const time      = t.timestamp ? new Date(t.timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'America/New_York', hour12: false }) : '';
      const phase     = t.phase ? `<span class="tx-turn__phase">${this._esc(t.phase)}</span>` : '';
      return `<div class="tx-turn ${roleClass}">
        <span class="tx-turn__meta">${time} ${label}${phase}</span>
        <span class="tx-turn__text">${this._esc(t.text)}</span>
      </div>`;
    }).join('');

    if (this._autoScroll) {
      this._transcriptEl.scrollTop = this._transcriptEl.scrollHeight;
    }
  }

  _renderEvents() {
    const cid = this._selectedCallId;
    if (!cid || !this._events.has(cid)) {
      this._eventsEl.innerHTML = '<p class="empty">Select a call to view events</p>';
      return;
    }
    const evts = this._events.get(cid);
    if (!evts.length) {
      this._eventsEl.innerHTML = '<p class="empty">No events yet</p>';
      return;
    }
    this._eventsEl.innerHTML = evts.map(e => {
      const typeClass = this._eventClass(e.event_type);
      const time      = e.timestamp ? new Date(e.timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'America/New_York', hour12: false }) : '';
      const detail    = e.data ? this._summariseData(e.data) : '';
      return `<div class="ev-row ${typeClass}">
        <span class="ev-row__time">${time}</span>
        <span class="ev-row__type">${this._esc(e.event_type)}</span>
        ${detail ? `<span class="ev-row__detail">${detail}</span>` : ''}
      </div>`;
    }).join('');
  }

  _eventClass(type) {
    if (['tool_failed', 'ws_failed', 'call_rejected'].includes(type)) return 'ev-row--error';
    if (['escalated', 'ws_reconnected'].includes(type)) return 'ev-row--warn';
    if (type === 'phase_entered') return 'ev-row--phase';
    return '';
  }

  _summariseData(data) {
    if (data.phase)  return this._esc(data.phase);
    if (data.tool)   return this._esc(data.tool) + (data.reason ? ` — ${this._esc(data.reason)}` : '');
    if (data.attempt !== undefined) return `attempt ${data.attempt}`;
    if (data.attempts !== undefined) return `after ${data.attempts} attempts`;
    return '';
  }

  _syncSelector(activeCalls) {
    const current = this._selectorEl.value;
    this._selectorEl.innerHTML = '<option value="">— select call —</option>';
    const snapshotIds = new Set(activeCalls.map(c => c.call_id));
    activeCalls.forEach(c => this._addSelectorOption(c.call_id, c));
    // Re-add any calls that arrived via CALL_CREATED before the snapshot landed
    // and were therefore not included in snapshot.active_calls.
    // Prepend so they appear above the historical ended calls.
    for (const [cid, meta] of this._callMeta) {
      if (!snapshotIds.has(cid)) this._addSelectorOption(cid, meta, true);
    }
    if (current && [...this._selectorEl.options].some(o => o.value === current)) {
      this._selectorEl.value = current;
      this._selectedCallId = current;
    } else if (!this._selectedCallId && activeCalls.length > 0) {
      // No call is selected yet — auto-select the first live call so the operator
      // sees the transcript immediately without having to touch the dropdown.
      const live = activeCalls.find(c => ['ACTIVE', 'RINGING', 'TRANSFERRING'].includes(c.state));
      const pick = live || activeCalls[0];
      this._selectorEl.value = pick.call_id;
      this._selectedCallId = pick.call_id;
    } else {
      this._selectedCallId = null;
    }
  }

  _addSelectorOption(cid, call, prepend = false) {
    if ([...this._selectorEl.options].some(o => o.value === cid)) return;
    const opt = document.createElement('option');
    opt.value = cid;
    opt.textContent = this._callLabel(cid, call);
    if (prepend && this._selectorEl.options.length > 0) {
      this._selectorEl.insertBefore(opt, this._selectorEl.options[1] || null);
    } else {
      this._selectorEl.appendChild(opt);
    }
  }

  _updateSelectorOption(cid, call) {
    const opt = [...this._selectorEl.options].find(o => o.value === cid);
    if (opt) opt.textContent = this._callLabel(cid, call);
  }

  _callLabel(cid, call) {
    const meta  = this._callMeta.get(cid) || call;
    const from  = meta.caller_number || meta.from_uri || cid.slice(0, 8);
    const state = call.state ? ` [${call.state}]` : '';
    const ts    = meta.created_at ? this._shortDateTime(meta.created_at) : '';
    return ts ? `${from}${state} — ${ts}` : `${from}${state}`;
  }

  _shortDateTime(ts) {
    if (!ts) return '';
    const d = new Date(ts);
    if (isNaN(d)) return '';
    const tz = 'America/New_York';
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: tz })
      + ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZone: tz });
  }

  _esc(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }
}
