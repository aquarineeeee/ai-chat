import { useState, useEffect, useRef, useCallback } from 'react'
import { useAuth } from './AuthContext'
import { useTheme } from './ThemeContext'
import { api } from './api'
import MessageBubble from './components/MessageBubble'
import Sidebar from './components/Sidebar'
import ChatInput from './components/ChatInput'
import EmptyState from './components/EmptyState'
import ApiKeysModal from './components/ApiKeysModal'
import BranchPane from './components/BranchPane'
import { Menu, X, Loader2, AlertCircle } from 'lucide-react'

function createBranchPane(sourceMessage) {
  return {
    id: `branch-${sourceMessage.id}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    rootMessageId: sourceMessage.id,
    sourceMessage,
    messages: [sourceMessage],
    currentLeafMessageId: sourceMessage.id,
    contextMode: 'full',
    loading: true,
    sending: false,
    regeneratingMessageId: null,
    deletingMessageId: null,
    creatingBranchMessageId: null,
    switchingSiblingMessageId: null,
    error: '',
    openedAt: Date.now(),
  }
}

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
  const [deletingMessageId, setDeletingMessageId] = useState(null)
  const [switchingSiblingMessageId, setSwitchingSiblingMessageId] = useState(null)
  const [creatingBranchMessageId, setCreatingBranchMessageId] = useState(null)
  const [streamingContent, setStreamingContent] = useState('')
  const [error, setError] = useState('')
  const [apiKeys, setApiKeys] = useState([])
  const [loadingKeys, setLoadingKeys] = useState(false)
  const [keysError, setKeysError] = useState('')
  const [modelOptions, setModelOptions] = useState([])
  const [loadingModels, setLoadingModels] = useState(false)
  const [savingModel, setSavingModel] = useState(false)
  const [modelError, setModelError] = useState('')
  const [pendingModel, setPendingModel] = useState('')
  const [importing, setImporting] = useState(false)
  const [importStatus, setImportStatus] = useState(null)
  const [branchPanes, setBranchPanes] = useState([])
  const bottomRef = useRef(null)
  const activeConv = conversations.find(c => c.id === activeId)

  const patchBranchPane = useCallback((paneId, updater) => {
    setBranchPanes(prev => prev.map(pane => (
      pane.id === paneId
        ? (typeof updater === 'function' ? updater(pane) : { ...pane, ...updater })
        : pane
    )))
  }, [])

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

  const refreshBranchPane = useCallback(async (conversationId, paneId, options = {}) => {
    const pane = branchPanes.find(item => item.id === paneId)
    if (!pane) return null

    const data = await api.getMessages(conversationId, {
      rootMessageId: pane.rootMessageId,
      leafMessageId: options.leafMessageId,
      expandLeaf: options.expandLeaf,
    })

    patchBranchPane(paneId, current => ({
      ...current,
      loading: false,
      messages: data?.items || [],
      currentLeafMessageId: data?.current_leaf_message_id ?? current.currentLeafMessageId,
      error: '',
    }))
    return data
  }, [branchPanes, patchBranchPane])

  const refreshBranchPanesSnapshot = useCallback(async (conversationId, panesSnapshot, deletedMessageId = null) => {
    if (!panesSnapshot.length) return

    const idsToClose = []
    const updates = []

    for (const pane of panesSnapshot) {
      if (pane.rootMessageId === deletedMessageId) {
        idsToClose.push(pane.id)
        continue
      }

      try {
        const data = await api.getMessages(conversationId, { rootMessageId: pane.rootMessageId })
        updates.push({ paneId: pane.id, data })
      } catch {
        idsToClose.push(pane.id)
      }
    }

    if (idsToClose.length > 0) {
      setBranchPanes(prev => prev.filter(pane => !idsToClose.includes(pane.id)))
    }

    for (const { paneId, data } of updates) {
      patchBranchPane(paneId, current => ({
        ...current,
        loading: false,
        messages: data?.items || [],
        currentLeafMessageId: data?.current_leaf_message_id ?? current.currentLeafMessageId,
        error: '',
      }))
    }
  }, [patchBranchPane])

  const loadApiKeys = useCallback(async () => {
    setKeysError('')
    setLoadingKeys(true)
    try {
      const data = await api.getApiKeys()
      setApiKeys(data || [])
    } catch (err) {
      setKeysError(err.message || '鍔犺浇 API Keys 澶辫触')
    } finally {
      setLoadingKeys(false)
    }
  }, [])

  const loadProviderModels = useCallback(async (provider) => {
    if (!provider) {
      setModelOptions([])
      return []
    }

    setModelError('')
    setLoadingModels(true)
    try {
      const data = await api.getProviderModels(provider)
      const items = Array.isArray(data) ? data : []
      setModelOptions(items)
      return items
    } catch (err) {
      setModelOptions([])
      setModelError(err.message || '鍔犺浇妯″瀷澶辫触')
      return []
    } finally {
      setLoadingModels(false)
    }
  }, [])

  const fetchConversations = useCallback(async () => {
    const data = await api.getConversations()
    return data || []
  }, [])

  const selectConversation = useCallback(async (conversationId) => {
    setActiveId(conversationId)
    setBranchPanes([])
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

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingContent])

  useEffect(() => {
    setPendingModel(activeConv?.model || '')
    setModelError('')
  }, [activeId, activeConv?.model])

  useEffect(() => {
    const provider = activeConv?.provider || 'openai'
    void loadProviderModels(provider)
  }, [activeConv?.provider, loadProviderModels])

  const createConversation = useCallback(async (title = '新对话', model = undefined) => {
    const payload = { title }
    if (model) payload.model = model
    const conv = await api.createConversation(payload)
    setConversations(prev => [conv, ...prev])
    await selectConversation(conv.id)
    return conv
  }, [selectConversation])

  const reloadConversations = useCallback(async (nextActiveId = null) => {
    const items = await fetchConversations()
    setConversations(items)
    const resolvedActiveId = nextActiveId ?? items[0]?.id ?? null

    if (resolvedActiveId) {
      await selectConversation(resolvedActiveId)
    } else {
      setActiveId(null)
      setMessages([])
      setBranchPanes([])
    }

    return items
  }, [fetchConversations, selectConversation])

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

  const changeConversationModel = useCallback(async (nextModel) => {
    setPendingModel(nextModel)
    setModelError('')

    if (!activeConv || !nextModel || nextModel === activeConv.model) {
      return
    }

    setSavingModel(true)
    try {
      const updated = await api.updateConversation(activeConv.id, { model: nextModel })
      setConversations(prev => prev.map(conv => (conv.id === updated.id ? updated : conv)))
    } catch (e) {
      setPendingModel(activeConv.model || '')
      setModelError(e.message || '保存模型失败')
    } finally {
      setSavingModel(false)
    }
  }, [activeConv])

  const openBranchPane = useCallback(async (sourceMessage, paneIdToMark = null) => {
    if (!activeId) return

    const nextPane = createBranchPane(sourceMessage)
    if (paneIdToMark) {
      patchBranchPane(paneIdToMark, { creatingBranchMessageId: sourceMessage.id })
    } else {
      setCreatingBranchMessageId(sourceMessage.id)
    }

    setBranchPanes(prev => {
      const remaining = prev.length >= 2
        ? [...prev].sort((a, b) => a.openedAt - b.openedAt).slice(1)
        : prev
      return [...remaining, nextPane]
    })

    try {
      const data = await api.getMessages(activeId, { rootMessageId: sourceMessage.id })
      patchBranchPane(nextPane.id, {
        loading: false,
        messages: data?.items || [sourceMessage],
        currentLeafMessageId: data?.current_leaf_message_id ?? sourceMessage.id,
        error: '',
      })
    } catch (e) {
      patchBranchPane(nextPane.id, {
        loading: false,
        error: e.message || '鍒涘缓鍒嗘敮澶辫触',
      })
    } finally {
      if (paneIdToMark) {
        patchBranchPane(paneIdToMark, { creatingBranchMessageId: null })
      } else {
        setCreatingBranchMessageId(null)
      }
    }
  }, [activeId, patchBranchPane])

  const closeBranchPane = useCallback((paneId) => {
    setBranchPanes(prev => prev.filter(pane => pane.id !== paneId))
  }, [])

  const sendMessage = useCallback(async (content) => {
    if (!content.trim() || sending || regeneratingMessageId !== null || switchingSiblingMessageId !== null) return
    setError('')

    let convId = activeId
    if (!convId) {
      try {
        const conv = await createConversation(content.slice(0, 40), pendingModel || undefined)
        convId = conv.id
      } catch {
        setError('鍒涘缓瀵硅瘽澶辫触')
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
            // ignore malformed chunks
          }
        }
      }

      setStreamingContent('')
      await refreshMessages(convId)
      if (!streamError && !accumulated) setError('妯″瀷娌℃湁杩斿洖鍐呭')
    } catch (e) {
      setError(e.message || '发送失败，请重试')
      setMessages(prev => prev.filter(m => m.id !== userMsg.id))
    } finally {
      setSending(false)
      setStreamingContent('')
    }
  }, [activeId, createConversation, pendingModel, refreshMessages, regeneratingMessageId, sending, switchingSiblingMessageId])

  const regenerateMainMessage = useCallback(async (messageId) => {
    if (!activeId || sending || regeneratingMessageId !== null || switchingSiblingMessageId !== null) return
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
        throw new Error(err?.error?.message || err?.detail || '閲嶆柊鐢熸垚澶辫触')
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
            // ignore malformed chunks
          }
        }
      }

      await refreshMessages(activeId)
      if (!streamError && !accumulated) setError('妯″瀷娌℃湁杩斿洖鍐呭')
    } catch (e) {
      setError(e.message || '閲嶆柊鐢熸垚澶辫触锛岃閲嶈瘯')
      await refreshMessages(activeId)
    } finally {
      setRegeneratingMessageId(null)
      setStreamingContent('')
    }
  }, [activeId, refreshMessages, regeneratingMessageId, sending, switchingSiblingMessageId])

  const switchMainSibling = useCallback(async (targetMessageId) => {
    if (!activeId || !targetMessageId || sending || regeneratingMessageId !== null || switchingSiblingMessageId !== null) return
    setError('')
    setSwitchingSiblingMessageId(targetMessageId)
    try {
      await api.activateMessageBranch(activeId, targetMessageId)
      await refreshMessages(activeId)
    } catch (e) {
      setError(e.message || '鍒囨崲鍒嗘敮澶辫触锛岃閲嶈瘯')
    } finally {
      setSwitchingSiblingMessageId(null)
    }
  }, [activeId, refreshMessages, regeneratingMessageId, sending, switchingSiblingMessageId])

  const deleteMainMessage = useCallback(async (messageId) => {
    if (
      !activeId
      || sending
      || regeneratingMessageId !== null
      || switchingSiblingMessageId !== null
      || creatingBranchMessageId !== null
      || deletingMessageId !== null
    ) return

    setError('')
    setDeletingMessageId(messageId)
    const panesSnapshot = branchPanes

    try {
      await api.deleteMessage(activeId, messageId)
      await refreshMessages(activeId)
      await refreshBranchPanesSnapshot(activeId, panesSnapshot, messageId)
    } catch (e) {
      setError(e.message || '删除消息失败，请重试')
    } finally {
      setDeletingMessageId(null)
    }
  }, [
    activeId,
    branchPanes,
    creatingBranchMessageId,
    deletingMessageId,
    refreshBranchPanesSnapshot,
    refreshMessages,
    regeneratingMessageId,
    sending,
    switchingSiblingMessageId,
  ])

  const togglePaneContextMode = useCallback((paneId) => {
    patchBranchPane(paneId, pane => ({
      ...pane,
      contextMode: pane.contextMode === 'full' ? 'root_only' : 'full',
    }))
  }, [patchBranchPane])

  const sendBranchMessage = useCallback(async (paneId, content) => {
    const pane = branchPanes.find(item => item.id === paneId)
    if (!activeId || !pane || pane.sending || pane.regeneratingMessageId || pane.switchingSiblingMessageId) return

    patchBranchPane(paneId, { sending: true, error: '' })
    try {
      const res = await api.sendMessage(activeId, {
        content,
        parent_id: pane.currentLeafMessageId,
        activate_branch: false,
        context_mode: pane.contextMode,
        context_root_message_id: pane.rootMessageId,
      })
      await refreshBranchPane(activeId, paneId, { leafMessageId: res?.current_leaf_message_id })
    } catch (e) {
      patchBranchPane(paneId, { error: e.message || '发送失败，请重试' })
    } finally {
      patchBranchPane(paneId, { sending: false })
    }
  }, [activeId, branchPanes, patchBranchPane, refreshBranchPane])

  const regenerateBranchMessage = useCallback(async (paneId, messageId) => {
    const pane = branchPanes.find(item => item.id === paneId)
    if (!activeId || !pane || pane.sending || pane.regeneratingMessageId || pane.switchingSiblingMessageId) return

    patchBranchPane(paneId, { regeneratingMessageId: messageId, error: '' })
    try {
      const res = await api.regenerateMessage(activeId, messageId, {
        activate_branch: false,
        context_mode: pane.contextMode,
        context_root_message_id: pane.rootMessageId,
      })
      await refreshBranchPane(activeId, paneId, { leafMessageId: res?.current_leaf_message_id })
    } catch (e) {
      patchBranchPane(paneId, { error: e.message || '重新生成失败，请重试' })
    } finally {
      patchBranchPane(paneId, { regeneratingMessageId: null })
    }
  }, [activeId, branchPanes, patchBranchPane, refreshBranchPane])

  const switchBranchPaneSibling = useCallback(async (paneId, targetMessageId) => {
    const pane = branchPanes.find(item => item.id === paneId)
    if (!activeId || !pane || !targetMessageId || pane.sending || pane.regeneratingMessageId || pane.switchingSiblingMessageId) return

    patchBranchPane(paneId, { switchingSiblingMessageId: targetMessageId, error: '' })
    try {
      await refreshBranchPane(activeId, paneId, {
        leafMessageId: targetMessageId,
        expandLeaf: true,
      })
    } catch (e) {
      patchBranchPane(paneId, { error: e.message || '切换分支失败，请重试' })
    } finally {
      patchBranchPane(paneId, { switchingSiblingMessageId: null })
    }
  }, [activeId, branchPanes, patchBranchPane, refreshBranchPane])

  const deleteBranchMessage = useCallback(async (paneId, messageId) => {
    const pane = branchPanes.find(item => item.id === paneId)
    if (
      !activeId
      || !pane
      || pane.sending
      || pane.regeneratingMessageId
      || pane.switchingSiblingMessageId
      || pane.creatingBranchMessageId
      || pane.deletingMessageId
    ) return

    patchBranchPane(paneId, { deletingMessageId: messageId, error: '' })
    const panesSnapshot = branchPanes

    try {
      await api.deleteMessage(activeId, messageId)
      await refreshMessages(activeId)
      await refreshBranchPanesSnapshot(activeId, panesSnapshot, messageId)
    } catch (e) {
      patchBranchPane(paneId, { error: e.message || '删除消息失败，请重试' })
    } finally {
      patchBranchPane(paneId, { deletingMessageId: null })
    }
  }, [activeId, branchPanes, patchBranchPane, refreshBranchPanesSnapshot, refreshMessages])

  const modelChoices = [
    ...(pendingModel && !modelOptions.some(option => option.id === pendingModel)
      ? [{ id: pendingModel }]
      : []),
    ...modelOptions,
  ]
  const mainBusy = sending || regeneratingMessageId !== null || switchingSiblingMessageId !== null || creatingBranchMessageId !== null || deletingMessageId !== null
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

          <div className="flex flex-1 min-h-0 overflow-hidden">
            <div className="flex flex-col flex-1 min-w-0">
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
                      const renderedMessage = isRegeneratingTarget
                        ? { ...msg, content: streamingContent, status: 'streaming', error_message: null }
                        : msg

                      return (
                        <MessageBubble
                          key={msg.id}
                          message={renderedMessage}
                          onRegenerate={msg.role === 'system' ? undefined : () => { void regenerateMainMessage(msg.id) }}
                          onDelete={msg.role === 'system' ? undefined : () => { void deleteMainMessage(msg.id) }}
                          onCreateBranch={msg.role === 'system' ? undefined : () => { void openBranchPane(msg) }}
                          onPrevSibling={msg.previous_sibling_id ? () => { void switchMainSibling(msg.previous_sibling_id) } : undefined}
                          onNextSibling={msg.next_sibling_id ? () => { void switchMainSibling(msg.next_sibling_id) } : undefined}
                          disableActions={mainBusy}
                          isRegenerating={regeneratingMessageId === msg.id}
                          isDeleting={deletingMessageId === msg.id}
                          isCreatingBranch={creatingBranchMessageId === msg.id}
                        />
                      )
                    })}
                    {sending && !streamingContent && (
                      <div className="flex gap-3 py-3">
                        <div
                          className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 text-xs font-bold"
                          style={{ background: 'var(--accent)', color: 'var(--text-primary)' }}
                        >
                          AI
                        </div>
                        <div className="flex items-center gap-1 pt-2">
                          {[0, 1, 2].map(i => (
                            <span
                              key={i}
                              className="w-1.5 h-1.5 rounded-full animate-bounce"
                              style={{ background: 'var(--text-muted)', animationDelay: `${i * 0.15}s` }}
                            />
                          ))}
                        </div>
                      </div>
                    )}
                    {streamingContent && (regeneratingMessageId === null || regeneratingMsg?.role === 'user') && (
                      <MessageBubble message={{ role: 'assistant', content: streamingContent, status: 'streaming' }} hideActions />
                    )}
                    {error && (
                      <div
                        className="flex items-center gap-2 text-sm py-2 px-3 rounded-xl"
                        style={{ background: 'var(--error-bg)', border: '1px solid var(--error-border)', color: 'var(--error-text)' }}
                      >
                        <AlertCircle className="w-4 h-4 shrink-0" />
                        {error}
                      </div>
                    )}
                    <div ref={bottomRef} />
                  </div>
                )}
              </div>

              <ChatInput
                onSend={sendMessage}
                disabled={mainBusy}
                modelValue={pendingModel}
                modelOptions={modelChoices}
                modelProvider={activeConv?.provider || 'openai'}
                modelLoading={loadingModels}
                modelSaving={savingModel}
                modelError={modelError}
                onModelChange={changeConversationModel}
              />
            </div>

            {branchPanes.length > 0 && (
              <div
                className="w-[440px] shrink-0 p-4 flex flex-col gap-4"
                style={{ borderLeft: '1px solid var(--border)', background: 'color-mix(in srgb, var(--bg-surface) 78%, transparent)' }}
              >
                {branchPanes.map(pane => (
                  <div key={pane.id} className="flex-1 min-h-0">
                    <BranchPane
                      pane={{
                        ...pane,
                        busy: pane.loading || pane.sending || pane.regeneratingMessageId !== null || pane.switchingSiblingMessageId !== null || pane.creatingBranchMessageId !== null || pane.deletingMessageId !== null,
                      }}
                      onClose={() => closeBranchPane(pane.id)}
                      onToggleContextMode={() => togglePaneContextMode(pane.id)}
                      onSend={content => sendBranchMessage(pane.id, content)}
                      onRegenerate={messageId => regenerateBranchMessage(pane.id, messageId)}
                      onDelete={messageId => deleteBranchMessage(pane.id, messageId)}
                      onCreateBranch={message => openBranchPane(message, pane.id)}
                      onPrevSibling={message => switchBranchPaneSibling(pane.id, message.previous_sibling_id)}
                      onNextSibling={message => switchBranchPaneSibling(pane.id, message.next_sibling_id)}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
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
