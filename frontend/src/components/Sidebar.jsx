import { useEffect, useRef, useState } from 'react'
import {
  Plus,
  Trash2,
  MessageSquare,
  Loader2,
  LogOut,
  ChevronLeft,
  User,
  Sun,
  Moon,
  Key,
  AlertTriangle,
  Palette,
  FileInput,
  CheckCircle2,
  Pencil,
  Check,
  X,
  GitBranch,
  MoreHorizontal,
} from 'lucide-react'
import { PALETTES } from '../ThemeContext'

const PALETTE_COLORS = {
  stone: '#6e5c52',
  lavender: '#7c6fa0',
  sage: '#5e8a6e',
  blue: '#4a72a8',
}

function buildVisibleBranchTree(branches) {
  const byParent = new Map()
  branches.forEach(branch => {
    const key = branch.parent_branch_id ?? null
    byParent.set(key, [...(byParent.get(key) || []), branch])
  })

  const rows = []
  const visit = (parentId, depth) => {
    const children = byParent.get(parentId) || []
    children.forEach(branch => {
      if (branch.parent_branch_id !== null) {
        rows.push({ branch, depth })
      }
      visit(branch.id, branch.parent_branch_id === null ? 0 : depth + 1)
    })
  }

  visit(null, 0)
  return rows
}

function DeleteConfirmModal({ title, description, onConfirm, onCancel, deleting, confirmLabel = '删除' }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'var(--overlay)' }}
      onClick={onCancel}
    >
      <div
        className="w-full max-w-sm rounded-2xl p-5 shadow-xl"
        style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}
        onClick={e => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start gap-3">
          <div
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
            style={{ background: 'var(--error-bg)' }}
          >
            <AlertTriangle className="h-4 w-4" style={{ color: 'var(--error-text)' }} />
          </div>
          <div>
            <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
              {title}
            </p>
            <p className="mt-1 text-xs" style={{ color: 'var(--text-muted)' }}>
              {description}
            </p>
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={deleting}
            className="rounded-lg px-3 py-1.5 text-sm transition"
            style={{ color: 'var(--text-secondary)', background: 'var(--bg-elevated)' }}
            onMouseEnter={e => {
              e.currentTarget.style.background = 'var(--border)'
            }}
            onMouseLeave={e => {
              e.currentTarget.style.background = 'var(--bg-elevated)'
            }}
          >
            取消
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={deleting}
            className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition"
            style={{
              background: 'var(--error-bg)',
              color: 'var(--error-text)',
              border: '1px solid var(--error-border)',
            }}
            onMouseEnter={e => {
              e.currentTarget.style.opacity = '0.85'
            }}
            onMouseLeave={e => {
              e.currentTarget.style.opacity = '1'
            }}
          >
            {deleting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Sidebar({
  open,
  conversations,
  branchesByConversation = {},
  loadingBranches = {},
  activeId,
  activeBranchId,
  loading,
  importLoading,
  importStatus,
  onSelect,
  onBranchSelect,
  onNew,
  onImport,
  onDelete,
  onRename,
  onRenameBranch,
  onDeleteBranch,
  onClose,
  onToggleTheme,
  palette,
  mode,
  onSetPalette,
  user,
  onLogout,
  onOpenKeys,
}) {
  const [deletingId, setDeletingId] = useState(null)
  const [pendingDelete, setPendingDelete] = useState(null)
  const [editingId, setEditingId] = useState(null)
  const [editingTitle, setEditingTitle] = useState('')
  const [renamingId, setRenamingId] = useState(null)
  const [renameError, setRenameError] = useState('')
  const [editingBranchId, setEditingBranchId] = useState(null)
  const [editingBranchTitle, setEditingBranchTitle] = useState('')
  const [renamingBranchId, setRenamingBranchId] = useState(null)
  const [branchRenameError, setBranchRenameError] = useState('')
  const [branchMenu, setBranchMenu] = useState(null)
  const [pendingBranchDelete, setPendingBranchDelete] = useState(null)
  const [deletingBranchId, setDeletingBranchId] = useState(null)
  const branchMenuRef = useRef(null)
  const fileInputRef = useRef(null)

  useEffect(() => {
    if (!branchMenu) return undefined

    const handlePointerDown = event => {
      if (branchMenuRef.current && !branchMenuRef.current.contains(event.target)) {
        setBranchMenu(null)
      }
    }

    const handleKeyDown = event => {
      if (event.key === 'Escape') {
        setBranchMenu(null)
      }
    }

    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [branchMenu])

  function handleDelete(e, id) {
    e.stopPropagation()
    setPendingDelete(id)
  }

  async function confirmDelete() {
    const id = pendingDelete
    setDeletingId(id)
    setPendingDelete(null)
    try {
      await onDelete(id)
    } finally {
      setDeletingId(null)
    }
  }

  function startRename(e, conv) {
    e.stopPropagation()
    setPendingDelete(null)
    setRenameError('')
    setEditingId(conv.id)
    setEditingTitle(conv.title || '')
  }

  function cancelRename(e) {
    e?.stopPropagation?.()
    setEditingId(null)
    setEditingTitle('')
    setRenameError('')
    setRenamingId(null)
  }

  async function submitRename(e, id) {
    e.preventDefault()
    e.stopPropagation()

    const nextTitle = editingTitle.trim()
    if (!nextTitle) {
      setRenameError('标题不能为空')
      return
    }
    if (typeof onRename !== 'function') {
      cancelRename()
      return
    }

    setRenameError('')
    setRenamingId(id)
    try {
      await onRename(id, nextTitle)
      cancelRename()
    } catch (err) {
      setRenameError(err.message || '重命名失败，请重试')
    } finally {
      setRenamingId(null)
    }
  }

  function branchTitle(branch) {
    return branch?.title || branch?.auto_title || '未命名分支'
  }

  function toggleBranchMenu(e, convId, branchId) {
    e.stopPropagation()
    setPendingDelete(null)
    setBranchMenu(current => (
      current?.conversationId === convId && current?.branchId === branchId
        ? null
        : { conversationId: convId, branchId }
    ))
  }

  function startBranchRename(e, conv, branch) {
    e.stopPropagation()
    setPendingDelete(null)
    setBranchMenu(null)
    setBranchRenameError('')
    setEditingBranchId(branch.id)
    setEditingBranchTitle(branchTitle(branch))
  }

  function promptBranchDelete(e, conv, branch) {
    e.stopPropagation()
    setBranchMenu(null)
    setPendingDelete(null)
    setPendingBranchDelete({
      conversationId: conv.id,
      branchId: branch.id,
      title: branchTitle(branch),
    })
  }

  function cancelBranchRename(e) {
    e?.stopPropagation?.()
    setEditingBranchId(null)
    setEditingBranchTitle('')
    setBranchRenameError('')
    setRenamingBranchId(null)
  }

  async function submitBranchRename(e, convId, branchId) {
    e.preventDefault()
    e.stopPropagation()

    const nextTitle = editingBranchTitle.trim()
    if (!nextTitle) {
      setBranchRenameError('分支名不能为空')
      return
    }
    if (typeof onRenameBranch !== 'function') {
      cancelBranchRename()
      return
    }

    setBranchRenameError('')
    setRenamingBranchId(branchId)
    try {
      await onRenameBranch(convId, branchId, nextTitle)
      cancelBranchRename()
    } catch (err) {
      setBranchRenameError(err.message || '重命名分支失败，请重试')
    } finally {
      setRenamingBranchId(null)
    }
  }

  async function confirmBranchDelete() {
    const target = pendingBranchDelete
    if (!target || typeof onDeleteBranch !== 'function') {
      setPendingBranchDelete(null)
      return
    }

    setDeletingBranchId(target.branchId)
    setPendingBranchDelete(null)
    try {
      await onDeleteBranch(target.conversationId, target.branchId)
    } finally {
      setDeletingBranchId(null)
    }
  }

  function formatDate(dateStr) {
    if (!dateStr) return ''
    const d = new Date(dateStr)
    return d.toLocaleString('zh-CN', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  async function handleImportChange(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    if (typeof onImport !== 'function') return
    await onImport(file)
  }

  const btnHover = {
    onMouseEnter: e => {
      e.currentTarget.style.background = 'var(--bg-elevated)'
    },
    onMouseLeave: e => {
      e.currentTarget.style.background = 'transparent'
    },
  }

  return (
    <aside
      className={`
        fixed md:relative z-30 inset-y-0 left-0
        flex flex-col w-64
        transition-transform duration-200
        ${open ? 'translate-x-0' : '-translate-x-full md:-translate-x-full'}
      `}
      style={{ background: 'var(--bg-surface)', borderRight: '1px solid var(--border)' }}
    >
      <div
        className="flex shrink-0 items-center justify-between px-4 py-4"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        <div className="flex items-center gap-2">
          <div
            className="flex h-7 w-7 items-center justify-center rounded-lg"
            style={{ background: 'var(--accent)' }}
          >
            <MessageSquare className="h-4 w-4" style={{ color: 'var(--text-primary)' }} />
          </div>
          <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
            AI Chat
          </span>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg p-1 transition md:hidden"
          style={{ color: 'var(--text-muted)' }}
          {...btnHover}
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
      </div>

      <div className="shrink-0 px-3 py-3">
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => onNew()}
            className="flex flex-1 items-center justify-center gap-2 rounded-xl px-3 py-2.5 text-sm font-medium transition"
            style={{ background: 'var(--accent)', color: 'var(--text-primary)' }}
            onMouseEnter={e => {
              e.currentTarget.style.background = 'var(--accent-hover)'
            }}
            onMouseLeave={e => {
              e.currentTarget.style.background = 'var(--accent)'
            }}
          >
            <Plus className="h-4 w-4" />
            新对话
          </button>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={importLoading || typeof onImport !== 'function'}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl transition"
            style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
            title="导入 Markdown"
            aria-label="导入 Markdown"
            onMouseEnter={e => {
              if (!e.currentTarget.disabled) e.currentTarget.style.background = 'var(--border)'
            }}
            onMouseLeave={e => {
              e.currentTarget.style.background = 'var(--bg-elevated)'
            }}
          >
            {importLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileInput className="h-4 w-4" />}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".md,.markdown,text/markdown"
            className="hidden"
            onChange={handleImportChange}
          />
        </div>
        {importStatus && (
          <div
            className="mt-3 rounded-xl px-3 py-2 text-xs leading-relaxed"
            style={{
              background: importStatus.type === 'success' ? 'var(--bg-elevated)' : 'var(--error-bg)',
              border: `1px solid ${importStatus.type === 'success' ? 'var(--border)' : 'var(--error-border)'}`,
              color: importStatus.type === 'success' ? 'var(--text-secondary)' : 'var(--error-text)',
            }}
          >
            <div className="flex items-center gap-2 font-medium" style={{ color: 'var(--text-primary)' }}>
              {importStatus.type === 'success'
                ? <CheckCircle2 className="h-3.5 w-3.5" />
                : <AlertTriangle className="h-3.5 w-3.5" />}
              <span className="truncate">{importStatus.title}</span>
            </div>
            <p className="mt-1">{importStatus.message}</p>
            {importStatus.type === 'success' && importStatus.meta && (
              <p className="mt-1" style={{ color: 'var(--text-muted)' }}>
                消息 {importStatus.meta.messageCount} · 忽略 {importStatus.meta.ignoredCount} · 警告 {importStatus.meta.warningCount}
              </p>
            )}
          </div>
        )}
      </div>

      <div className="scrollbar-thin flex-1 overflow-y-auto px-2 pb-2">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-5 w-5 animate-spin" style={{ color: 'var(--text-muted)' }} />
          </div>
        ) : conversations.length === 0 ? (
          <p className="px-4 py-8 text-center text-xs" style={{ color: 'var(--text-muted)' }}>
            还没有对话，点击上方按钮开始。
          </p>
        ) : (
          <ul className="space-y-0.5">
            {conversations.map(conv => {
              const isActive = activeId === conv.id
              const isEditing = editingId === conv.id
              const isRenaming = renamingId === conv.id
              const branches = branchesByConversation[conv.id] || []
              const visibleBranchRows = buildVisibleBranchTree(branches)
              const isLoadingBranches = !!loadingBranches[conv.id]
              const showBranches = visibleBranchRows.length > 0 || isLoadingBranches

              return (
                <li key={conv.id}>
                  <div className="group relative">
                    {isEditing ? (
                      <form
                        onSubmit={e => { void submitRename(e, conv.id) }}
                        className="rounded-xl px-3 py-2.5 pr-20 text-sm"
                        style={{ background: 'var(--bg-elevated)', color: 'var(--text-primary)' }}
                      >
                        <div className="flex items-start gap-2">
                          <MessageSquare className="mt-0.5 h-3.5 w-3.5 shrink-0 opacity-50" />
                          <div className="min-w-0 flex-1">
                            <input
                              autoFocus
                              type="text"
                              value={editingTitle}
                              maxLength={255}
                              disabled={isRenaming}
                              placeholder="输入对话标题"
                              onClick={e => e.stopPropagation()}
                              onChange={e => {
                                setEditingTitle(e.target.value)
                                if (renameError) setRenameError('')
                              }}
                              onKeyDown={e => {
                                if (e.key === 'Escape') cancelRename(e)
                              }}
                              className="w-full rounded-lg px-2 py-1 text-sm outline-none"
                              style={{
                                background: 'var(--bg-surface)',
                                color: 'var(--text-primary)',
                                border: `1px solid ${renameError ? 'var(--error-border)' : 'var(--border)'}`,
                              }}
                            />
                            {renameError ? (
                              <p className="mt-1 text-xs" style={{ color: 'var(--error-text)' }}>
                                {renameError}
                              </p>
                            ) : (
                              <p className="mt-1 text-xs" style={{ color: 'var(--text-muted)' }}>
                                {formatDate(conv.updated_at)}
                              </p>
                            )}
                          </div>
                        </div>
                      </form>
                    ) : (
                      <button
                        type="button"
                        onClick={() => onSelect(conv.id)}
                        className="w-full rounded-xl px-3 py-2.5 pr-16 text-left text-sm transition"
                        style={{
                          background: isActive ? 'var(--bg-elevated)' : 'transparent',
                          color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                        }}
                        onMouseEnter={e => {
                          if (!isActive) e.currentTarget.style.background = 'var(--bg-elevated)'
                        }}
                        onMouseLeave={e => {
                          if (!isActive) e.currentTarget.style.background = 'transparent'
                        }}
                      >
                        <div className="flex items-start gap-2">
                          <MessageSquare className="mt-0.5 h-3.5 w-3.5 shrink-0 opacity-50" />
                          <div className="min-w-0 flex-1">
                            <p className="truncate font-medium leading-snug">
                              {conv.title || '新对话'}
                            </p>
                            <p className="mt-0.5 text-xs" style={{ color: 'var(--text-muted)' }}>
                              {formatDate(conv.updated_at)}
                            </p>
                          </div>
                        </div>
                      </button>
                    )}

                    {isEditing ? (
                      <div className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center gap-1">
                        <button
                          type="button"
                          onClick={e => { void submitRename(e, conv.id) }}
                          disabled={isRenaming}
                          className="rounded-lg p-1 transition"
                          style={{ color: 'var(--text-muted)' }}
                          title="保存重命名"
                        >
                          {isRenaming
                            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            : <Check className="h-3.5 w-3.5" />}
                        </button>
                        <button
                          type="button"
                          onClick={cancelRename}
                          disabled={isRenaming}
                          className="rounded-lg p-1 transition"
                          style={{ color: 'var(--text-muted)' }}
                          title="取消重命名"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ) : (
                      <div className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center gap-1 opacity-0 transition group-hover:opacity-100">
                        <button
                          type="button"
                          onClick={e => startRename(e, conv)}
                          className="rounded-lg p-1 transition"
                          style={{ color: 'var(--text-muted)' }}
                          title="重命名对话"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={e => handleDelete(e, conv.id)}
                          disabled={deletingId === conv.id}
                          className="rounded-lg p-1 transition"
                          style={{ color: 'var(--text-muted)' }}
                          title="删除对话"
                        >
                          {deletingId === conv.id
                            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            : <Trash2 className="h-3.5 w-3.5" />}
                        </button>
                      </div>
                    )}
                  </div>
                  {showBranches && (
                    <div className="ml-5 mt-0.5 space-y-0.5">
                      {isLoadingBranches && visibleBranchRows.length === 0 ? (
                        <div className="flex items-center gap-2 px-3 py-1.5 text-xs" style={{ color: 'var(--text-muted)' }}>
                          <Loader2 className="h-3 w-3 animate-spin" />
                          <span>加载分支</span>
                        </div>
                      ) : visibleBranchRows.map(({ branch, depth }) => {
                        const isBranchActive = isActive && activeBranchId === branch.id
                        const isBranchEditing = editingBranchId === branch.id
                        const isBranchRenaming = renamingBranchId === branch.id
                        const isBranchMenuOpen = branchMenu?.conversationId === conv.id && branchMenu?.branchId === branch.id
                        const indent = depth * 14

                        return (
                          <div
                            key={branch.id}
                            ref={isBranchMenuOpen ? branchMenuRef : null}
                            className="group/branch relative pl-4"
                            style={{ marginLeft: `${indent}px` }}
                          >
                            <span
                              className="absolute left-0 top-0 h-5 w-3 rounded-bl-md"
                              style={{ borderLeft: '1px solid var(--border)', borderBottom: '1px solid var(--border)' }}
                            />
                            {isBranchEditing ? (
                              <form
                                onSubmit={e => { void submitBranchRename(e, conv.id, branch.id) }}
                                className="rounded-lg py-1.5 pl-2 pr-16 text-xs"
                                style={{ background: 'var(--bg-elevated)' }}
                              >
                                <input
                                  autoFocus
                                  type="text"
                                  value={editingBranchTitle}
                                  maxLength={255}
                                  disabled={isBranchRenaming}
                                  placeholder="输入分支名"
                                  onClick={e => e.stopPropagation()}
                                  onChange={e => {
                                    setEditingBranchTitle(e.target.value)
                                    if (branchRenameError) setBranchRenameError('')
                                  }}
                                  onKeyDown={e => {
                                    if (e.key === 'Escape') cancelBranchRename(e)
                                  }}
                                  className="w-full rounded-md px-2 py-1 text-xs outline-none"
                                  style={{
                                    background: 'var(--bg-surface)',
                                    color: 'var(--text-primary)',
                                    border: `1px solid ${branchRenameError ? 'var(--error-border)' : 'var(--border)'}`,
                                  }}
                                />
                                {branchRenameError && (
                                  <p className="mt-1" style={{ color: 'var(--error-text)' }}>
                                    {branchRenameError}
                                  </p>
                                )}
                              </form>
                            ) : (
                              <button
                                type="button"
                                onClick={() => onBranchSelect?.(conv.id, branch.id)}
                                className="flex w-full items-center gap-2 rounded-lg py-1.5 pl-2 pr-12 text-left text-xs transition"
                                style={{
                                  background: isBranchActive ? 'var(--bg-elevated)' : 'transparent',
                                  color: isBranchActive ? 'var(--text-primary)' : 'var(--text-muted)',
                                }}
                                onMouseEnter={e => {
                                  if (!isBranchActive) e.currentTarget.style.background = 'var(--bg-elevated)'
                                }}
                                onMouseLeave={e => {
                                  if (!isBranchActive) e.currentTarget.style.background = 'transparent'
                                }}
                              >
                                <GitBranch className="h-3.5 w-3.5 shrink-0 opacity-60" />
                                <span className="min-w-0 flex-1 truncate">{branchTitle(branch)}</span>
                              </button>
                            )}
                            {isBranchEditing ? (
                              <div className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center gap-1">
                                <button
                                  type="button"
                                  onClick={e => { void submitBranchRename(e, conv.id, branch.id) }}
                                  disabled={isBranchRenaming}
                                  className="rounded-md p-1 transition"
                                  style={{ color: 'var(--text-muted)' }}
                                  title="保存分支名"
                                >
                                  {isBranchRenaming
                                    ? <Loader2 className="h-3 w-3 animate-spin" />
                                    : <Check className="h-3 w-3" />}
                                </button>
                                <button
                                  type="button"
                                  onClick={cancelBranchRename}
                                  disabled={isBranchRenaming}
                                  className="rounded-md p-1 transition"
                                  style={{ color: 'var(--text-muted)' }}
                                  title="取消"
                                >
                                  <X className="h-3 w-3" />
                                </button>
                              </div>
                            ) : (
                              <>
                                <button
                                  type="button"
                                  onClick={e => toggleBranchMenu(e, conv.id, branch.id)}
                                  className={`absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 transition ${
                                    isBranchMenuOpen ? 'opacity-100' : 'opacity-0 group-hover/branch:opacity-100'
                                  }`}
                                  style={{
                                    color: 'var(--text-muted)',
                                    background: isBranchMenuOpen ? 'var(--bg-elevated)' : 'transparent',
                                  }}
                                  title="分支菜单"
                                  aria-label="分支菜单"
                                >
                                  <MoreHorizontal className="h-3.5 w-3.5" />
                                </button>
                                {isBranchMenuOpen && (
                                  <div
                                    className="absolute right-2 top-full z-20 mt-1 w-28 overflow-hidden rounded-xl border shadow-lg"
                                    style={{ background: 'var(--bg-surface)', borderColor: 'var(--border)' }}
                                    onClick={e => e.stopPropagation()}
                                  >
                                    <button
                                      type="button"
                                      onClick={e => startBranchRename(e, conv, branch)}
                                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition"
                                      style={{ color: 'var(--text-secondary)' }}
                                    >
                                      <Pencil className="h-3.5 w-3.5" />
                                      <span>重命名</span>
                                    </button>
                                    <button
                                      type="button"
                                      onClick={e => promptBranchDelete(e, conv, branch)}
                                      className="flex w-full items-center gap-2 border-t px-3 py-2 text-left text-xs transition"
                                      style={{
                                        color: 'var(--error-text)',
                                        borderTopColor: 'var(--border)',
                                        background: 'transparent',
                                      }}
                                    >
                                      <Trash2 className="h-3.5 w-3.5" />
                                      <span>删除</span>
                                    </button>
                                  </div>
                                )}
                              </>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>

      <div className="shrink-0 space-y-1 px-3 py-3" style={{ borderTop: '1px solid var(--border)' }}>
        <button
          type="button"
          onClick={onOpenKeys}
          className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-sm transition"
          style={{ color: 'var(--text-secondary)' }}
          {...btnHover}
        >
          <Key className="h-4 w-4" />
          管理 API Keys
        </button>

        <button
          type="button"
          onClick={onToggleTheme}
          className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-sm transition"
          style={{ color: 'var(--text-secondary)' }}
          {...btnHover}
        >
          {mode === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          {mode === 'dark' ? '切换日间模式' : '切换夜间模式'}
        </button>

        <div className="flex items-center gap-1.5 px-3 py-2">
          <Palette className="h-4 w-4 shrink-0" style={{ color: 'var(--text-secondary)' }} />
          <div className="flex flex-1 gap-1.5">
            {PALETTES.map(p => (
              <button
                key={p.id}
                type="button"
                onClick={() => onSetPalette(p.id)}
                title={p.label}
                className="h-5 w-5 rounded-full transition-transform"
                style={{
                  background: PALETTE_COLORS[p.id],
                  outline: palette === p.id ? `2px solid ${PALETTE_COLORS[p.id]}` : 'none',
                  outlineOffset: '2px',
                  transform: palette === p.id ? 'scale(1.2)' : 'scale(1)',
                }}
              />
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2.5 px-3 py-2">
          <div
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full"
            style={{ background: 'var(--bg-elevated)' }}
          >
            <User className="h-3.5 w-3.5" style={{ color: 'var(--text-secondary)' }} />
          </div>
          <span className="flex-1 truncate text-sm" style={{ color: 'var(--text-secondary)' }}>
            {user?.username}
          </span>
          <button
            type="button"
            onClick={onLogout}
            className="rounded-lg p-1 transition"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => {
              e.currentTarget.style.color = 'var(--error-text)'
              e.currentTarget.style.background = 'var(--error-bg)'
            }}
            onMouseLeave={e => {
              e.currentTarget.style.color = 'var(--text-muted)'
              e.currentTarget.style.background = 'transparent'
            }}
            title="退出登录"
          >
            <LogOut className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {pendingDelete && (
        <DeleteConfirmModal
          title="删除对话"
          description={`确定要删除「${conversations.find(c => c.id === pendingDelete)?.title || '新对话'}」吗？此操作无法撤销。`}
          onConfirm={confirmDelete}
          onCancel={() => setPendingDelete(null)}
          deleting={deletingId === pendingDelete}
          confirmLabel="删除对话"
        />
      )}

      {pendingBranchDelete && (
        <DeleteConfirmModal
          title="删除分支"
          description={`确定要删除「${pendingBranchDelete.title || '未命名分支'}」吗？该分支及其子分支和消息子树都会被删除，此操作无法撤销。`}
          onConfirm={confirmBranchDelete}
          onCancel={() => setPendingBranchDelete(null)}
          deleting={deletingBranchId === pendingBranchDelete.branchId}
          confirmLabel="删除分支"
        />
      )}
    </aside>
  )
}
