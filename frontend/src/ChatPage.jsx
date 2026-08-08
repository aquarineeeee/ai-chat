import { useState, useEffect, useLayoutEffect, useRef, useCallback, useMemo } from 'react'
import { useAuth } from './AuthContext'
import { useTheme } from './ThemeContext'
import { api } from './api'
import MessageBubble from './components/MessageBubble'
import Sidebar from './components/Sidebar'
import ChatInput from './components/ChatInput'
import EmptyState from './components/EmptyState'
import BranchPane from './components/BranchPane'
import MessageTreePanel from './components/MessageTreePanel'
import ConversationRuler from './components/ConversationRuler'
import SettingsPage from './SettingsPage'
import { CHAT_RULER_WINDOW_SIZE } from './config/chat'
import { Menu, X, Loader2, AlertCircle, Download, ChevronDown, Network } from 'lucide-react'

const DEFAULT_BRANCH_PANE_WIDTH = 440
const MIN_BRANCH_PANE_WIDTH = 280
const MAX_BRANCH_PANE_WIDTH = 760
const MIN_MAIN_PANEL_WIDTH = 320
const BRANCH_PANE_WIDTH_STORAGE_KEY = 'ai-chat.branch-pane-width'
const STREAM_INACTIVITY_TIMEOUT_MS = 60000
const DEFAULT_BRANCH_CONTEXT_MESSAGE_COUNT = 6
const INITIAL_MESSAGE_PAGE_SIZE = 40
const LOAD_EARLIER_THRESHOLD_PX = 120

function buildConversationTurns(messageItems) {
  const turns = []

  messageItems.forEach((message, index) => {
    if (message.role !== 'user') return

    const assistantMessage = messageItems.slice(index + 1).find(nextMessage => (
      nextMessage.role === 'assistant'
      || nextMessage.role === 'user'
    ))

    turns.push({
      userMessage: message,
      assistantMessage: assistantMessage?.role === 'assistant' ? assistantMessage : null,
    })
  })

  return turns
}

function clampBranchPaneWidth(width, containerWidth) {
  if (!Number.isFinite(width)) return DEFAULT_BRANCH_PANE_WIDTH

  if (!Number.isFinite(containerWidth) || containerWidth <= 0) {
    return Math.min(Math.max(width, MIN_BRANCH_PANE_WIDTH), MAX_BRANCH_PANE_WIDTH)
  }

  const minWidth = Math.min(MIN_BRANCH_PANE_WIDTH, Math.max(220, Math.round(containerWidth * 0.28)))
  const maxWidth = Math.min(
    MAX_BRANCH_PANE_WIDTH,
    Math.max(minWidth, Math.round(containerWidth - MIN_MAIN_PANEL_WIDTH)),
  )

  return Math.min(Math.max(width, minWidth), maxWidth)
}

function collectBranchTreeIds(branches, rootBranchId) {
  const byParent = new Map()
  branches.forEach(branch => {
    const key = branch.parent_branch_id ?? null
    byParent.set(key, [...(byParent.get(key) || []), branch])
  })

  const ids = new Set()
  const stack = [rootBranchId]
  while (stack.length > 0) {
    const branchId = stack.pop()
    if (ids.has(branchId)) continue

    ids.add(branchId)
    const children = byParent.get(branchId) || []
    children.forEach(child => stack.push(child.id))
  }
  return ids
}

function createBranchPane(sourceMessage, branch = null) {
  return {
    id: `branch-${sourceMessage.id}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    branchId: branch?.id ?? null,
    rootMessageId: sourceMessage.id,
    sourceMessage,
    messages: [sourceMessage],
    currentLeafMessageId: sourceMessage.id,
    contextMode: 'full',
    contextMessageCount: DEFAULT_BRANCH_CONTEXT_MESSAGE_COUNT,
    loading: true,
    sending: false,
    regeneratingMessageId: null,
    deletingMessageId: null,
    creatingBranchMessageId: null,
    switchingSiblingMessageId: null,
    editingMessageId: null,
    editingContent: '',
    editingMode: 'update',
    editingSubmittingMessageId: null,
    streamingAssistantId: null,
    error: '',
    openedAt: Date.now(),
  }
}

function buildBranchContextPayload(pane) {
  const contextMessageCount = Number.isInteger(pane.contextMessageCount) && pane.contextMessageCount > 0
    ? pane.contextMessageCount
    : DEFAULT_BRANCH_CONTEXT_MESSAGE_COUNT

  return {
    context_mode: pane.contextMode,
    ...(pane.contextMode === 'last_n'
      ? {
          context_message_count: contextMessageCount,
          context_root_message_id: pane.rootMessageId,
        }
      : {}),
  }
}

const EXPORT_OPTIONS = [
  { key: 'markdown-current_branch', label: 'Markdown · 当前分支', format: 'markdown', scope: 'current_branch' },
  { key: 'json-current_branch', label: 'JSON · 当前分支', format: 'json', scope: 'current_branch' },
  { key: 'json-all_branches', label: 'JSON · 全部分支', format: 'json', scope: 'all_branches' },
]

const ACTIVE_RUN_STATUSES = ['running', 'waiting_approval']

function parseProviderId(value) {
  const providerId = Number(value)
  return Number.isInteger(providerId) && providerId > 0 ? providerId : null
}

function createStreamingAssistantMessage(id, overrides = {}) {
  return {
    id,
    role: 'assistant',
    content: '',
    parts: [],
    parts_schema_version: 1,
    status: 'streaming',
    error_message: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    sibling_index: 1,
    sibling_count: 1,
    previous_sibling_id: null,
    next_sibling_id: null,
    ...overrides,
  }
}

function hasMessageId(messages, messageId) {
  return Array.isArray(messages) && messages.some(item => item.id === messageId)
}

// Insert a new message at its display position. This function deliberately
// never merges an existing message: callers that hold a fresh empty streaming
// placeholder must not overwrite text or parts accumulated from prior events.
function insertMessageAfter(messages, anchorId, message) {
  if (!Array.isArray(messages)) return [message]
  if (hasMessageId(messages, message.id)) return messages
  if (!anchorId) return [...messages, message]
  const anchorIndex = messages.findIndex(item => item.id === anchorId)
  if (anchorIndex < 0) return [...messages, message]
  return [
    ...messages.slice(0, anchorIndex + 1),
    message,
    ...messages.slice(anchorIndex + 1),
  ]
}

// Ensure a placeholder exists without changing an existing message. Streaming
// content is updated exclusively by applyRunEventToMessage below.
function ensureMessageAfter(messages, anchorId, message) {
  if (hasMessageId(messages, message.id)) return messages
  return insertMessageAfter(messages, anchorId, message)
}

function cloneParts(parts) {
  if (!Array.isArray(parts)) return []
  return parts.map(part => ({ ...part }))
}

function aggregateTextFromParts(parts) {
  if (!Array.isArray(parts)) return ''
  return parts
    .filter(part => part?.type === 'text')
    .map(part => part?.text || '')
    .join('')
}

function buildRunEventFromChunk(chunk, fallbackAssistantMessageId = null) {
  if (!chunk?.type) return null
  return {
    type: chunk.type,
    run_id: chunk.run_id || null,
    payload: chunk.payload && typeof chunk.payload === 'object' ? chunk.payload : {},
    tool_call_ref: chunk.tool_call_ref || null,
    assistant_message_id: chunk.assistant_message_id ?? fallbackAssistantMessageId,
    step_id: chunk.step_id || null,
    sequence: Number.isFinite(chunk.sequence) ? Number(chunk.sequence) : null,
  }
}

function isAbortError(error) {
  return error?.name === 'AbortError'
}

function dedupeRuns(runs) {
  const byId = new Map()
  for (const run of runs) {
    if (run?.id == null) continue
    byId.set(run.id, run)
  }
  return [...byId.values()]
}

function mapRunsByAssistantMessage(runs) {
  const byAssistantMessageId = {}
  for (const run of runs) {
    if (!run?.assistant_message_id) continue
    if (!ACTIVE_RUN_STATUSES.includes(run?.status)) continue
    byAssistantMessageId[run.assistant_message_id] = run
  }
  return byAssistantMessageId
}

function mapRunIdsByAssistantMessage(runs) {
  const byAssistantMessageId = {}
  for (const run of runs) {
    if (!run?.assistant_message_id || !run?.id) continue
    byAssistantMessageId[run.assistant_message_id] = run.id
  }
  return byAssistantMessageId
}

async function listActiveRuns(conversationId) {
  if (!conversationId) return []
  const responses = await Promise.all(
    ACTIVE_RUN_STATUSES.map(status => api.getAgentRuns(conversationId, { status })),
  )
  return dedupeRuns(responses.flatMap(response => response?.items || []))
}

function applyRunEventToParts(parts, event) {
  const nextParts = cloneParts(parts)
  const type = event?.type || ''
  const payload = event?.payload || {}
  const toolCallRef = event?.tool_call_ref || null

  if (type === 'message.text.delta') {
    const text = payload?.text || ''
    if (!text) return nextParts
    const lastPart = nextParts[nextParts.length - 1]
    if (lastPart?.type === 'text') {
      lastPart.text = `${lastPart.text || ''}${text}`
    } else {
      nextParts.push({ type: 'text', text })
    }
    return nextParts
  }

  if (type === 'message.text.retracted') {
    let remaining = String(payload?.text || '').length
    if (!remaining) return nextParts

    for (let index = nextParts.length - 1; index >= 0 && remaining > 0; index -= 1) {
      const part = nextParts[index]
      if (part?.type !== 'text') continue
      const currentText = String(part.text || '')
      const removeCount = Math.min(currentText.length, remaining)
      part.text = currentText.slice(0, currentText.length - removeCount)
      remaining -= removeCount
    }

    return nextParts.filter(part => part?.type !== 'text' || String(part?.text || '').length > 0)
  }

  if (type === 'tool_call.created') {
    nextParts.push({
      type: 'tool_call',
      tool_call_id: toolCallRef,
      tool_name: payload?.tool_name || '',
      status: 'pending',
      arguments_preview: payload?.display_input_preview || '',
    })
    return nextParts
  }

  if (type === 'tool_call.arguments.completed') {
    const toolPart = [...nextParts].reverse().find(part => part?.type === 'tool_call' && part?.tool_call_id === toolCallRef)
    if (toolPart) toolPart.arguments_preview = payload?.display_input_preview || ''
    return nextParts
  }

  if (type === 'tool_call.started') {
    const toolPart = [...nextParts].reverse().find(part => part?.type === 'tool_call' && part?.tool_call_id === toolCallRef)
    if (toolPart) toolPart.status = 'running'
    return nextParts
  }

  if (type === 'tool_call.approval.requested') {
    const toolPart = [...nextParts].reverse().find(part => part?.type === 'tool_call' && part?.tool_call_id === toolCallRef)
    if (toolPart) toolPart.status = 'waiting_approval'
    nextParts.push({
      type: 'approval',
      tool_call_id: toolCallRef,
      tool_name: payload?.tool_name || '',
      status: 'requested',
      message: 'Waiting for approval',
    })
    return nextParts
  }

  if (type === 'tool_call.approval.granted') {
    const toolPart = [...nextParts].reverse().find(part => part?.type === 'tool_call' && part?.tool_call_id === toolCallRef)
    if (toolPart) toolPart.status = 'ready'
    const approvalPart = [...nextParts].reverse().find(part => part?.type === 'approval' && part?.tool_call_id === toolCallRef)
    if (approvalPart) approvalPart.status = 'granted'
    return nextParts
  }

  if (type === 'tool_call.approval.denied') {
    const toolPart = [...nextParts].reverse().find(part => part?.type === 'tool_call' && part?.tool_call_id === toolCallRef)
    if (toolPart) toolPart.status = 'denied'
    const approvalPart = [...nextParts].reverse().find(part => part?.type === 'approval' && part?.tool_call_id === toolCallRef)
    if (approvalPart) {
      approvalPart.status = 'denied'
      approvalPart.message = payload?.comment || payload?.error_message || ''
    }
    return nextParts
  }

  if (type === 'tool_call.completed') {
    const toolPart = [...nextParts].reverse().find(part => part?.type === 'tool_call' && part?.tool_call_id === toolCallRef)
    if (toolPart) toolPart.status = 'completed'
    nextParts.push({
      type: 'tool_result',
      tool_call_id: toolCallRef,
      tool_name: payload?.tool_name || '',
      status: 'completed',
      content: payload?.display_output_preview || '',
    })
    return nextParts
  }

  if (type === 'tool_call.failed') {
    const toolPart = [...nextParts].reverse().find(part => part?.type === 'tool_call' && part?.tool_call_id === toolCallRef)
    if (toolPart) toolPart.status = 'error'
    nextParts.push({
      type: 'error',
      tool_call_id: toolCallRef,
      tool_name: payload?.tool_name || '',
      message: payload?.error_message || '',
    })
    return nextParts
  }

  if ((type === 'run.failed' || type === 'run.cancelled') && payload?.error_message) {
    nextParts.push({ type: 'error', message: payload.error_message })
  }

  return nextParts
}

function applyRunEventToMessage(message, event) {
  if (!message || !event) return message
  const nextParts = applyRunEventToParts(Array.isArray(message.parts) ? message.parts : [], event)
  const nextMessage = {
    ...message,
    parts: nextParts,
    content: aggregateTextFromParts(nextParts),
  }

  if (event.type === 'message.completed') {
    const completedParts = Array.isArray(event.payload?.parts)
      ? cloneParts(event.payload.parts)
      : nextParts
    nextMessage.parts = completedParts
    nextMessage.content = typeof event.payload?.content === 'string'
      ? event.payload.content
      : aggregateTextFromParts(completedParts)
    nextMessage.parts_schema_version = Number.isFinite(Number(event.payload?.parts_schema_version))
      ? Number(event.payload.parts_schema_version)
      : (message.parts_schema_version || 1)
    nextMessage.status = 'completed'
    nextMessage.error_message = null
    return nextMessage
  }

  if (event.type === 'run.failed' || event.type === 'run.cancelled') {
    nextMessage.status = 'failed'
    nextMessage.error_message = event.payload?.error_message || message.error_message
    return nextMessage
  }

  nextMessage.status = 'streaming'
  return nextMessage
}

function createRunView(runId, assistantMessageId = null) {
  return {
    run_id: runId,
    assistant_message_id: assistantMessageId,
    status: 'running',
    phase: null,
    last_sequence: 0,
    pending_approval: null,
    items: [],
  }
}

function findTimelineIndex(items, predicate) {
  return items.findIndex(predicate)
}

function ensureToolStepItem(items, event) {
  const stepId = event?.step_id || `legacy-${event?.tool_call_ref || items.length}`
  const toolCallRef = event?.tool_call_ref || null
  let index = findTimelineIndex(items, item => item?.type === 'tool_step' && item?.step_id === stepId)

  if (index < 0 && toolCallRef) {
    index = findTimelineIndex(items, item => item?.type === 'tool_step' && item?.tool_call_ref === toolCallRef)
  }

  if (index >= 0) {
    return { items, index, item: { ...items[index] } }
  }

  const nextItem = {
    type: 'tool_step',
    step_id: stepId,
    tool_call_ref: toolCallRef,
    tool_name: event?.payload?.tool_name || '',
    status: 'pending',
    started_at: null,
    completed_at: null,
    arguments_preview: event?.payload?.display_input_preview || '',
    result_preview: '',
  }
  return {
    items: [...items, nextItem],
    index: items.length,
    item: nextItem,
  }
}

function ensureProcessedGroupItem(items, stepId) {
  const index = findTimelineIndex(items, item => item?.type === 'processed_group' && item?.step_id === stepId)
  if (index >= 0) {
    return { items, index, item: { ...items[index] } }
  }

  const group = {
    type: 'processed_group',
    group_id: `pg_${stepId}`,
    step_id: stepId,
    label: 'Processed',
    status: 'active',
    created_at: null,
    commentary: [],
  }

  const toolIndex = findTimelineIndex(items, item => item?.type === 'tool_step' && item?.step_id === stepId)
  if (toolIndex < 0) {
    return {
      items: [...items, group],
      index: items.length,
      item: group,
    }
  }

  const nextItems = [...items]
  nextItems.splice(toolIndex + 1, 0, group)
  return {
    items: nextItems,
    index: toolIndex + 1,
    item: group,
  }
}

function ensureThinkingGroupItem(items) {
  const index = findTimelineIndex(items, item => item?.type === 'thinking_group')
  if (index >= 0) {
    return { items, index, item: { ...items[index] } }
  }

  const group = {
    type: 'thinking_group',
    group_id: 'thinking_group',
    label: 'Thinking',
    status: 'active',
    created_at: null,
    thinking: [],
  }
  return {
    items: [group, ...items],
    index: 0,
    item: group,
  }
}

function applyRunEventToRunView(runView, event) {
  if (!event?.run_id) return runView

  const nextView = runView
    ? { ...runView, items: Array.isArray(runView.items) ? [...runView.items] : [] }
    : createRunView(event.run_id, event.assistant_message_id || null)

  nextView.assistant_message_id = event.assistant_message_id ?? nextView.assistant_message_id
  nextView.last_sequence = Number.isFinite(event?.sequence)
    ? Math.max(Number(event.sequence) || 0, Number(nextView.last_sequence) || 0)
    : nextView.last_sequence

  const payload = event?.payload || {}

  if (event.type === 'run.phase.changed') {
    nextView.phase = payload?.phase || nextView.phase
    return nextView
  }

  if (event.type === 'tool_call.approval.requested') {
    nextView.pending_approval = {
      tool_call_ref: event.tool_call_ref || null,
      step_id: event.step_id || null,
      tool_name: payload?.tool_name || '',
      arguments_preview: payload?.display_input_preview || '',
    }
  } else if (event.type === 'tool_call.approval.granted' || event.type === 'tool_call.approval.denied') {
    nextView.pending_approval = null
  }

  if (event.type.startsWith('thinking.')) {
    const ensured = ensureThinkingGroupItem(nextView.items)
    const group = {
      ...ensured.item,
      label: ensured.item?.label || 'Thinking',
      thinking: Array.isArray(ensured.item?.thinking) ? [...ensured.item.thinking] : [],
    }
    const thinkingId = payload?.thinking_id || `thinking_${event.sequence}`
    const thinkingIndex = group.thinking.findIndex(item => item?.thinking_id === thinkingId)
    const currentThinking = thinkingIndex >= 0
      ? { ...group.thinking[thinkingIndex] }
      : { thinking_id: thinkingId, text: '', redacted: false }

    if (event.type === 'thinking.created') currentThinking.text = payload?.text || ''
    if (event.type === 'thinking.delta') currentThinking.text = `${currentThinking.text || ''}${payload?.text || ''}`
    if (event.type === 'thinking.redacted') currentThinking.redacted = true

    if (thinkingIndex >= 0) group.thinking[thinkingIndex] = currentThinking
    else group.thinking.push(currentThinking)
    group.status = 'active'
    group.created_at = group.created_at || new Date().toISOString()

    const nextItems = [...ensured.items]
    nextItems[ensured.index] = group
    nextView.items = nextItems
    return nextView
  }

  if (event.type.startsWith('tool_call.')) {
    const ensured = ensureToolStepItem(nextView.items, event)
    const item = { ...ensured.item }
    item.tool_call_ref = event.tool_call_ref || item.tool_call_ref
    item.tool_name = payload?.tool_name || item.tool_name
    if (payload?.display_input_preview !== undefined) item.arguments_preview = payload.display_input_preview || ''
    if (payload?.display_output_preview !== undefined) item.result_preview = payload.display_output_preview || ''
    if (event.type === 'tool_call.created') item.status = 'pending'
    if (event.type === 'tool_call.arguments.completed') item.status = 'ready'
    if (event.type === 'tool_call.approval.requested') item.status = 'waiting_approval'
    if (event.type === 'tool_call.approval.granted') item.status = 'ready'
    if (event.type === 'tool_call.approval.denied') item.status = 'denied'
    if (event.type === 'tool_call.started') {
      item.status = 'running'
      item.started_at = item.started_at || new Date().toISOString()
    }
    if (event.type === 'tool_call.completed') {
      item.status = 'completed'
      item.completed_at = item.completed_at || new Date().toISOString()
    }
    if (event.type === 'tool_call.failed') {
      item.status = 'error'
      item.completed_at = item.completed_at || new Date().toISOString()
    }
    const nextItems = [...ensured.items]
    nextItems[ensured.index] = item
    nextView.items = nextItems
    return nextView
  }

  if (event.type.startsWith('commentary.') && event.step_id) {
    const ensured = ensureProcessedGroupItem(nextView.items, event.step_id)
    const group = {
      ...ensured.item,
      label: ensured.item?.label || 'Processed',
      commentary: Array.isArray(ensured.item?.commentary) ? [...ensured.item.commentary] : [],
    }

    const commentaryId = payload?.commentary_id || `cm_${event.sequence}`
    const commentaryIndex = group.commentary.findIndex(item => item?.commentary_id === commentaryId)
    const currentCommentary = commentaryIndex >= 0
      ? { ...group.commentary[commentaryIndex] }
      : {
          commentary_id: commentaryId,
          text: '',
          source: payload?.source || 'model',
          style: payload?.style || 'progress',
        }

    currentCommentary.source = payload?.source || currentCommentary.source
    currentCommentary.style = payload?.style || currentCommentary.style
    if (event.type === 'commentary.created') currentCommentary.text = payload?.text || ''
    if (event.type === 'commentary.delta') currentCommentary.text = `${currentCommentary.text || ''}${payload?.text || ''}`
    if (event.type === 'commentary.completed' && payload?.text) currentCommentary.text = payload.text

    if (commentaryIndex >= 0) {
      group.commentary[commentaryIndex] = currentCommentary
    } else {
      group.commentary.push(currentCommentary)
    }

    group.status = 'active'
    group.created_at = group.created_at || new Date().toISOString()

    const nextItems = [...ensured.items]
    nextItems[ensured.index] = group
    nextView.items = nextItems
    return nextView
  }

  if (event.type === 'message.completed') {
    nextView.status = 'completed'
    nextView.pending_approval = null
    nextView.items = nextView.items.map(item => (
      item?.type === 'processed_group' || item?.type === 'thinking_group'
        ? { ...item, status: 'completed' }
        : item
    ))
    return nextView
  }

  if (event.type === 'run.failed') {
    nextView.status = 'failed'
    return nextView
  }

  if (event.type === 'run.cancelled') {
    nextView.status = 'cancelled'
    return nextView
  }

  return nextView
}

async function consumeSseJsonStream(response, onChunk) {
  const reader = response.body?.getReader()
  if (!reader) throw new Error('流式响应不可用')

  const decoder = new TextDecoder()
  let buffer = ''
  let sawDone = false

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const rawEvents = buffer.split('\n\n')
    buffer = rawEvents.pop() || ''

    for (const rawEvent of rawEvents) {
      const dataLines = rawEvent
        .split('\n')
        .filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).trimStart())

      if (dataLines.length === 0) continue
      const raw = dataLines.join('\n').trim()
      if (raw === '[DONE]') {
        sawDone = true
        continue
      }

      try {
        onChunk(JSON.parse(raw))
      } catch {
        // ignore malformed chunks
      }
    }
  }

  return { sawDone }
}

async function consumeRunStream(response, {
  fallbackAssistantMessageId = null,
  abortController = null,
  inactivityTimeoutMs = STREAM_INACTIVITY_TIMEOUT_MS,
  onChunk,
} = {}) {
  let streamRunId = null
  let lastSequence = 0
  let streamAssistantId = fallbackAssistantMessageId
  let sawEvent = false
  let timedOut = false
  let inactivityTimer = null

  function clearInactivityTimer() {
    if (inactivityTimer !== null) {
      window.clearTimeout(inactivityTimer)
      inactivityTimer = null
    }
  }

  function resetInactivityTimer() {
    clearInactivityTimer()
    if (!abortController || inactivityTimeoutMs <= 0) return
    inactivityTimer = window.setTimeout(() => {
      timedOut = true
      abortController.abort()
    }, inactivityTimeoutMs)
  }

  resetInactivityTimer()

  try {
    const { sawDone } = await consumeSseJsonStream(response, chunk => {
      sawEvent = true
      resetInactivityTimer()

      if (chunk?.run?.id) {
        streamRunId = chunk.run.id
        lastSequence = Number(chunk.run.sequence) || lastSequence
      }
      if (chunk?.run?.assistant_message_id) {
        streamAssistantId = chunk.run.assistant_message_id
      }
      if (Number.isFinite(chunk?.sequence)) {
        lastSequence = Math.max(lastSequence, Number(chunk.sequence) || 0)
      }

      onChunk?.(chunk, {
        streamRunId,
        lastSequence,
        streamAssistantId,
      })
    })

    return {
      sawDone,
      sawEvent,
      timedOut,
      streamRunId,
      lastSequence,
      streamAssistantId,
    }
  } catch (error) {
    if (timedOut && isAbortError(error)) {
      return {
        sawDone: false,
        sawEvent,
        timedOut,
        streamRunId,
        lastSequence,
        streamAssistantId,
      }
    }
    throw error
  } finally {
    clearInactivityTimer()
  }
}

function rebuildMessageFromEvents(message, run, events) {
  if (!Array.isArray(events) || events.length === 0) return message

  let parts = []
  for (const event of events) {
    parts = applyRunEventToParts(parts, event)
  }

  const lastFailureEvent = [...events].reverse().find(event => event?.type === 'run.failed' || event?.type === 'run.cancelled')
  const completed = events.some(event => event?.type === 'message.completed')

  return {
    ...message,
    parts,
    content: aggregateTextFromParts(parts),
    status: lastFailureEvent
      ? 'failed'
      : completed
        ? 'completed'
        : ACTIVE_RUN_STATUSES.includes(run?.status)
          ? 'streaming'
          : message.status,
    error_message: lastFailureEvent?.payload?.error_message || message.error_message,
  }
}

async function recoverMessagesFromRuns(conversationId, messages) {
  if (!conversationId || !Array.isArray(messages) || messages.length === 0) return messages
  try {
    const runs = await listActiveRuns(conversationId)
    if (runs.length === 0) return messages

    const recoveries = await Promise.all(
      runs
        .filter(run => run?.assistant_message_id)
        .map(async run => {
          const eventsData = await api.getRunEvents(conversationId, run.id, { afterSequence: 0 })
          return {
            assistantMessageId: run.assistant_message_id,
            run,
            events: eventsData?.items || [],
          }
        }),
    )

    const recoveryByMessageId = new Map(
      recoveries.map(item => [item.assistantMessageId, item]),
    )

    return messages.map(message => {
      const recovery = recoveryByMessageId.get(message.id)
      if (!recovery) return message
      return rebuildMessageFromEvents(message, recovery.run, recovery.events)
    })
  } catch {
    return messages
  }
}

function sortConversations(items) {
  return [...items].sort((a, b) => {
    const timeA = new Date(a.updated_at || a.created_at || 0).getTime()
    const timeB = new Date(b.updated_at || b.created_at || 0).getTime()
    if (timeA !== timeB) return timeB - timeA
    return (b.id || 0) - (a.id || 0)
  })
}

export default function ChatPage() {
  const { user, logout } = useAuth()
  const { palette, mode, toggle, setPalette } = useTheme()
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [conversations, setConversations] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [messages, setMessages] = useState([])
  const [hasMoreMessages, setHasMoreMessages] = useState(false)
  const [nextBeforeMessageId, setNextBeforeMessageId] = useState(null)
  const [loadingEarlierMessages, setLoadingEarlierMessages] = useState(false)
  const [loadingConvs, setLoadingConvs] = useState(true)
  const [loadingMsgs, setLoadingMsgs] = useState(false)
  const [sending, setSending] = useState(false)
  const [regeneratingMessageId, setRegeneratingMessageId] = useState(null)
  const [deletingMessageId, setDeletingMessageId] = useState(null)
  const [switchingSiblingMessageId, setSwitchingSiblingMessageId] = useState(null)
  const [creatingBranchMessageId, setCreatingBranchMessageId] = useState(null)
  const [editingMessageId, setEditingMessageId] = useState(null)
  const [editingContent, setEditingContent] = useState('')
  const [editingMode, setEditingMode] = useState('update')
  const [editingSubmittingMessageId, setEditingSubmittingMessageId] = useState(null)
  const [mainStreamingAssistantId, setMainStreamingAssistantId] = useState(null)
  const [error, setError] = useState('')
  const [apiKeys, setApiKeys] = useState([])
  const [loadingKeys, setLoadingKeys] = useState(false)
  const [keysError, setKeysError] = useState('')
  const [modelOptions, setModelOptions] = useState([])
  const [loadingModels, setLoadingModels] = useState(false)
  const [savingModel, setSavingModel] = useState(false)
  const [modelError, setModelError] = useState('')
  const [pendingProvider, setPendingProvider] = useState('')
  const [pendingModel, setPendingModel] = useState('')
  const [defaultProvider, setDefaultProvider] = useState(() => window.localStorage.getItem('ai-chat.default-provider') || '')
  const [defaultModel, setDefaultModel] = useState(() => window.localStorage.getItem('ai-chat.default-model') || '')
  const [importing, setImporting] = useState(false)
  const [importStatus, setImportStatus] = useState(null)
  const [branchesByConversation, setBranchesByConversation] = useState({})
  const [loadingBranches, setLoadingBranches] = useState({})
  const [branchPanes, setBranchPanes] = useState([])
  const [activeRunsByAssistantId, setActiveRunsByAssistantId] = useState({})
  const [runViewsByRunId, setRunViewsByRunId] = useState({})
  const [runIdByAssistantMessageId, setRunIdByAssistantMessageId] = useState({})
  const [approvalActions, setApprovalActions] = useState({})
  const [messageTreeOpen, setMessageTreeOpen] = useState(false)
  const [messageTreeRefreshToken, setMessageTreeRefreshToken] = useState(0)
  const [branchPaneWidth, setBranchPaneWidth] = useState(() => {
    if (typeof window === 'undefined') return DEFAULT_BRANCH_PANE_WIDTH
    const storedWidth = Number(window.localStorage.getItem(BRANCH_PANE_WIDTH_STORAGE_KEY))
    return clampBranchPaneWidth(storedWidth, NaN)
  })
  const [exportMenuOpen, setExportMenuOpen] = useState(false)
  const [exportingKey, setExportingKey] = useState('')
  const [rulerAnchorTurnId, setRulerAnchorTurnId] = useState(null)
  const [rulerPosition, setRulerPosition] = useState(null)
  const bottomRef = useRef(null)
  const messageListRef = useRef(null)
  const userMessageElementRefs = useRef(new Map())
  const rulerScrollFrameRef = useRef(null)
  const activeConversationIdRef = useRef(null)
  const loadingEarlierMessagesRef = useRef(false)
  const exportMenuRef = useRef(null)
  const conversationLayoutRef = useRef(null)
  const resizeCleanupRef = useRef(null)
  const modelLoadSeqRef = useRef(0)
  const runStreamControllersRef = useRef(new Map())
  const runSequenceRef = useRef(new Map())
  const activeConv = conversations.find(c => c.id === activeId)
  const providerOptions = useMemo(
    () => apiKeys
      .filter(provider => provider.enabled)
      .map(provider => ({ value: String(provider.id), label: provider.display_name })),
    [apiKeys],
  )
  const selectedProviderLabel = providerOptions.find(option => option.value === pendingProvider)?.label || ''

  useEffect(() => {
    if (!exportMenuOpen) return undefined

    function handlePointerDown(event) {
      if (!exportMenuRef.current?.contains(event.target)) {
        setExportMenuOpen(false)
      }
    }

    document.addEventListener('mousedown', handlePointerDown)
    return () => document.removeEventListener('mousedown', handlePointerDown)
  }, [exportMenuOpen])

  useEffect(() => {
    window.localStorage.setItem(BRANCH_PANE_WIDTH_STORAGE_KEY, String(branchPaneWidth))
  }, [branchPaneWidth])

  useEffect(() => {
    function syncBranchPaneWidth() {
      const containerWidth = conversationLayoutRef.current?.getBoundingClientRect().width
      if (!containerWidth) return
      setBranchPaneWidth(current => clampBranchPaneWidth(current, containerWidth))
    }

    syncBranchPaneWidth()
    window.addEventListener('resize', syncBranchPaneWidth)
    return () => window.removeEventListener('resize', syncBranchPaneWidth)
  }, [branchPanes.length])

  const stopBranchPaneResize = useCallback(() => {
    if (!resizeCleanupRef.current) return
    resizeCleanupRef.current()
    resizeCleanupRef.current = null
  }, [])

  useEffect(() => stopBranchPaneResize, [stopBranchPaneResize])

  const startBranchPaneResize = useCallback((event) => {
    if (event.button !== 0) return

    const updateWidth = (clientX) => {
      const containerWidth = conversationLayoutRef.current?.getBoundingClientRect().width
      const containerRight = conversationLayoutRef.current?.getBoundingClientRect().right
      if (!containerWidth || !containerRight) return
      setBranchPaneWidth(clampBranchPaneWidth(containerRight - clientX, containerWidth))
    }

    stopBranchPaneResize()
    event.preventDefault()
    updateWidth(event.clientX)

    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect

    const handlePointerMove = (moveEvent) => {
      updateWidth(moveEvent.clientX)
    }

    const handlePointerUp = () => {
      stopBranchPaneResize()
    }

    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', handlePointerUp)
    window.addEventListener('pointercancel', handlePointerUp)

    resizeCleanupRef.current = () => {
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)
      window.removeEventListener('pointercancel', handlePointerUp)
    }
  }, [stopBranchPaneResize])

  const patchBranchPane = useCallback((paneId, updater) => {
    setBranchPanes(prev => prev.map(pane => (
      pane.id === paneId
        ? (typeof updater === 'function' ? updater(pane) : { ...pane, ...updater })
        : pane
    )))
  }, [])

  const loadBranches = useCallback(async (conversationId) => {
    if (!conversationId) return []

    setLoadingBranches(prev => ({ ...prev, [conversationId]: true }))
    try {
      const data = await api.getBranches(conversationId)
      const items = data || []
      setBranchesByConversation(prev => ({ ...prev, [conversationId]: items }))
      return items
    } finally {
      setLoadingBranches(prev => ({ ...prev, [conversationId]: false }))
    }
  }, [])

  const patchConversationBranchState = useCallback((conversationId, data) => {
    setConversations(prev => prev.map(conv => (
      conv.id === conversationId
        ? {
          ...conv,
          current_branch_id: data?.current_branch_id ?? conv.current_branch_id,
          current_leaf_message_id: data?.current_leaf_message_id ?? conv.current_leaf_message_id,
        }
        : conv
    )))
  }, [])

  const patchActiveRuns = useCallback((runs) => {
    const items = Array.isArray(runs) ? runs : []
    setActiveRunsByAssistantId(mapRunsByAssistantMessage(items))
    setRunIdByAssistantMessageId(mapRunIdsByAssistantMessage(items))
  }, [])

  const getRunViewForAssistant = useCallback((assistantMessageId) => {
    const runId = runIdByAssistantMessageId[assistantMessageId]
    if (!runId) return null
    return runViewsByRunId[runId] || null
  }, [runIdByAssistantMessageId, runViewsByRunId])

  const hydrateRunViewsForMessages = useCallback(async (conversationId, messageItems) => {
    if (!conversationId) {
      patchActiveRuns([])
      setRunViewsByRunId({})
      setRunIdByAssistantMessageId({})
      return
    }

    const assistantMessageIds = new Set(
      (Array.isArray(messageItems) ? messageItems : [])
        .filter(message => message?.role === 'assistant' && message?.id)
        .map(message => message.id),
    )

    const runsData = await api.getAgentRuns(conversationId)
    const allRuns = runsData?.items || []
    patchActiveRuns(allRuns)

    const visibleRuns = allRuns.filter(run => assistantMessageIds.has(run?.assistant_message_id))
    const views = await Promise.all(
      visibleRuns.map(async run => {
        try {
          const view = await api.getRunView(conversationId, run.id)
          return [run.id, view]
        } catch {
          return [run.id, null]
        }
      }),
    )

    setRunViewsByRunId(current => {
      const next = { ...current }
      for (const [runId, view] of views) {
        if (view) next[runId] = view
      }
      return next
    })
  }, [patchActiveRuns])

  const refreshMessages = useCallback(async (conversationId) => {
    const data = await api.getMessages(conversationId, { limit: INITIAL_MESSAGE_PAGE_SIZE })
    const items = data?.items || []
    setMessages(items)
    setHasMoreMessages(Boolean(data?.has_more))
    setNextBeforeMessageId(data?.next_before_message_id ?? null)
    await hydrateRunViewsForMessages(conversationId, items)
    patchConversationBranchState(conversationId, data)
    setMessageTreeRefreshToken(token => token + 1)
    return { ...data, items }
  }, [hydrateRunViewsForMessages, patchConversationBranchState])

  const loadEarlierMessages = useCallback(async () => {
    if (!activeId || !hasMoreMessages || !nextBeforeMessageId || loadingEarlierMessagesRef.current) return

    const list = messageListRef.current
    const previousHeight = list?.scrollHeight ?? 0
    const previousTop = list?.scrollTop ?? 0
    const conversationId = activeId

    loadingEarlierMessagesRef.current = true
    setLoadingEarlierMessages(true)
    try {
      const data = await api.getMessages(conversationId, {
        beforeMessageId: nextBeforeMessageId,
        limit: INITIAL_MESSAGE_PAGE_SIZE,
      })
      const earlierItems = data?.items || []
      await hydrateRunViewsForMessages(conversationId, earlierItems)
      if (conversationId !== activeConversationIdRef.current) return

      setMessages(current => {
        const currentIds = new Set(current.map(message => message.id))
        return [...earlierItems.filter(message => !currentIds.has(message.id)), ...current]
      })
      setHasMoreMessages(Boolean(data?.has_more))
      setNextBeforeMessageId(data?.next_before_message_id ?? null)
      requestAnimationFrame(() => {
        if (!list) return
        list.scrollTop = previousTop + list.scrollHeight - previousHeight
      })
    } catch (err) {
      setError(err.message || '加载更早消息失败')
    } finally {
      loadingEarlierMessagesRef.current = false
      setLoadingEarlierMessages(false)
    }
  }, [activeId, hasMoreMessages, hydrateRunViewsForMessages, nextBeforeMessageId])

  const refreshBranchPane = useCallback(async (conversationId, paneId, options = {}) => {
    const pane = branchPanes.find(item => item.id === paneId)
    if (!pane) return null

    const data = await api.getMessages(conversationId, {
      rootMessageId: pane.rootMessageId,
      leafMessageId: options.leafMessageId,
      expandLeaf: options.expandLeaf,
    })
    const items = data?.items || []
    await hydrateRunViewsForMessages(conversationId, items)

    patchBranchPane(paneId, current => ({
      ...current,
      loading: false,
      messages: items,
      currentLeafMessageId: data?.current_leaf_message_id ?? current.currentLeafMessageId,
      error: '',
    }))
    return { ...data, items }
  }, [branchPanes, hydrateRunViewsForMessages, patchBranchPane])

  const refreshBranchPanesSnapshot = useCallback(async (conversationId, panesSnapshot, deletedMessageId = null) => {
    if (!panesSnapshot.length) return

    const idsToClose = []
    const updates = []

    for (const pane of panesSnapshot) {
      if (pane.rootMessageId === deletedMessageId) {
        idsToClose.push(pane.id)
        continue
      }

      try {
        const data = await api.getMessages(conversationId, { rootMessageId: pane.rootMessageId })
        const items = data?.items || []
        await hydrateRunViewsForMessages(conversationId, items)
        updates.push({ paneId: pane.id, data: { ...data, items } })
      } catch {
        idsToClose.push(pane.id)
      }
    }

    if (idsToClose.length > 0) {
      setBranchPanes(prev => prev.filter(pane => !idsToClose.includes(pane.id)))
    }

    for (const { paneId, data } of updates) {
      patchBranchPane(paneId, current => ({
        ...current,
        loading: false,
        messages: data?.items || [],
        currentLeafMessageId: data?.current_leaf_message_id ?? current.currentLeafMessageId,
        error: '',
      }))
    }
  }, [hydrateRunViewsForMessages, patchBranchPane])

  const recoverMainInterruptedStream = useCallback(async (conversationId, runId, lastSequence) => {
    if (!conversationId || !runId) return
    try {
      await api.getRunEvents(conversationId, runId, {
        afterSequence: Number.isFinite(lastSequence) ? lastSequence : 0,
      })
    } catch {
      // best-effort fallback; final refresh still attempts recovery from snapshots + events
    }
    await refreshMessages(conversationId)
    await loadBranches(conversationId)
  }, [loadBranches, refreshMessages])

  const recoverBranchInterruptedStream = useCallback(async (conversationId, paneId, runId, lastSequence) => {
    if (!conversationId || !paneId || !runId) return
    try {
      await api.getRunEvents(conversationId, runId, {
        afterSequence: Number.isFinite(lastSequence) ? lastSequence : 0,
      })
    } catch {
      // best-effort fallback; pane refresh below rebuilds from persisted state
    }
    await refreshBranchPane(conversationId, paneId)
    await loadBranches(conversationId)
  }, [loadBranches, refreshBranchPane])

  const ensureMainAssistantPlaceholder = useCallback((assistantMessageId, anchorMessageId = null) => {
    if (!assistantMessageId) return
    setMessages(current => ensureMessageAfter(
      current,
      anchorMessageId,
      createStreamingAssistantMessage(assistantMessageId),
    ))
  }, [])

  const ensureBranchAssistantPlaceholder = useCallback((paneId, assistantMessageId, anchorMessageId = null) => {
    if (!paneId || !assistantMessageId) return
    patchBranchPane(paneId, current => ({
      ...current,
      streamingAssistantId: assistantMessageId,
      messages: ensureMessageAfter(
        current.messages,
        anchorMessageId,
        createStreamingAssistantMessage(assistantMessageId),
      ),
    }))
  }, [patchBranchPane])

  const applyRunEventToUi = useCallback((assistantMessageId, event) => {
    if (!assistantMessageId || !event) return
    // message.text.retracted is intentionally excluded: it only trims the
    // persisted parts_json for the final tool-round preamble, not the live
    // optimistic UI. Patching it here would blank out already-streamed text
    // mid-stream; the retracted text stays visible via the processed_group
    // (commentary) path and the eventual message.completed replaces parts wholesale.
    const shouldPatchMessage = event.type === 'message.text.delta'
      || event.type === 'message.completed'
      || event.type === 'run.failed'
      || event.type === 'run.cancelled'

    if (event.run_id) {
      setRunIdByAssistantMessageId(current => ({
        ...current,
        [assistantMessageId]: event.run_id,
      }))
      setRunViewsByRunId(current => ({
        ...current,
        [event.run_id]: applyRunEventToRunView(current[event.run_id], event),
      }))
    }

    if (shouldPatchMessage) {
      setMessages(current => current.map(message => (
        message.id === assistantMessageId
          ? applyRunEventToMessage(message, event)
          : message
      )))
    }
    setBranchPanes(current => current.map(pane => ({
      ...pane,
      currentLeafMessageId: event.type === 'message.completed' && pane.currentLeafMessageId !== assistantMessageId
        ? assistantMessageId
        : pane.currentLeafMessageId,
      streamingAssistantId: (event.type === 'message.completed' || event.type === 'run.failed' || event.type === 'run.cancelled')
        && pane.streamingAssistantId === assistantMessageId
        ? null
        : pane.streamingAssistantId,
      messages: Array.isArray(pane.messages)
        ? pane.messages.map(message => (
          shouldPatchMessage && message.id === assistantMessageId
            ? applyRunEventToMessage(message, event)
            : message
        ))
        : pane.messages,
    })))
  }, [])

  const subscribeToRunStream = useCallback(async ({ conversationId, runId, assistantMessageId, afterSequence = 0 }) => {
    if (!conversationId || !runId || runStreamControllersRef.current.has(runId)) return

    const controller = new AbortController()
    runStreamControllersRef.current.set(runId, { controller, conversationId, assistantMessageId })

    try {
      const res = await fetch(
        api.getRunStreamPath(conversationId, runId, {
          afterSequence: Number.isFinite(afterSequence) ? afterSequence : 0,
        }),
        {
          credentials: 'include',
          headers: Number.isFinite(afterSequence) && afterSequence > 0
            ? { 'Last-Event-ID': String(afterSequence) }
            : {},
          signal: controller.signal,
        },
      )

      if (!res.ok) {
        throw new Error(`订阅 run ${runId} 事件流失败`)
      }

      const streamState = await consumeRunStream(res, {
        fallbackAssistantMessageId: assistantMessageId,
        abortController: controller,
        onChunk: (chunk, state) => {
          runSequenceRef.current.set(runId, state.lastSequence)
          if (state.streamAssistantId) {
            ensureMainAssistantPlaceholder(state.streamAssistantId)
          }
          if (state.streamRunId && state.streamAssistantId) {
            setRunIdByAssistantMessageId(current => ({
              ...current,
              [state.streamAssistantId]: state.streamRunId,
            }))
            setRunViewsByRunId(current => ({
              ...current,
              [state.streamRunId]: current[state.streamRunId] || createRunView(state.streamRunId, state.streamAssistantId),
            }))
          }

          const event = buildRunEventFromChunk(chunk, state.streamAssistantId)
          if (event) {
            applyRunEventToUi(event.assistant_message_id, event)
          }
        },
      })

      if (streamState.timedOut) {
        await recoverMainInterruptedStream(conversationId, runId, streamState.lastSequence)
      }
    } catch {
      // stream discovery effect will retry while the run remains active
    } finally {
      const entry = runStreamControllersRef.current.get(runId)
      if (entry?.controller === controller) {
        runStreamControllersRef.current.delete(runId)
      }
    }
  }, [applyRunEventToUi, ensureMainAssistantPlaceholder, recoverMainInterruptedStream])

  const loadApiKeys = useCallback(async () => {
    setKeysError('')
    setLoadingKeys(true)
    try {
      const data = await api.getProviders()
      const providers = Array.isArray(data) ? data : []
      setApiKeys(providers)
      return providers
    } catch (err) {
      setKeysError(err.message || '鍔犺浇 API Keys 澶辫触')
      return []
    } finally {
      setLoadingKeys(false)
    }
  }, [])

  const loadProviderModels = useCallback(async (providerValue) => {
    const providerId = parseProviderId(providerValue)
    if (!providerId) {
      modelLoadSeqRef.current += 1
      setModelOptions([])
      setModelError('')
      setLoadingModels(false)
      return []
    }

    const requestSeq = modelLoadSeqRef.current + 1
    modelLoadSeqRef.current = requestSeq
    setModelError('')
    setLoadingModels(true)

    try {
      // Model selectors only read the locally cached catalogue. Remote discovery
      // is deliberately limited to the explicit refresh action in settings.
      const data = await api.getProviderInstanceModels(providerId)
      const items = Array.isArray(data)
        ? data
          .filter(model => model.enabled && model.remote_available)
          .map(model => ({
            id: model.model_id,
            name: model.display_name_override || model.remote_display_name || model.model_id,
          }))
        : []
      if (modelLoadSeqRef.current === requestSeq) {
        setModelOptions(items)
        setModelError('')
      }
      return items
    } catch (err) {
      if (modelLoadSeqRef.current === requestSeq) {
      setModelError(err.message || '鍔犺浇妯″瀷澶辫触')
      }
      return []
    } finally {
      if (modelLoadSeqRef.current === requestSeq) {
        setLoadingModels(false)
      }
    }
  }, [])

  const resetMainEdit = useCallback(() => {
    setEditingMessageId(null)
    setEditingContent('')
    setEditingMode('update')
  }, [])

  const copyToClipboard = useCallback(async (content) => {
    const text = content ?? ''
    if (!navigator?.clipboard?.writeText) {
      throw new Error('当前环境不支持复制')
    }
    await navigator.clipboard.writeText(text)
  }, [])

  const fetchConversations = useCallback(async () => {
    const data = await api.getConversations()
    return data || []
  }, [])

  const selectConversation = useCallback(async (conversationId) => {
    activeConversationIdRef.current = conversationId
    setActiveId(conversationId)
    setBranchPanes([])
    setActiveRunsByAssistantId({})
    setApprovalActions({})
    resetMainEdit()
    if (!conversationId) {
      setMessages([])
      setHasMoreMessages(false)
      setNextBeforeMessageId(null)
      return
    }

    setLoadingMsgs(true)
    setMessages([])
    setHasMoreMessages(false)
    setNextBeforeMessageId(null)
    try {
      await Promise.all([
        refreshMessages(conversationId),
        loadBranches(conversationId),
      ])
    } catch {
      setMessages([])
    } finally {
      setLoadingMsgs(false)
    }
  }, [loadBranches, refreshMessages, resetMainEdit])

  const openSettings = useCallback(async () => {
    setSettingsOpen(true)
    const providers = await loadApiKeys()
    const providerValue = defaultProvider || String(providers.find(provider => provider.enabled && provider.is_default)?.id || providers.find(provider => provider.enabled)?.id || '')
    await loadProviderModels(providerValue)
  }, [defaultProvider, loadApiKeys, loadProviderModels])

  const changeDefaultProvider = useCallback(async (nextProvider) => {
    setDefaultProvider(nextProvider)
    setDefaultModel('')
    window.localStorage.setItem('ai-chat.default-provider', nextProvider)
    window.localStorage.removeItem('ai-chat.default-model')
    await loadProviderModels(nextProvider)
  }, [loadProviderModels])

  const changeDefaultModel = useCallback(nextModel => {
    setDefaultModel(nextModel)
    if (nextModel) window.localStorage.setItem('ai-chat.default-model', nextModel)
    else window.localStorage.removeItem('ai-chat.default-model')
  }, [])

  const createApiKey = useCallback(async (data) => {
    const result = await api.createProvider(data)
    await loadApiKeys()
    return result
  }, [loadApiKeys])

  const deleteApiKey = useCallback(async (id) => {
    const result = await api.deleteProvider(id)
    await loadApiKeys()
    if (pendingProvider === String(id)) {
      await loadProviderModels(null)
    }
    return result
  }, [loadApiKeys, loadProviderModels, pendingProvider])

  const testApiKey = useCallback(async (id) => {
    const result = await api.testProvider(id)
    await loadApiKeys()
    return result
  }, [loadApiKeys])

  const updateProvider = useCallback(async (id, data) => {
    const result = await api.updateProvider(id, data)
    await loadApiKeys()
    return result
  }, [loadApiKeys])

  const syncProviderModels = useCallback(async (id) => {
    const result = await api.syncProviderModels(id)
    if (pendingProvider === String(id)) await loadProviderModels(id)
    return result
  }, [loadProviderModels, pendingProvider])

  const updateProviderModel = useCallback(async (id, modelId, data) => {
    const result = await api.updateProviderModel(id, modelId, data)
    if (pendingProvider === String(id)) await loadProviderModels(id)
    return result
  }, [loadProviderModels, pendingProvider])

  useEffect(() => {
    void loadApiKeys()
  }, [loadApiKeys])

  useEffect(() => {
    let cancelled = false

    fetchConversations()
      .then(async data => {
        if (cancelled) return
        const items = data || []
        setConversations(items)
        if (items.length > 0) {
          await selectConversation(items[0].id)
        }
      })
      .catch(() => {
        if (!cancelled) setConversations([])
      })
      .finally(() => {
        if (!cancelled) setLoadingConvs(false)
      })

    return () => {
      cancelled = true
    }
  }, [fetchConversations, selectConversation])

  useLayoutEffect(() => {
    if (loadingMsgs || loadingEarlierMessagesRef.current) return
    const messageList = messageListRef.current
    if (messageList) {
      messageList.scrollTop = messageList.scrollHeight
    }
  }, [loadingMsgs, messages])

  useEffect(() => {
    queueMicrotask(() => {
      setPendingProvider(activeConv?.provider_id ? String(activeConv.provider_id) : defaultProvider)
      setPendingModel(activeConv?.model || '')
      setModelError('')
    })
  }, [activeId, activeConv?.model, activeConv?.provider_id, defaultProvider])

  useEffect(() => {
    const provider = activeConv?.provider_id ? String(activeConv.provider_id) : pendingProvider
    queueMicrotask(() => {
      void loadProviderModels(provider)
    })
  }, [activeConv?.provider_id, loadProviderModels, pendingProvider])

  useEffect(() => {
    if (providerOptions.length === 0) return

    const fallbackProvider = providerOptions.find(option => (
      apiKeys.find(provider => String(provider.id) === option.value)?.is_default
    )) || providerOptions[0]

    if (!providerOptions.some(option => option.value === defaultProvider)) {
      setDefaultProvider(fallbackProvider.value)
      window.localStorage.setItem('ai-chat.default-provider', fallbackProvider.value)
    }
    if (!activeConv?.provider_id && !providerOptions.some(option => option.value === pendingProvider)) {
      setPendingProvider(fallbackProvider.value)
    }
  }, [activeConv?.provider_id, apiKeys, defaultProvider, pendingProvider, providerOptions])

  useEffect(() => {
    if (!activeId) return undefined

    let disposed = false
    let inFlight = false
    let intervalId = null
    const runControllers = runStreamControllersRef.current
    const runSequences = runSequenceRef.current
    const hasForegroundRun = sending || regeneratingMessageId !== null || branchPanes.some(pane => pane.sending)

    function stopSyncLoop() {
      if (intervalId === null) return
      window.clearInterval(intervalId)
      intervalId = null
    }

    function ensureSyncLoop() {
      if (intervalId !== null) return
      intervalId = window.setInterval(() => {
        void syncRunStreams()
      }, 1000)
    }

    async function syncRunStreams() {
      if (disposed || inFlight || hasForegroundRun) return
      inFlight = true
      try {
        const runs = await listActiveRuns(activeId)
        if (disposed) return
        patchActiveRuns(runs)

        const activeRunIds = new Set(runs.map(run => run.id))
        for (const [runId, entry] of runControllers.entries()) {
          if (entry?.conversationId !== activeId || !activeRunIds.has(runId)) {
            entry?.controller?.abort()
            runControllers.delete(runId)
            runSequences.delete(runId)
          }
        }

        for (const run of runs) {
          if (!run?.assistant_message_id || runControllers.has(run.id)) continue
          const afterSequence = Number.isFinite(runSequences.get(run.id))
            ? runSequences.get(run.id)
            : Number(run.last_sequence) || 0
          void subscribeToRunStream({
            conversationId: activeId,
            runId: run.id,
            assistantMessageId: run.assistant_message_id,
            afterSequence,
          })
        }

        if (runs.length > 0) {
          ensureSyncLoop()
        } else {
          stopSyncLoop()
        }
      } catch {
        // Ignore discovery failures. Active loops will retry on the next tick.
      } finally {
        inFlight = false
      }
    }

    void syncRunStreams()

    return () => {
      disposed = true
      setActiveRunsByAssistantId({})
      setRunViewsByRunId({})
      setRunIdByAssistantMessageId({})
      stopSyncLoop()
      for (const [runId, entry] of runControllers.entries()) {
        if (entry?.conversationId === activeId) {
          entry?.controller?.abort()
          runControllers.delete(runId)
          runSequences.delete(runId)
        }
      }
    }
  }, [activeId, branchPanes, patchActiveRuns, regeneratingMessageId, sending, subscribeToRunStream])

  const setApprovalActionPending = useCallback((assistantMessageId, toolCallRef, pending) => {
    const key = `${assistantMessageId}:${toolCallRef}`
    setApprovalActions(current => {
      if (pending) return { ...current, [key]: true }
      if (!(key in current)) return current
      const next = { ...current }
      delete next[key]
      return next
    })
  }, [])

  const isApprovalActionPending = useCallback((assistantMessageId, toolCallRef) => (
    !!approvalActions[`${assistantMessageId}:${toolCallRef}`]
  ), [approvalActions])

  const resolveActiveRunForAssistant = useCallback(async (conversationId, assistantMessageId) => {
    if (!conversationId || !assistantMessageId) return null
    const cached = activeRunsByAssistantId[assistantMessageId]
    if (cached) return cached
    const runs = await listActiveRuns(conversationId)
    patchActiveRuns(runs)
    return runs.find(run => run?.assistant_message_id === assistantMessageId) || null
  }, [activeRunsByAssistantId, patchActiveRuns])

  const submitToolApproval = useCallback(async ({
    conversationId,
    assistantMessageId,
    toolCallRef,
    approved,
  }) => {
    if (!conversationId || !assistantMessageId || !toolCallRef) {
      throw new Error('缺少审批所需的 run 信息')
    }

    setApprovalActionPending(assistantMessageId, toolCallRef, true)
    try {
      const run = await resolveActiveRunForAssistant(conversationId, assistantMessageId)
      if (!run?.id) {
        throw new Error('未找到待审批的运行实例，请刷新后重试')
      }

      const payload = { tool_call_ref: toolCallRef }
      const updatedRun = approved
        ? await api.approveRunTool(conversationId, run.id, payload)
        : await api.denyRunTool(conversationId, run.id, payload)

      if (ACTIVE_RUN_STATUSES.includes(updatedRun?.status)) {
        setActiveRunsByAssistantId(current => ({
          ...current,
          [assistantMessageId]: updatedRun,
        }))
      } else {
        setActiveRunsByAssistantId(current => {
          if (!(assistantMessageId in current)) return current
          const next = { ...current }
          delete next[assistantMessageId]
          return next
        })
      }
      return updatedRun
    } finally {
      setApprovalActionPending(assistantMessageId, toolCallRef, false)
    }
  }, [resolveActiveRunForAssistant, setApprovalActionPending])

  const approveMainToolCall = useCallback(async (message, toolCallRef) => {
    if (!activeId || !message?.id || !toolCallRef) return
    setError('')
    try {
      await submitToolApproval({
        conversationId: activeId,
        assistantMessageId: message.id,
        toolCallRef,
        approved: true,
      })
    } catch (e) {
      setError(e.message || '批准工具调用失败，请重试')
    }
  }, [activeId, submitToolApproval])

  const denyMainToolCall = useCallback(async (message, toolCallRef) => {
    if (!activeId || !message?.id || !toolCallRef) return
    setError('')
    try {
      await submitToolApproval({
        conversationId: activeId,
        assistantMessageId: message.id,
        toolCallRef,
        approved: false,
      })
    } catch (e) {
      setError(e.message || '拒绝工具调用失败，请重试')
    }
  }, [activeId, submitToolApproval])

  const approveBranchToolCall = useCallback(async (paneId, message, toolCallRef) => {
    if (!activeId || !paneId || !message?.id || !toolCallRef) return
    patchBranchPane(paneId, { error: '' })
    try {
      await submitToolApproval({
        conversationId: activeId,
        assistantMessageId: message.id,
        toolCallRef,
        approved: true,
      })
    } catch (e) {
      patchBranchPane(paneId, { error: e.message || '批准工具调用失败，请重试' })
    }
  }, [activeId, patchBranchPane, submitToolApproval])

  const denyBranchToolCall = useCallback(async (paneId, message, toolCallRef) => {
    if (!activeId || !paneId || !message?.id || !toolCallRef) return
    patchBranchPane(paneId, { error: '' })
    try {
      await submitToolApproval({
        conversationId: activeId,
        assistantMessageId: message.id,
        toolCallRef,
        approved: false,
      })
    } catch (e) {
      patchBranchPane(paneId, { error: e.message || '拒绝工具调用失败，请重试' })
    }
  }, [activeId, patchBranchPane, submitToolApproval])

  const createConversation = useCallback(async (title = '新对话', model = undefined, providerValue = undefined) => {
    const payload = { title }
    const providerId = parseProviderId(providerValue || defaultProvider)
    if (providerId) payload.provider_id = providerId
    if (model || defaultModel) payload.model = model || defaultModel
    const conv = await api.createConversation(payload)
    setConversations(prev => [conv, ...prev])
    await selectConversation(conv.id)
    return conv
  }, [defaultModel, defaultProvider, selectConversation])

  const reloadConversations = useCallback(async (nextActiveId = null) => {
    const items = await fetchConversations()
    setConversations(items)
    const resolvedActiveId = nextActiveId ?? items[0]?.id ?? null

    if (resolvedActiveId) {
      await selectConversation(resolvedActiveId)
    } else {
      setActiveId(null)
      setMessages([])
      setBranchPanes([])
    }

    return items
  }, [fetchConversations, selectConversation])

  const importConversation = useCallback(async (file) => {
    if (!file || importing) return

    setImporting(true)
    setImportStatus(null)

    try {
      const result = await api.importMarkdownConversation(file)
      const importedId = result?.conversation?.id ?? null
      await reloadConversations(importedId)
      setImportStatus({
        type: 'success',
        title: result?.conversation?.title || file.name,
        message: `已导入 ${result?.message_count ?? 0} 条消息`,
        meta: {
          messageCount: result?.message_count ?? 0,
          ignoredCount: result?.ignored_count ?? 0,
          warningCount: result?.warnings?.length ?? 0,
        },
      })
    } catch (err) {
      setImportStatus({
        type: 'error',
        title: file.name,
        message: err.message || '导入 Markdown 失败',
      })
    } finally {
      setImporting(false)
    }
  }, [importing, reloadConversations])

  const renameConversation = useCallback(async (id, title) => {
    const updated = await api.updateConversation(id, { title })
    setConversations(prev => sortConversations(prev.map(conv => (conv.id === updated.id ? updated : conv))))
    return updated
  }, [])

  const activateBranch = useCallback(async (conversationId, branchId) => {
    activeConversationIdRef.current = conversationId
    setActiveId(conversationId)
    setBranchPanes([])
    resetMainEdit()
    setLoadingMsgs(true)
    setMessages([])
    setHasMoreMessages(false)
    setNextBeforeMessageId(null)
    try {
      const data = await api.activateBranch(conversationId, branchId, { limit: INITIAL_MESSAGE_PAGE_SIZE })
      setMessages(data?.items || [])
      setHasMoreMessages(Boolean(data?.has_more))
      setNextBeforeMessageId(data?.next_before_message_id ?? null)
      patchConversationBranchState(conversationId, data)
      await loadBranches(conversationId)
      return data
    } finally {
      setLoadingMsgs(false)
    }
  }, [loadBranches, patchConversationBranchState, resetMainEdit])

  const renameBranch = useCallback(async (conversationId, branchId, title) => {
    const updated = await api.updateBranch(conversationId, branchId, { title })
    setBranchesByConversation(prev => ({
      ...prev,
      [conversationId]: (prev[conversationId] || []).map(branch => (
        branch.id === updated.id ? updated : branch
      )),
    }))
    return updated
  }, [])

  const deleteBranch = useCallback(async (conversationId, branchId) => {
    const branches = branchesByConversation[conversationId] || []
    const deletedBranchIds = collectBranchTreeIds(branches, branchId)

    await api.deleteBranch(conversationId, branchId)
    setBranchPanes(prev => prev.filter(pane => (
      pane.branchId === null || !deletedBranchIds.has(pane.branchId)
    )))
    await loadBranches(conversationId)
    if (activeId === conversationId) {
      await refreshMessages(conversationId)
    }
  }, [activeId, branchesByConversation, loadBranches, refreshMessages])

  const archiveBranch = useCallback(async (conversationId, branchId) => {
    await api.archiveBranch(conversationId, branchId)
    setBranchPanes(prev => prev.filter(pane => pane.branchId !== branchId))
    await loadBranches(conversationId)
    if (activeId === conversationId) {
      await refreshMessages(conversationId)
    }
  }, [activeId, loadBranches, refreshMessages])

  const deleteConversation = useCallback(async (id) => {
    await api.deleteConversation(id)
    const remaining = conversations.filter(c => c.id !== id)
    setConversations(remaining)
    setBranchesByConversation(prev => {
      const next = { ...prev }
      delete next[id]
      return next
    })
    if (activeId === id) {
      await selectConversation(remaining[0]?.id ?? null)
    }
  }, [activeId, conversations, selectConversation])

  const exportConversation = useCallback(async ({ key, format, scope }) => {
    if (!activeConv || exportingKey) return

    setError('')
    setExportingKey(key)
    try {
      await api.exportConversation(activeConv.id, { format, scope })
      setExportMenuOpen(false)
    } catch (e) {
      setError(e.message || '导出失败，请重试')
    } finally {
      setExportingKey('')
    }
  }, [activeConv, exportingKey])

  const changeConversationModel = useCallback(async (nextModel) => {
    setPendingModel(nextModel)
    setModelError('')

    if (!activeConv || !nextModel || nextModel === activeConv.model) {
      return
    }

    setSavingModel(true)
    try {
      const updated = await api.updateConversation(activeConv.id, { model: nextModel })
      setConversations(prev => sortConversations(prev.map(conv => (conv.id === updated.id ? updated : conv))))
    } catch (e) {
      setPendingModel(activeConv.model || '')
      setModelError(e.message || '保存模型失败')
    } finally {
      setSavingModel(false)
    }
  }, [activeConv])

  const changeConversationProvider = useCallback(async (nextProvider) => {
    setPendingProvider(nextProvider)
    setPendingModel('')
    setModelError('')
    void loadProviderModels(nextProvider)

    const providerId = parseProviderId(nextProvider)
    if (!activeConv || !providerId || providerId === activeConv.provider_id) {
      return
    }

    setSavingModel(true)
    try {
      const updated = await api.updateConversation(activeConv.id, { provider_id: providerId, model: null })
      setConversations(prev => sortConversations(prev.map(conv => (conv.id === updated.id ? updated : conv))))
    } catch (e) {
      setPendingProvider(activeConv.provider_id ? String(activeConv.provider_id) : defaultProvider)
      setPendingModel(activeConv.model || '')
      setModelError(e.message || '保存 Provider 失败')
    } finally {
      setSavingModel(false)
    }
  }, [activeConv, defaultProvider, loadProviderModels])

  const openBranchPane = useCallback(async (sourceMessage, paneIdToMark = null) => {
    if (!activeId || sourceMessage?.role !== 'assistant') return

    const nextPane = createBranchPane(sourceMessage)
    if (paneIdToMark) {
      patchBranchPane(paneIdToMark, { creatingBranchMessageId: sourceMessage.id })
    } else {
      setCreatingBranchMessageId(sourceMessage.id)
    }

    setBranchPanes([nextPane])

    try {
      const sourcePane = paneIdToMark ? branchPanes.find(item => item.id === paneIdToMark) : null
      const parentBranchId = sourcePane?.branchId ?? activeConv?.current_branch_id ?? null
      const branch = await api.createBranch(activeId, {
        ...(parentBranchId ? { parent_branch_id: parentBranchId } : {}),
        forked_from_message_id: sourceMessage.id,
      })
      patchBranchPane(nextPane.id, { branchId: branch.id })
      await loadBranches(activeId)
      const data = await api.getMessages(activeId, { rootMessageId: sourceMessage.id })
      patchBranchPane(nextPane.id, {
        loading: false,
        messages: data?.items || [sourceMessage],
        currentLeafMessageId: data?.current_leaf_message_id ?? sourceMessage.id,
        error: '',
      })
    } catch (e) {
      patchBranchPane(nextPane.id, {
        loading: false,
        error: e.message || '鍒涘缓鍒嗘敮澶辫触',
      })
    } finally {
      if (paneIdToMark) {
        patchBranchPane(paneIdToMark, { creatingBranchMessageId: null })
      } else {
        setCreatingBranchMessageId(null)
      }
    }
  }, [activeConv, activeId, branchPanes, loadBranches, patchBranchPane])

  const closeBranchPane = useCallback((paneId) => {
    setBranchPanes(prev => prev.filter(pane => pane.id !== paneId))
  }, [])

  const copyMainMessage = useCallback(async (message) => {
    try {
      await copyToClipboard(message?.content || '')
    } catch (e) {
      setError(e.message || '复制失败，请重试')
    }
  }, [copyToClipboard])

  const startMainEdit = useCallback((message) => {
    setError('')
    setEditingMessageId(message.id)
    setEditingContent(message.content || '')
    setEditingMode('update')
  }, [])

  const submitMainEdit = useCallback(async (messageId) => {
    if (!activeId || !editingContent.trim() || editingSubmittingMessageId !== null) return

    setError('')
    setEditingSubmittingMessageId(messageId)
    const panesSnapshot = branchPanes

    try {
      await api.editMessage(activeId, messageId, {
        content: editingContent.trim(),
        mode: editingMode,
        ...(activeConv?.current_branch_id ? { branch_id: activeConv.current_branch_id } : {}),
      })
      await refreshMessages(activeId)
      await loadBranches(activeId)
      await refreshBranchPanesSnapshot(activeId, panesSnapshot)
      resetMainEdit()
    } catch (e) {
      setError(e.message || '编辑消息失败，请重试')
    } finally {
      setEditingSubmittingMessageId(null)
    }
  }, [
    activeId,
    branchPanes,
    editingContent,
    editingMode,
    editingSubmittingMessageId,
    activeConv,
    loadBranches,
    refreshBranchPanesSnapshot,
    refreshMessages,
    resetMainEdit,
  ])

  const sendMessage = useCallback(async (content) => {
    if (!content.trim() || sending || regeneratingMessageId !== null || switchingSiblingMessageId !== null) return
    setError('')

    let convId = activeId
    let branchId = activeConv?.current_branch_id ?? null
    if (!convId) {
      try {
        const conv = await createConversation(content.slice(0, 40), pendingModel || undefined, pendingProvider || undefined)
        convId = conv.id
        branchId = conv.current_branch_id ?? null
      } catch {
        setError('鍒涘缓瀵硅瘽澶辫触')
        return
      }
    }

    const userMsg = {
      id: Date.now(),
      role: 'user',
      content,
      status: 'completed',
      created_at: new Date().toISOString(),
    }
    setMessages(prev => [...prev, userMsg])
    setSending(true)
    let streamRunId = null
    let lastSequence = 0
    let sawDone = false
    let streamAssistantId = null
    let sawEvent = false

    try {
      const controller = new AbortController()
      const res = await fetch(`/api/conversations/${convId}/messages/stream`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, ...(branchId ? { branch_id: branchId } : {}) }),
        signal: controller.signal,
      })

      if (res.status === 404 || res.status === 405) {
        await api.sendMessage(convId, { content, ...(branchId ? { branch_id: branchId } : {}) })
        await refreshMessages(convId)
        await loadBranches(convId)
        return
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err?.error?.message || err?.detail || '发送失败')
      }

      let streamError = ''
      const streamState = await consumeRunStream(res, {
        abortController: controller,
        onChunk: (chunk, state) => {
          streamRunId = state.streamRunId
          lastSequence = state.lastSequence
          streamAssistantId = state.streamAssistantId
          if (state.streamAssistantId) {
            setMainStreamingAssistantId(state.streamAssistantId)
            ensureMainAssistantPlaceholder(state.streamAssistantId, userMsg.id)
          }
          const event = buildRunEventFromChunk(chunk, state.streamAssistantId)
          if (event) {
            applyRunEventToUi(event.assistant_message_id, event)
          }
          if (chunk.error) {
            streamError = chunk.error
            setError(chunk.error)
          }
        },
      })

      sawDone = streamState.sawDone
      sawEvent = streamState.sawEvent

      if (!sawDone && streamRunId) {
        await recoverMainInterruptedStream(convId, streamRunId, lastSequence)
      } else {
        await refreshMessages(convId)
        await loadBranches(convId)
      }
      if (!streamError && !sawEvent) setError('妯″瀷娌℃湁杩斿洖鍐呭')
    } catch (e) {
      setError(e.message || '发送失败，请重试')
      if (convId) {
        try {
          await recoverMainInterruptedStream(convId, streamRunId, lastSequence)
        } catch {
          setMessages(prev => prev.filter(m => m.id !== userMsg.id))
        }
      } else {
        setMessages(prev => prev.filter(m => m.id !== userMsg.id))
      }
    } finally {
      setSending(false)
      setMainStreamingAssistantId(null)
    }
  }, [activeConv, activeId, applyRunEventToUi, createConversation, ensureMainAssistantPlaceholder, loadBranches, pendingModel, pendingProvider, recoverMainInterruptedStream, refreshMessages, regeneratingMessageId, sending, switchingSiblingMessageId])

  const regenerateMainMessage = useCallback(async (messageId) => {
    if (!activeId || sending || regeneratingMessageId !== null || switchingSiblingMessageId !== null) return
    setError('')
    setRegeneratingMessageId(messageId)
    const branchId = activeConv?.current_branch_id ?? null
    let streamRunId = null
    let lastSequence = 0
    let sawDone = false
    let streamAssistantId = null
    let sawEvent = false

    try {
      const controller = new AbortController()
      const res = await fetch(`/api/conversations/${activeId}/messages/${messageId}/regenerate/stream`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(branchId ? { branch_id: branchId } : {}),
        signal: controller.signal,
      })

      if (res.status === 404 || res.status === 405) {
        await api.regenerateMessage(activeId, messageId, branchId ? { branch_id: branchId } : {})
        await refreshMessages(activeId)
        await loadBranches(activeId)
        return
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err?.error?.message || err?.detail || '閲嶆柊鐢熸垚澶辫触')
      }

      let streamError = ''
      const streamState = await consumeRunStream(res, {
        abortController: controller,
        onChunk: (chunk, state) => {
          streamRunId = state.streamRunId
          lastSequence = state.lastSequence
          streamAssistantId = state.streamAssistantId
          if (state.streamAssistantId) {
            setMainStreamingAssistantId(state.streamAssistantId)
            ensureMainAssistantPlaceholder(state.streamAssistantId, messageId)
          }
          const event = buildRunEventFromChunk(chunk, state.streamAssistantId)
          if (event) {
            applyRunEventToUi(event.assistant_message_id, event)
          }
          if (chunk.error) {
            streamError = chunk.error
            setError(chunk.error)
          }
        },
      })

      sawDone = streamState.sawDone
      sawEvent = streamState.sawEvent

      if (!sawDone && streamRunId) {
        await recoverMainInterruptedStream(activeId, streamRunId, lastSequence)
      } else {
        await refreshMessages(activeId)
        await loadBranches(activeId)
      }
      if (!streamError && !sawEvent) setError('妯″瀷娌℃湁杩斿洖鍐呭')
    } catch (e) {
      setError(e.message || '閲嶆柊鐢熸垚澶辫触锛岃閲嶈瘯')
      await recoverMainInterruptedStream(activeId, streamRunId, lastSequence)
    } finally {
      setRegeneratingMessageId(null)
      setMainStreamingAssistantId(null)
    }
  }, [activeConv, activeId, applyRunEventToUi, ensureMainAssistantPlaceholder, loadBranches, recoverMainInterruptedStream, refreshMessages, regeneratingMessageId, sending, switchingSiblingMessageId])

  const switchMainSibling = useCallback(async (targetMessageId) => {
    if (!activeId || !targetMessageId || sending || regeneratingMessageId !== null || switchingSiblingMessageId !== null) return
    setError('')
    setSwitchingSiblingMessageId(targetMessageId)
    try {
      await api.activateMessageBranch(activeId, targetMessageId)
      await refreshMessages(activeId)
      await loadBranches(activeId)
    } catch (e) {
      setError(e.message || '鍒囨崲鍒嗘敮澶辫触锛岃閲嶈瘯')
    } finally {
      setSwitchingSiblingMessageId(null)
    }
  }, [activeId, loadBranches, refreshMessages, regeneratingMessageId, sending, switchingSiblingMessageId])

  const activateMessageTreePath = useCallback(async (messageId) => {
    if (!activeId || !messageId || sending || regeneratingMessageId !== null || switchingSiblingMessageId !== null) return
    setError('')
    setSwitchingSiblingMessageId(messageId)
    try {
      await api.activateMessageBranch(activeId, messageId, { exact: true })
      await refreshMessages(activeId)
      await loadBranches(activeId)
    } catch (e) {
      setError(e.message || '切换 path 失败，请重试')
      throw e
    } finally {
      setSwitchingSiblingMessageId(null)
    }
  }, [activeId, loadBranches, refreshMessages, regeneratingMessageId, sending, switchingSiblingMessageId])

  const openBranchPaneFromTree = useCallback(async (message) => {
    await openBranchPane(message)
    setMessageTreeOpen(false)
  }, [openBranchPane])

  const deleteMainMessage = useCallback(async (messageId) => {
    if (
      !activeId
      || sending
      || regeneratingMessageId !== null
      || switchingSiblingMessageId !== null
      || creatingBranchMessageId !== null
      || deletingMessageId !== null
    ) return

    setError('')
    setDeletingMessageId(messageId)
    const panesSnapshot = branchPanes

    try {
      await api.deleteMessage(activeId, messageId)
      await refreshMessages(activeId)
      await loadBranches(activeId)
      await refreshBranchPanesSnapshot(activeId, panesSnapshot, messageId)
    } catch (e) {
      setError(e.message || '删除消息失败，请重试')
    } finally {
      setDeletingMessageId(null)
    }
  }, [
    activeId,
    branchPanes,
    creatingBranchMessageId,
    deletingMessageId,
    loadBranches,
    refreshBranchPanesSnapshot,
    refreshMessages,
    regeneratingMessageId,
    sending,
    switchingSiblingMessageId,
  ])

  const setPaneContextMode = useCallback((paneId, contextMode) => {
    patchBranchPane(paneId, { contextMode })
  }, [patchBranchPane])

  const setPaneContextMessageCount = useCallback((paneId, contextMessageCount) => {
    if (!Number.isInteger(contextMessageCount) || contextMessageCount < 1) return
    patchBranchPane(paneId, { contextMessageCount })
  }, [patchBranchPane])

  const copyBranchMessage = useCallback(async (paneId, message) => {
    try {
      await copyToClipboard(message?.content || '')
    } catch (e) {
      patchBranchPane(paneId, { error: e.message || '复制失败，请重试' })
    }
  }, [copyToClipboard, patchBranchPane])

  const startBranchEdit = useCallback((paneId, message) => {
    patchBranchPane(paneId, {
      editingMessageId: message.id,
      editingContent: message.content || '',
      editingMode: 'update',
      error: '',
    })
  }, [patchBranchPane])

  const cancelBranchEdit = useCallback((paneId) => {
    patchBranchPane(paneId, {
      editingMessageId: null,
      editingContent: '',
      editingMode: 'update',
    })
  }, [patchBranchPane])

  const sendBranchMessage = useCallback(async (paneId, content) => {
    const pane = branchPanes.find(item => item.id === paneId)
    if (!content.trim() || !activeId || !pane || pane.sending || pane.regeneratingMessageId || pane.switchingSiblingMessageId) return

    const userMsg = {
      id: Date.now(),
      role: 'user',
      content,
      status: 'completed',
      created_at: new Date().toISOString(),
    }
    const payload = {
      content,
      parent_id: pane.currentLeafMessageId,
      ...(pane.branchId ? { branch_id: pane.branchId } : {}),
      activate_branch: false,
      ...buildBranchContextPayload(pane),
    }

    patchBranchPane(paneId, current => ({
      ...current,
      messages: [...current.messages, userMsg],
      sending: true,
      streamingAssistantId: null,
      error: '',
    }))
    let streamRunId = null
    let lastSequence = 0
    let sawDone = false
    let streamAssistantId = null
    let sawEvent = false
    try {
      const controller = new AbortController()
      const res = await fetch(`/api/conversations/${activeId}/messages/stream`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,
      })

      if (res.status === 404 || res.status === 405) {
        const fallback = await api.sendMessage(activeId, payload)
        await refreshBranchPane(activeId, paneId, { leafMessageId: fallback?.current_leaf_message_id })
        await loadBranches(activeId)
        return
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err?.error?.message || err?.detail || '发送失败')
      }

      let streamError = ''
      const streamState = await consumeRunStream(res, {
        abortController: controller,
        onChunk: (chunk, state) => {
          streamRunId = state.streamRunId
          lastSequence = state.lastSequence
          streamAssistantId = state.streamAssistantId
          if (state.streamAssistantId) {
            ensureBranchAssistantPlaceholder(paneId, state.streamAssistantId, userMsg.id)
          }
          const event = buildRunEventFromChunk(chunk, state.streamAssistantId)
          if (event) {
            applyRunEventToUi(event.assistant_message_id, event)
          }
          if (chunk.error) {
            streamError = chunk.error
            patchBranchPane(paneId, { error: chunk.error })
          }
        },
      })

      sawDone = streamState.sawDone
      sawEvent = streamState.sawEvent

      if (!sawDone && streamRunId) {
        await recoverBranchInterruptedStream(activeId, paneId, streamRunId, lastSequence)
      } else {
        await refreshBranchPane(activeId, paneId)
        await loadBranches(activeId)
      }
      if (!streamError && !sawEvent) patchBranchPane(paneId, { error: '模型没有返回内容' })
    } catch (e) {
      patchBranchPane(paneId, current => ({
        ...current,
        messages: current.messages.filter(message => message.id !== userMsg.id),
        error: e.message || '发送失败，请重试',
      }))
    } finally {
      patchBranchPane(paneId, { sending: false, streamingAssistantId: null })
    }
  }, [activeId, applyRunEventToUi, branchPanes, ensureBranchAssistantPlaceholder, loadBranches, patchBranchPane, recoverBranchInterruptedStream, refreshBranchPane])

  const regenerateBranchMessage = useCallback(async (paneId, messageId) => {
    const pane = branchPanes.find(item => item.id === paneId)
    if (!activeId || !pane || pane.sending || pane.regeneratingMessageId || pane.switchingSiblingMessageId) return

    patchBranchPane(paneId, {
      regeneratingMessageId: messageId,
      error: '',
      streamingAssistantId: null,
    })
    let streamRunId = null
    let lastSequence = 0
    let sawDone = false
    let streamAssistantId = null
    let sawEvent = false
    try {
      const payload = {
        ...(pane.branchId ? { branch_id: pane.branchId } : {}),
        activate_branch: false,
        ...buildBranchContextPayload(pane),
      }
      const controller = new AbortController()
      const res = await fetch(`/api/conversations/${activeId}/messages/${messageId}/regenerate/stream`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,
      })

      if (res.status === 404 || res.status === 405) {
        const fallback = await api.regenerateMessage(activeId, messageId, payload)
        await refreshBranchPane(activeId, paneId, { leafMessageId: fallback?.current_leaf_message_id })
        await loadBranches(activeId)
        return
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err?.error?.message || err?.detail || '閲嶆柊鐢熸垚澶辫触锛岃閲嶈瘯')
      }

      const streamState = await consumeRunStream(res, {
        abortController: controller,
        onChunk: (chunk, state) => {
          streamRunId = state.streamRunId
          lastSequence = state.lastSequence
          streamAssistantId = state.streamAssistantId
          if (state.streamAssistantId) {
            ensureBranchAssistantPlaceholder(paneId, state.streamAssistantId, messageId)
          }
          const event = buildRunEventFromChunk(chunk, state.streamAssistantId)
          if (event) {
            applyRunEventToUi(event.assistant_message_id, event)
          }
          if (chunk.error) {
            patchBranchPane(paneId, { error: chunk.error })
          }
        },
      })

      sawDone = streamState.sawDone
      sawEvent = streamState.sawEvent

      if (!sawDone && streamRunId) {
        await recoverBranchInterruptedStream(activeId, paneId, streamRunId, lastSequence)
      } else {
        await refreshBranchPane(activeId, paneId)
        await loadBranches(activeId)
      }
      if (!sawEvent) {
        patchBranchPane(paneId, { error: '妯″瀷娌℃湁杩斿洖鍐呭' })
      }
    } catch (e) {
      patchBranchPane(paneId, { error: e.message || '重新生成失败，请重试' })
    } finally {
      patchBranchPane(paneId, {
        regeneratingMessageId: null,
        streamingAssistantId: null,
      })
    }
  }, [activeId, applyRunEventToUi, branchPanes, ensureBranchAssistantPlaceholder, loadBranches, patchBranchPane, recoverBranchInterruptedStream, refreshBranchPane])

  const switchBranchPaneSibling = useCallback(async (paneId, targetMessageId) => {
    const pane = branchPanes.find(item => item.id === paneId)
    if (!activeId || !pane || !targetMessageId || pane.sending || pane.regeneratingMessageId || pane.switchingSiblingMessageId) return

    patchBranchPane(paneId, { switchingSiblingMessageId: targetMessageId, error: '' })
    try {
      await refreshBranchPane(activeId, paneId, {
        leafMessageId: targetMessageId,
        expandLeaf: true,
      })
    } catch (e) {
      patchBranchPane(paneId, { error: e.message || '切换分支失败，请重试' })
    } finally {
      patchBranchPane(paneId, { switchingSiblingMessageId: null })
    }
  }, [activeId, branchPanes, patchBranchPane, refreshBranchPane])

  const deleteBranchMessage = useCallback(async (paneId, messageId) => {
    const pane = branchPanes.find(item => item.id === paneId)
    if (
      !activeId
      || !pane
      || pane.sending
      || pane.regeneratingMessageId
      || pane.switchingSiblingMessageId
      || pane.creatingBranchMessageId
      || pane.deletingMessageId
    ) return

    patchBranchPane(paneId, { deletingMessageId: messageId, error: '' })
    const panesSnapshot = branchPanes

    try {
      await api.deleteMessage(activeId, messageId)
      await refreshMessages(activeId)
      await loadBranches(activeId)
      await refreshBranchPanesSnapshot(activeId, panesSnapshot, messageId)
    } catch (e) {
      patchBranchPane(paneId, { error: e.message || '删除消息失败，请重试' })
    } finally {
      patchBranchPane(paneId, { deletingMessageId: null })
    }
  }, [activeId, branchPanes, loadBranches, patchBranchPane, refreshBranchPanesSnapshot, refreshMessages])

  const submitBranchEdit = useCallback(async (paneId, messageId) => {
    const pane = branchPanes.find(item => item.id === paneId)
    if (
      !activeId
      || !pane
      || !pane.editingContent.trim()
      || pane.editingSubmittingMessageId !== null
    ) return

    const panesSnapshot = branchPanes.filter(item => item.id !== paneId)
    patchBranchPane(paneId, { editingSubmittingMessageId: messageId, error: '' })

    try {
      const result = await api.editMessage(activeId, messageId, {
        content: pane.editingContent.trim(),
        mode: pane.editingMode,
        ...(pane.branchId ? { branch_id: pane.branchId } : {}),
        ...buildBranchContextPayload(pane),
      })
      await refreshMessages(activeId)
      await loadBranches(activeId)
      await refreshBranchPane(
        activeId,
        paneId,
        pane.editingMode === 'branch' && result?.current_leaf_message_id
          ? { leafMessageId: result.current_leaf_message_id }
          : {},
      )
      await refreshBranchPanesSnapshot(activeId, panesSnapshot)
      patchBranchPane(paneId, {
        editingMessageId: null,
        editingContent: '',
        editingMode: 'update',
      })
    } catch (e) {
      patchBranchPane(paneId, { error: e.message || '编辑消息失败，请重试' })
    } finally {
      patchBranchPane(paneId, { editingSubmittingMessageId: null })
    }
  }, [
    activeId,
    branchPanes,
    loadBranches,
    patchBranchPane,
    refreshBranchPane,
    refreshBranchPanesSnapshot,
    refreshMessages,
  ])

  const modelChoices = [
    ...(pendingModel && !modelOptions.some(option => option.id === pendingModel)
      ? [{ id: pendingModel }]
      : []),
    ...modelOptions,
  ]
  const mainBusy = sending
    || regeneratingMessageId !== null
    || switchingSiblingMessageId !== null
    || creatingBranchMessageId !== null
    || deletingMessageId !== null
    || editingSubmittingMessageId !== null
  const regenerationCutoffIndex = regeneratingMessageId === null
    ? -1
    : messages.findIndex(msg => msg.id === regeneratingMessageId)
  const streamingAssistantIndex = mainStreamingAssistantId === null
    ? -1
    : messages.findIndex(message => message.id === mainStreamingAssistantId)
  const displayedMessages = regenerationCutoffIndex >= 0
    ? messages.slice(0, Math.max(regenerationCutoffIndex + 1, streamingAssistantIndex + 1))
    : messages
  const conversationTurns = useMemo(
    () => buildConversationTurns(displayedMessages),
    [displayedMessages],
  )

  const updateRulerAnchor = useCallback(() => {
    const container = messageListRef.current
    if (!container || conversationTurns.length < CHAT_RULER_WINDOW_SIZE) return

    const containerRect = container.getBoundingClientRect()
    const viewportMiddle = containerRect.top + (containerRect.height / 2)
    let closestTurnId = null
    let closestDistance = Number.POSITIVE_INFINITY

    conversationTurns.forEach(turn => {
      const element = userMessageElementRefs.current.get(turn.userMessage.id)
      if (!element) return
      const rect = element.getBoundingClientRect()
      const distance = Math.abs((rect.top + (rect.height / 2)) - viewportMiddle)
      if (distance < closestDistance) {
        closestDistance = distance
        closestTurnId = turn.userMessage.id
      }
    })

    if (closestTurnId) {
      setRulerAnchorTurnId(current => current === closestTurnId ? current : closestTurnId)
    }
  }, [conversationTurns])

  const updateRulerPosition = useCallback(() => {
    const container = messageListRef.current
    if (!container || conversationTurns.length < CHAT_RULER_WINDOW_SIZE) {
      setRulerPosition(null)
      return
    }

    const rect = container.getBoundingClientRect()
    const nextPosition = {
      top: Math.round(rect.top + (rect.height / 2)),
      left: Math.round(rect.left + 10),
    }

    setRulerPosition(current => (
      current?.top === nextPosition.top && current?.left === nextPosition.left
        ? current
        : nextPosition
    ))
  }, [conversationTurns.length])

  useLayoutEffect(() => {
    updateRulerAnchor()
  }, [updateRulerAnchor])

  useLayoutEffect(() => {
    updateRulerPosition()
    const container = messageListRef.current
    if (!container) return undefined

    const observer = new ResizeObserver(updateRulerPosition)
    observer.observe(container)
    window.addEventListener('resize', updateRulerPosition)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', updateRulerPosition)
    }
  }, [updateRulerPosition])

  useEffect(() => () => {
    if (rulerScrollFrameRef.current) cancelAnimationFrame(rulerScrollFrameRef.current)
  }, [])

  const handleMessageListScroll = useCallback((event) => {
    if (event.currentTarget.scrollTop <= LOAD_EARLIER_THRESHOLD_PX) {
      void loadEarlierMessages()
    }

    if (rulerScrollFrameRef.current) return
    rulerScrollFrameRef.current = requestAnimationFrame(() => {
      rulerScrollFrameRef.current = null
      updateRulerAnchor()
    })
  }, [loadEarlierMessages, updateRulerAnchor])

  const jumpToTurn = useCallback((messageId) => {
    const container = messageListRef.current
    const target = userMessageElementRefs.current.get(messageId)
    if (!container || !target) return

    setRulerAnchorTurnId(messageId)
    const containerRect = container.getBoundingClientRect()
    const targetRect = target.getBoundingClientRect()
    const targetTop = container.scrollTop
      + targetRect.top
      - containerRect.top
      - ((container.clientHeight - targetRect.height) / 2)
    container.scrollTo({ top: Math.max(0, targetTop), behavior: 'smooth' })
  }, [])

  const chatView = (
    <>
      <div className="flex h-screen overflow-hidden" style={{ background: 'var(--bg-base)', color: 'var(--text-primary)' }}>
        <Sidebar
          open={sidebarOpen}
          conversations={conversations}
          branchesByConversation={branchesByConversation}
          loadingBranches={loadingBranches}
          activeId={activeId}
          activeBranchId={activeConv?.current_branch_id ?? null}
          loading={loadingConvs}
          importLoading={importing}
          importStatus={importStatus}
          palette={palette}
          mode={mode}
          onSelect={id => { void selectConversation(id); if (window.innerWidth < 768) setSidebarOpen(false) }}
          onBranchSelect={(conversationId, branchId) => {
            void activateBranch(conversationId, branchId)
            if (window.innerWidth < 768) setSidebarOpen(false)
          }}
          onNew={createConversation}
          onImport={importConversation}
          onDelete={deleteConversation}
          onRename={renameConversation}
          onRenameBranch={renameBranch}
          onDeleteBranch={deleteBranch}
          onClose={() => setSidebarOpen(false)}
          onOpenSettings={openSettings}
        />

        {sidebarOpen && (
          <div
            className="fixed inset-0 z-20 md:hidden"
            style={{ background: 'var(--overlay)' }}
            onClick={() => setSidebarOpen(false)}
          />
        )}

        <div className="flex flex-col flex-1 min-w-0">
          <header
            className="flex items-center gap-3 px-4 py-3 shrink-0 backdrop-blur-sm"
            style={{ borderBottom: '1px solid var(--border)', background: 'color-mix(in srgb, var(--bg-base) 85%, transparent)' }}
          >
            <button
              onClick={() => setSidebarOpen(v => !v)}
              className="p-1.5 rounded-lg transition"
              style={{ color: 'var(--text-muted)' }}
              onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-primary)'; e.currentTarget.style.background = 'var(--bg-elevated)' }}
              onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.background = 'transparent' }}
              aria-label="切换侧边栏"
            >
              {sidebarOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
            </button>
            <h2 className="text-sm font-medium truncate flex-1" style={{ color: 'var(--text-primary)' }}>
              {activeConv?.title || 'AI Chat'}
            </h2>
            {activeConv?.model && (
              <span
                className="text-xs px-2 py-1 rounded-lg shrink-0"
                style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)' }}
              >
                {activeConv.model}
              </span>
            )}
            <button
              type="button"
              onClick={() => activeConv && setMessageTreeOpen(true)}
              disabled={!activeConv}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition disabled:opacity-50 disabled:cursor-not-allowed"
              style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
              aria-label="消息树"
              title="消息树"
            >
              <Network className="w-4 h-4" />
              <span className="hidden text-sm sm:inline">消息树</span>
            </button>
            <div className="relative shrink-0" ref={exportMenuRef}>
              <button
                type="button"
                onClick={() => activeConv && setExportMenuOpen(open => !open)}
                disabled={!activeConv || !!exportingKey}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
                aria-haspopup="menu"
                aria-expanded={exportMenuOpen}
                aria-label="导出对话"
              >
                {exportingKey ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
                <span className="text-sm">导出</span>
                <ChevronDown className="w-4 h-4" />
              </button>
              {exportMenuOpen && activeConv && (
                <div
                  className="absolute right-0 top-full mt-2 w-56 rounded-xl p-1 z-20"
                  style={{
                    background: 'color-mix(in srgb, var(--bg-surface) 94%, transparent)',
                    border: '1px solid var(--border)',
                    boxShadow: '0 18px 48px rgba(0, 0, 0, 0.18)',
                    backdropFilter: 'blur(18px)',
                  }}
                  role="menu"
                >
                  {EXPORT_OPTIONS.map(option => (
                    <button
                      key={option.key}
                      type="button"
                      onClick={() => { void exportConversation(option) }}
                      disabled={!!exportingKey}
                      className="w-full text-left px-3 py-2 rounded-lg text-sm transition disabled:opacity-50"
                      style={{
                        color: 'var(--text-secondary)',
                        background: exportingKey === option.key ? 'var(--bg-elevated)' : 'transparent',
                      }}
                      role="menuitem"
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </header>

          <div className="flex flex-1 min-h-0 overflow-hidden" ref={conversationLayoutRef}>
            <div className="flex flex-col flex-1 min-w-0">
              <div
                ref={messageListRef}
                onScroll={handleMessageListScroll}
                className="relative flex-1 overflow-y-auto scrollbar-thin"
              >
                {loadingEarlierMessages && (
                  <div className="absolute inset-x-0 top-2 z-10 flex justify-center pointer-events-none">
                    <Loader2 className="w-4 h-4 animate-spin" style={{ color: 'var(--text-muted)' }} />
                  </div>
                )}
                {loadingMsgs ? (
                  <div className="flex items-center justify-center h-full">
                    <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--text-muted)' }} />
                  </div>
                ) : messages.length === 0 ? (
                  <EmptyState onSend={sendMessage} />
                ) : (
                  <div className="relative min-h-full">
                    <ConversationRuler
                      turns={conversationTurns}
                      anchorTurnId={rulerAnchorTurnId}
                      onJump={jumpToTurn}
                      position={rulerPosition}
                    />
                    <div className="max-w-3xl mx-auto px-4 py-6 space-y-1">
                      {displayedMessages.map(msg => {
                        return (
                          <div
                            key={msg.id}
                            ref={msg.role === 'user'
                              ? element => {
                                  if (element) userMessageElementRefs.current.set(msg.id, element)
                                  else userMessageElementRefs.current.delete(msg.id)
                                }
                              : undefined}
                          >
                            <MessageBubble
                            message={msg}
                            runView={getRunViewForAssistant(msg.id)}
                            onCopy={msg.role === 'system' ? undefined : () => { void copyMainMessage(msg) }}
                            onEdit={msg.role === 'user' ? () => { void startMainEdit(msg) } : undefined}
                            onRegenerate={msg.role === 'system' ? undefined : () => { void regenerateMainMessage(msg.id) }}
                            onDelete={msg.role === 'system' ? undefined : () => { void deleteMainMessage(msg.id) }}
                            onCreateBranch={msg.role === 'assistant' ? () => { void openBranchPane(msg) } : undefined}
                            onPrevSibling={msg.previous_sibling_id ? () => { void switchMainSibling(msg.previous_sibling_id) } : undefined}
                            onNextSibling={msg.next_sibling_id ? () => { void switchMainSibling(msg.next_sibling_id) } : undefined}
                            isEditing={editingMessageId === msg.id}
                            editDraft={editingMessageId === msg.id ? editingContent : ''}
                            editMode={editingMode}
                            onEditDraftChange={setEditingContent}
                            onEditModeChange={setEditingMode}
                            onEditCancel={resetMainEdit}
                            onEditSubmit={() => { void submitMainEdit(msg.id) }}
                            isEditSubmitting={editingSubmittingMessageId === msg.id}
                            disableActions={mainBusy}
                            isRegenerating={regeneratingMessageId === msg.id}
                            isDeleting={deletingMessageId === msg.id}
                            isCreatingBranch={creatingBranchMessageId === msg.id}
                            onApproveToolCall={msg.role === 'assistant' ? toolCallRef => { void approveMainToolCall(msg, toolCallRef) } : undefined}
                            onDenyToolCall={msg.role === 'assistant' ? toolCallRef => { void denyMainToolCall(msg, toolCallRef) } : undefined}
                            isApprovalSubmitting={toolCallRef => isApprovalActionPending(msg.id, toolCallRef)}
                            canApproveToolCall={toolCallRef => (
                              getRunViewForAssistant(msg.id)?.pending_approval?.tool_call_ref === toolCallRef
                            )}
                            />
                          </div>
                        )
                      })}
                    {(sending || regeneratingMessageId !== null) && !mainStreamingAssistantId && (
                      <div className="flex gap-3 py-3">
                        <div
                          className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 text-xs font-bold"
                          style={{ background: 'var(--accent)', color: 'var(--text-primary)' }}
                        >
                          AI
                        </div>
                        <div className="flex items-center gap-1 pt-2">
                          {[0, 1, 2].map(i => (
                            <span
                              key={i}
                              className="w-1.5 h-1.5 rounded-full animate-bounce"
                              style={{ background: 'var(--text-muted)', animationDelay: `${i * 0.15}s` }}
                            />
                          ))}
                        </div>
                      </div>
                    )}
                    {error && (
                      <div
                        className="flex items-center gap-2 text-sm py-2 px-3 rounded-xl"
                        style={{ background: 'var(--error-bg)', border: '1px solid var(--error-border)', color: 'var(--error-text)' }}
                      >
                        <AlertCircle className="w-4 h-4 shrink-0" />
                        {error}
                      </div>
                    )}
                    <div ref={bottomRef} />
                    </div>
                  </div>
                )}
              </div>

              <ChatInput
                onSend={sendMessage}
                disabled={mainBusy}
                providerValue={pendingProvider}
                providerOptions={providerOptions}
                modelValue={pendingModel}
                modelOptions={modelChoices}
                modelProvider={selectedProviderLabel}
                modelLoading={loadingModels}
                modelSaving={savingModel}
                modelError={modelError}
                onProviderChange={changeConversationProvider}
                onModelChange={changeConversationModel}
              />
            </div>

            {branchPanes.length > 0 && (
              <>
                <div
                  role="separator"
                  aria-orientation="vertical"
                  aria-label="调整分支视图宽度"
                  onPointerDown={startBranchPaneResize}
                  className="shrink-0 flex items-stretch justify-center cursor-col-resize group"
                  style={{ width: '14px', touchAction: 'none' }}
                >
                  <div
                    className="h-full transition-colors"
                    style={{
                      width: '1px',
                      background: 'transparent',
                      boxShadow: '0 0 0 3px transparent',
                    }}
                  />
                </div>

                <div
                  className="h-full shrink-0 p-4 flex flex-col gap-4"
                  style={{
                    width: `${branchPaneWidth}px`,
                    background: 'color-mix(in srgb, var(--bg-surface) 78%, transparent)',
                  }}
                >
                  {branchPanes.map(pane => (
                    <div key={pane.id} className="flex-1 min-h-0">
                      <BranchPane
                        pane={{
                          ...pane,
                          busy: pane.loading
                            || pane.sending
                            || pane.regeneratingMessageId !== null
                            || pane.switchingSiblingMessageId !== null
                            || pane.creatingBranchMessageId !== null
                            || pane.deletingMessageId !== null
                            || pane.editingSubmittingMessageId !== null,
                        }}
                        getRunView={getRunViewForAssistant}
                        onClose={() => closeBranchPane(pane.id)}
                        onContextModeChange={contextMode => setPaneContextMode(pane.id, contextMode)}
                        onContextMessageCountChange={count => setPaneContextMessageCount(pane.id, count)}
                        onCopy={message => copyBranchMessage(pane.id, message)}
                        onEdit={message => startBranchEdit(pane.id, message)}
                        onEditCancel={() => cancelBranchEdit(pane.id)}
                        onEditSubmit={messageId => submitBranchEdit(pane.id, messageId)}
                        onEditDraftChange={content => patchBranchPane(pane.id, { editingContent: content })}
                        onEditModeChange={mode => patchBranchPane(pane.id, { editingMode: mode })}
                        onSend={content => sendBranchMessage(pane.id, content)}
                        onRegenerate={messageId => regenerateBranchMessage(pane.id, messageId)}
                        onDelete={messageId => deleteBranchMessage(pane.id, messageId)}
                        onCreateBranch={message => openBranchPane(message, pane.id)}
                        onPrevSibling={message => switchBranchPaneSibling(pane.id, message.previous_sibling_id)}
                        onNextSibling={message => switchBranchPaneSibling(pane.id, message.next_sibling_id)}
                        onApproveToolCall={(message, toolCallRef) => approveBranchToolCall(pane.id, message, toolCallRef)}
                        onDenyToolCall={(message, toolCallRef) => denyBranchToolCall(pane.id, message, toolCallRef)}
                        isApprovalSubmitting={(messageId, toolCallRef) => isApprovalActionPending(messageId, toolCallRef)}
                        canApproveToolCall={(messageId, toolCallRef) => (
                          getRunViewForAssistant(messageId)?.pending_approval?.tool_call_ref === toolCallRef
                        )}
                        providerValue={pendingProvider}
                        providerOptions={providerOptions}
                        modelValue={pendingModel}
                        modelOptions={modelChoices}
                        modelProvider={selectedProviderLabel}
                        modelLoading={loadingModels}
                        modelSaving={savingModel}
                        modelError={modelError}
                        onProviderChange={changeConversationProvider}
                        onModelChange={changeConversationModel}
                      />
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <MessageTreePanel
        open={messageTreeOpen}
        conversationId={activeId}
        conversationTitle={activeConv?.title || 'AI Chat'}
        refreshToken={messageTreeRefreshToken}
        branches={activeId ? (branchesByConversation[activeId] || []) : []}
        currentBranchId={activeConv?.current_branch_id ?? null}
        branchesLoading={activeId ? !!loadingBranches[activeId] : false}
        onClose={() => setMessageTreeOpen(false)}
        onActivatePath={activateMessageTreePath}
        onOpenBranch={openBranchPaneFromTree}
        onActivateBranch={branchId => activateBranch(activeId, branchId)}
        onRenameBranch={(branchId, title) => renameBranch(activeId, branchId, title)}
        onArchiveBranch={branchId => archiveBranch(activeId, branchId)}
        onDeleteBranch={branchId => deleteBranch(activeId, branchId)}
      />

    </>
  )

  return settingsOpen ? (
    <SettingsPage
      user={user}
      palette={palette}
      mode={mode}
      onToggleTheme={toggle}
      onSetPalette={setPalette}
      onLogout={logout}
      onBack={() => setSettingsOpen(false)}
      apiKeys={apiKeys}
      loadingKeys={loadingKeys}
      keysError={keysError}
      onRefreshKeys={loadApiKeys}
      onCreateProvider={createApiKey}
      onDeleteProvider={deleteApiKey}
      onTestProvider={testApiKey}
      onUpdateProvider={updateProvider}
      onLoadProviderModels={id => api.getProviderInstanceModels(id)}
      onSyncProviderModels={syncProviderModels}
      onUpdateProviderModel={updateProviderModel}
      defaultProvider={defaultProvider}
      defaultModel={defaultModel}
      providerOptions={providerOptions}
      modelOptions={modelOptions}
      modelLoading={loadingModels}
      modelError={modelError}
      onDefaultProviderChange={changeDefaultProvider}
      onDefaultModelChange={changeDefaultModel}
    />
  ) : chatView
}
