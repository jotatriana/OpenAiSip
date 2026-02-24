class LogsPanel {
  /**
   * @param {string} containerId - ID of the log stream container div
   * @param {number} maxEntries - Max entries to keep in DOM
   */
  constructor(containerId, maxEntries = 200) {
    this._el = document.getElementById(containerId);
    this._maxEntries = maxEntries;
    this._autoScroll = true;
    this._levelFilter = 'DEBUG';
    this._entries = [];

    // Pause auto-scroll when user scrolls up
    this._el.addEventListener('scroll', () => {
      const { scrollTop, scrollHeight, clientHeight } = this._el;
      this._autoScroll = scrollHeight - scrollTop - clientHeight < 40;
    });
  }

  setLevelFilter(level) {
    this._levelFilter = level;
    this._renderAll();
  }

  handleMessage(msg) {
    if (msg.type === 'LOG_ENTRY') {
      this._addEntry(msg.payload);
    } else if (msg.type === 'SNAPSHOT') {
      this._entries = msg.payload.recent_logs || [];
      this._renderAll();
    }
  }

  _addEntry(entry) {
    this._entries.push(entry);
    if (this._entries.length > this._maxEntries * 2) {
      this._entries = this._entries.slice(-this._maxEntries);
    }
    if (this._shouldShow(entry)) {
      this._appendRow(entry);
      this._trimDOM();
      if (this._autoScroll) this._el.scrollTop = this._el.scrollHeight;
    }
  }

  _renderAll() {
    this._el.innerHTML = '';
    this._entries.filter(e => this._shouldShow(e)).forEach(e => this._appendRow(e));
    if (this._autoScroll) this._el.scrollTop = this._el.scrollHeight;
  }

  _appendRow(entry) {
    const levelClass = `log-entry__level--${entry.level.toLowerCase()}`;
    const rowClass = entry.level === 'WARNING' ? 'log-entry--warning'
      : ['ERROR', 'CRITICAL'].includes(entry.level) ? 'log-entry--error' : '';
    const time = new Date(entry.timestamp).toISOString().slice(11, 23);
    const row = document.createElement('div');
    row.className = `log-entry ${rowClass}`;
    row.innerHTML = `
      <span class="log-entry__time">${time}</span>
      <span class="log-entry__level ${levelClass}">${entry.level}</span>
      <span class="log-entry__msg">${this._esc(entry.message)}</span>
    `;
    this._el.appendChild(row);
  }

  _trimDOM() {
    while (this._el.childElementCount > this._maxEntries) {
      this._el.removeChild(this._el.firstElementChild);
    }
  }

  _shouldShow(entry) {
    const order = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];
    return order.indexOf(entry.level) >= order.indexOf(this._levelFilter);
  }

  _esc(str) {
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
}
