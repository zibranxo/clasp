// CLASP UI - Exact KeyKing Replica
// This implements the exact KeyKing interface using Alpine.js

function claspApp() {
  return {
    // State
    panel: 'providers',
    dirty: false,
    saving: false,

    // Vault UI State
    showEnableVaultModal: false,
    showUnlockVaultModal: false,
    showDisableVaultModal: false,
    showImportBackupModal: false,
    enableVaultPassphrase: '',
    enableVaultConfirm: '',
    unlockVaultPassphrase: '',
    disableVaultPassphrase: '',
    importBackupFile: null,
    importBackupPassphrase: '',

    // Navigation structure matching KeyKing
    nav: [
      { id: 'providers', label: 'Provider Keys', icon: '🔑' },
      { id: 'dashboard', label: 'Dashboard', icon: '📊' },
      { id: 'routing', label: 'Routing Logs', icon: '🔀' },
      { id: 'priority', label: 'Priority Rules', icon: '⚡' },
      { id: 'vault', label: 'Vault Encryption', icon: '🔐' },
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
      await this.initVault();
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

    // Vault Encryption State
    vault: {
      isEnabled: false,
      isUnlocked: false,
      passphrase: '',
      salt: null,
      encryptionKey: null,
      initializationVector: null,
      encryptedConfig: null
    },

    // Initialize vault system
    async initVault() {
      // Check if vault is enabled in localStorage
      const vaultEnabled = localStorage.getItem('clasp_vault_enabled') === 'true';
      this.vault.isEnabled = vaultEnabled;

      if (vaultEnabled) {
        // Try to load encrypted config
        const encryptedData = localStorage.getItem('clasp_encrypted_config');
        if (encryptedData) {
          this.vault.encryptedConfig = JSON.parse(encryptedData);
        }
      } else {
        // Load plaintext config normally
        await this.loadConfig();
      }
    },

    // Enable vault encryption
    async enableVault(passphrase) {
      try {
        // Generate salt
        const salt = new Uint8Array(16);
        window.crypto.getRandomValues(salt);
        this.vault.salt = salt;

        // Derive encryption key from passphrase
        const keyMaterial = await window.crypto.subtle.importKey(
          'raw',
          new TextEncoder().encode(passphrase),
          { name: 'PBKDF2' },
          false,
          ['deriveKey']
        );

        const key = await window.crypto.subtle.deriveKey(
          {
            name: 'PBKDF2',
            salt: salt,
            iterations: 100000,
            hash: 'SHA-256'
          },
          keyMaterial,
          { name: 'AES-GCM', length: 256 },
          true,
          ['encrypt', 'decrypt']
        );

        this.vault.encryptionKey = key;
        this.vault.isEnabled = true;
        this.vault.isUnlocked = true;
        this.vault.passphrase = passphrase;

        // Save vault enabled status
        localStorage.setItem('clasp_vault_enabled', 'true');
        localStorage.setItem('clasp_vault_salt', Array.from(salt).join(','));

        // Encrypt current config
        await this.encryptConfig();

        this.showToast('Vault encryption enabled successfully', 'success');
        return true;
      } catch (error) {
        console.error('Error enabling vault:', error);
        this.showToast('Failed to enable vault: ' + error.message, 'error');
        return false;
      }
    },

    // Unlock vault with passphrase
    async unlockVault(passphrase) {
      try {
        // Get salt from localStorage
        const saltString = localStorage.getItem('clasp_vault_salt');
        if (!saltString) {
          throw new Error('Vault not properly initialized');
        }

        const salt = new Uint8Array(saltString.split(',').map(Number));

        // Derive key from passphrase
        const keyMaterial = await window.crypto.subtle.importKey(
          'raw',
          new TextEncoder().encode(passphrase),
          { name: 'PBKDF2' },
          false,
          ['deriveKey']
        );

        const key = await window.crypto.subtle.deriveKey(
          {
            name: 'PBKDF2',
            salt: salt,
            iterations: 100000,
            hash: 'SHA-256'
          },
          keyMaterial,
          { name: 'AES-GCM', length: 256 },
          true,
          ['encrypt', 'decrypt']
        );

        this.vault.encryptionKey = key;
        this.vault.isUnlocked = true;
        this.vault.passphrase = passphrase;
        this.vault.salt = salt;

        // Load and decrypt config
        await this.loadEncryptedConfig();

        this.showToast('Vault unlocked successfully', 'success');
        return true;
      } catch (error) {
        console.error('Error unlocking vault:', error);
        this.showToast('Failed to unlock vault: ' + error.message, 'error');
        return false;
      }
    },

    // Lock vault
    lockVault() {
      // Clear sensitive data from memory
      this.vault.isUnlocked = false;
      this.vault.passphrase = '';
      this.vault.encryptionKey = null;
      this.vault.initializationVector = null;

      // Clear config from memory (will need to unlock to access)
      this.config = {};

      this.showToast('Vault locked', 'info');
    },

    // Disable vault encryption
    async disableVault() {
      try {
        // Decrypt and save config in plaintext
        await this.loadEncryptedConfig();
        await this.saveConfigPlaintext();

        // Clear vault data
        localStorage.removeItem('clasp_vault_enabled');
        localStorage.removeItem('clasp_vault_salt');
        localStorage.removeItem('clasp_encrypted_config');

        // Reset vault state
        this.vault = {
          isEnabled: false,
          isUnlocked: false,
          passphrase: '',
          salt: null,
          encryptionKey: null,
          initializationVector: null,
          encryptedConfig: null
        };

        this.showToast('Vault encryption disabled', 'success');
        return true;
      } catch (error) {
        console.error('Error disabling vault:', error);
        this.showToast('Failed to disable vault: ' + error.message, 'error');
        return false;
      }
    },

    // Encrypt configuration
    async encryptConfig() {
      try {
        if (!this.vault.encryptionKey) {
          throw new Error('Vault not unlocked');
        }

        // Generate initialization vector
        const iv = new Uint8Array(12);
        window.crypto.getRandomValues(iv);
        this.vault.initializationVector = iv;

        // Convert config to JSON string
        const configJson = JSON.stringify(this.config);

        // Encrypt
        const encrypted = await window.crypto.subtle.encrypt(
          {
            name: 'AES-GCM',
            iv: iv
          },
          this.vault.encryptionKey,
          new TextEncoder().encode(configJson)
        );

        // Store encrypted data
        const encryptedData = {
          data: Array.from(new Uint8Array(encrypted)),
          iv: Array.from(iv),
          timestamp: Date.now()
        };

        this.vault.encryptedConfig = encryptedData;
        localStorage.setItem('clasp_encrypted_config', JSON.stringify(encryptedData));

        return true;
      } catch (error) {
        console.error('Error encrypting config:', error);
        this.showToast('Encryption failed: ' + error.message, 'error');
        return false;
      }
    },

    // Decrypt and load configuration
    async loadEncryptedConfig() {
      try {
        if (!this.vault.encryptionKey) {
          throw new Error('Vault not unlocked');
        }

        const encryptedData = this.vault.encryptedConfig ||
                            JSON.parse(localStorage.getItem('clasp_encrypted_config'));

        if (!encryptedData) {
          throw new Error('No encrypted config found');
        }

        // Decrypt
        const decrypted = await window.crypto.subtle.decrypt(
          {
            name: 'AES-GCM',
            iv: new Uint8Array(encryptedData.iv)
          },
          this.vault.encryptionKey,
          new Uint8Array(encryptedData.data)
        );

        // Parse decrypted config
        const configJson = new TextDecoder().decode(decrypted);
        this.config = JSON.parse(configJson);

        return true;
      } catch (error) {
        console.error('Error decrypting config:', error);
        this.showToast('Decryption failed: ' + error.message, 'error');
        return false;
      }
    },

    // Save config in plaintext (when vault is disabled)
    async saveConfigPlaintext() {
      // This is the existing saveAndApply function
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

    // Check if Web Crypto API is available
    isCryptoAvailable() {
      return 'crypto' in window && 'subtle' in window.crypto;
    },

    // Generate secure random passphrase
    generateSecurePassphrase() {
      const words = ['apple', 'banana', 'cherry', 'date', 'elderberry', 'fig', 'grape', 'honeydew',
                     'kiwi', 'lemon', 'mango', 'nectarine', 'orange', 'papaya', 'quince', 'raspberry'];

      // Generate 4 random words
      const passphrase = [];
      for (let i = 0; i < 4; i++) {
        const randomIndex = Math.floor(Math.random() * words.length);
        passphrase.push(words[randomIndex]);
      }

      // Add random numbers
      const numbers = Math.floor(1000 + Math.random() * 9000);

      return passphrase.join('-') + '-' + numbers;
    },

    // Export encrypted backup
    exportEncryptedBackup() {
      if (!this.vault.encryptedConfig) {
        this.showToast('No encrypted backup available', 'error');
        return;
      }

      const backupData = {
        version: '1.0',
        timestamp: Date.now(),
        salt: this.vault.salt ? Array.from(this.vault.salt) : null,
        encryptedConfig: this.vault.encryptedConfig
      };

      const dataStr = JSON.stringify(backupData, null, 2);
      const blob = new Blob([dataStr], { type: 'application/json' });
      const url = URL.createObjectURL(blob);

      const a = document.createElement('a');
      a.href = url;
      a.download = 'clasp-vault-backup-' + new Date().toISOString().split('T')[0] + '.json';
      a.click();

      URL.revokeObjectURL(url);
      this.showToast('Encrypted backup exported', 'success');
    },

    // Import encrypted backup
    async importEncryptedBackup(file, passphrase) {
      try {
        const reader = new FileReader();
        reader.onload = async (e) => {
          try {
            const backupData = JSON.parse(e.target.result);

            // Verify backup format
            if (backupData.version !== '1.0' || !backupData.encryptedConfig) {
              throw new Error('Invalid backup format');
            }

            // Set up vault with backup data
            this.vault.salt = new Uint8Array(backupData.salt);
            this.vault.encryptedConfig = backupData.encryptedConfig;

            // Unlock with provided passphrase
            const success = await this.unlockVault(passphrase);
            if (success) {
              this.showToast('Backup imported and vault unlocked', 'success');
              this.showImportBackupModal = false;
              this.importBackupFile = null;
              this.importBackupPassphrase = '';
            }
          } catch (error) {
            console.error('Error importing backup:', error);
            this.showToast('Failed to import backup: ' + error.message, 'error');
          }
        };
        reader.readAsText(file);
      } catch (error) {
        console.error('Error reading backup file:', error);
        this.showToast('Failed to read backup file: ' + error.message, 'error');
      }
    },

    // Validate passphrase strength
    validatePassphraseStrength(passphrase) {
      if (!passphrase || passphrase.length < 8) {
        return { valid: false, message: 'Passphrase must be at least 8 characters' };
      }

      // Check for mixed case, numbers, special chars
      const hasUpper = /[A-Z]/.test(passphrase);
      const hasLower = /[a-z]/.test(passphrase);
      const hasNumber = /[0-9]/.test(passphrase);
      const hasSpecial = /[!@#$%^&*(),.?":{}|<>]/.test(passphrase);

      const strengthChecks = [hasUpper, hasLower, hasNumber, hasSpecial].filter(Boolean).length;

      if (strengthChecks < 2) {
        return { valid: false, message: 'Passphrase should include mix of upper/lower case, numbers, and special characters' };
      }

      return { valid: true, message: 'Strong passphrase' };
    },

    // Generate random salt
    generateRandomSalt() {
      const salt = new Uint8Array(16);
      window.crypto.getRandomValues(salt);
      return salt;
    },

    // Modified saveAndApply to handle vault encryption
    async saveAndApply() {
      if (this.vault.isEnabled && this.vault.isUnlocked) {
        // Encrypt and save
        await this.encryptConfig();
        this.dirty = false;
        this.showToast('Config saved and encrypted', 'success');
      } else {
        // Original save logic
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
      }
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
    },

    // Discard changes
    discardChanges() {
      this.loadConfig();
      this.dirty = false;
    },

    // Export config
    exportConfig() {
      const data = JSON.stringify(this.config, null, 2);
      const blob = new Blob([data], { type: 'application/yaml' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'clasp-config-export.yaml';
      a.click();
      URL.revokeObjectURL(url);
    },

    // Import config
    importConfig(event) {
      const file = event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (e) => {
        try {
          const importedConfig = JSON.parse(e.target.result);
          this.config = importedConfig;
          this.showToast('Config imported successfully', 'success');
        } catch (err) {
          this.showToast('Error importing config: ' + err.message, 'error');
        }
      };
      reader.readAsText(file);
    },

    // Clear cache
    clearCache() {
      fetch('/internal/cache/clear', { method: 'POST' })
        .then(() => this.showToast('Cache cleared', 'success'))
        .catch(() => this.showToast('Failed to clear cache', 'error'));
    },

    // Reset model defaults
    resetModelDefaults() {
      fetch('/internal/catalog/defaults')
        .then(res => res.json())
        .then(defaults => {
          this.config.routing.models = defaults.models;
          this.config.routing.by_type = defaults.by_type;
          this.showToast('Reset to defaults', 'success');
          this.markDirty();
        })
        .catch(() => this.showToast('Failed to reset defaults', 'error'));
    },

    // Command Palette
    commandPalette: {
      open: false,
      query: '',
      filteredCommands: [],
      history: [],
      favorites: [],
      selectedIndex: 0
    },

    // Command definitions
    commands: [
      {
        id: 'test_key',
        name: 'Test API Key',
        description: 'Test connection to a provider API key',
        action: (providerName, keyIndex) => {
          const prov = this.config.providers[providerName];
          if (prov?.keys?.[keyIndex]) {
            this.testKey(providerName, prov.keys[keyIndex].value, keyIndex);
          }
        },
        category: 'Providers'
      },
      {
        id: 'add_key',
        name: 'Add API Key',
        description: 'Add a new API key to a provider',
        action: (providerName) => {
          this.addKey(providerName);
        },
        category: 'Providers'
      },
      {
        id: 'remove_key',
        name: 'Remove API Key',
        description: 'Remove an API key from a provider',
        action: (providerName, keyIndex) => {
          this.removeKey(providerName, keyIndex);
        },
        category: 'Providers'
      },
      {
        id: 'save_config',
        name: 'Save Configuration',
        description: 'Save all changes to config',
        action: () => {
          this.saveAndApply();
        },
        category: 'Configuration'
      },
      {
        id: 'discard_changes',
        name: 'Discard Changes',
        description: 'Discard all unsaved changes',
        action: () => {
          this.discardChanges();
        },
        category: 'Configuration'
      },
      {
        id: 'export_config',
        name: 'Export Configuration',
        description: 'Export config as YAML file',
        action: () => {
          this.exportConfig();
        },
        category: 'Configuration'
      },
      {
        id: 'import_config',
        name: 'Import Configuration',
        description: 'Import config from YAML file',
        action: () => {
          document.getElementById('config-import-input').click();
        },
        category: 'Configuration'
      },
      {
        id: 'clear_cache',
        name: 'Clear Cache',
        description: 'Clear response cache',
        action: () => {
          this.clearCache();
        },
        category: 'Cache'
      },
      {
        id: 'reset_defaults',
        name: 'Reset to Defaults',
        description: 'Reset model mappings to defaults',
        action: () => {
          this.resetModelDefaults();
        },
        category: 'Models'
      },
      {
        id: 'nav_providers',
        name: 'Go to Providers',
        description: 'Navigate to Providers panel',
        action: () => {
          this.panel = 'providers';
          this.closeCommandPalette();
        },
        category: 'Navigation'
      },
      {
        id: 'nav_dashboard',
        name: 'Go to Dashboard',
        description: 'Navigate to Dashboard panel',
        action: () => {
          this.panel = 'dashboard';
          this.closeCommandPalette();
        },
        category: 'Navigation'
      },
      {
        id: 'nav_routing',
        name: 'Go to Routing',
        description: 'Navigate to Routing panel',
        action: () => {
          this.panel = 'routing';
          this.closeCommandPalette();
        },
        category: 'Navigation'
      },
      {
        id: 'nav_priority',
        name: 'Go to Priority Rules',
        description: 'Navigate to Priority Rules panel',
        action: () => {
          this.panel = 'priority';
          this.closeCommandPalette();
        },
        category: 'Navigation'
      },
      {
        id: 'nav_settings',
        name: 'Go to Settings',
        description: 'Navigate to Settings panel',
        action: () => {
          this.panel = 'settings';
          this.closeCommandPalette();
        },
        category: 'Navigation'
      },
      {
        id: 'enable_vault',
        name: 'Enable Vault Encryption',
        description: 'Encrypt all API keys with a passphrase',
        action: () => {
          this.showEnableVaultModal = true;
          this.closeCommandPalette();
        },
        category: 'Security'
      },
      {
        id: 'unlock_vault',
        name: 'Unlock Vault',
        description: 'Unlock encrypted vault with passphrase',
        action: () => {
          this.showUnlockVaultModal = true;
          this.closeCommandPalette();
        },
        category: 'Security'
      },
      {
        id: 'lock_vault',
        name: 'Lock Vault',
        description: 'Lock vault and clear sensitive data',
        action: () => {
          this.lockVault();
          this.closeCommandPalette();
        },
        category: 'Security'
      },
      {
        id: 'nav_vault',
        name: 'Go to Vault',
        description: 'Navigate to Vault Encryption panel',
        action: () => {
          this.panel = 'vault';
          this.closeCommandPalette();
        },
        category: 'Navigation'
      }
    ],

    // Open command palette
    openCommandPalette() {
      this.commandPalette.open = true;
      this.commandPalette.query = '';
      this.commandPalette.selectedIndex = 0;
      this.filterCommands();
      this.$nextTick(() => {
        this.$refs.commandInput.focus();
      });
    },

    // Close command palette
    closeCommandPalette() {
      this.commandPalette.open = false;
    },

    // Filter commands based on query
    filterCommands() {
      const query = this.commandPalette.query.toLowerCase();
      if (!query) {
        this.commandPalette.filteredCommands = [...this.commands];
      } else {
        this.commandPalette.filteredCommands = this.commands.filter(cmd =>
          cmd.name.toLowerCase().includes(query) ||
          cmd.description.toLowerCase().includes(query) ||
          cmd.category.toLowerCase().includes(query)
        );
      }
    },

    // Execute selected command
    executeCommand() {
      const cmd = this.commandPalette.filteredCommands[this.commandPalette.selectedIndex];
      if (cmd) {
        // Add to history
        this.commandPalette.history = [
          ...this.commandPalette.history.filter(item => item.id !== cmd.id),
          { id: cmd.id, name: cmd.name, timestamp: Date.now() }
        ].slice(-10); // Keep last 10

        // Execute action
        cmd.action();
        this.closeCommandPalette();
      }
    },

    // Navigate command selection
    navigateCommands(direction) {
      if (this.commandPalette.filteredCommands.length === 0) return;

      if (direction === 'up') {
        this.commandPalette.selectedIndex = Math.max(0, this.commandPalette.selectedIndex - 1);
      } else if (direction === 'down') {
        this.commandPalette.selectedIndex = Math.min(
          this.commandPalette.filteredCommands.length - 1,
          this.commandPalette.selectedIndex + 1
        );
      }

      // Scroll to selected item
      this.$nextTick(() => {
        const selectedEl = this.$refs.commandList?.querySelector('.selected');
        if (selectedEl) {
          selectedEl.scrollIntoView({ block: 'nearest' });
        }
      });
    },

    // Toggle favorite
    toggleFavorite(cmdId) {
      const index = this.commandPalette.favorites.indexOf(cmdId);
      if (index > -1) {
        this.commandPalette.favorites.splice(index, 1);
      } else {
        this.commandPalette.favorites.push(cmdId);
      }
    },

    // Check if command is favorite
    isFavorite(cmdId) {
      return this.commandPalette.favorites.includes(cmdId);
    },

    // Get favorite commands
    getFavorites() {
      return this.commands.filter(cmd => this.isFavorite(cmd.id));
    }
  };
}

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  const app = Alpine.store('app');

  // Ctrl+K or Cmd+K to open command palette
  if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
    e.preventDefault();
    app.openCommandPalette();
  }

  // Escape to close command palette
  if (e.key === 'Escape' && app.commandPalette.open) {
    e.preventDefault();
    app.closeCommandPalette();
  }

  // Command palette navigation
  if (app.commandPalette.open) {
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      app.navigateCommands('up');
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      app.navigateCommands('down');
    } else if (e.key === 'Enter') {
      e.preventDefault();
      app.executeCommand();
    }
  }
});

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  console.log('CLASP UI initialized - Exact KeyKing replica with Command Palette');
});