import { useState } from 'react'
import { ArrowLeft, Check, LogOut, Moon, Sun, User } from 'lucide-react'
import ApiKeysModal from './components/ApiKeysModal'
import { PALETTES } from './ThemeContext'

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
]

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
  onUpdateProviderModel,
  defaultProvider,
  defaultModel,
  providerOptions,
  modelOptions,
  modelLoading,
  modelError,
  onDefaultProviderChange,
  onDefaultModelChange,
}) {
  const [activeTab, setActiveTab] = useState('account')
  const [providerOpen] = useState(true)

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
              onClick={() => setActiveTab(tab.id)}
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
                onUpdateProviderModel={onUpdateProviderModel}
              />
            </section>
          )}

          {activeTab === 'model' && (
            <section id="settings-panel-model" role="tabpanel" aria-label="默认模型">
              <div className="settings-section-heading"><h1>默认模型</h1><p>新建对话时优先使用的服务商与模型</p></div>
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
        </div>
      </div>
    </main>
  )
}
