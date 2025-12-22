import axios from 'axios';

const API_BASE = '/api';

// Projects API
export const projectsAPI = {
  list: () => axios.get(`${API_BASE}/projects`),
  get: (id) => axios.get(`${API_BASE}/projects/${id}`),
  create: (data) => axios.post(`${API_BASE}/projects`, data),
  delete: (id) => axios.delete(`${API_BASE}/projects/${id}`),
  getStats: (id) => axios.get(`${API_BASE}/projects/${id}/stats`),
  getDashboard: (id) => axios.get(`${API_BASE}/projects/${id}/dashboard`),
};

// Cards API
export const cardsAPI = {
  // Character cards
  listCharacters: (projectId) => axios.get(`${API_BASE}/projects/${projectId}/cards/characters`),
  getCharacter: (projectId, name) => axios.get(`${API_BASE}/projects/${projectId}/cards/characters/${name}`),
  createCharacter: (projectId, data) => axios.post(`${API_BASE}/projects/${projectId}/cards/characters`, data),
  updateCharacter: (projectId, name, data) => axios.put(`${API_BASE}/projects/${projectId}/cards/characters/${name}`, data),
  deleteCharacter: (projectId, name) => axios.delete(`${API_BASE}/projects/${projectId}/cards/characters/${name}`),

  // World cards
  listWorld: (projectId) => axios.get(`${API_BASE}/projects/${projectId}/cards/world`),
  getWorld: (projectId, name) => axios.get(`${API_BASE}/projects/${projectId}/cards/world/${name}`),
  createWorld: (projectId, data) => axios.post(`${API_BASE}/projects/${projectId}/cards/world`, data),
  updateWorld: (projectId, name, data) => axios.put(`${API_BASE}/projects/${projectId}/cards/world/${name}`, data),
  
  // Style card
  getStyle: (projectId) => axios.get(`${API_BASE}/projects/${projectId}/cards/style`),
  updateStyle: (projectId, data) => axios.put(`${API_BASE}/projects/${projectId}/cards/style`, data),
  
  // Rules card
  getRules: (projectId) => axios.get(`${API_BASE}/projects/${projectId}/cards/rules`),
  updateRules: (projectId, data) => axios.put(`${API_BASE}/projects/${projectId}/cards/rules`, data),
};

// Session API
export const sessionAPI = {
  start: (projectId, data) => axios.post(`${API_BASE}/projects/${projectId}/session/start`, data),
  getStatus: (projectId) => axios.get(`${API_BASE}/projects/${projectId}/session/status`),
  submitFeedback: (projectId, data) => axios.post(`${API_BASE}/projects/${projectId}/session/feedback`, data),
  cancel: (projectId) => axios.post(`${API_BASE}/projects/${projectId}/session/cancel`),
};

// Drafts API
export const draftsAPI = {
  listChapters: (projectId) => axios.get(`${API_BASE}/projects/${projectId}/drafts`),
  listVersions: (projectId, chapter) => axios.get(`${API_BASE}/projects/${projectId}/drafts/${chapter}/versions`),
  getDraft: (projectId, chapter, version) => axios.get(`${API_BASE}/projects/${projectId}/drafts/${chapter}/${version}`),
  getSceneBrief: (projectId, chapter) => axios.get(`${API_BASE}/projects/${projectId}/drafts/${chapter}/scene-brief`),
  getReview: (projectId, chapter) => axios.get(`${API_BASE}/projects/${projectId}/drafts/${chapter}/review`),
  getFinal: (projectId, chapter) => axios.get(`${API_BASE}/projects/${projectId}/drafts/${chapter}/final`),
  getSummary: (projectId, chapter) => axios.get(`${API_BASE}/projects/${projectId}/drafts/${chapter}/summary`),
  deleteChapter: (projectId, chapter) => axios.delete(`${API_BASE}/projects/${projectId}/drafts/${chapter}`),
};

// Config API
export const configAPI = {
  getLLM: () => axios.get(`${API_BASE}/config/llm`),
  updateLLM: (data) => axios.post(`${API_BASE}/config/llm`, data),
};

// WebSocket for real-time updates
export const createWebSocket = (projectId, onMessage) => {
  const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const wsHost = window.location.host;
  const ws = new WebSocket(`${wsProtocol}://${wsHost}/ws/${projectId}/session`);
  let heartbeatTimer = null;
  
  ws.onopen = () => {
    console.log('WebSocket connected');
    heartbeatTimer = window.setInterval(() => {
      try {
        ws.send(String(Date.now()));
      } catch {
        // ignore
      }
    }, 20000);
  };
  
  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    onMessage(data);
  };
  
  ws.onerror = (error) => {
    console.error('WebSocket error:', error);
  };
  
  ws.onclose = () => {
    console.log('WebSocket disconnected');
    if (heartbeatTimer) {
      window.clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  };
  
  return ws;
};
