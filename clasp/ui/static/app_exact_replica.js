// CLASP UI - Exact KeyKing Replica
// This implements the exact KeyKing interface using Alpine.js

function claspApp() {
  return {
    // State
    panel: 'providers',
    dirty: false,
    saving: false,

    // Navigation structure matching KeyKing
    nav: [
      { id: 'providers', label: 'Provider Keys', icon: '🔑' },
      { id: 'dashboard', label: 'Dashboard', icon: '📊' },
      { id: 'routing', label: 'Routing Logs', icon: '🔀' },
      { id: 'priority', label: 'Priority Rules', icon: '⚡' },
      { id: 'settings', label: 'Settings', icon: '⚙️' },
    ],

    // Data
    config: {},
    catalog: {},
    live: {},

    // Initialize
    async init() {
      await this.loadConfig();
      await this.loadCatalog();
      this.startLiveStream();
    },

    // Load config
    async loadConfig() {
      const res = await fetch('/internal/config');
      this.config = await res.json();
    },

    // Load catalog
    async loadCatalog() {
      const res = await fetch('/internal/catalog');
      this.catalog = await res.json();
    },

    // Mark changes as dirty
    markDirty() {
      this.dirty = true;
    },

    // Save configuration
    async saveAndApply() {
      this.saving = true;
      try {
        const payload = this._buildPayload();
        const res = await fetch('/internal/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (res.ok) {
          this.dirty = false;
          this.showToast('Config saved successfully', 'success');
          await this.loadConfig();
        } else {
          const err = await res.json();
          this.showToast('Error: ' + (err.detail || 'Failed to save'), 'error');
        }
      } catch (err) {
        this.showToast('Network error: ' + err.message, 'error');
      } finally {
        this.saving = false;
      }
    },

    // Build payload for saving
    _buildPayload() {
      const cfg = JSON.parse(JSON.stringify(this.config));
      for (const name in cfg.providers) {
        const prov = cfg.providers[name];
        prov.keys = prov.keys.map(k => k.value);
        delete prov._open;
      }
      return cfg;
    },

    // Show toast notification
    showToast(message, type = 'success') {
      this.toast = { show: true, message, type };
      setTimeout(() => {
        this.toast.show = false;
      }, 3500);
    },

    // Provider status text
    providerStatusText(name) {
      const pd = this.live.providers?.[name];
      if (!pd) return 'OFF';
      return pd.status || 'UNKNOWN';
    },

    // Provider status class
    providerStatusClass(name) {
      const status = this.providerStatusText(name);
      return {
        'neo-badge neo-badge-healthy': status === 'HEALTHY',
        'neo-badge neo-badge-warning': status === 'SOFT_LIMIT',
        'neo-badge neo-badge-info': status === 'COOLING_DOWN',
        'neo-badge neo-badge-error': status === 'CIRCUIT_OPEN',
        'neo-badge neo-badge-off': status === 'OFF' || status === 'UNKNOWN'
      };
    },

    // Add API key
    addKey(providerName) {
      const prov = this.config.providers[providerName];
      if (!prov) return;
      prov.keys.push({
        value: '',
        _reveal: false,
        _testing: false,
        _testResult: null
      });
      this.markDirty();
    },

    // Remove API key
    removeKey(providerName, idx) {
      const prov = this.config.providers[providerName];
      if (prov) {
        prov.keys.splice(idx, 1);
        this.markDirty();
      }
    },

    // Test API key
    async testKey(providerName, keyValue, keyIndex) {
      const prov = this.config.providers[providerName];
      if (!prov?.keys?.[keyIndex]) return;

      prov.keys[keyIndex]._testing = true;
      try {
        const res = await fetch('/internal/config/test-key', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            provider: providerName,
            key: keyValue,
            key_index: keyIndex
          })
        });
        prov.keys[keyIndex]._testResult = await res.json();
      } catch (err) {
        prov.keys[keyIndex]._testResult = {
          ok: false,
          error: 'Network error'
        };
      } finally {
        prov.keys[keyIndex]._testing = false;
      }
    },

    // Toggle key visibility
    toggleKeyVisibility(providerName, keyIndex) {
      const prov = this.config.providers[providerName];
      if (prov?.keys?.[keyIndex]) {
        prov.keys[keyIndex]._reveal = !prov.keys[keyIndex]._reveal;
      }
    }
  };
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  console.log('CLASP UI initialized - Exact KeyKing replica');
});

    // Mark changes as dirty
    markDirty() {
      this.dirty = true;
    },

    // Save config
    async saveAndApply() {
      this.saving = true;
      try {
        const res = await fetch('/internal/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.config),
        });
        if (res.ok) {
          this.dirty = false;
          await this.loadConfig();
        }
      } finally {
        this.saving = false;
      }
    },

    // Provider status
    providerStatusText(name) {
      return this.live.providers?.[name]?.status ?? 'OFF';
    },

    // Add key
    addKey(providerName) {
      // Implementation
    },

    // Remove key
    removeKey(providerName, idx) {
      // Implementation
    },

    // Test key
    async testKey(providerName, keyValue, keyIndex) {
      // Implementation
    }
  };
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  // App will be initialized here
});