import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './src/App'
import './src/index.css'

// Set the API base URL to point to the FastAPI server
window.CLASP_API_BASE = '/internal'

const root = ReactDOM.createRoot(document.getElementById('root') as HTMLElement)
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)