const BASE = ''

async function request(method, path, body) {
  const opts = {
    method,
    credentials: 'include',
    headers: {},
  }

  if (body !== undefined) {
    if (body instanceof FormData) {
      opts.body = body
    } else {
      opts.headers['Content-Type'] = 'application/json'
      opts.body = JSON.stringify(body)
    }
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

function parseFilename(contentDisposition) {
  if (!contentDisposition) return null

  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1])
    } catch {
      // ignore malformed encoding
    }
  }

  const fallbackMatch = contentDisposition.match(/filename="([^"]+)"/i)
  if (fallbackMatch?.[1]) return fallbackMatch[1]
  return null
}

async function download(path) {
  const res = await fetch(BASE + path, { credentials: 'include' })
  if (!res.ok) {
    let data
    try {
      data = await res.json()
    } catch {
      data = null
    }
    const message = data?.error?.message || data?.detail || '下载失败'
    throw Object.assign(new Error(message), { status: res.status, data })
  }

  const blob = await res.blob()
  const filename = parseFilename(res.headers.get('Content-Disposition')) || 'download'
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export const api = {
  login: (username, password) => request('POST', '/api/auth/login', { username, password }),
  logout: () => request('POST', '/api/auth/logout'),
  me: () => request('GET', '/api/auth/me'),

  getConversations: () => request('GET', '/api/conversations'),
  createConversation: (data) => request('POST', '/api/conversations', data),
  exportConversation: (id, { format, scope }) =>
    download(`/api/conversations/${id}/export?${new URLSearchParams({ format, scope }).toString()}`),
  importMarkdownConversation: (file) => {
    const formData = new FormData()
    formData.append('file', file)
    return request('POST', '/api/conversations/import-md', formData)
  },
  updateConversation: (id, data) => request('PUT', `/api/conversations/${id}`, data),
  deleteConversation: (id) => request('DELETE', `/api/conversations/${id}`),

  getBranches: (convId) => request('GET', `/api/conversations/${convId}/branches`),
  createBranch: (convId, data) => request('POST', `/api/conversations/${convId}/branches`, data),
  updateBranch: (convId, branchId, data) => request('PUT', `/api/conversations/${convId}/branches/${branchId}`, data),
  activateBranch: (convId, branchId) => request('POST', `/api/conversations/${convId}/branches/${branchId}/activate`),
  archiveBranch: (convId, branchId) => request('POST', `/api/conversations/${convId}/branches/${branchId}/archive`),
  deleteBranch: (convId, branchId) => request('DELETE', `/api/conversations/${convId}/branches/${branchId}`),

  getMessages: (convId, params = {}) => {
    const search = new URLSearchParams()
    if (params.leafMessageId) search.set('leaf_message_id', params.leafMessageId)
    if (params.rootMessageId) search.set('root_message_id', params.rootMessageId)
    if (params.expandLeaf) search.set('expand_leaf_descendants', 'true')
    const qs = search.toString()
    return request('GET', `/api/conversations/${convId}/messages${qs ? `?${qs}` : ''}`)
  },
  getAgentRuns: (convId, params = {}) => {
    const search = new URLSearchParams()
    if (params.status) search.set('status', params.status)
    const qs = search.toString()
    return request('GET', `/api/conversations/${convId}/runs${qs ? `?${qs}` : ''}`)
  },
  getRunEvents: (convId, runId, params = {}) => {
    const search = new URLSearchParams()
    if (Number.isFinite(params.afterSequence)) search.set('after_sequence', String(params.afterSequence))
    const qs = search.toString()
    return request('GET', `/api/conversations/${convId}/runs/${runId}/events${qs ? `?${qs}` : ''}`)
  },
  getRunStreamPath: (convId, runId, params = {}) => {
    const search = new URLSearchParams()
    if (Number.isFinite(params.afterSequence)) search.set('after_sequence', String(params.afterSequence))
    const qs = search.toString()
    return `${BASE}/api/conversations/${convId}/runs/${runId}/stream${qs ? `?${qs}` : ''}`
  },
  getMessageTree: (convId) => request('GET', `/api/conversations/${convId}/message-tree`),
  getMessage: (convId, messageId) => request('GET', `/api/conversations/${convId}/messages/${messageId}`),
  deleteMessage: (convId, messageId) => request('DELETE', `/api/conversations/${convId}/messages/${messageId}`),
  editMessage: (convId, messageId, data) => request('POST', `/api/conversations/${convId}/messages/${messageId}/edit`, data),
  sendMessage: (convId, data) => request('POST', `/api/conversations/${convId}/messages`, data),
  regenerateMessage: (convId, messageId, data = {}) =>
    request('POST', `/api/conversations/${convId}/messages/${messageId}/regenerate`, data),
  activateMessageBranch: (convId, messageId, params = {}) => {
    const search = new URLSearchParams()
    if (params.exact) search.set('exact', 'true')
    const qs = search.toString()
    return request('POST', `/api/conversations/${convId}/messages/${messageId}/activate${qs ? `?${qs}` : ''}`)
  },

  getApiKeys: () => request('GET', '/api/keys'),
  createApiKey: (data) => request('POST', '/api/keys', data),
  deleteApiKey: (id) => request('DELETE', `/api/keys/${id}`),
  testApiKey: (id) => request('POST', `/api/keys/${id}/test`),
  getProviderModels: (provider) => request('GET', `/api/keys/providers/${encodeURIComponent(provider)}/models`),
}
