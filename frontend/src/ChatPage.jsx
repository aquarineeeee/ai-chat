import { useState, useEffect, useRef, useCallback } from 'react'
import { useAuth } from './AuthContext'
import { useTheme } from './ThemeContext'
import { api } from './api'
import MessageBubble from './components/MessageBubble'
import Sidebar from './components/Sidebar'
import ChatInput from './components/ChatInput'
import EmptyState from './components/EmptyState'
import ApiKeysModal from './components/ApiKeysModal'
import { Menu, X, Loader2, AlertCircle } from 'lucide-react'

export default function ChatPage() {
  const { user, logout } = useAuth()
  const { palette, mode, toggle, setPalette } = useTheme()
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [keysOpen, setKeysOpen] = useState(false)
  const [conversations, setConversations] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [messages, setMessages] = useState([])
  const [loadingConvs, setLoadingConvs] = useState(true)
  const [loadingMsgs, setLoadingMsgs] = useState(false)
  const [sending, setSending] = useState(false)
  const [regeneratingMessageId, setRegeneratingMessageId] = useState(null)
  const [switchingBranchMessageId, setSwitchingBranchMessageId] = useState(null)
  const [streamingContent, setStreamingContent] = useState('')
  const [error, setError] = useState('')
  const [apiKeys, setApiKeys] = useState([])
  const [loadingKeys, setLoadingKeys] = useState(false)
  const [keysError, setKeysError] = useState('')
  const [importing, setImporting] = useState(false)
  const [importStatus, setImportStatus] = useState(null)
  const bottomRef = useRef(null)

  const refreshMessages = useCallback(async (conversationId) => {
    const data = await api.getMessages(conversationId)
    setMessages(data?.items || [])
    setConversations(prev => prev.map(conv => (
      conv.id === conversationId
        ? { ...conv, current_leaf_message_id: data?.current_leaf_message_id ?? conv.current_leaf_message_id }
        : conv
    )))
    return data
  }, [])

  const fetchConversations = useCallback(async () => {
    const data = await api.getConversations()
    return data || []
  }, [])

  const loadApiKeys = useCallback(async () => {
    setKeysError('')
    setLoadingKeys(true)
    try {
      const data = await api.getApiKeys()
      setApiKeys(data || [])
    } catch (err) {
      setKeysError(err.message || '加载 API Keys 失败')
    } finally {
      setLoadingKeys(false)
    }
  }, [])

  const selectConversation = useCallback(async (conversationId) => {
    setActiveId(conversationId)
    if (!conversationId) {
      setMessages([])
      return
    }

    setLoadingMsgs(true)
    setMessages([])
    try {
      await refreshMessages(conversationId)
    } catch {
      setMessages([])
    } finally {
      setLoadingMsgs(false)
    }
  }, [refreshMessages])

  const openKeysModal = useCallback(async () => {
    setKeysOpen(true)
    await loadApiKeys()
  }, [loadApiKeys])

  useEffect(() => {
    let cancelled = false

    fetchConversations()
      .then(async data => {
        if (cancelled) return
        const items = data || []
        setConversations(items)
        if (items.length > 0) {
          await selectConversation(items[0].id)
        }
      })
      .catch(() => {
        if (!cancelled) setConversations([])
      })
      .finally(() => {
        if (!cancelled) setLoadingConvs(false)
      })

    return () => {
      cancelled = true
    }
  }, [fetchConversations, selectConversation])

  const reloadConversations = useCallback(async (nextActiveId = null) => {
    const items = await fetchConversations()
    setConversations(items)
    const resolvedActiveId = nextActiveId ?? items[0]?.id ?? null

    if (resolvedActiveId) {
      await selectConversation(resolvedActiveId)
    } else {
      setActiveId(null)
      setMessages([])
    }

    return items
  }, [fetchConversations, selectConversation])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingContent])

  const createConversation = useCallback(async (title = '新对话') => {
    const conv = await api.createConversation({ title })
    setConversations(prev => [conv, ...prev])
    await selectConversation(conv.id)
    return conv
  }, [selectConversation])

  const importConversation = useCallback(async (file) => {
    if (!file || importing) return

    setImporting(true)
    setImportStatus(null)

    try {
      const result = await api.importMarkdownConversation(file)
      const importedId = result?.conversation?.id ?? null
      await reloadConversations(importedId)
      setImportStatus({
        type: 'success',
        title: result?.conversation?.title || file.name,
        message: `已导入 ${result?.message_count ?? 0} 条消息`,
        meta: {
          messageCount: result?.message_count ?? 0,
          ignoredCount: result?.ignored_count ?? 0,
          warningCount: result?.warnings?.length ?? 0,
        },
      })
    } catch (err) {
      setImportStatus({
        type: 'error',
        title: file.name,
        message: err.message || '导入 Markdown 失败',
      })
    } finally {
      setImporting(false)
    }
  }, [importing, reloadConversations])

  const deleteConversation = useCallback(async (id) => {
    await api.deleteConversation(id)
    const remaining = conversations.filter(c => c.id !== id)
    setConversations(remaining)
    if (activeId === id) {
      await selectConversation(remaining[0]?.id ?? null)
    }
  }, [activeId, conversations, selectConversation])

  const sendMessage = useCallback(async (content) => {
    if (!content.trim() || sending || regeneratingMessageId !== null) return
    setError('')

    let convId = activeId
    if (!convId) {
      try {
        const conv = await createConversation(content.slice(0, 40))
        convId = conv.id
      } catch {
        setError('创建对话失败')
        return
      }
    }

    const userMsg = { id: Date.now(), role: 'user', content, status: 'completed' }
    setMessages(prev => [...prev, userMsg])
    setSending(true)
    setStreamingContent('')

    try {
      const res = await fetch(`/api/conversations/${convId}/messages/stream`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      })

      if (res.status === 404 || res.status === 405) {
        await api.sendMessage(convId, { content })
        await refreshMessages(convId)
        return
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err?.error?.message || err?.detail || '发送失败')
      }

      const reader = res.body?.getReader()
      if (!reader) throw new Error('流式响应不可用')

      const decoder = new TextDecoder()
      let buffer = ''
      let accumulated = ''
      let streamError = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const raw = line.slice(6).trim()
          if (raw === '[DONE]') continue

          try {
            const chunk = JSON.parse(raw)
            if (chunk.content) {
              accumulated += chunk.content
              setStreamingContent(accumulated)
            }
            if (chunk.error) {
              streamError = chunk.error
              setError(chunk.error)
            }
          } catch {
            // ignore malformed SSE chunks
          }
        }
      }

      setStreamingContent('')
      await refreshMessages(convId)
      if (streamError) {
        return
      }
      if (!accumulated) setError('模型没有返回内容')
    } catch (e) {
      setError(e.message || '发送失败，请重试')
      setMessages(prev => prev.filter(m => m.id !== userMsg.id))
    } finally {
      setSending(false)
      setStreamingContent('')
    }
  }, [activeId, createConversation, refreshMessages, regeneratingMessageId, sending])

  const regenerateMessage = useCallback(async (messageId) => {
    if (!activeId || sending || regeneratingMessageId !== null || switchingBranchMessageId !== null) return
    setError('')
    setRegeneratingMessageId(messageId)
    setStreamingContent('')

    try {
      const res = await fetch(`/api/conversations/${activeId}/messages/${messageId}/regenerate/stream`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })

      if (res.status === 404 || res.status === 405) {
        await api.regenerateMessage(activeId, messageId, {})
        await refreshMessages(activeId)
        return
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err?.error?.message || err?.detail || '重新生成失败')
      }

      const reader = res.body?.getReader()
      if (!reader) throw new Error('流式响应不可用')

      const decoder = new TextDecoder()
      let buffer = ''
      let accumulated = ''
      let streamError = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const raw = line.slice(6).trim()
          if (raw === '[DONE]') continue

          try {
            const chunk = JSON.parse(raw)
            if (chunk.content) {
              accumulated += chunk.content
              setStreamingContent(accumulated)
            }
            if (chunk.error) {
              streamError = chunk.error
              setError(chunk.error)
            }
          } catch {
            // ignore malformed SSE chunks
          }
        }
      }

      await refreshMessages(activeId)
      if (streamError) {
        return
      }
      if (!accumulated) setError('模型没有返回内容')
    } catch (e) {
      setError(e.message || '重新生成失败，请重试')
      await refreshMessages(activeId)
    } finally {
      setRegeneratingMessageId(null)
      setStreamingContent('')
    }
  }, [activeId, refreshMessages, regeneratingMessageId, sending, switchingBranchMessageId])

  const switchBranch = useCallback(async (messageId) => {
    if (!activeId || sending || regeneratingMessageId !== null || switchingBranchMessageId !== null) return
    const sourceMessage = messages.find(msg => msg.id === messageId)
    const targetMessageId = sourceMessage?.next_sibling_id
    if (!targetMessageId) return

    setError('')
    setSwitchingBranchMessageId(messageId)
    try {
      await api.activateMessageBranch(activeId, targetMessageId)
      await refreshMessages(activeId)
    } catch (e) {
      setError(e.message || '切换分支失败，请重试')
    } finally {
      setSwitchingBranchMessageId(null)
    }
  }, [activeId, messages, refreshMessages, regeneratingMessageId, sending, switchingBranchMessageId])

  const activeConv = conversations.find(c => c.id === activeId)
  const busy = sending || regeneratingMessageId !== null || switchingBranchMessageId !== null
  const regeneratingMsg = regeneratingMessageId !== null
    ? messages.find(m => m.id === regeneratingMessageId)
    : null
  const regenerationCutoffIndex = regeneratingMessageId === null
    ? -1
    : messages.findIndex(msg => msg.id === regeneratingMessageId)
  const displayedMessages = regenerationCutoffIndex >= 0
    ? messages.slice(0, regenerationCutoffIndex + 1)
    : messages

  return (
    <>
      <div className="flex h-screen overflow-hidden" style={{ background: 'var(--bg-base)', color: 'var(--text-primary)' }}>
        <Sidebar
          open={sidebarOpen}
          conversations={conversations}
          activeId={activeId}
          loading={loadingConvs}
          importLoading={importing}
          importStatus={importStatus}
          palette={palette}
          mode={mode}
          onSelect={id => { void selectConversation(id); if (window.innerWidth < 768) setSidebarOpen(false) }}
          onNew={createConversation}
          onImport={importConversation}
          onDelete={deleteConversation}
          onClose={() => setSidebarOpen(false)}
          onToggleTheme={toggle}
          onSetPalette={setPalette}
          onOpenKeys={openKeysModal}
          user={user}
          onLogout={logout}
        />

        {sidebarOpen && (
          <div
            className="fixed inset-0 z-20 md:hidden"
            style={{ background: 'var(--overlay)' }}
            onClick={() => setSidebarOpen(false)}
          />
        )}

        <div className="flex flex-col flex-1 min-w-0">
          <header
            className="flex items-center gap-3 px-4 py-3 shrink-0 backdrop-blur-sm"
            style={{ borderBottom: '1px solid var(--border)', background: 'color-mix(in srgb, var(--bg-base) 85%, transparent)' }}
          >
            <button
              onClick={() => setSidebarOpen(v => !v)}
              className="p-1.5 rounded-lg transition"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-primary)'; e.currentTarget.style.background = 'var(--bg-elevated)' }}
              onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.background = 'transparent' }}
              aria-label="切换侧边栏"
            >
              {sidebarOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
            </button>
            <h2 className="text-sm font-medium truncate flex-1" style={{ color: 'var(--text-primary)' }}>
              {activeConv?.title || 'AI Chat'}
            </h2>
            {activeConv?.model && (
              <span
                className="text-xs px-2 py-1 rounded-lg shrink-0"
                style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)' }}
              >
                {activeConv.model}
              </span>
            )}
          </header>

          <div className="flex-1 overflow-y-auto scrollbar-thin">
            {loadingMsgs ? (
              <div className="flex items-center justify-center h-full">
                <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--text-muted)' }} />
              </div>
            ) : messages.length === 0 && !streamingContent ? (
              <EmptyState onSend={sendMessage} />
            ) : (
              <div className="max-w-3xl mx-auto px-4 py-6 space-y-1">
                {displayedMessages.map(msg => {
                  const isRegeneratingTarget = regeneratingMessageId === msg.id && msg.role === 'assistant'
                  const isRegeneratingSource = regeneratingMessageId === msg.id
                  const renderedMessage = isRegeneratingTarget
                    ? { ...msg, content: streamingContent, status: 'streaming', error_message: null }
                    : msg

                  return (
                    <MessageBubble
                      key={msg.id}
                      message={renderedMessage}
                      onRegenerate={msg.role === 'system' ? undefined : () => { void regenerateMessage(msg.id) }}
                      onSwitchBranch={msg.next_sibling_id ? () => { void switchBranch(msg.id) } : undefined}
                      disableActions={busy}
                      isRegenerating={isRegeneratingSource}
                      isSwitchingBranch={switchingBranchMessageId === msg.id}
                    />
                  )
                })}
                {sending && !streamingContent && (
                  <div className="flex gap-3 py-3">
                    <div className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 text-xs font-bold"
                      style={{ background: 'var(--accent)', color: 'var(--text-primary)' }}>
                      AI
                    </div>
                    <div className="flex items-center gap-1 pt-2">
                      {[0, 1, 2].map(i => (
                        <span key={i} className="w-1.5 h-1.5 rounded-full animate-bounce"
                          style={{ background: 'var(--text-muted)', animationDelay: `${i * 0.15}s` }} />
                      ))}
                    </div>
                  </div>
                )}
                {streamingContent && (regeneratingMessageId === null || regeneratingMsg?.role === 'user') && (
                  <MessageBubble message={{ role: 'assistant', content: streamingContent, status: 'streaming' }} />
                )}
                {error && (
                  <div className="flex items-center gap-2 text-sm py-2 px-3 rounded-xl"
                    style={{ background: 'var(--error-bg)', border: '1px solid var(--error-border)', color: 'var(--error-text)' }}>
                    <AlertCircle className="w-4 h-4 shrink-0" />
                    {error}
                  </div>
                )}
                <div ref={bottomRef} />
              </div>
            )}
          </div>

          <ChatInput onSend={sendMessage} disabled={busy} />
        </div>
      </div>

      {keysOpen && (
        <ApiKeysModal
          open={keysOpen}
          onClose={() => setKeysOpen(false)}
          apiKeys={apiKeys}
          loading={loadingKeys}
          loadError={keysError}
          onRefresh={loadApiKeys}
          onCreate={api.createApiKey}
          onDelete={api.deleteApiKey}
          onTest={api.testApiKey}
        />
      )}
    </>
  )
}
