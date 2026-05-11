import { Sparkles } from 'lucide-react'

const SUGGESTIONS = [
  '帮我写一首关于秋天的诗',
  '解释一下量子纠缠是什么',
  '用 Python 写一个快速排序',
  '给我推荐几本科幻小说',
]

export default function EmptyState({ onSend }) {
  return (
    <div className="flex flex-col items-center justify-center h-full px-4 py-12 text-center">
      <div
        className="w-14 h-14 rounded-2xl flex items-center justify-center mb-5"
        style={{ background: 'var(--accent-subtle)', border: '1px solid var(--accent-border)' }}
      >
        <Sparkles className="w-7 h-7" style={{ color: 'var(--accent)' }} />
      </div>
      <h2 className="text-xl font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>
        有什么可以帮你的？
      </h2>
      <p className="text-sm mb-8 max-w-sm" style={{ color: 'var(--text-secondary)' }}>
        发送消息开始对话，或者从下面的建议中选一个
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full max-w-lg">
        {SUGGESTIONS.map(s => (
          <button
            key={s}
            onClick={() => onSend(s)}
            className="text-left px-4 py-3 rounded-xl text-sm transition"
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border)',
              color: 'var(--text-secondary)',
            }}
            onMouseEnter={e => {
              e.currentTarget.style.borderColor = 'var(--accent-border)'
              e.currentTarget.style.color = 'var(--text-primary)'
              e.currentTarget.style.background = 'var(--bg-elevated)'
            }}
            onMouseLeave={e => {
              e.currentTarget.style.borderColor = 'var(--border)'
              e.currentTarget.style.color = 'var(--text-secondary)'
              e.currentTarget.style.background = 'var(--bg-surface)'
            }}
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}
