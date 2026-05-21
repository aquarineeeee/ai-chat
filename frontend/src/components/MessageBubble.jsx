import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'

export default function MessageBubble({
  message,
  onRegenerate,
  disableActions = false,
  isRegenerating = false,
}) {
  const isUser = message.role === 'user'
  const isStreaming = message.status === 'streaming'
  const showBranchInfo = Number(message.sibling_count) > 1

  if (isUser) {
    return (
      <div className="flex justify-end py-2">
        <div
          className="max-w-[80%] rounded-2xl rounded-tr-sm px-4 py-3 text-sm"
          style={{ background: 'var(--bubble-bg)', color: 'var(--text-primary)' }}
        >
          <div className="prose-chat whitespace-pre-wrap break-words">
            {message.content}
          </div>
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
            {onRegenerate && (
              <button
                type="button"
                onClick={onRegenerate}
                disabled={disableActions || isStreaming}
                className="px-2 py-1 rounded-full transition"
                style={{
                  background: 'var(--bg-elevated)',
                  color: disableActions || isStreaming ? 'var(--text-muted)' : 'var(--text-primary)',
                  opacity: disableActions || isStreaming ? 0.7 : 1,
                  cursor: disableActions || isStreaming ? 'not-allowed' : 'pointer',
                }}
              >
                {isRegenerating ? '重新生成中...' : '重新生成'}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
