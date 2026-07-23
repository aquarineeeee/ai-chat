import { X } from 'lucide-react'
import MessageBubble from './MessageBubble'
import ChatInput from './ChatInput'

export default function BranchPane({
  pane,
  getRunView,
  onClose,
  onContextModeChange,
  onContextMessageCountChange,
  onCopy,
  onEdit,
  onEditCancel,
  onEditSubmit,
  onEditDraftChange,
  onEditModeChange,
  onSend,
  onRegenerate,
  onDelete,
  onCreateBranch,
  onPrevSibling,
  onNextSibling,
  onApproveToolCall,
  onDenyToolCall,
  isApprovalSubmitting,
  canApproveToolCall,
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
  const rootMessage = pane.messages[0] || pane.sourceMessage
  const branchMessages = pane.messages.length > 0 && pane.messages[0]?.id === pane.rootMessageId
    ? pane.messages.slice(1)
    : pane.messages
  const showPendingAssistant = (pane.sending || pane.regeneratingMessageId !== null) && !pane.streamingAssistantId
  const contextSummary = pane.contextMode === 'full'
    ? '携带全部上下文'
    : `携带根节点及其前 ${pane.contextMessageCount} 条消息`

  return (
    <section
      className="flex h-full min-h-0 flex-col overflow-hidden"
      style={{ background: 'var(--bg-surface)' }}
    >
      <div
        className="flex items-center justify-between px-4 py-3 shrink-0"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        <div>
          <p className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
            分支视图
          </p>
          <p className="mt-1 text-xs" style={{ color: 'var(--text-muted)' }}>
            {contextSummary}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-2">
            <select
              value={pane.contextMode}
              onChange={event => onContextModeChange?.(event.target.value)}
              className="rounded-lg px-2.5 py-1 text-xs outline-none transition"
              style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
              aria-label="上下文模式"
            >
              <option value="full">全部上下文</option>
              <option value="last_n">根节点前 N 条 + 根节点</option>
            </select>
            {pane.contextMode === 'last_n' && (
              <input
                type="number"
                min="1"
                step="1"
                value={pane.contextMessageCount}
                onChange={event => {
                  const nextCount = Number.parseInt(event.target.value, 10)
                  if (Number.isInteger(nextCount) && nextCount > 0) {
                    onContextMessageCountChange?.(nextCount)
                  }
                }}
                className="w-20 rounded-lg px-2.5 py-1 text-xs outline-none transition"
                style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
                aria-label="前置上下文条数"
              />
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full p-1.5 transition"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={event => { event.currentTarget.style.background = 'var(--bg-elevated)' }}
            onMouseLeave={event => { event.currentTarget.style.background = 'transparent' }}
            aria-label="关闭分支"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        {rootMessage && (
          <MessageBubble
            message={rootMessage}
            runView={getRunView?.(rootMessage.id) || null}
            onCopy={rootMessage.role === 'system' ? undefined : () => { void onCopy(rootMessage) }}
            onEdit={rootMessage.role === 'user' ? () => { void onEdit(rootMessage) } : undefined}
            onRegenerate={rootMessage.role === 'system' ? undefined : () => { void onRegenerate(rootMessage.id) }}
            onDelete={rootMessage.role === 'system' ? undefined : () => { void onDelete(rootMessage.id) }}
            onCreateBranch={rootMessage.role === 'assistant' ? () => { void onCreateBranch(rootMessage) } : undefined}
            onPrevSibling={rootMessage.previous_sibling_id ? () => { void onPrevSibling(rootMessage) } : undefined}
            onNextSibling={rootMessage.next_sibling_id ? () => { void onNextSibling(rootMessage) } : undefined}
            isEditing={pane.editingMessageId === rootMessage.id}
            editDraft={pane.editingMessageId === rootMessage.id ? pane.editingContent : ''}
            editMode={pane.editingMode}
            onEditDraftChange={onEditDraftChange}
            onEditModeChange={onEditModeChange}
            onEditCancel={onEditCancel}
            onEditSubmit={() => { void onEditSubmit(rootMessage.id) }}
            isEditSubmitting={pane.editingSubmittingMessageId === rootMessage.id}
            disableActions={pane.busy}
            isRegenerating={pane.regeneratingMessageId === rootMessage.id}
            isCreatingBranch={pane.creatingBranchMessageId === rootMessage.id}
            isDeleting={pane.deletingMessageId === rootMessage.id}
            onApproveToolCall={rootMessage.role === 'assistant' ? toolCallRef => { void onApproveToolCall?.(rootMessage, toolCallRef) } : undefined}
            onDenyToolCall={rootMessage.role === 'assistant' ? toolCallRef => { void onDenyToolCall?.(rootMessage, toolCallRef) } : undefined}
            isApprovalSubmitting={toolCallRef => isApprovalSubmitting?.(rootMessage.id, toolCallRef)}
            canApproveToolCall={toolCallRef => canApproveToolCall?.(rootMessage.id, toolCallRef)}
          />
        )}

        <div className="my-4 flex items-center gap-3">
          <div className="h-px flex-1" style={{ background: 'var(--border)' }} />
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
            已创建分支
          </span>
          <div className="h-px flex-1" style={{ background: 'var(--border)' }} />
        </div>

        <div className="space-y-1">
          {branchMessages.map(message => (
            <MessageBubble
              key={message.id}
              message={message}
              runView={getRunView?.(message.id) || null}
              onCopy={message.role === 'system' ? undefined : () => { void onCopy(message) }}
              onEdit={message.role === 'user' ? () => { void onEdit(message) } : undefined}
              onRegenerate={message.role === 'system' ? undefined : () => { void onRegenerate(message.id) }}
              onDelete={message.role === 'system' ? undefined : () => { void onDelete(message.id) }}
              onCreateBranch={message.role === 'assistant' ? () => { void onCreateBranch(message) } : undefined}
              onPrevSibling={message.previous_sibling_id ? () => { void onPrevSibling(message) } : undefined}
              onNextSibling={message.next_sibling_id ? () => { void onNextSibling(message) } : undefined}
              isEditing={pane.editingMessageId === message.id}
              editDraft={pane.editingMessageId === message.id ? pane.editingContent : ''}
              editMode={pane.editingMode}
              onEditDraftChange={onEditDraftChange}
              onEditModeChange={onEditModeChange}
              onEditCancel={onEditCancel}
              onEditSubmit={() => { void onEditSubmit(message.id) }}
              isEditSubmitting={pane.editingSubmittingMessageId === message.id}
              disableActions={pane.busy}
              isRegenerating={pane.regeneratingMessageId === message.id}
              isCreatingBranch={pane.creatingBranchMessageId === message.id}
              isDeleting={pane.deletingMessageId === message.id}
              onApproveToolCall={message.role === 'assistant' ? toolCallRef => { void onApproveToolCall?.(message, toolCallRef) } : undefined}
              onDenyToolCall={message.role === 'assistant' ? toolCallRef => { void onDenyToolCall?.(message, toolCallRef) } : undefined}
              isApprovalSubmitting={toolCallRef => isApprovalSubmitting?.(message.id, toolCallRef)}
              canApproveToolCall={toolCallRef => canApproveToolCall?.(message.id, toolCallRef)}
            />
          ))}
          {showPendingAssistant && (
            <div className="flex gap-3 py-3">
              <div
                className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold"
                style={{ background: 'var(--accent)', color: 'var(--text-primary)' }}
              >
                AI
              </div>
              <div className="flex items-center gap-1 pt-2">
                {[0, 1, 2].map(index => (
                  <span
                    key={index}
                    className="h-1.5 w-1.5 rounded-full animate-bounce"
                    style={{ background: 'var(--text-muted)', animationDelay: `${index * 0.15}s` }}
                  />
                ))}
              </div>
            </div>
          )}
          {pane.error && (
            <div
              className="rounded-xl px-3 py-2 text-sm"
              style={{ background: 'var(--error-bg)', border: '1px solid var(--error-border)', color: 'var(--error-text)' }}
            >
              {pane.error}
            </div>
          )}
        </div>
      </div>

      <ChatInput
        onSend={onSend}
        disabled={pane.busy}
        providerValue={providerValue}
        providerOptions={providerOptions}
        modelValue={modelValue}
        modelOptions={modelOptions}
        modelProvider={modelProvider}
        modelLoading={modelLoading}
        modelSaving={modelSaving}
        modelError={modelError}
        onProviderChange={onProviderChange}
        onModelChange={onModelChange}
      />
    </section>
  )
}
