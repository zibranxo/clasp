# CLASP UI Migration Plan: Tauri React → FastAPI Web

## Current State Analysis

### Current CLASP UI (Alpine.js)
- Location: `clasp/ui/static/`
- Framework: Alpine.js + Tailwind CSS
- Entry point: `index.html`
- JavaScript: `app.js` (Alpine.js application)
- CSS: `style.css`
- Backend: FastAPI endpoints at `/internal/*`
- Serving: Static files via FastAPI

### Target UI (Tauri React App)
- Location: `web/desktop/`
- Framework: React + TypeScript + Vite
- Entry point: `src/main.tsx`
- Main App: `src/App.tsx`
- Pages: `src/pages/*.tsx` (DashboardPage, KeysPage, etc.)
- Styling: Tailwind CSS + custom CSS
- Backend: Tauri Rust backend with custom commands
- Build: Vite configuration

## Key Challenges

1. **Backend Integration**: Tauri uses Rust commands (`invoke()`), CLASP uses FastAPI HTTP endpoints
2. **Authentication**: Tauri app has Google auth flow, CLASP needs session management
3. **State Management**: Tauri uses React context, CLASP needs to adapt to FastAPI sessions
4. **Routing**: Tauri uses React Router, CLASP can use client-side routing
5. **Build Process**: Tauri uses Vite build, CLASP needs static files for FastAPI

## Migration Strategy

### Phase 1: Adapt React UI for FastAPI

**Goal**: Make the React UI work with FastAPI endpoints instead of Tauri commands

#### Files to Create/Modify:

1. **New React Entry Point** (`clasp/ui/static/react/index.html`)
   - Replace Tauri-specific elements with FastAPI-compatible ones
   - Remove Tauri script imports
   - Add FastAPI API base URL configuration

2. **API Adapter Layer** (`clasp/ui/static/react/api.ts`)
   - Create wrapper functions that map Tauri commands to FastAPI HTTP calls
   - Handle authentication via session cookies instead of Tauri sessions
   - Map response formats between Tauri and FastAPI

3. **Modified App Component** (`clasp/ui/static/react/App.tsx`)
   - Replace `invoke()` calls with API adapter calls
   - Update authentication flow to use FastAPI sessions
   - Modify event listeners to use FastAPI SSE endpoints

4. **Build Configuration** (`clasp/ui/static/react/vite.config.ts`)
   - Configure Vite to output static files compatible with FastAPI
   - Set correct base path for production builds
   - Configure asset handling

#### Key Endpoint Mappings:

| Tauri Command | FastAPI Endpoint | Notes |
|---------------|------------------|-------|
| `invoke('list_routing_events')` | `GET /internal/status` | Adapt response format |
| `invoke('get_session')` | `GET /internal/session` | Need to implement |
| `invoke('save_session')` | `POST /internal/session` | Need to implement |
| `invoke('clear_session')` | `POST /internal/session/clear` | Need to implement |
| `listen('routing-event')` | `GET /internal/stream` | Use SSE |
| `invoke('list_keys')` | `GET /internal/config` | Extract keys from config |
| `invoke('get_api_key')` | `GET /internal/config` | Extract API key |

### Phase 2: Implement Missing FastAPI Endpoints

**New endpoints needed:**

1. **Session Management**
   - `GET /internal/session` - Get current session
   - `POST /internal/session` - Create/update session
   - `POST /internal/session/clear` - Clear session

2. **Routing Events**
   - Enhance `/internal/stream` to include routing events
   - Add endpoint for historical routing events

3. **Proxy Status**
   - `GET /internal/proxy-status` - Proxy active status and port

### Phase 3: Build and Integration

1. **Build Process**
   - Run `npm install` in the React directory
   - Run `npm run build` to generate static files
   - Copy built files to `clasp/ui/static/react/`

2. **FastAPI Integration**
   - Update `clasp/ui/routes.py` to serve React entry point
   - Add route for `/ui/react` or make React the default UI
   - Ensure CORS headers are properly configured

3. **Fallback Mechanism**
   - Keep Alpine.js UI as fallback
   - Add UI version toggle in configuration
   - Allow switching between React and Alpine UIs

### Phase 4: Testing and Validation

1. **Endpoint Testing**
   - Verify all Tauri→FastAPI mappings work correctly
   - Test authentication flow
   - Test real-time updates via SSE

2. **UI Functionality Testing**
   - Test all panels: Providers, Models, Dashboard, Routing, Advanced, Logs
   - Test key management (add/remove/test)
   - Test configuration save/export/import
   - Test live metrics and status updates

3. **Performance Testing**
   - Ensure no fake data or simulated latency
   - Verify all data comes from real endpoints
   - Test with actual provider keys

## Implementation Timeline

1. **Day 1**: Set up React build environment, create API adapter
2. **Day 2**: Implement missing FastAPI endpoints
3. **Day 3**: Adapt React components to use API adapter
4. **Day 4**: Build and integrate with FastAPI
5. **Day 5**: Testing and bug fixing

## Risk Mitigation

1. **Fallback UI**: Keep Alpine.js UI as backup
2. **Feature Flags**: Add configuration to enable/disable React UI
3. **Gradual Rollout**: Implement one panel at a time
4. **Comprehensive Logging**: Add detailed logging for API adapter calls

## Success Criteria

1. All UI panels work with real data (no fake values)
2. All buttons and actions make real HTTP calls
3. Authentication works via FastAPI sessions
4. Real-time updates work via SSE
5. Performance is comparable to or better than Alpine.js UI
6. No breaking changes to existing FastAPI endpoints