import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { ChevronLeft, ChevronRight, GitBranch, RotateCw, Trash2 } from 'lucide-react'

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

export default function MessageBubble({
  message,
  onRegenerate,
  onCreateBranch,
  onDelete,
  onPrevSibling,
  onNextSibling,
  disableActions = false,
  isRegenerating = false,
  isCreatingBranch = false,
  isDeleting = false,
  hideActions = false,
}) {
  const isUser = message.role === 'user'
  const isStreaming = message.status === 'streaming'
  const actionDisabled = disableActions || isStreaming
  const timeLabel = formatMessageTime(message.created_at)

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
          {(timeLabel || !hideActions) && (
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
