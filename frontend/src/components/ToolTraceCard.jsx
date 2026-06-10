import { ChevronDown, Loader2, Search } from 'lucide-react'

function summaryLabel(trace) {
  if (!trace) return ''
  if (trace.name === 'memory_search') {
    return trace.status === 'running' ? '正在检索记忆…' : '已检索记忆'
  }
  return trace.status === 'running' ? '工具执行中…' : '工具执行完成'
}

export default function ToolTraceCard({ trace, onToggle }) {
  if (!trace) return null

  const expanded = !!trace.expanded
  const hasContent = !!(trace.content && String(trace.content).trim())
  const canToggle = hasContent && trace.status !== 'running'

  return (
    <div
      className="mb-2 rounded-2xl border overflow-hidden"
      style={{
        background: 'color-mix(in srgb, var(--bg-surface) 88%, transparent)',
        borderColor: 'var(--border)',
      }}
    >
      <button
        type="button"
        onClick={canToggle ? onToggle : undefined}
        disabled={!canToggle}
        className="w-full flex items-center gap-2 px-3 py-2 text-left disabled:cursor-default"
        style={{ color: 'var(--text-secondary)' }}
      >
        {trace.status === 'running' ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
        ) : (
          <Search className="w-3.5 h-3.5 shrink-0" />
        )}
        <span className="text-xs flex-1">{summaryLabel(trace)}</span>
        {canToggle && (
          <ChevronDown
            className="w-3.5 h-3.5 shrink-0 transition-transform"
            style={{ transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)' }}
          />
        )}
      </button>
      {expanded && hasContent && (
        <div
          className="px-3 py-2 text-xs whitespace-pre-wrap break-words border-t"
          style={{
            color: 'var(--text-primary)',
            borderColor: 'var(--border)',
            background: 'var(--bg-elevated)',
          }}
        >
          {trace.content}
        </div>
      )}
    </div>
  )
}
