import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { ChevronLeft, ChevronRight, GitBranch, RotateCw, Trash2 } from 'lucide-react'

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

  if (isUser) {
    return (
      <div className="flex justify-end py-2">
        <div className="max-w-[80%]">
          <div
            className="rounded-2xl rounded-tr-sm px-4 py-3 text-sm"
            style={{ background: 'var(--bubble-bg)', color: 'var(--text-primary)' }}
          >
            <div className="prose-chat whitespace-pre-wrap break-words">
              {message.content}
            </div>
          </div>
          {!hideActions && (
            <div className="flex justify-end items-center gap-1 mt-2">
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
                <IconButton label={isRegenerating ? '重新回答中' : '重新回答'} onClick={onRegenerate} disabled={actionDisabled}>
                  <RotateCw className={`w-3.5 h-3.5 ${isRegenerating ? 'animate-spin' : ''}`} />
                </IconButton>
              )}
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="flex gap-3 py-2">
      <div
        className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 mt-0.5 text-xs font-bold"
        style={{ background: 'var(--bubble-bg)', color: 'var(--text-secondary)' }}
      >
        AI
      </div>
      <div
        className={`flex-1 min-w-0 text-sm pt-1 ${isStreaming ? 'typing-cursor' : ''}`}
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
        {!hideActions && (
          <div className="flex items-center justify-between gap-3 mt-3">
            <SiblingNavigator
              message={message}
              onPrevSibling={onPrevSibling}
              onNextSibling={onNextSibling}
              disabled={actionDisabled}
            />
            <div className="flex items-center gap-1 ml-auto">
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
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
