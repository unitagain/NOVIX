/**
 * Copyright (c) 2026 丁逸飞 (Ding Yifei) <1467673018@qq.com>
 * 
 * This source code is licensed under the PolyForm Noncommercial License 1.0.0.
 * COMMERCIAL USE IS STRICTLY PROHIBITED.
 * 
 * See the LICENSE file for details.
 */
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { CloudAuthProvider } from './context/CloudAuthContext'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <CloudAuthProvider>
        <App />
      </CloudAuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
