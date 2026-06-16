/**
 * CLASP web UI — Alpine.js application skeleton.
 *
 * Sprint 1 scope: Providers panel only (load config + catalog, toggle
 * enabled, add/remove keys, save & apply, discard).
 *
 * Deliberately NOT implemented yet (arrives in Sprint 6 — plan.md §15, §20
 * step 72): nav sidebar / other panels, live SSE metrics stream (`live`),
 * log stream, testKey(), model tier overrides, shared pool, export/import.
 * The shape of this object is kept compatible with the full app.js spec so
 * those features can be added without restructuring existing state.
 */
function claspApp() {
  return {
    // ── UI state ──────────────────────────────────────────────────────
    dirty: false,
    saving: false,
    toast: { show: false, message: '', type: 'success' },

    // ── Data ──────────────────────────────────────────────────────────
    meta: { version: '0.1.0' },
    config: {},
    catalog: {},   // provider_catalog.py data served from /internal/catalog

    async init() {
      await Promise.all([this.loadConfig(), this.loadCatalog()]);
    },

    async loadConfig() {
      const res = await fetch('/internal/config');
      const data = await res.json();
      // Add UI-only fields to each provider (not persisted on save).
      for (const name in (data.providers ?? {})) {
        const p = data.providers[name];
        p._open = p.enabled;  // Expand enabled providers by default
        p.keys = (p.keys ?? []).map(k => ({ value: k, _reveal: false }));
      }
      this.config = data;
      this.meta.version = data.version ?? this.meta.version;
    },

    async loadCatalog() {
      const res = await fetch('/internal/catalog');
      this.catalog = await res.json();
    },

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

    addKey(providerName) {
      this.config.providers[providerName].keys.push({ value: '', _reveal: false });
      this.markDirty();
    },

    removeKey(providerName, idx) {
      this.config.providers[providerName].keys.splice(idx, 1);
      this.markDirty();
    },

    showToast(message, type) {
      this.toast = { show: true, message, type };
      setTimeout(() => { this.toast.show = false; }, 3500);
    },
  };
}