import { useState } from 'react'
import {
  Plus, Trash2, MessageSquare, Loader2,
  LogOut, ChevronLeft, User, Sun, Moon
} from 'lucide-react'

export default function Sidebar({
  open, conversations, activeId, loading,
  onSelect, onNew, onDelete, onClose,
  onToggleTheme, theme, user, onLogout
}) {
  const [deletingId, setDeletingId] = useState(null)
  const [confirmId, setConfirmId] = useState(null)

  async function handleDelete(e, id) {
    e.stopPropagation()
    if (confirmId !== id) { setConfirmId(id); return }
    setDeletingId(id)
    setConfirmId(null)
    try { await onDelete(id) } finally { setDeletingId(null) }
  }

  function formatDate(dateStr) {
    if (!dateStr) return ''
    const d = new Date(dateStr)
    const diff = Date.now() - d
    if (diff < 60000) return '刚刚'
    if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
    if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
    if (diff < 604800000) return `${Math.floor(diff / 86400000)} 天前`
    return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
  }

  const btnHover = {
    onMouseEnter: e => e.currentTarget.style.background = 'var(--bg-elevated)',
    onMouseLeave: e => e.currentTarget.style.background = 'transparent',
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
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-4 shrink-0"
        style={{ borderBottom: '1px solid var(--border)' }}>
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center"
            style={{ background: 'var(--accent)' }}>
            <MessageSquare className="w-4 h-4" style={{ color: 'var(--text-primary)' }} />
          </div>
          <span className="font-semibold text-sm" style={{ color: 'var(--text-primary)' }}>AI Chat</span>
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded-lg transition md:hidden"
          style={{ color: 'var(--text-muted)' }}
          {...btnHover}
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
      </div>

      {/* New chat */}
      <div className="px-3 py-3 shrink-0">
        <button
          onClick={() => onNew()}
          className="w-full flex items-center gap-2 px-3 py-2.5 rounded-xl text-sm font-medium transition"
          style={{ background: 'var(--accent)', color: 'var(--text-primary)' }}
          onMouseEnter={e => e.currentTarget.style.background = 'var(--accent-hover)'}
          onMouseLeave={e => e.currentTarget.style.background = 'var(--accent)'}
        >
          <Plus className="w-4 h-4" />
          新对话
        </button>
      </div>

      {/* Conversation list */}
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
                  <button
                    onClick={() => onSelect(conv.id)}
                    className="w-full text-left px-3 py-2.5 rounded-xl text-sm transition group relative"
                    style={{
                      background: isActive ? 'var(--bg-elevated)' : 'transparent',
                      color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                    }}
                    onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'var(--bg-elevated)' }}
                    onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'transparent' }}
                  >
                    <div className="flex items-start gap-2 pr-6">
                      <MessageSquare className="w-3.5 h-3.5 mt-0.5 shrink-0 opacity-50" />
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-medium leading-snug">{conv.title || '新对话'}</p>
                        <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                          {formatDate(conv.updated_at)}
                        </p>
                      </div>
                    </div>
                    <button
                      onClick={e => handleDelete(e, conv.id)}
                      disabled={deletingId === conv.id}
                      className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded-lg transition opacity-0 group-hover:opacity-100"
                      style={confirmId === conv.id
                        ? { color: 'var(--error-text)', background: 'var(--error-bg)', opacity: 1 }
                        : { color: 'var(--text-muted)' }
                      }
                      title={confirmId === conv.id ? '再次点击确认删除' : '删除对话'}
                    >
                      {deletingId === conv.id
                        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        : <Trash2 className="w-3.5 h-3.5" />
                      }
                    </button>
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>

      {/* Footer */}
      <div className="px-3 py-3 shrink-0 space-y-1" style={{ borderTop: '1px solid var(--border)' }}>
        {/* Theme toggle */}
        <button
          onClick={onToggleTheme}
          className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-sm transition"
          style={{ color: 'var(--text-secondary)' }}
          {...btnHover}
        >
          {theme === 'dark'
            ? <Sun className="w-4 h-4" />
            : <Moon className="w-4 h-4" />
          }
          {theme === 'dark' ? '切换日间模式' : '切换夜间模式'}
        </button>

        {/* User row */}
        <div className="flex items-center gap-2.5 px-3 py-2">
          <div className="w-6 h-6 rounded-full flex items-center justify-center shrink-0"
            style={{ background: 'var(--bg-elevated)' }}>
            <User className="w-3.5 h-3.5" style={{ color: 'var(--text-secondary)' }} />
          </div>
          <span className="text-sm flex-1 truncate" style={{ color: 'var(--text-secondary)' }}>
            {user?.username}
          </span>
          <button
            onClick={onLogout}
            className="p-1 rounded-lg transition"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => { e.currentTarget.style.color = 'var(--error-text)'; e.currentTarget.style.background = 'var(--error-bg)' }}
            onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.background = 'transparent' }}
            title="退出登录"
          >
            <LogOut className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </aside>
  )
}
