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
import { Menu, X, Loader2, AlertCircle, Download, ChevronDown } from 'lucide-react'

const DEFAULT_BRANCH_PANE_WIDTH = 440
const MIN_BRANCH_PANE_WIDTH = 280
const MAX_BRANCH_PANE_WIDTH = 760
const MIN_MAIN_PANEL_WIDTH = 320
const BRANCH_PANE_WIDTH_STORAGE_KEY = 'ai-chat.branch-pane-width'

function clampBranchPaneWidth(width, containerWidth) {
  if (!Number.isFinite(width)) return DEFAULT_BRANCH_PANE_WIDTH

  if (!Number.isFinite(containerWidth) || containerWidth <= 0) {
    return Math.min(Math.max(width, MIN_BRANCH_PANE_WIDTH), MAX_BRANCH_PANE_WIDTH)
  }

  const minWidth = Math.min(MIN_BRANCH_PANE_WIDTH, Math.max(220, Math.round(containerWidth * 0.28)))
  const maxWidth = Math.min(
    MAX_BRANCH_PANE_WIDTH,
    Math.max(minWidth, Math.round(containerWidth - MIN_MAIN_PANEL_WIDTH)),
  )

  return Math.min(Math.max(width, minWidth), maxWidth)
}

function createBranchPane(sourceMessage, branch = null) {
  return {
    id: `branch-${sourceMessage.id}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    branchId: branch?.id ?? null,
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
    editingMessageId: null,
    editingContent: '',
    editingMode: 'update',
    editingSubmittingMessageId: null,
    error: '',
    openedAt: Date.now(),
  }
}

const EXPORT_OPTIONS = [
  { key: 'markdown-current_branch', label: 'Markdown · 当前分支', format: 'markdown', scope: 'current_branch' },
  { key: 'json-current_branch', label: 'JSON · 当前分支', format: 'json', scope: 'current_branch' },
  { key: 'json-all_branches', label: 'JSON · 全部分支', format: 'json', scope: 'all_branches' },
]

function sortConversations(items) {
  return [...items].sort((a, b) => {
    const timeA = new Date(a.updated_at || a.created_at || 0).getTime()
    const timeB = new Date(b.updated_at || b.created_at || 0).getTime()
    if (timeA !== timeB) return timeB - timeA
    return (b.id || 0) - (a.id || 0)
  })
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
  const [editingMessageId, setEditingMessageId] = useState(null)
  const [editingContent, setEditingContent] = useState('')
  const [editingMode, setEditingMode] = useState('update')
  const [editingSubmittingMessageId, setEditingSubmittingMessageId] = useState(null)
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
  const [branchesByConversation, setBranchesByConversation] = useState({})
  const [loadingBranches, setLoadingBranches] = useState({})
  const [branchPanes, setBranchPanes] = useState([])
  const [branchPaneWidth, setBranchPaneWidth] = useState(() => {
    if (typeof window === 'undefined') return DEFAULT_BRANCH_PANE_WIDTH
    const storedWidth = Number(window.localStorage.getItem(BRANCH_PANE_WIDTH_STORAGE_KEY))
    return clampBranchPaneWidth(storedWidth, NaN)
  })
  const [exportMenuOpen, setExportMenuOpen] = useState(false)
  const [exportingKey, setExportingKey] = useState('')
  const bottomRef = useRef(null)
  const exportMenuRef = useRef(null)
  const conversationLayoutRef = useRef(null)
  const resizeCleanupRef = useRef(null)
  const activeConv = conversations.find(c => c.id === activeId)

  useEffect(() => {
    if (!exportMenuOpen) return undefined

    function handlePointerDown(event) {
      if (!exportMenuRef.current?.contains(event.target)) {
        setExportMenuOpen(false)
      }
    }

    document.addEventListener('mousedown', handlePointerDown)
    return () => document.removeEventListener('mousedown', handlePointerDown)
  }, [exportMenuOpen])

  useEffect(() => {
    window.localStorage.setItem(BRANCH_PANE_WIDTH_STORAGE_KEY, String(branchPaneWidth))
  }, [branchPaneWidth])

  useEffect(() => {
    function syncBranchPaneWidth() {
      const containerWidth = conversationLayoutRef.current?.getBoundingClientRect().width
      if (!containerWidth) return
      setBranchPaneWidth(current => clampBranchPaneWidth(current, containerWidth))
    }

    syncBranchPaneWidth()
    window.addEventListener('resize', syncBranchPaneWidth)
    return () => window.removeEventListener('resize', syncBranchPaneWidth)
  }, [branchPanes.length])

  const stopBranchPaneResize = useCallback(() => {
    if (!resizeCleanupRef.current) return
    resizeCleanupRef.current()
    resizeCleanupRef.current = null
  }, [])

  useEffect(() => stopBranchPaneResize, [stopBranchPaneResize])

  const startBranchPaneResize = useCallback((event) => {
    if (event.button !== 0) return

    const updateWidth = (clientX) => {
      const containerWidth = conversationLayoutRef.current?.getBoundingClientRect().width
      const containerRight = conversationLayoutRef.current?.getBoundingClientRect().right
      if (!containerWidth || !containerRight) return
      setBranchPaneWidth(clampBranchPaneWidth(containerRight - clientX, containerWidth))
    }

    stopBranchPaneResize()
    event.preventDefault()
    updateWidth(event.clientX)

    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect

    const handlePointerMove = (moveEvent) => {
      updateWidth(moveEvent.clientX)
    }

    const handlePointerUp = () => {
      stopBranchPaneResize()
    }

    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', handlePointerUp)
    window.addEventListener('pointercancel', handlePointerUp)

    resizeCleanupRef.current = () => {
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)
      window.removeEventListener('pointercancel', handlePointerUp)
    }
  }, [stopBranchPaneResize])

  const patchBranchPane = useCallback((paneId, updater) => {
    setBranchPanes(prev => prev.map(pane => (
      pane.id === paneId
        ? (typeof updater === 'function' ? updater(pane) : { ...pane, ...updater })
        : pane
    )))
  }, [])

  const loadBranches = useCallback(async (conversationId) => {
    if (!conversationId) return []

    setLoadingBranches(prev => ({ ...prev, [conversationId]: true }))
    try {
      const data = await api.getBranches(conversationId)
      const items = data || []
      setBranchesByConversation(prev => ({ ...prev, [conversationId]: items }))
      return items
    } finally {
      setLoadingBranches(prev => ({ ...prev, [conversationId]: false }))
    }
  }, [])

  const patchConversationBranchState = useCallback((conversationId, data) => {
    setConversations(prev => prev.map(conv => (
      conv.id === conversationId
        ? {
          ...conv,
          current_branch_id: data?.current_branch_id ?? conv.current_branch_id,
          current_leaf_message_id: data?.current_leaf_message_id ?? conv.current_leaf_message_id,
        }
        : conv
    )))
  }, [])

  const refreshMessages = useCallback(async (conversationId) => {
    const data = await api.getMessages(conversationId)
    setMessages(data?.items || [])
    patchConversationBranchState(conversationId, data)
    return data
  }, [patchConversationBranchState])

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

  const resetMainEdit = useCallback(() => {
    setEditingMessageId(null)
    setEditingContent('')
    setEditingMode('update')
  }, [])

  const copyToClipboard = useCallback(async (content) => {
    const text = content ?? ''
    if (!navigator?.clipboard?.writeText) {
      throw new Error('当前环境不支持复制')
    }
    await navigator.clipboard.writeText(text)
  }, [])

  const fetchConversations = useCallback(async () => {
    const data = await api.getConversations()
    return data || []
  }, [])

  const selectConversation = useCallback(async (conversationId) => {
    setActiveId(conversationId)
    setBranchPanes([])
    resetMainEdit()
    if (!conversationId) {
      setMessages([])
      return
    }

    setLoadingMsgs(true)
    setMessages([])
    try {
      await Promise.all([
        refreshMessages(conversationId),
        loadBranches(conversationId),
      ])
    } catch {
      setMessages([])
    } finally {
      setLoadingMsgs(false)
    }
  }, [loadBranches, refreshMessages, resetMainEdit])

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
    queueMicrotask(() => {
      setPendingModel(activeConv?.model || '')
      setModelError('')
    })
  }, [activeId, activeConv?.model])

  useEffect(() => {
    const provider = activeConv?.provider || 'openai'
    queueMicrotask(() => {
      void loadProviderModels(provider)
    })
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

  const renameConversation = useCallback(async (id, title) => {
    const updated = await api.updateConversation(id, { title })
    setConversations(prev => sortConversations(prev.map(conv => (conv.id === updated.id ? updated : conv))))
    return updated
  }, [])

  const activateBranch = useCallback(async (conversationId, branchId) => {
    setActiveId(conversationId)
    setBranchPanes([])
    resetMainEdit()
    setLoadingMsgs(true)
    setMessages([])
    try {
      const data = await api.activateBranch(conversationId, branchId)
      setMessages(data?.items || [])
      patchConversationBranchState(conversationId, data)
      await loadBranches(conversationId)
      return data
    } finally {
      setLoadingMsgs(false)
    }
  }, [loadBranches, patchConversationBranchState, resetMainEdit])

  const renameBranch = useCallback(async (conversationId, branchId, title) => {
    const updated = await api.updateBranch(conversationId, branchId, { title })
    setBranchesByConversation(prev => ({
      ...prev,
      [conversationId]: (prev[conversationId] || []).map(branch => (
        branch.id === updated.id ? updated : branch
      )),
    }))
    return updated
  }, [])

  const deleteConversation = useCallback(async (id) => {
    await api.deleteConversation(id)
    const remaining = conversations.filter(c => c.id !== id)
    setConversations(remaining)
    setBranchesByConversation(prev => {
      const next = { ...prev }
      delete next[id]
      return next
    })
    if (activeId === id) {
      await selectConversation(remaining[0]?.id ?? null)
    }
  }, [activeId, conversations, selectConversation])

  const exportConversation = useCallback(async ({ key, format, scope }) => {
    if (!activeConv || exportingKey) return

    setError('')
    setExportingKey(key)
    try {
      await api.exportConversation(activeConv.id, { format, scope })
      setExportMenuOpen(false)
    } catch (e) {
      setError(e.message || '导出失败，请重试')
    } finally {
      setExportingKey('')
    }
  }, [activeConv, exportingKey])

  const changeConversationModel = useCallback(async (nextModel) => {
    setPendingModel(nextModel)
    setModelError('')

    if (!activeConv || !nextModel || nextModel === activeConv.model) {
      return
    }

    setSavingModel(true)
    try {
      const updated = await api.updateConversation(activeConv.id, { model: nextModel })
      setConversations(prev => sortConversations(prev.map(conv => (conv.id === updated.id ? updated : conv))))
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

    setBranchPanes([nextPane])

    try {
      const sourcePane = paneIdToMark ? branchPanes.find(item => item.id === paneIdToMark) : null
      const parentBranchId = sourcePane?.branchId ?? activeConv?.current_branch_id ?? null
      const branch = await api.createBranch(activeId, {
        ...(parentBranchId ? { parent_branch_id: parentBranchId } : {}),
        forked_from_message_id: sourceMessage.id,
      })
      patchBranchPane(nextPane.id, { branchId: branch.id })
      await loadBranches(activeId)
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
  }, [activeConv, activeId, branchPanes, loadBranches, patchBranchPane])

  const closeBranchPane = useCallback((paneId) => {
    setBranchPanes(prev => prev.filter(pane => pane.id !== paneId))
  }, [])

  const copyMainMessage = useCallback(async (message) => {
    try {
      await copyToClipboard(message?.content || '')
    } catch (e) {
      setError(e.message || '复制失败，请重试')
    }
  }, [copyToClipboard])

  const startMainEdit = useCallback((message) => {
    setError('')
    setEditingMessageId(message.id)
    setEditingContent(message.content || '')
    setEditingMode('update')
  }, [])

  const submitMainEdit = useCallback(async (messageId) => {
    if (!activeId || !editingContent.trim() || editingSubmittingMessageId !== null) return

    setError('')
    setEditingSubmittingMessageId(messageId)
    const panesSnapshot = branchPanes

    try {
      await api.editMessage(activeId, messageId, {
        content: editingContent.trim(),
        mode: editingMode,
        ...(activeConv?.current_branch_id ? { branch_id: activeConv.current_branch_id } : {}),
      })
      await refreshMessages(activeId)
      await loadBranches(activeId)
      await refreshBranchPanesSnapshot(activeId, panesSnapshot)
      resetMainEdit()
    } catch (e) {
      setError(e.message || '编辑消息失败，请重试')
    } finally {
      setEditingSubmittingMessageId(null)
    }
  }, [
    activeId,
    branchPanes,
    editingContent,
    editingMode,
    editingSubmittingMessageId,
    activeConv,
    loadBranches,
    refreshBranchPanesSnapshot,
    refreshMessages,
    resetMainEdit,
  ])

  const sendMessage = useCallback(async (content) => {
    if (!content.trim() || sending || regeneratingMessageId !== null || switchingSiblingMessageId !== null) return
    setError('')

    let convId = activeId
    let branchId = activeConv?.current_branch_id ?? null
    if (!convId) {
      try {
        const conv = await createConversation(content.slice(0, 40), pendingModel || undefined)
        convId = conv.id
        branchId = conv.current_branch_id ?? null
      } catch {
        setError('鍒涘缓瀵硅瘽澶辫触')
        return
      }
    }

    const userMsg = {
      id: Date.now(),
      role: 'user',
      content,
      status: 'completed',
      created_at: new Date().toISOString(),
    }
    setMessages(prev => [...prev, userMsg])
    setSending(true)
    setStreamingContent('')

    try {
      const res = await fetch(`/api/conversations/${convId}/messages/stream`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, ...(branchId ? { branch_id: branchId } : {}) }),
      })

      if (res.status === 404 || res.status === 405) {
        await api.sendMessage(convId, { content, ...(branchId ? { branch_id: branchId } : {}) })
        await refreshMessages(convId)
        await loadBranches(convId)
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
      await loadBranches(convId)
      if (!streamError && !accumulated) setError('妯″瀷娌℃湁杩斿洖鍐呭')
    } catch (e) {
      setError(e.message || '发送失败，请重试')
      setMessages(prev => prev.filter(m => m.id !== userMsg.id))
    } finally {
      setSending(false)
      setStreamingContent('')
    }
  }, [activeConv, activeId, createConversation, loadBranches, pendingModel, refreshMessages, regeneratingMessageId, sending, switchingSiblingMessageId])

  const regenerateMainMessage = useCallback(async (messageId) => {
    if (!activeId || sending || regeneratingMessageId !== null || switchingSiblingMessageId !== null) return
    setError('')
    setRegeneratingMessageId(messageId)
    setStreamingContent('')
    const branchId = activeConv?.current_branch_id ?? null

    try {
      const res = await fetch(`/api/conversations/${activeId}/messages/${messageId}/regenerate/stream`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(branchId ? { branch_id: branchId } : {}),
      })

      if (res.status === 404 || res.status === 405) {
        await api.regenerateMessage(activeId, messageId, branchId ? { branch_id: branchId } : {})
        await refreshMessages(activeId)
        await loadBranches(activeId)
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
      await loadBranches(activeId)
      if (!streamError && !accumulated) setError('妯″瀷娌℃湁杩斿洖鍐呭')
    } catch (e) {
      setError(e.message || '閲嶆柊鐢熸垚澶辫触锛岃閲嶈瘯')
      await refreshMessages(activeId)
    } finally {
      setRegeneratingMessageId(null)
      setStreamingContent('')
    }
  }, [activeConv, activeId, loadBranches, refreshMessages, regeneratingMessageId, sending, switchingSiblingMessageId])

  const switchMainSibling = useCallback(async (targetMessageId) => {
    if (!activeId || !targetMessageId || sending || regeneratingMessageId !== null || switchingSiblingMessageId !== null) return
    setError('')
    setSwitchingSiblingMessageId(targetMessageId)
    try {
      await api.activateMessageBranch(activeId, targetMessageId)
      await refreshMessages(activeId)
      await loadBranches(activeId)
    } catch (e) {
      setError(e.message || '鍒囨崲鍒嗘敮澶辫触锛岃閲嶈瘯')
    } finally {
      setSwitchingSiblingMessageId(null)
    }
  }, [activeId, loadBranches, refreshMessages, regeneratingMessageId, sending, switchingSiblingMessageId])

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
      await loadBranches(activeId)
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
    loadBranches,
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

  const copyBranchMessage = useCallback(async (paneId, message) => {
    try {
      await copyToClipboard(message?.content || '')
    } catch (e) {
      patchBranchPane(paneId, { error: e.message || '复制失败，请重试' })
    }
  }, [copyToClipboard, patchBranchPane])

  const startBranchEdit = useCallback((paneId, message) => {
    patchBranchPane(paneId, {
      editingMessageId: message.id,
      editingContent: message.content || '',
      editingMode: 'update',
      error: '',
    })
  }, [patchBranchPane])

  const cancelBranchEdit = useCallback((paneId) => {
    patchBranchPane(paneId, {
      editingMessageId: null,
      editingContent: '',
      editingMode: 'update',
    })
  }, [patchBranchPane])

  const sendBranchMessage = useCallback(async (paneId, content) => {
    const pane = branchPanes.find(item => item.id === paneId)
    if (!activeId || !pane || pane.sending || pane.regeneratingMessageId || pane.switchingSiblingMessageId) return

    patchBranchPane(paneId, { sending: true, error: '' })
    try {
      const res = await api.sendMessage(activeId, {
        content,
        parent_id: pane.currentLeafMessageId,
        ...(pane.branchId ? { branch_id: pane.branchId } : {}),
        activate_branch: false,
        context_mode: pane.contextMode,
        context_root_message_id: pane.rootMessageId,
      })
      await refreshBranchPane(activeId, paneId, { leafMessageId: res?.current_leaf_message_id })
      await loadBranches(activeId)
    } catch (e) {
      patchBranchPane(paneId, { error: e.message || '发送失败，请重试' })
    } finally {
      patchBranchPane(paneId, { sending: false })
    }
  }, [activeId, branchPanes, loadBranches, patchBranchPane, refreshBranchPane])

  const regenerateBranchMessage = useCallback(async (paneId, messageId) => {
    const pane = branchPanes.find(item => item.id === paneId)
    if (!activeId || !pane || pane.sending || pane.regeneratingMessageId || pane.switchingSiblingMessageId) return

    patchBranchPane(paneId, { regeneratingMessageId: messageId, error: '' })
    try {
      const res = await api.regenerateMessage(activeId, messageId, {
        ...(pane.branchId ? { branch_id: pane.branchId } : {}),
        activate_branch: false,
        context_mode: pane.contextMode,
        context_root_message_id: pane.rootMessageId,
      })
      await refreshBranchPane(activeId, paneId, { leafMessageId: res?.current_leaf_message_id })
      await loadBranches(activeId)
    } catch (e) {
      patchBranchPane(paneId, { error: e.message || '重新生成失败，请重试' })
    } finally {
      patchBranchPane(paneId, { regeneratingMessageId: null })
    }
  }, [activeId, branchPanes, loadBranches, patchBranchPane, refreshBranchPane])

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
      await loadBranches(activeId)
      await refreshBranchPanesSnapshot(activeId, panesSnapshot, messageId)
    } catch (e) {
      patchBranchPane(paneId, { error: e.message || '删除消息失败，请重试' })
    } finally {
      patchBranchPane(paneId, { deletingMessageId: null })
    }
  }, [activeId, branchPanes, loadBranches, patchBranchPane, refreshBranchPanesSnapshot, refreshMessages])

  const submitBranchEdit = useCallback(async (paneId, messageId) => {
    const pane = branchPanes.find(item => item.id === paneId)
    if (
      !activeId
      || !pane
      || !pane.editingContent.trim()
      || pane.editingSubmittingMessageId !== null
    ) return

    const panesSnapshot = branchPanes.filter(item => item.id !== paneId)
    patchBranchPane(paneId, { editingSubmittingMessageId: messageId, error: '' })

    try {
      const result = await api.editMessage(activeId, messageId, {
        content: pane.editingContent.trim(),
        mode: pane.editingMode,
        ...(pane.branchId ? { branch_id: pane.branchId } : {}),
        context_mode: pane.contextMode,
        context_root_message_id: pane.rootMessageId,
      })
      await refreshMessages(activeId)
      await loadBranches(activeId)
      await refreshBranchPane(
        activeId,
        paneId,
        pane.editingMode === 'branch' && result?.current_leaf_message_id
          ? { leafMessageId: result.current_leaf_message_id }
          : {},
      )
      await refreshBranchPanesSnapshot(activeId, panesSnapshot)
      patchBranchPane(paneId, {
        editingMessageId: null,
        editingContent: '',
        editingMode: 'update',
      })
    } catch (e) {
      patchBranchPane(paneId, { error: e.message || '编辑消息失败，请重试' })
    } finally {
      patchBranchPane(paneId, { editingSubmittingMessageId: null })
    }
  }, [
    activeId,
    branchPanes,
    loadBranches,
    patchBranchPane,
    refreshBranchPane,
    refreshBranchPanesSnapshot,
    refreshMessages,
  ])

  const modelChoices = [
    ...(pendingModel && !modelOptions.some(option => option.id === pendingModel)
      ? [{ id: pendingModel }]
      : []),
    ...modelOptions,
  ]
  const mainBusy = sending
    || regeneratingMessageId !== null
    || switchingSiblingMessageId !== null
    || creatingBranchMessageId !== null
    || deletingMessageId !== null
    || editingSubmittingMessageId !== null
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
          branchesByConversation={branchesByConversation}
          loadingBranches={loadingBranches}
          activeId={activeId}
          activeBranchId={activeConv?.current_branch_id ?? null}
          loading={loadingConvs}
          importLoading={importing}
          importStatus={importStatus}
          palette={palette}
          mode={mode}
          onSelect={id => { void selectConversation(id); if (window.innerWidth < 768) setSidebarOpen(false) }}
          onBranchSelect={(conversationId, branchId) => {
            void activateBranch(conversationId, branchId)
            if (window.innerWidth < 768) setSidebarOpen(false)
          }}
          onNew={createConversation}
          onImport={importConversation}
          onDelete={deleteConversation}
          onRename={renameConversation}
          onRenameBranch={renameBranch}
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
            <div className="relative shrink-0" ref={exportMenuRef}>
              <button
                type="button"
                onClick={() => activeConv && setExportMenuOpen(open => !open)}
                disabled={!activeConv || !!exportingKey}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
                aria-haspopup="menu"
                aria-expanded={exportMenuOpen}
                aria-label="导出对话"
              >
                {exportingKey ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
                <span className="text-sm">导出</span>
                <ChevronDown className="w-4 h-4" />
              </button>
              {exportMenuOpen && activeConv && (
                <div
                  className="absolute right-0 top-full mt-2 w-56 rounded-xl p-1 z-20"
                  style={{
                    background: 'color-mix(in srgb, var(--bg-surface) 94%, transparent)',
                    border: '1px solid var(--border)',
                    boxShadow: '0 18px 48px rgba(0, 0, 0, 0.18)',
                    backdropFilter: 'blur(18px)',
                  }}
                  role="menu"
                >
                  {EXPORT_OPTIONS.map(option => (
                    <button
                      key={option.key}
                      type="button"
                      onClick={() => { void exportConversation(option) }}
                      disabled={!!exportingKey}
                      className="w-full text-left px-3 py-2 rounded-lg text-sm transition disabled:opacity-50"
                      style={{
                        color: 'var(--text-secondary)',
                        background: exportingKey === option.key ? 'var(--bg-elevated)' : 'transparent',
                      }}
                      role="menuitem"
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </header>

          <div className="flex flex-1 min-h-0 overflow-hidden" ref={conversationLayoutRef}>
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
                          onCopy={msg.role === 'system' ? undefined : () => { void copyMainMessage(msg) }}
                          onEdit={msg.role === 'user' ? () => { void startMainEdit(msg) } : undefined}
                          onRegenerate={msg.role === 'system' ? undefined : () => { void regenerateMainMessage(msg.id) }}
                          onDelete={msg.role === 'system' ? undefined : () => { void deleteMainMessage(msg.id) }}
                          onCreateBranch={msg.role === 'system' ? undefined : () => { void openBranchPane(msg) }}
                          onPrevSibling={msg.previous_sibling_id ? () => { void switchMainSibling(msg.previous_sibling_id) } : undefined}
                          onNextSibling={msg.next_sibling_id ? () => { void switchMainSibling(msg.next_sibling_id) } : undefined}
                          isEditing={editingMessageId === msg.id}
                          editDraft={editingMessageId === msg.id ? editingContent : ''}
                          editMode={editingMode}
                          onEditDraftChange={setEditingContent}
                          onEditModeChange={setEditingMode}
                          onEditCancel={resetMainEdit}
                          onEditSubmit={() => { void submitMainEdit(msg.id) }}
                          isEditSubmitting={editingSubmittingMessageId === msg.id}
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
              <>
                <div
                  role="separator"
                  aria-orientation="vertical"
                  aria-label="调整分支视图宽度"
                  onPointerDown={startBranchPaneResize}
                  className="shrink-0 flex items-stretch justify-center cursor-col-resize group"
                  style={{ width: '14px', touchAction: 'none' }}
                >
                  <div
                    className="h-full transition-colors"
                    style={{
                      width: '1px',
                      background: 'transparent',
                      boxShadow: '0 0 0 3px transparent',
                    }}
                  />
                </div>

                <div
                  className="h-full shrink-0 p-4 flex flex-col gap-4"
                  style={{
                    width: `${branchPaneWidth}px`,
                    background: 'color-mix(in srgb, var(--bg-surface) 78%, transparent)',
                  }}
                >
                  {branchPanes.map(pane => (
                    <div key={pane.id} className="flex-1 min-h-0">
                      <BranchPane
                        pane={{
                          ...pane,
                          busy: pane.loading
                            || pane.sending
                            || pane.regeneratingMessageId !== null
                            || pane.switchingSiblingMessageId !== null
                            || pane.creatingBranchMessageId !== null
                            || pane.deletingMessageId !== null
                            || pane.editingSubmittingMessageId !== null,
                        }}
                        onClose={() => closeBranchPane(pane.id)}
                        onToggleContextMode={() => togglePaneContextMode(pane.id)}
                        onCopy={message => copyBranchMessage(pane.id, message)}
                        onEdit={message => startBranchEdit(pane.id, message)}
                        onEditCancel={() => cancelBranchEdit(pane.id)}
                        onEditSubmit={messageId => submitBranchEdit(pane.id, messageId)}
                        onEditDraftChange={content => patchBranchPane(pane.id, { editingContent: content })}
                        onEditModeChange={mode => patchBranchPane(pane.id, { editingMode: mode })}
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
              </>
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
