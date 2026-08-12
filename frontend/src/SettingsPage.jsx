import { useEffect, useState } from 'react'
import { ArrowLeft, Check, ChevronDown, ChevronUp, FolderKanban, FlaskConical, Loader2, LogOut, Moon, Plus, Power, ShieldCheck, Sun, Trash2, User, Wrench, X } from 'lucide-react'
import ApiKeysModal from './components/ApiKeysModal'
import { PALETTES } from './ThemeContext'
import { api } from './api'

const PALETTE_COLORS = {
  stone: '#6e5c52',
  lavender: '#7c6fa0',
  sage: '#5e8a6e',
  blue: '#4a72a8',
}

const TABS = [
  { id: 'account', label: '账户资料' },
  { id: 'appearance', label: '外观' },
  { id: 'provider', label: '服务商' },
  { id: 'model', label: '默认模型' },
  { id: 'projects', label: '项目' },
  { id: 'mcp', label: 'MCP' },
]

function McpSwitch({ checked, disabled = false, label, onChange }) {
  return (
    <label className="provider-switch" title={label} onClick={event => event.stopPropagation()}>
      <input type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={onChange} aria-label={label} />
      <span className="provider-switch-track" aria-hidden="true" />
    </label>
  )
}

function mcpStatus(server) {
  if (server.last_test_status === 'success') return { label: '连接正常', tone: 'success' }
  if (server.last_test_status === 'error') return { label: '测试失败', tone: 'error' }
  return { label: '待测试', tone: 'pending' }
}

function schemaType(schema) {
  if (Array.isArray(schema?.type)) return schema.type.join(' / ')
  if (schema?.type) return schema.type
  if (Array.isArray(schema?.enum)) return '枚举'
  return '任意类型'
}

export default function SettingsPage({
  user,
  palette,
  mode,
  onToggleTheme,
  onSetPalette,
  onLogout,
  onBack,
  apiKeys,
  loadingKeys,
  keysError,
  onRefreshKeys,
  onCreateProvider,
  onDeleteProvider,
  onTestProvider,
  onUpdateProvider,
  onLoadProviderModels,
  onSyncProviderModels,
  onCreateProviderModel,
  onUpdateProviderModel,
  defaultProvider,
  defaultModel,
  providerOptions,
  modelOptions,
  modelLoading,
  modelError,
  onDefaultProviderChange,
  onDefaultModelChange,
  onProjectsChange = () => {},
}) {
  const [activeTab, setActiveTab] = useState('account')
  const [providerOpen] = useState(true)
  const [mcpServers, setMcpServers] = useState([])
  const [mcpLoading, setMcpLoading] = useState(false)
  const [mcpError, setMcpError] = useState('')
  const [mcpDraft, setMcpDraft] = useState({ display_name: '', url: '', transport: 'streamable_http', headers: [] })
  const [expandedMcpId, setExpandedMcpId] = useState(null)
  const [selectedMcpTool, setSelectedMcpTool] = useState(null)
  const [mcpAction, setMcpAction] = useState(null)
  const [projects, setProjects] = useState([])
  const [projectsLoading, setProjectsLoading] = useState(false)
  const [projectDraft, setProjectDraft] = useState({ id: null, name: '', system_prompt: '', default_model_id: '' })
  const [projectError, setProjectError] = useState('')
  const [projectSaving, setProjectSaving] = useState(false)
  const [deletingProject, setDeletingProject] = useState(null)

  useEffect(() => {
    if (!selectedMcpTool) return undefined
    const closeOnEscape = event => {
      if (event.key === 'Escape') setSelectedMcpTool(null)
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [selectedMcpTool])

  const loadMcp = async () => {
    setMcpLoading(true)
    try { setMcpServers(await api.getMcpServers()); setMcpError('') }
    catch (error) { setMcpError(error.message) }
    finally { setMcpLoading(false) }
  }
  const loadProjects = async () => {
    setProjectsLoading(true)
    try {
      const items = await api.getProjects()
      setProjects(items)
      onProjectsChange(items)
      setProjectError('')
    } catch (error) { setProjectError(error.message || '加载项目失败') }
    finally { setProjectsLoading(false) }
  }
  const selectProject = project => setProjectDraft({ id: project.id, name: project.name, system_prompt: project.system_prompt || '', default_model_id: project.default_model_id || '' })
  const saveProject = async event => {
    event.preventDefault()
    if (!projectDraft.name.trim()) return
    setProjectSaving(true)
    try {
      const payload = { name: projectDraft.name.trim(), system_prompt: projectDraft.system_prompt || null, default_model_id: projectDraft.default_model_id || null }
      const saved = projectDraft.id ? await api.updateProject(projectDraft.id, payload) : await api.createProject(payload)
      await loadProjects()
      selectProject(saved)
    } catch (error) { setProjectError(error.message || '保存项目失败') }
    finally { setProjectSaving(false) }
  }
  const setProjectTool = async (tool, selected) => {
    if (!projectDraft.id) return
    try {
      if (selected) await api.addProjectTool(projectDraft.id, { mcp_tool_id: tool.id, requires_approval: tool.requires_approval })
      else await api.deleteProjectTool(projectDraft.id, tool.id)
      const updated = await api.getProject(projectDraft.id)
      setProjects(items => items.map(item => item.id === updated.id ? updated : item))
      onProjectsChange(await api.getProjects())
    } catch (error) { setProjectError(error.message || '更新项目工具失败') }
  }
  const setProjectToolApproval = async (toolId, requires_approval) => {
    if (!projectDraft.id) return
    try {
      const updated = await api.updateProjectTool(projectDraft.id, toolId, { requires_approval })
      setProjects(items => items.map(item => item.id === updated.id ? updated : item))
    } catch (error) { setProjectError(error.message || '更新审批策略失败') }
  }
  const deleteProject = async project => {
    setDeletingProject(project.id)
    try {
      await api.deleteProject(project.id)
      setProjectDraft({ id: null, name: '', system_prompt: '', default_model_id: '' })
      await loadProjects()
    } catch (error) { setProjectError(error.message || '删除项目失败') }
    finally { setDeletingProject(null) }
  }
  const saveMcp = async event => {
    event.preventDefault()
    if (!mcpDraft.display_name.trim() || !mcpDraft.url.trim()) return
    try {
      const created = await api.createMcpServer({ ...mcpDraft, display_name: mcpDraft.display_name.trim(), url: mcpDraft.url.trim(), headers: mcpDraft.headers.filter(item => item.name && item.value) })
      setMcpDraft({ display_name: '', url: '', transport: 'streamable_http', headers: [] })
      await api.testMcpServer(created.id); await loadMcp()
    } catch (error) { setMcpError(error.message) }
  }

  const runMcpAction = async (key, action) => {
    setMcpError('')
    setMcpAction(key)
    try {
      await action()
      await loadMcp()
    } catch (error) {
      setMcpError(error.message || 'MCP 服务操作失败')
    } finally {
      setMcpAction(null)
    }
  }

  const selectedToolSchema = selectedMcpTool?.input_schema && typeof selectedMcpTool.input_schema === 'object' ? selectedMcpTool.input_schema : {}
  const selectedToolParameters = Object.entries(selectedToolSchema.properties && typeof selectedToolSchema.properties === 'object' ? selectedToolSchema.properties : {})
  const selectedToolRequired = Array.isArray(selectedToolSchema.required) ? selectedToolSchema.required : []

  return (
    <main className="settings-page min-h-screen overflow-y-auto" style={{ background: 'var(--bg-base)', color: 'var(--text-primary)' }}>
      <div className="mx-auto w-full max-w-5xl px-5 py-6 sm:px-10 sm:py-10">
        <button
          type="button"
          onClick={onBack}
          className="mb-8 inline-flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm transition"
          style={{ color: 'var(--text-secondary)' }}
          onMouseEnter={event => { event.currentTarget.style.background = 'var(--bg-elevated)' }}
          onMouseLeave={event => { event.currentTarget.style.background = 'transparent' }}
        >
          <ArrowLeft className="h-4 w-4" />
          返回聊天
        </button>

        <div className="settings-tabs" role="tablist" aria-label="设置分类">
          {TABS.map(tab => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              aria-controls={`settings-panel-${tab.id}`}
              className="settings-tab"
              onClick={() => { setActiveTab(tab.id); if ((tab.id === 'mcp' || tab.id === 'projects') && mcpServers.length === 0 && !mcpLoading) void loadMcp(); if (tab.id === 'projects' && projects.length === 0 && !projectsLoading) void loadProjects() }}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="settings-content">
          {activeTab === 'account' && (
            <section id="settings-panel-account" role="tabpanel" aria-label="账户资料">
              <div className="settings-profile">
                <div className="settings-avatar"><User className="h-5 w-5" /></div>
                <div>
                  <p className="text-base font-medium">{user?.username || '用户'}</p>
                  <p className="mt-1 text-sm" style={{ color: 'var(--text-muted)' }}>管理账户资料与登录状态</p>
                </div>
              </div>
              <div className="settings-row">
                <div><p className="settings-row-title">登录账户</p><p className="settings-row-help">当前登录的 AI Chat 账户</p></div>
                <button type="button" className="settings-outline-button" onClick={onLogout}><LogOut className="h-3.5 w-3.5" />退出登录</button>
              </div>
            </section>
          )}

          {activeTab === 'appearance' && (
            <section id="settings-panel-appearance" role="tabpanel" aria-label="外观">
              <div className="settings-section-heading"><h1>外观</h1><p>调整界面模式与主题颜色</p></div>
              <div className="settings-row">
                <div><p className="settings-row-title">界面模式</p><p className="settings-row-help">在日间与夜间模式之间切换</p></div>
                <button type="button" className="settings-mode-toggle" onClick={onToggleTheme} aria-label="切换界面模式">
                  <span className={mode === 'light' ? 'is-selected' : ''}><Sun className="h-3.5 w-3.5" />日间</span>
                  <span className={mode === 'dark' ? 'is-selected' : ''}><Moon className="h-3.5 w-3.5" />夜间</span>
                </button>
              </div>
              <div className="settings-row">
                <div><p className="settings-row-title">主题颜色</p><p className="settings-row-help">应用于按钮、选中状态与强调色</p></div>
                <div className="settings-palette" role="radiogroup" aria-label="主题颜色">
                  {PALETTES.map(item => (
                    <button
                      key={item.id}
                      type="button"
                      role="radio"
                      aria-checked={palette === item.id}
                      aria-label={item.label}
                      className="settings-swatch"
                      style={{ background: PALETTE_COLORS[item.id] }}
                      onClick={() => onSetPalette(item.id)}
                    >{palette === item.id && <Check className="h-3.5 w-3.5" />}</button>
                  ))}
                </div>
              </div>
            </section>
          )}

          {activeTab === 'provider' && (
            <section id="settings-panel-provider" role="tabpanel" aria-label="服务商">
              <ApiKeysModal
                open={providerOpen}
                embedded
                onClose={() => {}}
                apiKeys={apiKeys}
                loading={loadingKeys}
                loadError={keysError}
                onRefresh={onRefreshKeys}
                onCreate={onCreateProvider}
                onDelete={onDeleteProvider}
                onTest={onTestProvider}
                onUpdateProvider={onUpdateProvider}
                onLoadProviderModels={onLoadProviderModels}
                onSyncProviderModels={onSyncProviderModels}
                onCreateProviderModel={onCreateProviderModel}
                onUpdateProviderModel={onUpdateProviderModel}
              />
            </section>
          )}

          {activeTab === 'model' && (
            <section id="settings-panel-model" role="tabpanel" aria-label="默认模型">
              <div className="settings-section-heading"><h1>无项目时默认模型</h1><p>新建无项目会话时优先使用的服务商与模型</p></div>
              <div className="settings-row settings-row-column">
                <label className="settings-field-label" htmlFor="default-provider">默认服务商</label>
                <select id="default-provider" value={defaultProvider} onChange={event => onDefaultProviderChange(event.target.value)} className="settings-select">
                  {providerOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              </div>
              <div className="settings-row settings-row-column">
                <label className="settings-field-label" htmlFor="default-model">默认模型</label>
                <select id="default-model" value={defaultModel} onChange={event => onDefaultModelChange(event.target.value)} className="settings-select" disabled={modelLoading || modelOptions.length === 0}>
                  <option value="">{modelLoading ? '正在加载模型…' : modelError || '请选择模型'}</option>
                  {modelOptions.map(option => <option key={option.id || option} value={option.id || option}>{option.name || option.id || option}</option>)}
                </select>
              </div>
            </section>
          )}

          {activeTab === 'projects' && (
            <section id="settings-panel-projects" role="tabpanel" aria-label="项目">
              <div className="settings-section-heading"><h1>项目</h1><p>项目会话创建时固定模型、提示词和当前可用的 MCP 工具。</p></div>
              {projectError && <p className="settings-row-help" style={{ color: 'var(--error-text)' }}>{projectError}</p>}
              <div className="grid gap-5 lg:grid-cols-[220px_minmax(0,1fr)]">
                <div className="space-y-1">
                  <button type="button" className="settings-outline-button w-full justify-start" onClick={() => setProjectDraft({ id: null, name: '', system_prompt: '', default_model_id: '' })}><Plus className="h-3.5 w-3.5" />新建项目</button>
                  {projectsLoading && <p className="settings-row-help">正在加载…</p>}
                  {projects.map(project => <button type="button" key={project.id} onClick={() => selectProject(project)} className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm transition" style={{ background: projectDraft.id === project.id ? 'var(--bg-elevated)' : 'transparent', color: 'var(--text-secondary)' }}><span className="truncate">{project.name}</span><span className="text-xs" style={{ color: 'var(--text-muted)' }}>{project.conversation_count}</span></button>)}
                </div>
                <form className="space-y-4" onSubmit={saveProject}>
                  <div><label className="settings-field-label" htmlFor="project-name">项目名称</label><input id="project-name" className="settings-input" value={projectDraft.name} onChange={event => setProjectDraft({ ...projectDraft, name: event.target.value })} placeholder="例如：产品研究" /></div>
                  <div><label className="settings-field-label" htmlFor="project-model">默认模型</label><select id="project-model" className="settings-select" value={projectDraft.default_model_id} onChange={event => setProjectDraft({ ...projectDraft, default_model_id: event.target.value })}><option value="">使用无项目默认模型</option>{modelOptions.map(option => <option key={option.id || option} value={option.id || option}>{option.name || option.id || option}</option>)}</select></div>
                  <div><label className="settings-field-label" htmlFor="project-prompt">系统提示词</label><textarea id="project-prompt" className="settings-input min-h-28" value={projectDraft.system_prompt} onChange={event => setProjectDraft({ ...projectDraft, system_prompt: event.target.value })} placeholder="项目级系统提示词" /></div>
                  <div className="flex items-center gap-2"><button type="submit" className="settings-primary-button" disabled={projectSaving}>{projectSaving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}{projectDraft.id ? '保存项目' : '创建项目'}</button>{projectDraft.id && <button type="button" className="settings-outline-button" disabled={deletingProject === projectDraft.id} onClick={() => { const project = projects.find(item => item.id === projectDraft.id); if (project && window.confirm(`确定删除「${project.name}」吗？这会永久删除其 ${project.conversation_count} 个会话。`)) void deleteProject(project) }}><Trash2 className="h-3.5 w-3.5" />删除项目</button>}</div>
                  {projectDraft.id && <div className="pt-3" style={{ borderTop: '1px solid var(--border)' }}><p className="settings-row-title">项目 MCP 工具</p><div className="mt-2 space-y-2">{mcpServers.flatMap(server => server.tools.filter(tool => server.enabled && tool.enabled && tool.remote_available).map(tool => { const project = projects.find(item => item.id === projectDraft.id); const selected = project?.tools?.find(item => item.mcp_tool_id === tool.id); return <div key={tool.id} className="flex items-center gap-3 rounded-lg px-3 py-2" style={{ background: 'var(--bg-elevated)' }}><input type="checkbox" checked={!!selected} onChange={event => { void setProjectTool(tool, event.target.checked) }} aria-label={`选择 ${tool.remote_tool_name}`} /><span className="min-w-0 flex-1 truncate text-sm">{tool.remote_tool_name}</span>{selected && <McpSwitch checked={selected.requires_approval} label="调用前需要审批" onChange={event => { void setProjectToolApproval(tool.id, event.target.checked) }} />}</div> }))}</div></div>}
                </form>
              </div>
            </section>
          )}

          {activeTab === 'mcp' && (
            <section id="settings-panel-mcp" role="tabpanel" aria-label="MCP">
              <div className="settings-section-heading"><h1>MCP</h1><p>管理按用户隔离的 MCP 服务，支持 Streamable HTTP 和旧版 SSE 传输。</p></div>
              <form className="settings-row settings-row-column" onSubmit={saveMcp}>
                <label className="settings-field-label" htmlFor="mcp-transport">传输方式</label>
                <select id="mcp-transport" className="settings-select" value={mcpDraft.transport} onChange={event => setMcpDraft({ ...mcpDraft, transport: event.target.value })}>
                  <option value="streamable_http">Streamable HTTP</option>
                  <option value="sse">SSE（旧版）</option>
                </select>
                <label className="settings-field-label">添加服务</label>
                <input className="settings-input" placeholder="名称" value={mcpDraft.display_name} onChange={event => setMcpDraft({ ...mcpDraft, display_name: event.target.value })} />
                <input className="settings-input" placeholder="MCP 地址（http/https）" value={mcpDraft.url} onChange={event => setMcpDraft({ ...mcpDraft, url: event.target.value })} />
                {mcpDraft.headers.map((header, index) => <div className="flex gap-2" key={index}><input className="settings-input" placeholder="请求头名称" value={header.name} onChange={event => setMcpDraft({ ...mcpDraft, headers: mcpDraft.headers.map((item, itemIndex) => itemIndex === index ? { ...item, name: event.target.value } : item) })} /><input className="settings-input" placeholder="请求头值" value={header.value} onChange={event => setMcpDraft({ ...mcpDraft, headers: mcpDraft.headers.map((item, itemIndex) => itemIndex === index ? { ...item, value: event.target.value } : item) })} /><button className="settings-outline-button" type="button" onClick={() => setMcpDraft({ ...mcpDraft, headers: mcpDraft.headers.filter((_, itemIndex) => itemIndex !== index) })}>移除</button></div>)}
                <button className="settings-outline-button" type="button" onClick={() => setMcpDraft({ ...mcpDraft, headers: [...mcpDraft.headers, { name: '', value: '' }] })}>添加请求头</button>
                <button className="settings-primary-button" type="submit">添加并测试</button>
              </form>
              {mcpLoading && <p className="settings-row-help">正在加载…</p>}
              {mcpError && <p className="settings-row-help" style={{ color: 'var(--error-text)' }}>{mcpError}</p>}
              {!mcpLoading && mcpServers.length === 0 && <p className="settings-row-help">暂无 MCP 服务</p>}
              {mcpServers.map(server => (
                <div className="settings-row mcp-server" key={server.id}>
                  <button
                    type="button"
                    className="mcp-server-summary"
                    aria-expanded={expandedMcpId === server.id}
                    aria-controls={`mcp-server-tools-${server.id}`}
                    onClick={() => setExpandedMcpId(current => current === server.id ? null : server.id)}
                  >
                    <span className="mcp-server-chevron" aria-hidden="true">{expandedMcpId === server.id ? <ChevronUp /> : <ChevronDown />}</span>
                    <span className="mcp-server-copy">
                      <span className="settings-row-title">{server.display_name}</span>
                      <span className="settings-row-help">{server.tools?.filter(tool => tool.remote_available).length || 0} 个可用工具 <span className={`mcp-status mcp-status-${mcpStatus(server).tone}`}>{mcpStatus(server).label}</span></span>
                    </span>
                  </button>
                  <div className="mcp-server-actions" onClick={event => event.stopPropagation()}>
                    <button
                      className="mcp-icon-button"
                      type="button"
                      title="测试连接并同步工具"
                      aria-label={`测试 ${server.display_name} 的连接`}
                      disabled={mcpAction === `test-${server.id}`}
                      onClick={() => runMcpAction(`test-${server.id}`, () => api.testMcpServer(server.id))}
                    >
                      {mcpAction === `test-${server.id}` ? <Loader2 className="animate-spin" /> : <FlaskConical />}
                    </button>
                    <McpSwitch
                      checked={server.enabled}
                      disabled={mcpAction === `server-${server.id}`}
                      label={`${server.enabled ? '停用' : '启用'} ${server.display_name}`}
                      onChange={() => runMcpAction(`server-${server.id}`, () => api.updateMcpServer(server.id, { enabled: !server.enabled }))}
                    />
                    <button
                      className="mcp-icon-button mcp-delete-button"
                      type="button"
                      title="删除服务"
                      aria-label={`删除 ${server.display_name}`}
                      disabled={mcpAction === `delete-${server.id}`}
                      onClick={() => { if (window.confirm(`删除“${server.display_name}”及其工具？`)) void runMcpAction(`delete-${server.id}`, () => api.deleteMcpServer(server.id)) }}
                    >
                      {mcpAction === `delete-${server.id}` ? <Loader2 className="animate-spin" /> : <Trash2 />}
                    </button>
                  </div>
                  {expandedMcpId === server.id && (
                    <div className="mcp-server-details" id={`mcp-server-tools-${server.id}`}>
                      <div className="mcp-server-config">
                        <label className="settings-field-label" htmlFor={`mcp-transport-${server.id}`}>传输方式</label>
                        <select
                          id={`mcp-transport-${server.id}`}
                          className="settings-select"
                          value={server.transport || 'streamable_http'}
                          onChange={event => runMcpAction(`transport-${server.id}`, () => api.updateMcpServer(server.id, { transport: event.target.value }))}
                          disabled={mcpAction === `transport-${server.id}`}
                        >
                          <option value="streamable_http">Streamable HTTP</option>
                          <option value="sse">SSE（旧版）</option>
                        </select>
                      </div>
                      <div className="mcp-tools-heading"><span><Wrench /> 工具</span><span>{server.tools?.filter(tool => tool.remote_available).length || 0}</span></div>
                      {(server.tools || []).filter(tool => tool.remote_available).map(tool => (
                        <div
                          className="mcp-tool-row"
                          key={tool.id}
                          role="button"
                          tabIndex={0}
                          aria-haspopup="dialog"
                          onClick={() => setSelectedMcpTool(tool)}
                          onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelectedMcpTool(tool) } }}
                        >
                          <div className="mcp-tool-copy">
                            <p>{tool.remote_tool_name}</p>
                            <span>{tool.description || '服务未提供工具简介'}</span>
                          </div>
                          <div className="mcp-tool-actions" onClick={event => event.stopPropagation()}>
                            <span className="mcp-tool-switch" title="是否启用"><Power aria-hidden="true" /><McpSwitch checked={tool.enabled} disabled={mcpAction === `tool-${tool.id}`} label={`${tool.enabled ? '停用' : '启用'} ${tool.remote_tool_name}`} onChange={() => runMcpAction(`tool-${tool.id}`, () => api.updateMcpTool(server.id, tool.id, { enabled: !tool.enabled }))} /></span>
                            <span className="mcp-tool-switch" title="调用前审批"><ShieldCheck aria-hidden="true" /><McpSwitch checked={tool.requires_approval} disabled={mcpAction === `approval-${tool.id}`} label={`${tool.requires_approval ? '取消调用前审批' : '启用调用前审批'} ${tool.remote_tool_name}`} onChange={() => runMcpAction(`approval-${tool.id}`, () => api.updateMcpTool(server.id, tool.id, { requires_approval: !tool.requires_approval }))} /></span>
                          </div>
                        </div>
                      ))}
                      {(server.tools || []).filter(tool => tool.remote_available).length === 0 && <p className="mcp-empty-tools">还没有同步到可用工具。请先测试连接。</p>}
                    </div>
                  )}
                </div>
              ))}
            </section>
          )}
        </div>
      </div>
      {selectedMcpTool && (
        <div className="mcp-tool-dialog-backdrop" onMouseDown={() => setSelectedMcpTool(null)}>
          <section className="mcp-tool-dialog" role="dialog" aria-modal="true" aria-labelledby="mcp-tool-dialog-title" onMouseDown={event => event.stopPropagation()}>
            <header className="mcp-dialog-header">
              <div>
                <h2 id="mcp-tool-dialog-title">{selectedMcpTool.remote_tool_name}</h2>
                <p>{selectedMcpTool.description || '服务未提供工具简介'}</p>
              </div>
              <button className="mcp-dialog-close" type="button" onClick={() => setSelectedMcpTool(null)} aria-label="关闭工具参数" title="关闭"><X /></button>
            </header>
            <div className="mcp-dialog-body">
              <h3>可用参数</h3>
              {selectedToolParameters.length === 0 ? <p className="settings-row-help">此工具没有定义输入参数。</p> : (
                <ul className="mcp-parameter-list">
                  {selectedToolParameters.map(([name, schema]) => (
                    <li className="mcp-parameter" key={name}>
                      <div className="mcp-parameter-topline">
                        <code>{name}</code>
                        <span className="mcp-parameter-type">{schemaType(schema)}</span>
                        {selectedToolRequired.includes(name) && <span className="mcp-parameter-required">必填</span>}
                      </div>
                      {(schema.description || schema.default !== undefined || Array.isArray(schema.enum)) && <p>{schema.description || ''}{schema.default !== undefined ? ` 默认值：${JSON.stringify(schema.default)}` : ''}{Array.isArray(schema.enum) ? ` 可选值：${schema.enum.map(value => JSON.stringify(value)).join('、')}` : ''}</p>}
                    </li>
                  ))}
                </ul>
              )}
              <details className="mcp-schema-details">
                <summary>查看原始 Schema</summary>
                <pre>{JSON.stringify(selectedToolSchema, null, 2)}</pre>
              </details>
            </div>
          </section>
        </div>
      )}
    </main>
  )
}
