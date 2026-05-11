import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'

export default function MessageBubble({ message }) {
  const isUser = message.role === 'user'
  const isStreaming = message.status === 'streaming'

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
      <div className={`flex-1 min-w-0 text-sm pt-1 ${isStreaming ? 'typing-cursor' : ''}`}
        style={{ color: 'var(--text-primary)' }}>
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
      </div>
    </div>
  )
}
