import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Copy,
  GitBranch,
  Pencil,
  RotateCw,
  Send,
  Trash2,
} from 'lucide-react'

const EDIT_MODE_OPTIONS = [
  { value: 'update', label: '仅编辑消息' },
  { value: 'branch', label: '从这里创建分支' },
]

function padNumber(value) {
  return String(value).padStart(2, '0')
}

function formatClock(date) {
  return `${padNumber(date.getHours())}:${padNumber(date.getMinutes())}`
}

function formatMessageTime(value) {
  if (!value) return ''

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''

  const now = new Date()
  if (date.getTime() > now.getTime()) {
    return '时间有误，请检查系统时间'
  }

  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const dateStart = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const dayDiff = Math.round((todayStart.getTime() - dateStart.getTime()) / 86400000)
  const clock = formatClock(date)

  if (dayDiff === 0) return `今天 ${clock}`
  if (dayDiff === 1) return `昨天 ${clock}`

  if (date.getFullYear() === now.getFullYear()) {
    return `${padNumber(date.getMonth() + 1)}-${padNumber(date.getDate())} ${clock}`
  }

  return `${date.getFullYear()}-${padNumber(date.getMonth() + 1)}-${padNumber(date.getDate())} ${clock}`
}

function IconButton({ label, onClick, disabled, children, pulse = false }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="p-1.5 rounded-full transition"
      aria-label={label}
      title={label}
      style={{
        background: 'transparent',
        color: disabled ? 'var(--text-muted)' : 'var(--text-secondary)',
        opacity: disabled ? 0.7 : 1,
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}
      onMouseEnter={e => {
        if (!disabled) e.currentTarget.style.background = 'var(--bg-elevated)'
      }}
      onMouseLeave={e => {
        e.currentTarget.style.background = 'transparent'
      }}
    >
      <span className={pulse ? 'animate-spin' : ''}>
        {children}
      </span>
    </button>
  )
}

function SiblingNavigator({ message, onPrevSibling, onNextSibling, disabled }) {
  if (message.role !== 'assistant' || Number(message.sibling_count) <= 1) return null

  return (
    <div className="flex items-center gap-1 text-xs" style={{ color: 'var(--text-muted)' }}>
      <IconButton label="上一条分支" onClick={onPrevSibling} disabled={disabled}>
        <ChevronLeft className="w-3.5 h-3.5" />
      </IconButton>
      <span className="min-w-[44px] text-center">
        {message.sibling_index}/{message.sibling_count}
      </span>
      <IconButton label="下一条分支" onClick={onNextSibling} disabled={disabled}>
        <ChevronRight className="w-3.5 h-3.5" />
      </IconButton>
    </div>
  )
}

function InlineEditComposer({
  value,
  mode,
  disabled,
  onChange,
  onCancel,
  onSubmit,
  onModeChange,
}) {
  const [menuOpen, setMenuOpen] = useState(false)
  const textareaRef = useRef(null)

  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 220)}px`
  }, [value])

  function submit() {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    onSubmit()
    setMenuOpen(false)
  }

  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <div
      className="mt-3 rounded-2xl px-4 py-3"
      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}
    >
      <textarea
        ref={textareaRef}
        value={value}
        onChange={event => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        rows={1}
        className="w-full bg-transparent text-sm resize-none focus:outline-none leading-relaxed min-h-[24px] max-h-[220px] scrollbar-thin"
        style={{ color: 'var(--text-primary)', caretColor: 'var(--accent)' }}
      />
      <div className="mt-3 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={disabled}
          className="px-3 py-1.5 rounded-xl text-sm transition disabled:opacity-50"
          style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
        >
          取消
        </button>
        <div className="relative">
          <div
            className="flex items-center overflow-hidden rounded-2xl transition"
            style={{
              background: 'var(--bg-elevated)',
              border: '1px solid var(--border)',
              opacity: disabled ? 0.5 : 1,
            }}
          >
            <button
              type="button"
              onClick={submit}
              disabled={!value.trim() || disabled}
              className="w-11 h-8 flex items-center justify-center transition disabled:cursor-not-allowed"
              style={{ color: 'var(--text-primary)' }}
              aria-label="发送编辑"
            >
              <Send className="w-3.5 h-3.5" />
            </button>
            <div className="w-px h-4" style={{ background: 'var(--border)' }} />
            <button
              type="button"
              onClick={() => setMenuOpen(open => !open)}
              disabled={disabled}
              className="w-10 h-8 flex items-center justify-center transition disabled:cursor-not-allowed"
              style={{ color: 'var(--text-primary)' }}
              aria-label="选择编辑模式"
              title={EDIT_MODE_OPTIONS.find(option => option.value === mode)?.label || EDIT_MODE_OPTIONS[0].label}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
            >
              <ChevronDown className="w-3.5 h-3.5" />
            </button>
          </div>
          {menuOpen && (
            <div
              className="absolute right-0 top-full mt-2 min-w-[156px] rounded-xl p-1 z-10"
              style={{
                background: 'color-mix(in srgb, var(--bg-surface) 94%, transparent)',
                border: '1px solid var(--border)',
                boxShadow: '0 12px 32px rgba(0, 0, 0, 0.16)',
              }}
              role="menu"
            >
              {EDIT_MODE_OPTIONS.map(option => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => {
                    onModeChange(option.value)
                    setMenuOpen(false)
                  }}
                  className="w-full rounded-lg px-3 py-2 text-left text-sm transition"
                  style={{
                    color: option.value === mode ? 'var(--text-primary)' : 'var(--text-secondary)',
                    background: option.value === mode ? 'var(--bg-elevated)' : 'transparent',
                  }}
                  role="menuitem"
                >
                  {option.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default function MessageBubble({
  message,
  onRegenerate,
  onCreateBranch,
  onDelete,
  onPrevSibling,
  onNextSibling,
  onCopy,
  onEdit,
  isEditing = false,
  editDraft = '',
  editMode = 'update',
  onEditDraftChange,
  onEditModeChange,
  onEditCancel,
  onEditSubmit,
  isEditSubmitting = false,
  disableActions = false,
  isRegenerating = false,
  isCreatingBranch = false,
  isDeleting = false,
  hideActions = false,
}) {
  const isUser = message.role === 'user'
  const isStreaming = message.status === 'streaming'
  const actionDisabled = disableActions || isStreaming || isEditSubmitting
  const timeLabel = formatMessageTime(message.updated_at || message.created_at)

  if (isUser) {
    return (
      <div className="group flex justify-end py-2">
        <div className="max-w-[80%]">
          <div
            className="rounded-2xl rounded-tr-sm px-4 py-3 text-sm"
            style={{ background: 'var(--bubble-bg)', color: 'var(--text-primary)' }}
          >
            <div className="prose-chat whitespace-pre-wrap break-words">
              {message.content}
            </div>
          </div>
          {isEditing && (
            <InlineEditComposer
              value={editDraft}
              mode={editMode}
              disabled={isEditSubmitting}
              onChange={onEditDraftChange}
              onCancel={onEditCancel}
              onSubmit={onEditSubmit}
              onModeChange={onEditModeChange}
            />
          )}
          {!isEditing && (timeLabel || !hideActions) && (
            <div className="flex flex-wrap items-center gap-1 mt-2">
              {timeLabel && (
                <span
                  className="pointer-events-none whitespace-nowrap text-xs opacity-0 transition-opacity duration-150 group-hover:opacity-100"
                  style={{ color: 'var(--text-muted)' }}
                >
                  {timeLabel}
                </span>
              )}
              <div className="flex items-center gap-1 ml-auto">
                {!hideActions && onCopy && (
                  <IconButton label="复制" onClick={onCopy} disabled={actionDisabled}>
                    <Copy className="w-3.5 h-3.5" />
                  </IconButton>
                )}
                {!hideActions && onEdit && (
                  <IconButton label="编辑" onClick={onEdit} disabled={actionDisabled}>
                    <Pencil className="w-3.5 h-3.5" />
                  </IconButton>
                )}
                {!hideActions && onDelete && (
                  <IconButton label={isDeleting ? '删除中' : '删除消息'} onClick={onDelete} disabled={actionDisabled}>
                    <Trash2 className={`w-3.5 h-3.5 ${isDeleting ? 'animate-pulse' : ''}`} />
                  </IconButton>
                )}
                {!hideActions && onCreateBranch && (
                  <IconButton label={isCreatingBranch ? '创建分支中' : '创建分支'} onClick={onCreateBranch} disabled={actionDisabled}>
                    <GitBranch className={`w-3.5 h-3.5 ${isCreatingBranch ? 'animate-pulse' : ''}`} />
                  </IconButton>
                )}
                {!hideActions && onRegenerate && (
                  <IconButton label={isRegenerating ? '重新回答中' : '重新回答'} onClick={onRegenerate} disabled={actionDisabled}>
                    <RotateCw className={`w-3.5 h-3.5 ${isRegenerating ? 'animate-spin' : ''}`} />
                  </IconButton>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="group flex gap-3 py-2">
      <div
        className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5 text-xs font-bold"
        style={{ background: 'var(--bubble-bg)', color: 'var(--text-secondary)' }}
      >
        AI
      </div>
      <div className="flex-1 min-w-0 pt-1">
        <div
          className={`min-w-0 text-sm ${isStreaming ? 'typing-cursor' : ''}`}
          style={{ color: 'var(--text-primary)' }}
        >
          <div className="prose-chat">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
              {message.content || ''}
            </ReactMarkdown>
          </div>
          {message.status === 'failed' && (
            <p className="text-xs mt-1" style={{ color: 'var(--error-text)' }}>
              {message.error_message || '生成失败'}
            </p>
          )}
          {(timeLabel || !hideActions) && (
            <div className="flex flex-wrap items-center gap-2 mt-3">
              {!hideActions && (
                <>
                  <SiblingNavigator
                    message={message}
                    onPrevSibling={onPrevSibling}
                    onNextSibling={onNextSibling}
                    disabled={actionDisabled}
                  />
                  {onCopy && (
                    <IconButton label="复制" onClick={onCopy} disabled={actionDisabled}>
                      <Copy className="w-3.5 h-3.5" />
                    </IconButton>
                  )}
                  {onDelete && (
                    <IconButton label={isDeleting ? '删除中' : '删除消息'} onClick={onDelete} disabled={actionDisabled}>
                      <Trash2 className={`w-3.5 h-3.5 ${isDeleting ? 'animate-pulse' : ''}`} />
                    </IconButton>
                  )}
                  {onCreateBranch && (
                    <IconButton label={isCreatingBranch ? '创建分支中' : '创建分支'} onClick={onCreateBranch} disabled={actionDisabled}>
                      <GitBranch className={`w-3.5 h-3.5 ${isCreatingBranch ? 'animate-pulse' : ''}`} />
                    </IconButton>
                  )}
                  {onRegenerate && (
                    <IconButton label={isRegenerating ? '重新生成中' : '重新生成'} onClick={onRegenerate} disabled={actionDisabled}>
                      <RotateCw className={`w-3.5 h-3.5 ${isRegenerating ? 'animate-spin' : ''}`} />
                    </IconButton>
                  )}
                </>
              )}
              {timeLabel && (
                <span
                  className="pointer-events-none ml-auto whitespace-nowrap text-xs opacity-0 transition-opacity duration-150 group-hover:opacity-100"
                  style={{ color: 'var(--text-muted)' }}
                >
                  {timeLabel}
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
