import { useState } from 'react'
import { ChevronDown, ChevronUp, FlaskConical, Key, Loader2, Pencil, Plus, RefreshCw, Server, Trash2, X } from 'lucide-react'

const PROVIDERS = {
  openai: { label: 'OpenAI', displayName: 'OpenAI', defaultBaseUrl: 'https://api.openai.com/v1', adapter: 'openai_responses' },
  openrouter: { label: 'OpenRouter', displayName: 'OpenRouter', defaultBaseUrl: 'https://openrouter.ai/api/v1', adapter: 'openai_chat_completions' },
  anthropic: { label: 'Anthropic', displayName: 'Anthropic', defaultBaseUrl: 'https://api.anthropic.com/v1', adapter: 'anthropic_messages' },
  gemini: { label: 'Gemini', displayName: 'Gemini', defaultBaseUrl: 'https://generativelanguage.googleapis.com', adapter: 'google_gemini_generate_content' },
  deepseek: { label: 'DeepSeek', displayName: 'DeepSeek', defaultBaseUrl: 'https://api.deepseek.com/v1', adapter: 'openai_chat_completions' },
  qwen: { label: '通义千问', displayName: '通义千问', defaultBaseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', adapter: 'openai_chat_completions' },
  groq: { label: 'Groq', displayName: 'Groq', defaultBaseUrl: 'https://api.groq.com/openai/v1', adapter: 'openai_chat_completions' },
  mistral: { label: 'Mistral AI', displayName: 'Mistral AI', defaultBaseUrl: 'https://api.mistral.ai/v1', adapter: 'openai_chat_completions' },
  xai: { label: 'xAI', displayName: 'xAI', defaultBaseUrl: 'https://api.x.ai/v1', adapter: 'openai_chat_completions' },
  together: { label: 'Together AI', displayName: 'Together AI', defaultBaseUrl: 'https://api.together.xyz/v1', adapter: 'openai_chat_completions' },
  custom: { label: '自定义服务商', displayName: '自定义服务商', defaultBaseUrl: '', adapter: 'openai_chat_completions' },
}

const ADAPTERS = [
  { value: 'openai_responses', label: 'OpenAI Responses' },
  { value: 'openai_chat_completions', label: 'OpenAI Chat Completions' },
  { value: 'anthropic_messages', label: 'Anthropic Messages' },
]

const initialForm = () => ({ preset_id: '', display_name: '', base_url: '', api_key: '', default_adapter_id: '' })

function providerDescription(item) {
  return item.base_url || PROVIDERS[item.preset_id]?.defaultBaseUrl || '默认服务商地址'
}

function readPricing(model) {
  try {
    const pricing = JSON.parse(model.metadata_json || '{}')?.pricing
    return pricing && typeof pricing === 'object' ? pricing : {}
  } catch {
    return {}
  }
}

function pricePerMillion(value) {
  const perToken = Number(value)
  if (!Number.isFinite(perToken) || perToken < 0) return '—'
  const amount = perToken * 1_000_000
  return `$${amount >= 1 ? amount.toFixed(2) : amount.toPrecision(3)}/M`
}

function ModelSwitch({ checked, disabled, onChange, label }) {
  return (
    <label className="provider-switch" title={label}>
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        onChange={onChange}
        aria-label={label}
      />
      <span className="provider-switch-track" aria-hidden="true" />
    </label>
  )
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
  onUpdateProvider,
  onLoadProviderModels,
  onSyncProviderModels,
  onCreateProviderModel,
  onUpdateProviderModel,
  embedded = false,
}) {
  const [form, setForm] = useState(initialForm)
  const [saving, setSaving] = useState(false)
  const [testingId, setTestingId] = useState(null)
  const [deletingId, setDeletingId] = useState(null)
  const [syncingId, setSyncingId] = useState(null)
  const [updatingId, setUpdatingId] = useState(null)
  const [expandedId, setExpandedId] = useState(null)
  const [modelsByProvider, setModelsByProvider] = useState({})
  const [editingProviderId, setEditingProviderId] = useState(null)
  const [providerDraft, setProviderDraft] = useState(null)
  const [manualModelByProvider, setManualModelByProvider] = useState({})
  const [manualModelOpenByProvider, setManualModelOpenByProvider] = useState({})
  const [error, setError] = useState('')

  if (!open) return null

  const enabledProviders = apiKeys.filter(item => item.enabled)
  const disabledProviders = apiKeys.filter(item => !item.enabled)
  const selectedProvider = PROVIDERS[form.preset_id]

  function updateForm(values) {
    setForm(previous => ({ ...previous, ...values }))
  }

  function handleProviderChange(presetId) {
    const config = PROVIDERS[presetId]
    if (!config) {
      setForm(initialForm())
      return
    }
    setForm({
      preset_id: presetId,
      display_name: config.displayName,
      base_url: '',
      api_key: '',
      default_adapter_id: config.adapter,
    })
  }

  async function handleSubmit(event) {
    event.preventDefault()
    if (!form.preset_id) return
    setError('')
    setSaving(true)
    try {
      await onCreate(form)
      setForm(initialForm())
      await onRefresh()
    } catch (err) {
      setError(err.message || '保存服务商失败')
    } finally {
      setSaving(false)
    }
  }

  async function handleToggleProvider(item) {
    setError('')
    setUpdatingId(item.id)
    try {
      await onUpdateProvider(item.id, { enabled: !item.enabled })
    } catch (err) {
      setError(err.message || '更新服务商状态失败')
    } finally {
      setUpdatingId(null)
    }
  }

  async function handleTest(id) {
    setError('')
    setTestingId(id)
    try {
      await onTest(id)
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
      setModelsByProvider(previous => {
        const next = { ...previous }
        delete next[id]
        return next
      })
    } catch (err) {
      setError(err.message || '删除服务商失败')
    } finally {
      setDeletingId(null)
    }
  }

  async function loadModels(id, force = false) {
    if (!force && modelsByProvider[id]) return modelsByProvider[id]
    const models = await onLoadProviderModels(id)
    const items = Array.isArray(models) ? models : []
    setModelsByProvider(previous => ({ ...previous, [id]: items }))
    return items
  }

  async function toggleExpand(item) {
    setError('')
    if (expandedId === item.id) {
      setExpandedId(null)
      return
    }
    setExpandedId(item.id)
    try {
      await loadModels(item.id)
    } catch (err) {
      setError(err.message || '读取缓存模型失败')
    }
  }

  async function handleSync(item) {
    setError('')
    setSyncingId(item.id)
    try {
      const models = await onSyncProviderModels(item.id)
      setModelsByProvider(previous => ({ ...previous, [item.id]: Array.isArray(models) ? models : [] }))
      setManualModelOpenByProvider(previous => ({ ...previous, [item.id]: false }))
      setManualModelByProvider(previous => ({ ...previous, [item.id]: '' }))
      setExpandedId(item.id)
    } catch (err) {
      setError(err.message || '获取模型列表失败')
    } finally {
      setSyncingId(null)
    }
  }

  async function handleToggleModel(providerId, model) {
    setError('')
    const key = `${providerId}:${model.model_id}`
    setUpdatingId(key)
    try {
      const updated = await onUpdateProviderModel(providerId, model.model_id, { enabled: !model.enabled })
      setModelsByProvider(previous => ({
        ...previous,
        [providerId]: (previous[providerId] || []).map(item => item.model_id === model.model_id ? updated : item),
      }))
    } catch (err) {
      setError(err.message || '更新模型状态失败')
    } finally {
      setUpdatingId(null)
    }
  }

  function startProviderEdit(item) {
    setError('')
    setEditingProviderId(item.id)
    setExpandedId(item.id)
    setProviderDraft({
      display_name: item.display_name,
      base_url: item.base_url || '',
      api_key: '',
      default_adapter_id: item.default_adapter_id,
    })
  }

  async function saveProviderEdit(item, event) {
    event.preventDefault()
    if (!providerDraft?.display_name?.trim()) return
    setError('')
    setUpdatingId(`provider-${item.id}`)
    try {
      const payload = {
        display_name: providerDraft.display_name.trim(),
        base_url: providerDraft.base_url.trim(),
        ...(item.preset_id === 'custom' ? { default_adapter_id: providerDraft.default_adapter_id } : {}),
        ...(providerDraft.api_key.trim() ? { api_key: providerDraft.api_key.trim() } : {}),
      }
      await onUpdateProvider(item.id, payload)
      setEditingProviderId(null)
      setProviderDraft(null)
      setManualModelOpenByProvider(previous => ({ ...previous, [item.id]: false }))
      setManualModelByProvider(previous => ({ ...previous, [item.id]: '' }))
      await onRefresh()
    } catch (err) {
      setError(err.message || '更新服务商失败')
    } finally {
      setUpdatingId(null)
    }
  }

  async function addManualModel(item, event) {
    event.preventDefault()
    const modelId = (manualModelByProvider[item.id] || '').trim()
    if (!modelId) return
    setError('')
    setUpdatingId(`model-create-${item.id}`)
    try {
      const model = await onCreateProviderModel(item.id, { model_id: modelId })
      setModelsByProvider(previous => ({
        ...previous,
        [item.id]: [...(previous[item.id] || []), model].sort((left, right) => left.model_id.localeCompare(right.model_id)),
      }))
      setManualModelByProvider(previous => ({ ...previous, [item.id]: '' }))
      setManualModelOpenByProvider(previous => ({ ...previous, [item.id]: false }))
      setExpandedId(item.id)
    } catch (err) {
      setError(err.message || '添加模型失败')
    } finally {
      setUpdatingId(null)
    }
  }

  function CompactProviderCard({ item }) {
    return (
      <div className="flex items-center gap-3 rounded-2xl px-3 py-3" style={{ background: 'var(--bg-base)', border: '1px solid var(--border)' }}>
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl" style={{ background: 'var(--accent-subtle)', color: 'var(--accent)' }}>
          <Server className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{item.display_name}</p>
          <p className="truncate text-xs" style={{ color: 'var(--text-muted)' }}>{item.preset_id}</p>
        </div>
        <ModelSwitch checked={item.enabled} disabled={updatingId === item.id} onChange={() => handleToggleProvider(item)} label={`${item.enabled ? '禁用' : '启用'} ${item.display_name}`} />
      </div>
    )
  }

  function ProviderDetails({ item }) {
    const models = modelsByProvider[item.id] || []
    const enabledModels = models.filter(model => model.enabled)
    const disabledModels = models.filter(model => !model.enabled)
    const expanded = expandedId === item.id
    const isEditing = editingProviderId === item.id

    function ModelRow({ model }) {
      const key = `${item.id}:${model.model_id}`
      const pricing = readPricing(model)
      const inputPrice = pricePerMillion(pricing.prompt ?? pricing.input)
      const outputPrice = pricePerMillion(pricing.completion ?? pricing.output)
      const cachePrice = pricePerMillion(pricing.input_cache_read ?? pricing.cache_read ?? pricing.cache)
      return (
        <div className="grid grid-cols-[minmax(0,1fr)_auto_auto_auto] items-center gap-3 px-4 py-3 text-sm" style={{ borderTop: '1px solid var(--border)' }}>
          <div className="min-w-0">
            <p className="truncate font-medium">{model.display_name_override || model.remote_display_name || model.model_id}</p>
            <p className="truncate text-xs" style={{ color: model.remote_available ? 'var(--text-muted)' : 'var(--error-text)' }}>{model.model_id}{model.remote_available ? '' : ' · 已从远端下线'}</p>
          </div>
          <span className="text-xs whitespace-nowrap" style={{ color: 'var(--text-secondary)' }}>价格 {inputPrice}/{outputPrice}</span>
          <span className="text-xs whitespace-nowrap" style={{ color: 'var(--text-secondary)' }}>缓存命中价 {cachePrice}</span>
          <ModelSwitch checked={model.enabled} disabled={updatingId === key} onChange={() => handleToggleModel(item.id, model)} label={`${model.enabled ? '禁用' : '启用'} ${model.model_id}`} />
        </div>
      )
    }

    return (
      <article className="overflow-hidden rounded-2xl" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
        <div className="flex items-start gap-3 px-4 py-4">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl" style={{ background: 'var(--accent-subtle)', color: 'var(--accent)' }}><Key className="h-4 w-4" /></div>
          <button type="button" className="min-w-0 flex-1 text-left" onClick={() => toggleExpand(item)}>
            <div className="flex items-center gap-2"><span className="truncate text-sm font-semibold">{item.display_name}</span><span className="rounded-md px-1.5 py-0.5 text-[10px]" style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)' }}>{item.preset_id}</span></div>
            <p className="mt-1 truncate text-xs" style={{ color: 'var(--text-muted)' }}>{providerDescription(item)}</p>
          </button>
          <div className="flex shrink-0 items-center gap-1">
            <button type="button" onClick={() => handleSync(item)} disabled={syncingId === item.id} className="rounded-lg px-2 py-1.5 text-xs disabled:opacity-50" style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }} title="从服务商刷新并保存模型列表">
              {syncingId === item.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              <span className="ml-1.5 hidden sm:inline">获取模型列表</span>
            </button>
            <button type="button" onClick={() => handleTest(item.id)} disabled={testingId === item.id} className="rounded-lg p-1.5 disabled:opacity-50" style={{ color: 'var(--text-secondary)' }} title="测试连接">{testingId === item.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <FlaskConical className="h-4 w-4" />}</button>
            <button type="button" onClick={() => startProviderEdit(item)} disabled={updatingId === `provider-${item.id}`} className="rounded-lg p-1.5 disabled:opacity-50" style={{ color: 'var(--text-secondary)' }} title="编辑服务商"><Pencil className="h-4 w-4" /></button>
            <button type="button" onClick={() => handleDelete(item.id)} disabled={deletingId === item.id} className="rounded-lg p-1.5 disabled:opacity-50" style={{ color: 'var(--error-text)' }} title="删除服务商">{deletingId === item.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}</button>
            <button type="button" onClick={() => toggleExpand(item)} className="rounded-lg p-1.5" style={{ color: 'var(--text-muted)' }} aria-label={expanded ? '收起模型列表' : '展开模型列表'}>{expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}</button>
          </div>
        </div>
        {expanded && (
          <div>
            {isEditing && providerDraft && (
              <form onSubmit={event => saveProviderEdit(item, event)} className="space-y-2 px-4 py-3" style={{ background: 'var(--bg-base)', borderTop: '1px solid var(--border)' }}>
                <input value={providerDraft.display_name} onChange={event => setProviderDraft(previous => ({ ...previous, display_name: event.target.value }))} placeholder="显示名称" required className="w-full rounded-lg px-2.5 py-2 text-xs outline-none" style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
                <input value={providerDraft.base_url} onChange={event => setProviderDraft(previous => ({ ...previous, base_url: event.target.value }))} placeholder="API Base URL" required={item.preset_id === 'custom'} className="w-full rounded-lg px-2.5 py-2 text-xs outline-none" style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
                <input type="password" value={providerDraft.api_key} onChange={event => setProviderDraft(previous => ({ ...previous, api_key: event.target.value }))} placeholder="新 API Key（留空不修改）" className="w-full rounded-lg px-2.5 py-2 text-xs outline-none" style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
                {item.preset_id === 'custom' && <select value={providerDraft.default_adapter_id} onChange={event => setProviderDraft(previous => ({ ...previous, default_adapter_id: event.target.value }))} className="w-full rounded-lg px-2.5 py-2 text-xs outline-none" style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}>{ADAPTERS.map(adapter => <option key={adapter.value} value={adapter.value}>{adapter.label}</option>)}</select>}
                <div className="flex justify-end gap-2"><button type="button" onClick={() => { setEditingProviderId(null); setProviderDraft(null) }} className="rounded-lg px-3 py-2 text-xs" style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}>取消</button><button type="submit" disabled={updatingId === `provider-${item.id}`} className="rounded-lg px-3 py-2 text-xs disabled:opacity-50" style={{ background: 'var(--accent)', color: 'var(--text-primary)' }}>保存</button></div>
              </form>
            )}
            <div className="flex items-center justify-between px-4 py-2 text-xs" style={{ background: 'var(--bg-base)', color: 'var(--text-muted)', borderTop: '1px solid var(--border)' }}><span>已缓存模型 · 启用的模型优先显示</span><span>{models.length} 个</span></div>
            {manualModelOpenByProvider[item.id] ? (
              <form onSubmit={event => addManualModel(item, event)} className="flex gap-2 px-4 py-3" style={{ background: 'var(--bg-base)', borderTop: '1px solid var(--border)' }}>
                <input autoFocus value={manualModelByProvider[item.id] || ''} onChange={event => setManualModelByProvider(previous => ({ ...previous, [item.id]: event.target.value }))} placeholder="手动添加模型 ID" className="min-w-0 flex-1 rounded-lg px-2.5 py-2 text-xs outline-none" style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
                <button type="submit" disabled={updatingId === `model-create-${item.id}`} className="rounded-lg px-2.5 py-2 text-xs disabled:opacity-50" style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }} title="添加模型" aria-label="添加模型">{updatingId === `model-create-${item.id}` ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}</button>
              </form>
            ) : (
              <div className="px-4 py-2" style={{ background: 'var(--bg-base)', borderTop: '1px solid var(--border)' }}>
                <button type="button" onClick={() => setManualModelOpenByProvider(previous => ({ ...previous, [item.id]: true }))} className="text-xs" style={{ color: 'var(--text-secondary)' }}>手动添加模型</button>
              </div>
            )}
            {models.length === 0 ? <p className="px-4 py-6 text-center text-sm" style={{ color: 'var(--text-muted)' }}>还没有缓存模型。点击“获取模型列表”后会保存在本地。</p> : <>
              {enabledModels.map(model => <ModelRow key={model.model_id} model={model} />)}
              {disabledModels.length > 0 && <p className="px-4 py-2 text-xs" style={{ color: 'var(--text-muted)', background: 'var(--bg-base)', borderTop: '1px solid var(--border)' }}>未启用的模型</p>}
              {disabledModels.map(model => <ModelRow key={model.model_id} model={model} />)}
            </>}
          </div>
        )}
      </article>
    )
  }

  return (
    <div className={embedded ? 'w-full' : 'fixed inset-0 z-40 flex items-center justify-center p-4'} style={embedded ? undefined : { background: 'var(--overlay)' }}>
      <div className={embedded ? 'w-full' : 'w-full max-w-6xl max-h-[90vh] overflow-auto rounded-3xl'} style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
        <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: '1px solid var(--border)' }}>
          <div className="flex items-center gap-3"><div className="flex h-9 w-9 items-center justify-center rounded-2xl" style={{ background: 'var(--accent-subtle)', color: 'var(--accent)' }}><Key className="h-4 w-4" /></div><div><h2 className="text-sm font-semibold">服务商设置</h2><p className="text-xs" style={{ color: 'var(--text-muted)' }}>模型只会在此处刷新并缓存；聊天中的选择器不会请求远端。</p></div></div>
          {!embedded && <button type="button" onClick={onClose} className="rounded-xl p-2" style={{ color: 'var(--text-muted)' }} aria-label="关闭"><X className="h-4 w-4" /></button>}
        </div>

        <div className="grid min-h-[520px] lg:grid-cols-[300px_minmax(0,1fr)]">
          <aside className="space-y-4 p-4" style={{ borderRight: '1px solid var(--border)' }}>
            <section><p className="mb-2 text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>已启用服务商</p><div className="space-y-2">{enabledProviders.length ? enabledProviders.map(item => <CompactProviderCard key={item.id} item={item} />) : <p className="rounded-xl px-3 py-4 text-xs" style={{ background: 'var(--bg-base)', color: 'var(--text-muted)' }}>尚未启用服务商</p>}</div></section>
            <div style={{ borderTop: '1px solid var(--border)' }} />
            <section><p className="mb-2 text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>未启用服务商</p><div className="space-y-2">{disabledProviders.map(item => <CompactProviderCard key={item.id} item={item} />)}</div></section>
            <section className="space-y-3 pt-1">
              <label className="block text-xs" style={{ color: 'var(--text-secondary)' }}>选择服务商</label>
              <select value={form.preset_id} onChange={event => handleProviderChange(event.target.value)} className="w-full rounded-xl px-3 py-2.5 text-sm outline-none" style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}>
                <option value="">选择服务商</option>
                {Object.entries(PROVIDERS).filter(([id]) => id !== 'custom' && id !== 'gemini').map(([id, item]) => <option key={id} value={id}>{item.label}</option>)}
                <option value="custom">自定义服务商…</option>
              </select>
              {selectedProvider && <form onSubmit={handleSubmit} className="space-y-2 rounded-2xl p-3" style={{ background: 'var(--bg-base)', border: '1px solid var(--border)' }}>
                <p className="text-sm font-medium">{form.preset_id === 'custom' ? '自定义服务商' : `添加 ${selectedProvider.label}`}</p>
                <input value={form.display_name} onChange={event => updateForm({ display_name: event.target.value })} placeholder="显示名称" required className="w-full rounded-lg px-2.5 py-2 text-xs outline-none" style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
                <input value={form.base_url} onChange={event => updateForm({ base_url: event.target.value })} placeholder={selectedProvider.defaultBaseUrl || 'API Base URL'} required={form.preset_id === 'custom'} className="w-full rounded-lg px-2.5 py-2 text-xs outline-none" style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
                <input type="password" value={form.api_key} onChange={event => updateForm({ api_key: event.target.value })} placeholder="API Key" className="w-full rounded-lg px-2.5 py-2 text-xs outline-none" style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
                {form.preset_id === 'custom' && <><label className="block pt-1 text-xs" style={{ color: 'var(--text-secondary)' }}>接口格式</label><select value={form.default_adapter_id} onChange={event => updateForm({ default_adapter_id: event.target.value })} className="w-full rounded-lg px-2.5 py-2 text-xs outline-none" style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}>{ADAPTERS.map(adapter => <option key={adapter.value} value={adapter.value}>{adapter.label}</option>)}</select></>}
                <button type="submit" disabled={saving} className="flex w-full items-center justify-center gap-2 rounded-lg py-2 text-xs font-medium disabled:opacity-60" style={{ background: 'var(--accent)', color: 'var(--text-primary)' }}>{saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}{saving ? '保存中…' : '添加服务商'}</button>
              </form>}
            </section>
          </aside>

          <section className="min-w-0 p-4 sm:p-5">
            <div className="mb-4 flex items-center justify-between"><div><h3 className="text-base font-semibold">已启用服务商</h3><p className="mt-1 text-xs" style={{ color: 'var(--text-muted)' }}>展开卡片查看已缓存模型；开关决定模型是否出现在聊天选择器中。</p></div><button type="button" onClick={onRefresh} className="rounded-lg p-2" style={{ color: 'var(--text-muted)' }} title="刷新服务商状态"><RefreshCw className="h-4 w-4" /></button></div>
            {error && <div className="mb-4 rounded-xl px-3 py-2.5 text-sm" style={{ background: 'var(--error-bg)', border: '1px solid var(--error-border)', color: 'var(--error-text)' }}>{error}</div>}
            {loading ? <div className="flex justify-center py-16"><Loader2 className="h-5 w-5 animate-spin" style={{ color: 'var(--text-muted)' }} /></div> : loadError ? <div className="rounded-xl px-3 py-2.5 text-sm" style={{ background: 'var(--error-bg)', border: '1px solid var(--error-border)', color: 'var(--error-text)' }}>{loadError}</div> : enabledProviders.length === 0 ? <div className="rounded-2xl px-4 py-12 text-center text-sm" style={{ background: 'var(--bg-base)', color: 'var(--text-muted)' }}>从左侧选择一个服务商并配置 API Key 后，它会显示在这里。</div> : <div className="space-y-3">{enabledProviders.map(item => <ProviderDetails key={item.id} item={item} />)}</div>}
          </section>
        </div>
      </div>
    </div>
  )
}
