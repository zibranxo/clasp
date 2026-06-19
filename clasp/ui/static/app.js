/**
 * CLASP web UI — Alpine.js application.
 *
 * Full build (plan.md §15, Phase 7): all 6 panels —
 * Providers, Models, Dashboard, Routing, Advanced, Logs — plus live
 * SSE metrics/log streaming.
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

    // ── Data ──────────────────────────────────────────────────────────
    meta: { version: '1.0.0', status: 'healthy', port: 8082 },
    config: {},
    catalog: {},   // provider_catalog.py data served from /internal/catalog
    live: {},      // Live metrics from SSE (/internal/stream)

    nav: [
      { id: 'providers', icon: '🔑', label: 'Providers' },
      { id: 'models',    icon: '🎯', label: 'Models' },
      { id: 'dashboard', icon: '📊', label: 'Dashboard' },
      { id: 'routing',   icon: '🔀', label: 'Routing' },
      { id: 'advanced',  icon: '⚙️',  label: 'Advanced' },
      { id: 'logs',      icon: '📋', label: 'Logs' },
    ],

    // Models & Routing data structures
    modelTiers: [
      { key: 'opus',    label: 'claude-opus-*',    hint: 'Most capable' },
      { key: 'sonnet',  label: 'claude-sonnet-*',  hint: 'Balanced' },
      { key: 'haiku',   label: 'claude-haiku-*',   hint: 'Fast & light' },
      { key: 'fable',   label: 'claude-fable-*',   hint: 'Catch-all for new names' },
      { key: 'default', label: 'default',           hint: 'No tier match' },
    ],

    requestTypes: [
      { key: 'think',        label: 'Thinking / reasoning',
        hint: 'Extended thinking enabled' },
      { key: 'long_context', label: 'Long context  (>50k tokens)',
        hint: 'Route to high-context provider' },
      { key: 'background',   label: 'Background tasks',
        hint: 'File indexing, summarization' },
      { key: 'vision',       label: 'Vision / image input',
        hint: 'Route to vision-capable provider' },
    ],

    strategies: [
      { key: 'priority-chain', label: 'Priority Chain',
        description: 'Try providers in order above. Skip unhealthy ones.' },
      { key: 'least-loaded',   label: 'Least Loaded',
        description: 'Always route to provider with the most remaining RPM.' },
      { key: 'cost-aware',     label: 'Cost Aware',
        description: 'Prefer fully-free providers, then free-credit providers.' },
    ],

    async init() {
      await Promise.all([this.loadConfig(), this.loadCatalog()]);
      this.startLiveStream();
      this.startLogStream();
    },

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
      this.meta.port = data.server?.port ?? this.meta.port;
      this.meta.version = data.version ?? this.meta.version;
      if (data.shared_pool?.auth_tokens) {
        this.sharedPoolTokensText = data.shared_pool.auth_tokens.join('\n');
      }
    },

    async loadCatalog() {
      const res = await fetch('/internal/catalog');
      this.catalog = await res.json();
    },

    // ── Live streams ──────────────────────────────────────────────────

    startLiveStream() {
      const connect = () => {
        const es = new EventSource('/internal/stream');
        es.onmessage = (e) => {
          this.live = JSON.parse(e.data);
          this.meta.status = this.live.status;
        };
        es.onerror = () => {
          this.meta.status = 'error';
          es.close();
          setTimeout(connect, 3000);  // Reconnect after 3s
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

    // ── Config save/discard ───────────────────────────────────────────

    markDirty() {
      this.dirty = true;
    },

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

    // ── Provider key management ───────────────────────────────────────

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
        'bg-green-900 text-green-400':  status === 'HEALTHY',
        'bg-yellow-900 text-yellow-400': status === 'SOFT_LIMIT',
        'bg-orange-900 text-orange-400': status === 'COOLING_DOWN',
        'bg-red-900 text-red-400':      status === 'CIRCUIT_OPEN',
        'bg-gray-800 text-gray-500':    status === 'OFF' || status === 'UNKNOWN',
      };
    },

    // ── Models & Routing management ───────────────────────────────────

    resetModelDefaults() {
      fetch('/internal/catalog/defaults')
        .then(r => r.json())
        .then(defaults => {
          this.config.routing.models = defaults.models;
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

    // ── Advanced panel: cache / shared pool / export-import ───────────

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
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = 'clasp-config.yaml';
      a.click(); URL.revokeObjectURL(url);
    },

    async importConfig(event) {
      const file = event.target.files[0];
      if (!file) return;
      const text = await file.text();
      const res = await fetch('/internal/config/import', {
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

    // ── Misc helpers ───────────────────────────────────────────────────

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
  };
}