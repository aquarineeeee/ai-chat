import { useState, useRef, useEffect } from 'react'
import { Loader2, Send, Square } from 'lucide-react'

export default function ChatInput({
  onSend,
  disabled,
  providerValue = 'openai',
  providerOptions = [],
  modelValue = '',
  modelOptions = [],
  modelProvider = 'openai',
  modelLoading = false,
  modelSaving = false,
  modelError = '',
  onProviderChange,
  onModelChange,
}) {
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
      <div className="max-w-3xl mx-auto space-y-2">
        <div className="flex items-center justify-between gap-3 px-1">
          <div className="flex items-center gap-2 min-w-0">
            {providerOptions.length > 0 && (
              <>
                <span className="text-xs font-medium shrink-0" style={{ color: 'var(--text-muted)' }}>
                  Provider
                </span>
                <select
                  value={providerValue || 'openai'}
                  onChange={e => onProviderChange?.(e.target.value)}
                  disabled={disabled || modelSaving}
                  className="text-xs rounded-lg px-2.5 py-1.5 min-w-[44px]"
                  style={{
                    background: 'var(--bg-surface)',
                    border: '1px solid var(--border)',
                    color: 'var(--text-primary)',
                  }}
                  aria-label="选择 Provider"
                >
                  {providerOptions.map(option => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </>
            )}
            <span className="text-xs font-medium shrink-0" style={{ color: 'var(--text-muted)' }}>
              模型
            </span>
            <select
              value={modelValue || ''}
              onChange={e => onModelChange?.(e.target.value)}
              disabled={disabled || modelSaving || modelLoading || modelOptions.length === 0}
              className="text-xs rounded-lg px-2.5 py-1.5 min-w-[44px] max-w-sm"
              style={{
                background: 'var(--bg-surface)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
              }}
              aria-label="选择模型"
            >
              {!modelValue && <option value="">默认模型</option>}
              {modelOptions.map(option => (
                <option key={option.id} value={option.id}>
                  {option.name || option.id}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-1.5 text-xs min-w-0" style={{ color: modelError ? 'var(--error-text)' : 'var(--text-muted)' }}>
            {(modelLoading || modelSaving) && <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />}
            <span className="truncate">
              {modelError || (modelLoading
                ? '加载模型中'
                : modelSaving
                  ? '保存中'
                  : (modelProvider || 'openai'))}
            </span>
          </div>
        </div>

        <div
          className="flex items-end gap-3 rounded-2xl px-4 py-3 transition"
          style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}
          onFocusCapture={e => { e.currentTarget.style.borderColor = 'var(--accent)' }}
          onBlurCapture={e => { e.currentTarget.style.borderColor = 'var(--border)' }}
        >
          <textarea
            ref={textareaRef}
            value={value}
            onChange={e => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            placeholder="发送消息…（Shift+Enter 换行）"
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
            onMouseLeave={e => { e.currentTarget.style.background = 'var(--accent)' }}
            aria-label="发送消息"
          >
            {disabled
              ? <Square className="w-3.5 h-3.5 fill-current" style={{ color: 'var(--text-primary)' }} />
              : <Send className="w-3.5 h-3.5" style={{ color: 'var(--text-primary)' }} />
            }
          </button>
        </div>

        <p className="text-center text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
          AI 可能会出错，请核实重要信息。
        </p>
      </div>
    </div>
  )
}
