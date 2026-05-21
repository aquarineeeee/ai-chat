import { useState } from 'react'
import { Key, Loader2, FlaskConical, Trash2, X } from 'lucide-react'

const INITIAL_FORM = {
  provider: 'openai',
  display_name: 'OpenAI',
  base_url: '',
  api_key: '',
}

export default function ApiKeysModal({
  open,
  onClose,
  apiKeys,
  loading,
  loadError,
  onRefresh,
  onCreate,
  onDelete,
  onTest,
}) {
  const [form, setForm] = useState(INITIAL_FORM)
  const [saving, setSaving] = useState(false)
  const [testingId, setTestingId] = useState(null)
  const [deletingId, setDeletingId] = useState(null)
  const [error, setError] = useState('')

  if (!open) return null

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setSaving(true)
    try {
      await onCreate(form)
      setForm(INITIAL_FORM)
      await onRefresh()
    } catch (err) {
      setError(err.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  async function handleTest(id) {
    setError('')
    setTestingId(id)
    try {
      await onTest(id)
      await onRefresh()
    } catch (err) {
      setError(err.message || '测试失败')
    } finally {
      setTestingId(null)
    }
  }

  async function handleDelete(id) {
    setError('')
    setDeletingId(id)
    try {
      await onDelete(id)
      await onRefresh()
    } catch (err) {
      setError(err.message || '删除失败')
    } finally {
      setDeletingId(null)
    }
  }

  function formatTime(value) {
    if (!value) return '未测试'
    return new Date(value).toLocaleString('zh-CN')
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center p-4" style={{ background: 'var(--overlay)' }}>
      <div
        className="w-full max-w-3xl max-h-[90vh] overflow-hidden rounded-3xl"
        style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}
      >
        <div
          className="flex items-center justify-between px-5 py-4"
          style={{ borderBottom: '1px solid var(--border)' }}
        >
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-2xl flex items-center justify-center" style={{ background: 'var(--accent-subtle)' }}>
              <Key className="w-4 h-4" style={{ color: 'var(--accent)' }} />
            </div>
            <div>
              <h2 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>API Keys</h2>
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>当前先支持 OpenAI-compatible 的 `openai` provider</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-xl transition"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-elevated)'; e.currentTarget.style.color = 'var(--text-primary)' }}
            onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-muted)' }}
            aria-label="关闭"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="grid md:grid-cols-[1.1fr_1fr] max-h-[calc(90vh-74px)]">
          <div className="overflow-y-auto p-5 space-y-4" style={{ borderRight: '1px solid var(--border)' }}>
            <form onSubmit={handleSubmit} className="space-y-3">
              <div>
                <label className="block text-xs mb-1.5" style={{ color: 'var(--text-secondary)' }}>Provider</label>
                <input
                  value={form.provider}
                  onChange={e => setForm(prev => ({ ...prev, provider: e.target.value }))}
                  className="w-full rounded-xl px-3 py-2.5 text-sm focus:outline-none"
                  style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
                />
              </div>

              <div>
                <label className="block text-xs mb-1.5" style={{ color: 'var(--text-secondary)' }}>显示名称</label>
                <input
                  value={form.display_name}
                  onChange={e => setForm(prev => ({ ...prev, display_name: e.target.value }))}
                  className="w-full rounded-xl px-3 py-2.5 text-sm focus:outline-none"
                  style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
                />
              </div>

              <div>
                <label className="block text-xs mb-1.5" style={{ color: 'var(--text-secondary)' }}>Base URL（可选）</label>
                <input
                  value={form.base_url}
                  onChange={e => setForm(prev => ({ ...prev, base_url: e.target.value }))}
                  placeholder="留空默认使用 https://api.openai.com/v1"
                  className="w-full rounded-xl px-3 py-2.5 text-sm focus:outline-none"
                  style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
                />
              </div>

              <div>
                <label className="block text-xs mb-1.5" style={{ color: 'var(--text-secondary)' }}>API Key</label>
                <input
                  type="password"
                  value={form.api_key}
                  onChange={e => setForm(prev => ({ ...prev, api_key: e.target.value }))}
                  className="w-full rounded-xl px-3 py-2.5 text-sm focus:outline-none"
                  style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
                />
              </div>

              {error && (
                <div
                  className="rounded-xl px-3 py-2.5 text-sm"
                  style={{ background: 'var(--error-bg)', border: '1px solid var(--error-border)', color: 'var(--error-text)' }}
                >
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={saving}
                className="w-full rounded-xl py-2.5 text-sm font-medium flex items-center justify-center gap-2 disabled:opacity-60"
                style={{ background: 'var(--accent)', color: 'var(--text-primary)' }}
              >
                {saving && <Loader2 className="w-4 h-4 animate-spin" />}
                {saving ? '保存中...' : '保存 API Key'}
              </button>
            </form>
          </div>

          <div className="overflow-y-auto p-5">
            {loading ? (
              <div className="flex items-center justify-center py-10">
                <Loader2 className="w-5 h-5 animate-spin" style={{ color: 'var(--text-muted)' }} />
              </div>
            ) : loadError ? (
              <div
                className="rounded-2xl px-4 py-3 text-sm"
                style={{ background: 'var(--error-bg)', border: '1px solid var(--error-border)', color: 'var(--error-text)' }}
              >
                {loadError}
              </div>
            ) : apiKeys.length === 0 ? (
              <div className="rounded-2xl px-4 py-6 text-sm text-center" style={{ background: 'var(--bg-base)', color: 'var(--text-muted)' }}>
                还没有配置 API Key
              </div>
            ) : (
              <div className="space-y-3">
                {apiKeys.map(item => (
                  <div
                    key={item.id}
                    className="rounded-2xl px-4 py-4"
                    style={{ background: 'var(--bg-base)', border: '1px solid var(--border)' }}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{item.display_name}</span>
                          <span className="text-[11px] px-2 py-0.5 rounded-lg" style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)' }}>
                            {item.provider}
                          </span>
                        </div>
                        <p className="text-xs mt-1 break-all" style={{ color: 'var(--text-secondary)' }}>
                          {item.base_url || 'https://api.openai.com/v1'}
                        </p>
                        <p className="text-xs mt-2" style={{ color: 'var(--text-muted)' }}>
                          Key 尾号: {item.key_last_four || '未知'}
                        </p>
                        <p className="text-xs mt-1" style={{ color: item.last_test_status === 'success' ? 'var(--text-secondary)' : 'var(--text-muted)' }}>
                          最近测试: {formatTime(item.last_tested_at)}
                        </p>
                        {item.last_test_message && (
                          <p className="text-xs mt-1" style={{ color: item.last_test_status === 'failed' ? 'var(--error-text)' : 'var(--text-muted)' }}>
                            {item.last_test_message}
                          </p>
                        )}
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        <button
                          onClick={() => handleTest(item.id)}
                          disabled={testingId === item.id}
                          className="p-2 rounded-xl transition disabled:opacity-60"
                          style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
                          title="测试"
                        >
                          {testingId === item.id
                            ? <Loader2 className="w-4 h-4 animate-spin" />
                            : <FlaskConical className="w-4 h-4" />}
                        </button>
                        <button
                          onClick={() => handleDelete(item.id)}
                          disabled={deletingId === item.id}
                          className="p-2 rounded-xl transition disabled:opacity-60"
                          style={{ background: 'var(--error-bg)', color: 'var(--error-text)' }}
                          title="删除"
                        >
                          {deletingId === item.id
                            ? <Loader2 className="w-4 h-4 animate-spin" />
                            : <Trash2 className="w-4 h-4" />}
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
