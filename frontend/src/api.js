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
  const data = await res.json()
  if (!res.ok) throw Object.assign(new Error(data.detail || '请求失败'), { status: res.status, data })
  return data
}

export const api = {
  login: (username, password) => request('POST', '/api/auth/login', { username, password }),
  logout: () => request('POST', '/api/auth/logout'),
  me: () => request('GET', '/api/auth/me'),

  // Conversations (to be implemented in backend)
  getConversations: () => request('GET', '/api/conversations'),
  createConversation: (data) => request('POST', '/api/conversations', data),
  updateConversation: (id, data) => request('PATCH', `/api/conversations/${id}`, data),
  deleteConversation: (id) => request('DELETE', `/api/conversations/${id}`),

  // Messages
  getMessages: (convId) => request('GET', `/api/conversations/${convId}/messages`),
  sendMessage: (convId, data) => request('POST', `/api/conversations/${convId}/messages`, data),

  // API Keys
  getApiKeys: () => request('GET', '/api/keys'),
  createApiKey: (data) => request('POST', '/api/keys', data),
  deleteApiKey: (id) => request('DELETE', `/api/keys/${id}`),
  testApiKey: (id) => request('POST', `/api/keys/${id}/test`),
}

export async function streamChat(convId, data, onChunk, onDone, onError) {
  try {
    const res = await fetch(`/api/conversations/${convId}/messages/stream`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      onError(new Error(err.detail || '请求失败'))
      return
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop()
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const raw = line.slice(6).trim()
          if (raw === '[DONE]') { onDone(); return }
          try { onChunk(JSON.parse(raw)) } catch {}
        }
      }
    }
    onDone()
  } catch (e) {
    onError(e)
  }
}
