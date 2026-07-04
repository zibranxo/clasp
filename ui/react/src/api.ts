// clasp/ui/react/src/api.ts
// API adapter that replaces Tauri commands with FastAPI HTTP calls

const API_BASE = window.CLASP_API_BASE || '/internal'

interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
}

class ClaspApi {
  private async request<T>(endpoint: string, method: string = 'GET', body?: any): Promise<T> {
    const url = `${API_BASE}${endpoint}`
    const options: RequestInit = {
      method,
      headers: {
        'Content-Type': 'application/json',
      },
    }

    if (body) {
      options.body = JSON.stringify(body)
    }

    try {
      const response = await fetch(url, options)
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(errorData.detail || `HTTP error! status: ${response.status}`)
      }
      return await response.json()
    } catch (error) {
      console.error('API request failed:', error)
      throw error
    }
  }

  // Provider key management
  async listKeys(): Promise<any[]> {
    const config = await this.getConfig()
    return config.providers ? Object.entries(config.providers).flatMap(([name, prov]) =>
      (prov.keys || []).map((key: string, index: number) => ({
        provider: name,
        key: key,
        index: index,
        providerName: name
      }))
    ) : []
  }

  async getSystemKey(): Promise<string> {
    const config = await this.getConfig()
    return config.server?.api_key || ''
  }

  // Configuration management
  async getConfig(): Promise<any> {
    return this.request('/config')
  }

  async saveConfig(config: any): Promise<any> {
    return this.request('/config', 'POST', config)
  }

  async exportConfig(): Promise<string> {
    const response = await fetch(`${API_BASE}/config/export`)
    return await response.text()
  }

  async importConfig(yamlContent: string): Promise<any> {
    return this.request('/config/import', 'POST', yamlContent)
  }

  // Routing events
  async listRoutingEvents(): Promise<any[]> {
    const status = await this.getStatus()
    // Extract routing events from status or use a dedicated endpoint
    return status.recent_requests || []
  }

  async clearRoutingEvents(): Promise<void> {
    // This would need a new endpoint in FastAPI
    await this.request('/routing-events/clear', 'POST')
  }

  // Status and metrics
  async getStatus(): Promise<any> {
    return this.request('/status')
  }

  async getLiveStream(): Promise<EventSource> {
    return new EventSource(`${API_BASE}/stream`)
  }

  async getLogStream(): Promise<EventSource> {
    return new EventSource(`${API_BASE}/logs/stream`)
  }

  // Provider management
  async testKey(provider: string, key: string): Promise<{ok: boolean, latency_ms?: number, error?: string}> {
    return this.request('/config/test-key', 'POST', { provider, key })
  }

  async getProviderModels(provider: string): Promise<string[]> {
    return this.request(`/providers/${provider}/models`)
  }

  // Cache management
  async clearCache(): Promise<void> {
    await this.request('/cache/clear', 'POST')
  }

  // Session management (would need new endpoints)
  async getSession(): Promise<{session_id: string, user_id: string, email?: string} | null> {
    try {
      return await this.request('/session')
    } catch (error) {
      return null
    }
  }

  async saveSession(session: any): Promise<void> {
    await this.request('/session', 'POST', session)
  }

  async clearSession(): Promise<void> {
    await this.request('/session', 'DELETE')
  }

  // Update management
  async checkForUpdates(): Promise<any | null> {
    // This would need implementation based on your update strategy
    return null
  }

  async installUpdate(): Promise<void> {
    // Not applicable for web version
  }

  // Open browser URL (not applicable for web)
  async openBrowser(url: string): Promise<void> {
    window.open(url, '_blank')
  }

  // Proxy port
  async getProxyPort(): Promise<number> {
    const config = await this.getConfig()
    return config.server?.port || 8787
  }
}

export const api = new ClaspApi()