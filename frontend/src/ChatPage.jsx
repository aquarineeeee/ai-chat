import { useState, useEffect, useRef, useCallback } from 'react'
import { useAuth } from './AuthContext'
import { useTheme } from './ThemeContext'
import { api } from './api'
import MessageBubble from './components/MessageBubble'
import Sidebar from './components/Sidebar'
import ChatInput from './components/ChatInput'
import EmptyState from './components/EmptyState'
import { Menu, X, Loader2, AlertCircle } from 'lucide-react'

export default function ChatPage() {
  const { user, logout } = useAuth()
  const { theme, toggle } = useTheme()
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [conversations, setConversations] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [messages, setMessages] = useState([])
  const [loadingConvs, setLoadingConvs] = useState(true)
  const [loadingMsgs, setLoadingMsgs] = useState(false)
  const [sending, setSending] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [error, setError] = useState('')
  const bottomRef = useRef(null)

  useEffect(() => {
    api.getConversations()
      .then(data => {
        setConversations(data || [])
        if (data?.length > 0) setActiveId(data[0].id)
      })
      .catch(() => setConversations([]))
      .finally(() => setLoadingConvs(false))
  }, [])

  useEffect(() => {
    if (!activeId) { setMessages([]); return }
    setLoadingMsgs(true)
    setMessages([])
    api.getMessages(activeId)
      .then(data => setMessages(data || []))
      .catch(() => setMessages([]))
      .finally(() => setLoadingMsgs(false))
  }, [activeId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingContent])

  const createConversation = useCallback(async (title = '新对话') => {
    const conv = await api.createConversation({ title })
    setConversations(prev => [conv, ...prev])
    setActiveId(conv.id)
    setMessages([])
    return conv
  }, [])

  const deleteConversation = useCallback(async (id) => {
    await api.deleteConversation(id)
    setConversations(prev => prev.filter(c => c.id !== id))
    if (activeId === id) {
      const remaining = conversations.filter(c => c.id !== id)
      setActiveId(remaining[0]?.id ?? null)
    }
  }, [activeId, conversations])

  const sendMessage = useCallback(async (content) => {
    if (!content.trim() || sending) return
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
        const data = await api.sendMessage(convId, { content })
        setMessages(prev => {
          const withoutTemp = prev.filter(m => m.id !== userMsg.id)
          return [...withoutTemp, ...(data.messages || [data])]
        })
        setSending(false)
        return
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || '发送失败')
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let accumulated = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const raw = line.slice(6).trim()
          if (raw === '[DONE]') break
          try {
            const chunk = JSON.parse(raw)
            if (chunk.content) { accumulated += chunk.content; setStreamingContent(accumulated) }
          } catch {}
        }
      }

      setMessages(prev => [...prev, { id: Date.now() + 1, role: 'assistant', content: accumulated, status: 'completed' }])
      setStreamingContent('')
    } catch (e) {
      setError(e.message || '发送失败，请重试')
      setMessages(prev => prev.filter(m => m.id !== userMsg.id))
    } finally {
      setSending(false)
      setStreamingContent('')
    }
  }, [activeId, sending, createConversation])

  const activeConv = conversations.find(c => c.id === activeId)

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: 'var(--bg-base)', color: 'var(--text-primary)' }}>
      <Sidebar
        open={sidebarOpen}
        conversations={conversations}
        activeId={activeId}
        loading={loadingConvs}
        theme={theme}
        onSelect={id => { setActiveId(id); if (window.innerWidth < 768) setSidebarOpen(false) }}
        onNew={createConversation}
        onDelete={deleteConversation}
        onClose={() => setSidebarOpen(false)}
        onToggleTheme={toggle}
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
        {/* Header */}
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

        {/* Messages */}
        <div className="flex-1 overflow-y-auto scrollbar-thin">
          {loadingMsgs ? (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--text-muted)' }} />
            </div>
          ) : messages.length === 0 && !streamingContent ? (
            <EmptyState onSend={sendMessage} />
          ) : (
            <div className="max-w-3xl mx-auto px-4 py-6 space-y-1">
              {messages.map(msg => (
                <MessageBubble key={msg.id} message={msg} />
              ))}
              {streamingContent && (
                <MessageBubble message={{ role: 'assistant', content: streamingContent, status: 'streaming' }} />
              )}
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

        <ChatInput onSend={sendMessage} disabled={sending} />
      </div>
    </div>
  )
}
