import { useState, useRef, useEffect } from 'react'
import { Send, Square } from 'lucide-react'

export default function ChatInput({ onSend, disabled }) {
  const [value, setValue] = useState('')
  const textareaRef = useRef(null)

  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 200) + 'px'
  }, [value])

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  function submit() {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setValue('')
  }

  return (
    <div
      className="shrink-0 px-4 py-4"
      style={{ borderTop: '1px solid var(--border)', background: 'var(--bg-base)' }}
    >
      <div className="max-w-3xl mx-auto">
        <div
          className="flex items-end gap-3 rounded-2xl px-4 py-3 transition"
          style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}
          onFocusCapture={e => e.currentTarget.style.borderColor = 'var(--accent)'}
          onBlurCapture={e => e.currentTarget.style.borderColor = 'var(--border)'}
        >
          <textarea
            ref={textareaRef}
            value={value}
            onChange={e => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            placeholder="发送消息… (Shift+Enter 换行)"
            rows={1}
            className="flex-1 bg-transparent text-sm resize-none focus:outline-none leading-relaxed min-h-[24px] max-h-[200px] scrollbar-thin"
            style={{ color: 'var(--text-primary)', caretColor: 'var(--accent)' }}
          />
          <button
            onClick={submit}
            disabled={!value.trim() || disabled}
            className="shrink-0 w-8 h-8 rounded-xl flex items-center justify-center transition disabled:opacity-40 disabled:cursor-not-allowed"
            style={{ background: 'var(--accent)' }}
            onMouseEnter={e => { if (!e.currentTarget.disabled) e.currentTarget.style.background = 'var(--accent-hover)' }}
            onMouseLeave={e => e.currentTarget.style.background = 'var(--accent)'}
            aria-label="发送"
          >
            {disabled
              ? <Square className="w-3.5 h-3.5 fill-current" style={{ color: 'var(--text-primary)' }} />
              : <Send className="w-3.5 h-3.5" style={{ color: 'var(--text-primary)' }} />
            }
          </button>
        </div>
        <p className="text-center text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
          AI 可能会犯错，请核实重要信息
        </p>
      </div>
    </div>
  )
}
