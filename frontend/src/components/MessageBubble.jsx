import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { GitBranch, RotateCw } from 'lucide-react'

export default function MessageBubble({
  message,
  onRegenerate,
  onSwitchBranch,
  disableActions = false,
  isRegenerating = false,
  isSwitchingBranch = false,
}) {
  const isUser = message.role === 'user'
  const isStreaming = message.status === 'streaming'
  const showBranchInfo = Number(message.sibling_count) > 1
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
          {onRegenerate && (
            <div className="flex justify-end mt-2">
              <button
                type="button"
                onClick={onRegenerate}
                disabled={actionDisabled}
                className="p-1.5 rounded-full transition"
                aria-label={isRegenerating ? '重新回答中' : '重新回答'}
                title={isRegenerating ? '重新回答中' : '重新回答'}
                style={{
                  background: 'transparent',
                  color: actionDisabled ? 'var(--text-muted)' : 'var(--text-secondary)',
                  opacity: actionDisabled ? 0.7 : 1,
                  cursor: actionDisabled ? 'not-allowed' : 'pointer',
                }}
                onMouseEnter={e => {
                  if (!actionDisabled) e.currentTarget.style.background = 'var(--bg-elevated)'
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.background = 'transparent'
                }}
              >
                <RotateCw className={`w-3.5 h-3.5 ${isRegenerating ? 'animate-spin' : ''}`} />
              </button>
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
        {(showBranchInfo || onRegenerate) && (
          <div className="flex items-center gap-2 mt-3 text-xs">
            {showBranchInfo && (
              <span
                className="px-2 py-1 rounded-full"
                style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)' }}
              >
                {`分支 ${message.sibling_index}/${message.sibling_count}`}
              </span>
            )}
            {onSwitchBranch && (
              <button
                type="button"
                onClick={onSwitchBranch}
                disabled={actionDisabled}
                className="p-1.5 rounded-full transition"
                aria-label={isSwitchingBranch ? '切换分支中' : '切换分支'}
                title={isSwitchingBranch ? '切换分支中' : '切换分支'}
                style={{
                  background: 'transparent',
                  color: actionDisabled ? 'var(--text-muted)' : 'var(--text-secondary)',
                  opacity: actionDisabled ? 0.7 : 1,
                  cursor: actionDisabled ? 'not-allowed' : 'pointer',
                }}
                onMouseEnter={e => {
                  if (!actionDisabled) e.currentTarget.style.background = 'var(--bg-elevated)'
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.background = 'transparent'
                }}
              >
                <GitBranch className={`w-3.5 h-3.5 ${isSwitchingBranch ? 'animate-pulse' : ''}`} />
              </button>
            )}
            {onRegenerate && (
              <button
                type="button"
                onClick={onRegenerate}
                disabled={actionDisabled}
                className="p-1.5 rounded-full transition"
                aria-label={isRegenerating ? '重新生成中' : '重新生成'}
                title={isRegenerating ? '重新生成中' : '重新生成'}
                style={{
                  background: 'transparent',
                  color: actionDisabled ? 'var(--text-muted)' : 'var(--text-secondary)',
                  opacity: actionDisabled ? 0.7 : 1,
                  cursor: actionDisabled ? 'not-allowed' : 'pointer',
                }}
                onMouseEnter={e => {
                  if (!actionDisabled) e.currentTarget.style.background = 'var(--bg-elevated)'
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.background = 'transparent'
                }}
              >
                <RotateCw className={`w-3.5 h-3.5 ${isRegenerating ? 'animate-spin' : ''}`} />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
