# Dangerous Pattern Scan (OWASP A05, A08, A04, A10, A02)

**Files scanned:** 69  
**Findings:** 21

| Location | OWASP | Sev hint | Pattern | Evidence |
| --- | --- | --- | --- | --- |
| dashboard/app.py:46 | A10 | Low | Bare/broad except (Python) | except Exception: |
| tasks/feature-transcripts-dashboard-architecture.md:307 | A10 | Low | Bare/broad except (Python) | except Exception: |
| core/logger.py:45 | A10 | Low | Bare/broad except (Python) | except Exception: |
| sip_bridge/webhook_handler.py:246 | A10 | Low | Bare/broad except (Python) | except Exception: |
| dashboard/routes/logs.py:33 | A10 | Low | Bare/broad except (Python) | except Exception: |
| dashboard/static/js/dashboard.js:73 | A05 | Medium | innerHTML assignment | connBadge.innerHTML = `<span class="conn-badge__dot"></span>${labels[state] \|\| state}`; |
| dashboard/static/js/ws-client.js:92 | A04 | Low | Insecure random for security | const jitter = Math.random() * 1000; |
| dashboard/static/js/calls-panel.js:28 | A05 | Medium | innerHTML assignment | this._tbody.innerHTML = '<tr><td colspan="6" class="empty">No calls</td></tr>'; |
| dashboard/static/js/calls-panel.js:32 | A05 | Medium | innerHTML assignment | this._tbody.innerHTML = calls.map(c => ` |
| dashboard/static/js/logs-panel.js:47 | A05 | Medium | innerHTML assignment | this._el.innerHTML = ''; |
| dashboard/static/js/logs-panel.js:59 | A05 | Medium | innerHTML assignment | row.innerHTML = ` |
| dashboard/static/js/tokens-panel.js:20 | A05 | Medium | innerHTML assignment | this._el.innerHTML = '<p class="empty">No token data yet</p>'; |
| dashboard/static/js/tokens-panel.js:28 | A05 | Medium | innerHTML assignment | this._el.innerHTML = ` |
| dashboard/static/js/transcript-panel.js:121 | A05 | Medium | innerHTML assignment | this._transcriptEl.innerHTML = '<p class="empty">Select a call to view transcript</p>'; |
| dashboard/static/js/transcript-panel.js:128 | A05 | Medium | innerHTML assignment | this._transcriptEl.innerHTML = '<p class="empty">No transcript yet</p>'; |
| dashboard/static/js/transcript-panel.js:132 | A05 | Medium | innerHTML assignment | this._transcriptEl.innerHTML = turns.map(t => { |
| dashboard/static/js/transcript-panel.js:151 | A05 | Medium | innerHTML assignment | this._eventsEl.innerHTML = '<p class="empty">Select a call to view events</p>'; |
| dashboard/static/js/transcript-panel.js:156 | A05 | Medium | innerHTML assignment | this._eventsEl.innerHTML = '<p class="empty">No events yet</p>'; |
| dashboard/static/js/transcript-panel.js:159 | A05 | Medium | innerHTML assignment | this._eventsEl.innerHTML = evts.map(e => { |
| dashboard/static/js/transcript-panel.js:188 | A05 | Medium | innerHTML assignment | this._selectorEl.innerHTML = '<option value="">— select call —</option>'; |
| dashboard/static/js/health-panel.js:17 | A05 | Medium | innerHTML assignment | this._el.innerHTML = ` |
