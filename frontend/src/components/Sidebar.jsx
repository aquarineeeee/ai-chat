import { useRef, useState } from 'react'
import {
  Plus, Trash2, MessageSquare, Loader2,
  LogOut, ChevronLeft, User, Sun, Moon, Key, AlertTriangle, Palette,
  FileInput, CheckCircle2,
} from 'lucide-react'
import { PALETTES } from '../ThemeContext'

const PALETTE_COLORS = {
  stone:    '#6e5c52',
  lavender: '#7c6fa0',
  sage:     '#5e8a6e',
  blue:     '#4a72a8',
}

function DeleteConfirmModal({ conv, onConfirm, onCancel, deleting }) {
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
        <div className="flex items-start gap-3 mb-4">
          <div
            className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: 'var(--error-bg)' }}
          >
            <AlertTriangle className="w-4 h-4" style={{ color: 'var(--error-text)' }} />
          </div>
          <div>
            <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
              删除对话
            </p>
            <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
              确定要删除「{conv?.title || '新对话'}」吗？此操作无法撤销。
            </p>
          </div>
        </div>
        <div className="flex gap-2 justify-end">
          <button
            type="button"
            onClick={onCancel}
            disabled={deleting}
            className="px-3 py-1.5 rounded-lg text-sm transition"
            style={{ color: 'var(--text-secondary)', background: 'var(--bg-elevated)' }}
            onMouseEnter={e => { e.currentTarget.style.background = 'var(--border)' }}
            onMouseLeave={e => { e.currentTarget.style.background = 'var(--bg-elevated)' }}
          >
            取消
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={deleting}
            className="px-3 py-1.5 rounded-lg text-sm font-medium transition flex items-center gap-1.5"
            style={{ background: 'var(--error-bg)', color: 'var(--error-text)', border: '1px solid var(--error-border)' }}
            onMouseEnter={e => { e.currentTarget.style.opacity = '0.85' }}
            onMouseLeave={e => { e.currentTarget.style.opacity = '1' }}
          >
            {deleting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            删除
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Sidebar({
  open, conversations, activeId, loading,
  importLoading, importStatus,
  onSelect, onNew, onImport, onDelete, onClose,
  onToggleTheme, palette, mode, onSetPalette, user, onLogout, onOpenKeys,
}) {
  const [deletingId, setDeletingId] = useState(null)
  const [pendingDelete, setPendingDelete] = useState(null)
  const fileInputRef = useRef(null)

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
        className="flex items-center justify-between px-4 py-4 shrink-0"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        <div className="flex items-center gap-2">
          <div
            className="w-7 h-7 rounded-lg flex items-center justify-center"
            style={{ background: 'var(--accent)' }}
          >
            <MessageSquare className="w-4 h-4" style={{ color: 'var(--text-primary)' }} />
          </div>
          <span className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>
            AI Chat
          </span>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="p-1 rounded-lg transition md:hidden"
          style={{ color: 'var(--text-muted)' }}
          {...btnHover}
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
      </div>

      <div className="px-3 py-3 shrink-0">
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => onNew()}
            className="flex-1 flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl text-sm font-medium transition"
            style={{ background: 'var(--accent)', color: 'var(--text-primary)' }}
            onMouseEnter={e => {
              e.currentTarget.style.background = 'var(--accent-hover)'
            }}
            onMouseLeave={e => {
              e.currentTarget.style.background = 'var(--accent)'
            }}
          >
            <Plus className="w-4 h-4" />
            新对话
          </button>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={importLoading || typeof onImport !== 'function'}
            className="shrink-0 w-11 h-11 rounded-xl flex items-center justify-center transition"
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
            {importLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileInput className="w-4 h-4" />}
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
              {importStatus.type === 'success' ? <CheckCircle2 className="w-3.5 h-3.5" /> : <AlertTriangle className="w-3.5 h-3.5" />}
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

      <div className="flex-1 overflow-y-auto scrollbar-thin px-2 pb-2">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-5 h-5 animate-spin" style={{ color: 'var(--text-muted)' }} />
          </div>
        ) : conversations.length === 0 ? (
          <p className="text-center text-xs py-8 px-4" style={{ color: 'var(--text-muted)' }}>
            还没有对话，点击上方按钮开始
          </p>
        ) : (
          <ul className="space-y-0.5">
            {conversations.map(conv => {
              const isActive = activeId === conv.id

              return (
                <li key={conv.id}>
                  <div className="group relative">
                    <button
                      type="button"
                      onClick={() => onSelect(conv.id)}
                      className="w-full text-left px-3 py-2.5 pr-10 rounded-xl text-sm transition"
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
                        <MessageSquare className="w-3.5 h-3.5 mt-0.5 shrink-0 opacity-50" />
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-medium leading-snug">
                            {conv.title || '新对话'}
                          </p>
                          <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                            {formatDate(conv.updated_at)}
                          </p>
                        </div>
                      </div>
                    </button>

                    <button
                      type="button"
                      onClick={e => handleDelete(e, conv.id)}
                      disabled={deletingId === conv.id}
                      className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded-lg transition opacity-0 group-hover:opacity-100"
                      style={{ color: 'var(--text-muted)' }}
                      title="删除对话"
                    >
                      {deletingId === conv.id
                        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        : <Trash2 className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </div>

      <div className="px-3 py-3 shrink-0 space-y-1" style={{ borderTop: '1px solid var(--border)' }}>
        <button
          type="button"
          onClick={onOpenKeys}
          className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-sm transition"
          style={{ color: 'var(--text-secondary)' }}
          {...btnHover}
        >
          <Key className="w-4 h-4" />
          管理 API Keys
        </button>

        <button
          type="button"
          onClick={onToggleTheme}
          className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-sm transition"
          style={{ color: 'var(--text-secondary)' }}
          {...btnHover}
        >
          {mode === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
          {mode === 'dark' ? '切换日间模式' : '切换夜间模式'}
        </button>

        <div className="flex items-center gap-1.5 px-3 py-2">
          <Palette className="w-4 h-4 shrink-0" style={{ color: 'var(--text-secondary)' }} />
          <div className="flex gap-1.5 flex-1">
            {PALETTES.map(p => (
              <button
                key={p.id}
                type="button"
                onClick={() => onSetPalette(p.id)}
                title={p.label}
                className="w-5 h-5 rounded-full transition-transform"
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
            className="w-6 h-6 rounded-full flex items-center justify-center shrink-0"
            style={{ background: 'var(--bg-elevated)' }}
          >
            <User className="w-3.5 h-3.5" style={{ color: 'var(--text-secondary)' }} />
          </div>
          <span className="text-sm flex-1 truncate" style={{ color: 'var(--text-secondary)' }}>
            {user?.username}
          </span>
          <button
            type="button"
            onClick={onLogout}
            className="p-1 rounded-lg transition"
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
            <LogOut className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {pendingDelete && (
        <DeleteConfirmModal
          conv={conversations.find(c => c.id === pendingDelete)}
          onConfirm={confirmDelete}
          onCancel={() => setPendingDelete(null)}
          deleting={deletingId === pendingDelete}
        />
      )}
    </aside>
  )
}
