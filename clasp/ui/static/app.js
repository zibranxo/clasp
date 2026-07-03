/**
 * CLASP web UI — Alpine.js application.
 *
 * Full build (plan.md §15, Phase 7): all 6 panels —
 * Providers, Models, Dashboard, Routing, Advanced, Logs — plus live
 * SSE metrics/log streaming.
 *
 * Visual layer additions (cosmetic only — no endpoint changes):
 *  - initParallax()       : mouse-move + scroll + blob-drift parallax on background orbs
 *  - initCardTilt()       : 3-D perspective tilt on .tilt-card elements
 *  - initMagneticButtons(): subtle magnetic pull on .magnetic elements
 *  - startClock()         : live HH:MM:SS in header
 *  - animateValue()       : smooth animated counter for SSE stat updates
 *  - animateStatCard()    : breathing glow on stat card update
 *  - pushSparkPoint()     : ring-buffer data accumulator for sparklines
 *  - renderSparkline()    : SVG polyline renderer
 *  - Command palette      : showCommandPalette, commandQuery, commands, filteredCommands()
 *  - navIcons             : crisp inline SVG icons (replaces emoji)
 */
function claspApp() {
  return {

    // ── UI state ──────────────────────────────────────────────────────
    panel: 'providers',
    dirty: false,
    saving: false,
    toast: { show: false, message: '', type: 'success' },
    logLevel: 'INFO',
    logLines: [],
    sharedPoolTokensText: '',

    // ── Visual state ──────────────────────────────────────────────────
    currentTime: '',
    showCommandPalette: false,
    commandQuery: '',

    // Sparkline ring-buffer (last 24 data points per metric)
    sparkData: {
      requests: [],
      tokens:   [],
      absorbed: [],
      cache:    [],
    },

    // ── Data ──────────────────────────────────────────────────────────
    meta: { version: '1.0.0', status: 'healthy', port: 8082 },
    config: {},
    catalog: {},   // provider_catalog.py data served from /internal/catalog
    live: {},      // Live metrics from SSE (/internal/stream)

    nav: [
      { id: 'providers', label: 'Providers' },
      { id: 'models',    label: 'Models'    },
      { id: 'dashboard', label: 'Dashboard' },
      { id: 'routing',   label: 'Routing'   },
      { id: 'advanced',  label: 'Advanced'  },
      { id: 'logs',      label: 'Logs'      },
    ],

    // Inline SVG nav icons (18×18, Lucide-style, stroke="currentColor")
    navIcons: {
      providers: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="7.5" cy="15.5" r="5.5"/><path d="M21 2l-9.6 9.6M15.5 7.5l3 3"/></svg>`,
      models:    `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>`,
      dashboard: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></svg>`,
      routing:   `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6"  cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51"  x2="8.59"  y2="10.49"/></svg>`,
      advanced:  `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><line x1="4"  y1="21" x2="4"  y2="14"/><line x1="4"  y1="10" x2="4"  y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8"  x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1"  y1="14" x2="7"  y2="14"/><line x1="9"  y1="8"  x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>`,
      logs:      `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>`,
      save:      `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>`,
      discard:   `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
      export:    `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>`,
      clear:     `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>`,
    },

    // Command palette commands (cosmetic only — call existing methods)
    commands: [
      { id: 'go-providers', icon: 'providers', label: 'Go to Providers',  kbd: '',     action() { this.panel = 'providers'; this.showCommandPalette = false; } },
      { id: 'go-models',    icon: 'models',    label: 'Go to Models',     kbd: '',     action() { this.panel = 'models';    this.showCommandPalette = false; } },
      { id: 'go-dashboard', icon: 'dashboard', label: 'Go to Dashboard',  kbd: '',     action() { this.panel = 'dashboard'; this.showCommandPalette = false; } },
      { id: 'go-routing',   icon: 'routing',   label: 'Go to Routing',    kbd: '',     action() { this.panel = 'routing';   this.showCommandPalette = false; } },
      { id: 'go-advanced',  icon: 'advanced',  label: 'Go to Advanced',   kbd: '',     action() { this.panel = 'advanced';  this.showCommandPalette = false; } },
      { id: 'go-logs',      icon: 'logs',      label: 'Go to Logs',       kbd: '',     action() { this.panel = 'logs';      this.showCommandPalette = false; } },
      { id: 'save',         icon: 'save',      label: 'Save & Apply',     kbd: '',     action() { this.saveAndApply();    this.showCommandPalette = false; } },
      { id: 'discard',      icon: 'discard',   label: 'Discard Changes',  kbd: '',     action() { this.discardChanges();  this.showCommandPalette = false; } },
      { id: 'export',       icon: 'export',    label: 'Export config.yaml', kbd: '',   action() { this.exportConfig();    this.showCommandPalette = false; } },
      { id: 'clear-cache',  icon: 'clear',     label: 'Clear Cache',      kbd: '',     action() { this.clearCache();      this.showCommandPalette = false; } },
    ],

    filteredCommands() {
      const q = this.commandQuery.trim().toLowerCase();
      return q
        ? this.commands.filter(c => c.label.toLowerCase().includes(q))
        : this.commands;
    },

    // Models & Routing data structures (unchanged from original)
    modelTiers: [
      { key: 'opus',    label: 'claude-opus-*',    hint: 'Most capable' },
      { key: 'sonnet',  label: 'claude-sonnet-*',  hint: 'Balanced' },
      { key: 'haiku',   label: 'claude-haiku-*',   hint: 'Fast & light' },
      { key: 'fable',   label: 'claude-fable-*',   hint: 'Catch-all for new names' },
      { key: 'default', label: 'default',           hint: 'No tier match' },
    ],

    requestTypes: [
      { key: 'think',        label: 'Thinking / reasoning',   hint: 'Extended thinking enabled'       },
      { key: 'long_context', label: 'Long context (>50k tok)', hint: 'Route to high-context provider'  },
      { key: 'background',   label: 'Background tasks',        hint: 'File indexing, summarization'    },
      { key: 'vision',       label: 'Vision / image input',    hint: 'Route to vision-capable provider' },
    ],

    strategies: [
      { key: 'priority-chain', label: 'Priority Chain',  description: 'Try providers in order above. Skip unhealthy ones.' },
      { key: 'least-loaded',   label: 'Least Loaded',    description: 'Always route to provider with the most remaining RPM.' },
      { key: 'cost-aware',     label: 'Cost Aware',      description: 'Prefer fully-free providers, then free-credit providers.' },
    ],

    // ── Init ──────────────────────────────────────────────────────────
    async init() {
      // Core data (unchanged)
      await Promise.all([this.loadConfig(), this.loadCatalog()]);
      this.startLiveStream();
      this.startLogStream();

      // Visual enhancements (purely cosmetic)
      this.startClock();
      this.$nextTick(() => {
        this.initParallax();
        this.initCardTilt();
        this.initMagneticButtons();
        this.initKeyboardShortcuts();
      });
    },

    // ── Data loading (unchanged) ───────────────────────────────────────
    async loadConfig() {
      const res = await fetch('/internal/config');
      const data = await res.json();
      // Add UI-only fields to each provider (not persisted on save).
      for (const name in (data.providers ?? {})) {
        const p = data.providers[name];
        p._open = p.enabled;  // Expand enabled providers by default
        p.keys = (p.keys ?? []).map(k => ({
          value: k, _reveal: false, _testing: false, _testResult: null
        }));
      }
      this.config = data;
      this.meta.port    = data.server?.port    ?? this.meta.port;
      this.meta.version = data.version         ?? this.meta.version;
      if (data.shared_pool?.auth_tokens) {
        this.sharedPoolTokensText = data.shared_pool.auth_tokens.join('\n');
      }
    },

    async loadCatalog() {
      const res = await fetch('/internal/catalog');
      this.catalog = await res.json();
    },

    // ── Live streams (unchanged) ───────────────────────────────────────
    startLiveStream() {
      const connect = () => {
        const es = new EventSource('/internal/stream');
        es.onmessage = (e) => {
          const prev = { ...this.live };
          this.live = JSON.parse(e.data);
          this.meta.status = this.live.status;

          // Feed sparklines
          this.pushSparkPoint('requests', this.live.requests_today ?? 0);
          this.pushSparkPoint('tokens',   this.live.tokens_today   ?? 0);
          this.pushSparkPoint('absorbed', this.live.absorbed_429s_today ?? 0);
          this.pushSparkPoint('cache',    this.live.cache_hits_today    ?? 0);

          // Animate stat cards on value change
          this.$nextTick(() => {
            if ((prev.requests_today ?? 0) !== (this.live.requests_today ?? 0))
              this.animateStatCard('stat-requests');
            if ((prev.absorbed_429s_today ?? 0) !== (this.live.absorbed_429s_today ?? 0))
              this.animateStatCard('stat-absorbed');
          });
        };
        es.onerror = () => {
          this.meta.status = 'error';
          es.close();
          setTimeout(connect, 3000);
        };
      };
      connect();
    },

    startLogStream() {
      const connect = () => {
        const es = new EventSource('/internal/logs/stream');
        es.onmessage = (e) => {
          const line = JSON.parse(e.data);
          this.logLines.push(line);
          if (this.logLines.length > 500) this.logLines.shift();
          this.$nextTick(() => {
            const el = document.getElementById('log-container');
            if (el) el.scrollTop = el.scrollHeight;
          });
        };
        es.onerror = () => { es.close(); setTimeout(connect, 3000); };
      };
      connect();
    },

    filteredLogs() {
      const levels = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3 };
      const minLevel = levels[this.logLevel] ?? 1;
      return this.logLines.filter(l => (levels[l.level] ?? 0) >= minLevel);
    },

    // ── Config save/discard (unchanged) ───────────────────────────────
    markDirty() { this.dirty = true; },

    discardChanges() {
      this.dirty = false;
      this.loadConfig();
    },

    async saveAndApply() {
      this.saving = true;
      try {
        const payload = this._buildPayload();
        const res = await fetch('/internal/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (res.ok) {
          this.dirty = false;
          this.showToast('Config saved and applied ✓', 'success');
          await this.loadConfig();
        } else {
          const err = await res.json();
          this.showToast('Error: ' + (err.detail ?? 'Validation failed'), 'error');
        }
      } catch (e) {
        this.showToast('Network error: ' + e.message, 'error');
      } finally {
        this.saving = false;
      }
    },

    _buildPayload() {
      // Serialize config back to wire format (strip UI-only _fields).
      const cfg = JSON.parse(JSON.stringify(this.config));
      for (const name in (cfg.providers ?? {})) {
        const p = cfg.providers[name];
        p.keys = (p.keys ?? []).map(k => k.value);
        delete p._open;
      }
      return cfg;
    },

    // ── Provider key management (unchanged) ───────────────────────────
    addKey(providerName) {
      this.config.providers[providerName].keys.push({
        value: '', _reveal: false, _testing: false, _testResult: null
      });
      this.markDirty();
    },

    removeKey(providerName, idx) {
      this.config.providers[providerName].keys.splice(idx, 1);
      this.markDirty();
    },

    async testKey(providerName, keyValue, keyIndex) {
      const prov = this.config.providers[providerName];
      if (!prov?.keys?.[keyIndex]) return;
      prov.keys[keyIndex]._testing = true;
      try {
        const res = await fetch('/internal/config/test-key', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider: providerName, key: keyValue, key_index: keyIndex }),
        });
        prov.keys[keyIndex]._testResult = await res.json();
      } finally {
        prov.keys[keyIndex]._testing = false;
      }
    },

    providerStatusText(name) {
      const pd = this.live.providers?.[name];
      if (!pd) return 'OFF';
      return pd.status ?? 'UNKNOWN';
    },

    providerStatusClass(name) {
      const status = this.providerStatusText(name);
      return {
        'badge-healthy': status === 'HEALTHY',
        'badge-warning': status === 'SOFT_LIMIT',
        'badge-cooling': status === 'COOLING_DOWN',
        'badge-error':   status === 'CIRCUIT_OPEN',
        'badge-off':     status === 'OFF' || status === 'UNKNOWN',
      };
    },

    // ── Models & Routing (unchanged) ──────────────────────────────────
    resetModelDefaults() {
      fetch('/internal/catalog/defaults')
        .then(r => r.json())
        .then(defaults => {
          this.config.routing.models  = defaults.models;
          this.config.routing.by_type = defaults.by_type;
          this.markDirty();
        });
    },

    removeFromChain(idx) {
      this.config.provider_chain.splice(idx, 1);
      this.markDirty();
    },

    enabledProviders() {
      return Object.keys(this.config.providers ?? {})
        .filter(n => this.config.providers[n].enabled);
    },

    // ── Advanced panel (unchanged) ─────────────────────────────────────
    syncSharedTokens() {
      this.config.shared_pool.auth_tokens = this.sharedPoolTokensText
        .split('\n').map(t => t.trim()).filter(Boolean);
      this.markDirty();
    },

    async clearCache() {
      await fetch('/internal/cache/clear', { method: 'POST' });
      this.showToast('Cache cleared', 'success');
    },

    async exportConfig() {
      const res = await fetch('/internal/config/export');
      const yaml = await res.text();
      const blob = new Blob([yaml], { type: 'text/yaml' });
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      a.href = url; a.download = 'clasp-config.yaml';
      a.click(); URL.revokeObjectURL(url);
    },

    async importConfig(event) {
      const file = event.target.files[0];
      if (!file) return;
      const text = await file.text();
      const res  = await fetch('/internal/config/import', {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain' },
        body: text,
      });
      if (res.ok) {
        await this.loadConfig();
        this.showToast('Config imported ✓', 'success');
      } else {
        this.showToast('Import failed — invalid YAML', 'error');
      }
    },

    // ── Misc helpers (unchanged) ───────────────────────────────────────
    formatUptime(seconds) {
      if (!seconds) return '—';
      const h = Math.floor(seconds / 3600);
      const m = Math.floor((seconds % 3600) / 60);
      return h > 0 ? `${h}h ${m}m` : `${m}m`;
    },

    showToast(message, type) {
      this.toast = { show: true, message, type };
      setTimeout(() => { this.toast.show = false; }, 3500);
    },

    // ════════════════════════════════════════════════════════════════
    //  VISUAL LAYER — cosmetic additions below, no endpoint impact
    // ════════════════════════════════════════════════════════════════

    // ── Live clock ────────────────────────────────────────────────────
    startClock() {
      const tick = () => {
        const now = new Date();
        this.currentTime = now.toLocaleTimeString('en-US', {
          hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit'
        });
      };
      tick();
      setInterval(tick, 1000);
    },

    // ── Keyboard shortcuts ────────────────────────────────────────────
    initKeyboardShortcuts() {
      document.addEventListener('keydown', (e) => {
        // Ctrl+K / Cmd+K → command palette
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
          e.preventDefault();
          this.showCommandPalette = !this.showCommandPalette;
          if (this.showCommandPalette) {
            this.commandQuery = '';
            this.$nextTick(() => this.$refs.cmdInput?.focus());
          }
        }
        // Escape → close palette
        if (e.key === 'Escape') {
          this.showCommandPalette = false;
        }
      });
    },

    // ── Parallax ─────────────────────────────────────────────────────
    /**
     * Three-layer parallax driven by a single requestAnimationFrame loop:
     *  1. Sinusoidal blob drift  — slow, organic background movement
     *  2. Mouse parallax         — orbs shift against cursor at different rates
     *  3. Scroll parallax        — orbs shift as panel content scrolls
     *
     * All three are combined into a single translate3d per orb — no
     * CSS animation conflict, full GPU acceleration.
     */
    initParallax() {
      const shells = [
        document.getElementById('orb-shell-1'),
        document.getElementById('orb-shell-2'),
        document.getElementById('orb-shell-3'),
      ];

      if (!shells[0]) return;   // bail if DOM not ready

      // Per-orb parameters
      const cfg = [
        // blobAmp=px,  blobPeriod=ms,   blobPhase=rad,  mouseX, mouseY, scrollY
        { bx: 90,  by: 60,  bt: 22000, bp: 0,             mx:  0.028, my:  0.022, sy:  0.18  },
        { bx: 70,  by: 80,  bt: 30000, bp: Math.PI*0.66,  mx: -0.044, my: -0.034, sy: -0.26  },
        { bx: 55,  by: 45,  bt: 25000, bp: Math.PI*1.33,  mx:  0.018, my:  0.038, sy:  0.14  },
      ];

      // Smoothed mouse position (lerped for silky movement)
      let rawMX = 0, rawMY = 0;
      let smMX  = 0, smMY  = 0;
      let scrollY = 0;
      const LERP = 0.055;   // lower = smoother but slower response

      document.addEventListener('mousemove', (e) => {
        rawMX = e.clientX - window.innerWidth  / 2;
        rawMY = e.clientY - window.innerHeight / 2;
      }, { passive: true });

      const mainContent = document.getElementById('main-content');
      if (mainContent) {
        mainContent.addEventListener('scroll', () => {
          scrollY = mainContent.scrollTop;
        }, { passive: true });
      }

      const startTime = performance.now();

      const tick = (now) => {
        // Lerp mouse for smooth follow
        smMX += (rawMX - smMX) * LERP;
        smMY += (rawMY - smMY) * LERP;

        cfg.forEach((c, i) => {
          if (!shells[i]) return;
          const t = (now - startTime) / c.bt * Math.PI * 2;

          const blobX = Math.sin(t + c.bp)        * c.bx;
          const blobY = Math.cos(t * 0.7 + c.bp)  * c.by;

          const totalX = blobX + smMX  * c.mx + scrollY * c.sy * 0.3;
          const totalY = blobY + smMY  * c.my + scrollY * c.sy;

          shells[i].style.transform =
            `translate3d(${totalX.toFixed(2)}px, ${totalY.toFixed(2)}px, 0)`;
        });

        requestAnimationFrame(tick);
      };

      requestAnimationFrame(tick);
    },

    // ── 3-D card tilt on hover ────────────────────────────────────────
    initCardTilt() {
      const MAX_TILT = 4;   // degrees

      const applyTilt = (card, e) => {
        const rect = card.getBoundingClientRect();
        const x    = (e.clientX - rect.left)  / rect.width  - 0.5;  // –0.5 … +0.5
        const y    = (e.clientY - rect.top)   / rect.height - 0.5;

        card.style.transform =
          `perspective(700px) rotateX(${(-y * MAX_TILT).toFixed(2)}deg) rotateY(${(x * MAX_TILT).toFixed(2)}deg) translateY(-3px)`;
        card.style.transition = 'transform 0.1s ease';
      };

      const resetTilt = (card) => {
        card.style.transform  = '';
        card.style.transition = 'transform 0.4s ease';
      };

      // Delegate via document to pick up dynamically rendered Alpine cards
      document.addEventListener('mousemove', (e) => {
        const card = e.target.closest('.tilt-card');
        if (card) applyTilt(card, e);
      }, { passive: true });

      document.addEventListener('mouseleave', (e) => {
        const card = e.target.closest?.('.tilt-card');
        if (card) resetTilt(card);
      }, { passive: true });

      // Use mouseover/mouseout to detect leaving a card
      document.addEventListener('mouseout', (e) => {
        const card = e.target.closest('.tilt-card');
        if (card && !card.contains(e.relatedTarget)) resetTilt(card);
      }, { passive: true });
    },

    // ── Magnetic button pull ──────────────────────────────────────────
    initMagneticButtons() {
      const PULL = 0.18;   // fraction of offset to pull by

      document.addEventListener('mousemove', (e) => {
        document.querySelectorAll('.magnetic').forEach(el => {
          const rect = el.getBoundingClientRect();
          // Only activate when cursor is close to the button
          const dx = e.clientX - (rect.left + rect.width  / 2);
          const dy = e.clientY - (rect.top  + rect.height / 2);
          const dist = Math.hypot(dx, dy);
          const threshold = Math.max(rect.width, rect.height) * 1.5;

          if (dist < threshold) {
            el.style.transform = `translate(${(dx * PULL).toFixed(2)}px, ${(dy * PULL).toFixed(2)}px)`;
          } else {
            el.style.transform = '';
          }
        });
      }, { passive: true });

      // Reset on any scroll
      document.addEventListener('scroll', () => {
        document.querySelectorAll('.magnetic').forEach(el => {
          el.style.transform = '';
        });
      }, { passive: true, capture: true });
    },

    // ── Stat card breathe animation on SSE data change ─────────────────
    animateStatCard(elId) {
      const el = document.getElementById(elId);
      if (!el) return;
      el.classList.remove('updating');
      void el.offsetWidth;        // force reflow to restart animation
      el.classList.add('updating');
    },

    // ── Smooth animated counter ───────────────────────────────────────
    animateValue(el, end, duration = 480) {
      if (!el) return;
      const start    = parseInt(el.textContent.replace(/[^0-9]/g, '')) || 0;
      const range    = end - start;
      if (range === 0) return;
      const startT   = performance.now();

      const step = (now) => {
        const progress = Math.min((now - startT) / duration, 1);
        const eased    = 1 - Math.pow(1 - progress, 3);   // ease-out cubic
        el.textContent = Math.floor(start + range * eased).toLocaleString();
        if (progress < 1) requestAnimationFrame(step);
        else              el.textContent = end.toLocaleString();
      };
      requestAnimationFrame(step);
    },

    // ── Sparkline ring-buffer ─────────────────────────────────────────
    pushSparkPoint(key, value) {
      if (!this.sparkData[key]) return;
      this.sparkData[key].push(value);
      if (this.sparkData[key].length > 24) this.sparkData[key].shift();
    },

    /** Returns an SVG polyline string or '' if not enough data. */
    renderSparkline(data) {
      if (!data || data.length < 2) return '';
      const W = 60, H = 22;
      const max = Math.max(...data, 1);
      const min = Math.min(...data, 0);
      const range = max - min || 1;

      const pts = data.map((v, i) => {
        const x = (i / (data.length - 1)) * W;
        const y = H - ((v - min) / range) * H;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');

      // Area fill gradient path
      const firstX  = '0';
      const firstY  = (H - ((data[0] - min) / range) * H).toFixed(1);
      const lastX   = W.toString();
      const lastY   = (H - ((data[data.length - 1] - min) / range) * H).toFixed(1);

      return `<svg class="sparkline" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">
        <defs>
          <linearGradient id="spark-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stop-color="var(--accent)" stop-opacity="0.25"/>
            <stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/>
          </linearGradient>
        </defs>
        <path d="M${firstX},${H} L${firstX},${firstY} L${pts.replace(/(\d+\.\d+),(\d+\.\d+)/g, (_, x, y) => `${x},${y}`)} L${lastX},${H} Z"
              fill="url(#spark-fill)" />
        <polyline points="${pts}"
                  fill="none"
                  stroke="var(--accent)"
                  stroke-width="1.5"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  vector-effect="non-scaling-stroke"/>
      </svg>`;
    },

  };  // end claspApp() return
}