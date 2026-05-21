const BASE = ''

async function request(method, path, body) {
  const opts = {
    method,
    credentials: 'include',
    headers: {},
  }

  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }

  const res = await fetch(BASE + path, opts)
  if (res.status === 204) return null

  let data
  try {
    data = await res.json()
  } catch {
    data = null
  }

  if (!res.ok) {
    const message = data?.error?.message || data?.detail || '请求失败'
    throw Object.assign(new Error(message), { status: res.status, data })
  }

  return data
}

export const api = {
  login: (username, password) => request('POST', '/api/auth/login', { username, password }),
  logout: () => request('POST', '/api/auth/logout'),
  me: () => request('GET', '/api/auth/me'),

  getConversations: () => request('GET', '/api/conversations'),
  createConversation: (data) => request('POST', '/api/conversations', data),
  updateConversation: (id, data) => request('PUT', `/api/conversations/${id}`, data),
  deleteConversation: (id) => request('DELETE', `/api/conversations/${id}`),

  getMessages: (convId) => request('GET', `/api/conversations/${convId}/messages`),
  sendMessage: (convId, data) => request('POST', `/api/conversations/${convId}/messages`, data),
  regenerateMessage: (convId, messageId, data = {}) =>
    request('POST', `/api/conversations/${convId}/messages/${messageId}/regenerate`, data),

  getApiKeys: () => request('GET', '/api/keys'),
  createApiKey: (data) => request('POST', '/api/keys', data),
  deleteApiKey: (id) => request('DELETE', `/api/keys/${id}`),
  testApiKey: (id) => request('POST', `/api/keys/${id}/test`),
}
