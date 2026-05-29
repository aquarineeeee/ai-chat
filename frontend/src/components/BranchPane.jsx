import { X } from 'lucide-react'
import MessageBubble from './MessageBubble'
import ChatInput from './ChatInput'

export default function BranchPane({
  pane,
  onClose,
  onToggleContextMode,
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
  modelValue = '',
  modelOptions = [],
  modelProvider = 'openai',
  modelLoading = false,
  modelSaving = false,
  modelError = '',
  onModelChange,
}) {
  const rootMessage = pane.messages[0] || pane.sourceMessage
  const branchMessages = pane.messages.length > 0 && pane.messages[0]?.id === pane.rootMessageId
    ? pane.messages.slice(1)
    : pane.messages

  return (
    <section
      className="flex h-full flex-col min-h-0 overflow-hidden"
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
          <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
            {pane.contextMode === 'full' ? '携带全部上下文' : '仅携带根节点'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onToggleContextMode}
            className="px-2.5 py-1 rounded-full text-xs transition"
            style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
          >
            {pane.contextMode === 'full' ? '切换到仅根节点' : '切换到全部上下文'}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-full transition"
            style={{ color: 'var(--text-muted)' }}
            onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-elevated)' }}
            onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
            aria-label="关闭分支"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        {rootMessage && (
          <MessageBubble
            message={rootMessage}
            onCopy={rootMessage.role === 'system' ? undefined : () => { void onCopy(rootMessage) }}
            onEdit={rootMessage.role === 'user' ? () => { void onEdit(rootMessage) } : undefined}
            onRegenerate={rootMessage.role === 'system' ? undefined : () => { void onRegenerate(rootMessage.id) }}
            onDelete={rootMessage.role === 'system' ? undefined : () => { void onDelete(rootMessage.id) }}
            onCreateBranch={rootMessage.role === 'system' ? undefined : () => { void onCreateBranch(rootMessage) }}
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
          />
        )}

        <div className="flex items-center gap-3 my-4">
          <div className="flex-1 h-px" style={{ background: 'var(--border)' }} />
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
            已创建分支
          </span>
          <div className="flex-1 h-px" style={{ background: 'var(--border)' }} />
        </div>

        <div className="space-y-1">
          {branchMessages.map(msg => (
            <MessageBubble
              key={msg.id}
              message={msg}
              onCopy={msg.role === 'system' ? undefined : () => { void onCopy(msg) }}
              onEdit={msg.role === 'user' ? () => { void onEdit(msg) } : undefined}
              onRegenerate={msg.role === 'system' ? undefined : () => { void onRegenerate(msg.id) }}
              onDelete={msg.role === 'system' ? undefined : () => { void onDelete(msg.id) }}
              onCreateBranch={msg.role === 'system' ? undefined : () => { void onCreateBranch(msg) }}
              onPrevSibling={msg.previous_sibling_id ? () => { void onPrevSibling(msg) } : undefined}
              onNextSibling={msg.next_sibling_id ? () => { void onNextSibling(msg) } : undefined}
              isEditing={pane.editingMessageId === msg.id}
              editDraft={pane.editingMessageId === msg.id ? pane.editingContent : ''}
              editMode={pane.editingMode}
              onEditDraftChange={onEditDraftChange}
              onEditModeChange={onEditModeChange}
              onEditCancel={onEditCancel}
              onEditSubmit={() => { void onEditSubmit(msg.id) }}
              isEditSubmitting={pane.editingSubmittingMessageId === msg.id}
              disableActions={pane.busy}
              isRegenerating={pane.regeneratingMessageId === msg.id}
              isCreatingBranch={pane.creatingBranchMessageId === msg.id}
              isDeleting={pane.deletingMessageId === msg.id}
            />
          ))}
          {pane.streamingContent && (
            <MessageBubble message={{ role: 'assistant', content: pane.streamingContent, status: 'streaming' }} hideActions />
          )}
          {pane.error && (
            <div
              className="text-sm px-3 py-2 rounded-xl"
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
        modelValue={modelValue}
        modelOptions={modelOptions}
        modelProvider={modelProvider}
        modelLoading={modelLoading}
        modelSaving={modelSaving}
        modelError={modelError}
        onModelChange={onModelChange}
      />
    </section>
  )
}
