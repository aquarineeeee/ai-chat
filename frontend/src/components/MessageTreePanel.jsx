import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  AlertCircle,
  Archive,
  ArrowRight,
  Bot,
  Check,
  Crosshair,
  GitBranch,
  Loader2,
  LocateFixed,
  Network,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
  User,
  X,
} from 'lucide-react'
import { api } from '../api'

const NODE_WIDTH = 220
const NODE_HEIGHT = 112
const X_GAP = 72
const Y_GAP = 168
const ROOT_GAP = 120

function formatTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function roleLabel(role) {
  if (role === 'user') return '用户'
  if (role === 'assistant') return '助理'
  return '系统'
}

function markerLabel(marker) {
  const title = marker.title || marker.auto_title || `分支 ${marker.id}`
  return marker.marker_type === 'fork' ? `fork: ${title}` : `leaf: ${title}`
}

function compactPreview(text) {
  return (text || '').trim() || '空消息'
}

function computeMatches(tree, query) {
  const nodes = tree?.nodes || []
  const normalizedQuery = query.trim().toLowerCase()
  if (!normalizedQuery) return new Set()
  return new Set(
    nodes
      .filter(node => `${node.preview || ''} ${node.model || ''} ${roleLabel(node.role)}`.toLowerCase().includes(normalizedQuery))
      .map(node => node.id),
  )
}

function buildLayout(tree) {
  const nodes = tree?.nodes || []
  const edges = tree?.edges || []
  const byId = new Map(nodes.map(node => [node.id, node]))
  const activePath = new Set(tree?.active_path || [])

  const childrenByParent = new Map()
  nodes.forEach(node => {
    // A node whose parent is absent (e.g. trimmed by the server's node cap) is
    // treated as a root so it is laid out instead of stacking at the origin.
    const realParent = node.parent_id ?? null
    const key = realParent !== null && byId.has(realParent) ? realParent : null
    childrenByParent.set(key, [...(childrenByParent.get(key) || []), node])
  })
  childrenByParent.forEach(children => {
    children.sort((a, b) => {
      if ((a.sibling_index || 0) !== (b.sibling_index || 0)) return (a.sibling_index || 0) - (b.sibling_index || 0)
      return new Date(a.created_at || 0).getTime() - new Date(b.created_at || 0).getTime() || a.id - b.id
    })
  })

  const rootsToPlace = childrenByParent.get(null) || []

  const widths = new Map()
  const measure = (nodeId) => {
    if (widths.has(nodeId)) return widths.get(nodeId)
    const children = childrenByParent.get(nodeId) || []
    if (children.length === 0) {
      widths.set(nodeId, NODE_WIDTH)
      return NODE_WIDTH
    }
    const childWidth = children.reduce((sum, child, index) => (
      sum + measure(child.id) + (index > 0 ? X_GAP : 0)
    ), 0)
    const width = Math.max(NODE_WIDTH, childWidth)
    widths.set(nodeId, width)
    return width
  }

  const positions = new Map()
  const place = (node, left, depth) => {
    const width = measure(node.id)
    positions.set(node.id, {
      x: left + width / 2 - NODE_WIDTH / 2,
      y: depth * Y_GAP,
    })

    const children = childrenByParent.get(node.id) || []
    const childTotalWidth = children.reduce((sum, child, index) => (
      sum + measure(child.id) + (index > 0 ? X_GAP : 0)
    ), 0)
    let childLeft = left + Math.max(0, (width - childTotalWidth) / 2)
    children.forEach(child => {
      place(child, childLeft, depth + 1)
      childLeft += measure(child.id) + X_GAP
    })
  }

  let nextLeft = 0
  rootsToPlace.forEach(root => {
    place(root, nextLeft, 0)
    nextLeft += measure(root.id) + ROOT_GAP
  })

  const flowNodes = nodes.map(node => ({
    id: String(node.id),
    type: 'messageNode',
    position: positions.get(node.id) || { x: 0, y: 0 },
    sourcePosition: 'bottom',
    targetPosition: 'top',
    data: {
      node,
      isActivePath: activePath.has(node.id),
    },
    style: { width: NODE_WIDTH, height: NODE_HEIGHT },
  }))

  const flowEdges = edges.map(edge => ({
    id: edge.id,
    source: String(edge.source),
    target: String(edge.target),
    type: 'smoothstep',
    markerEnd: {
      type: MarkerType.ArrowClosed,
      width: 16,
      height: 16,
      color: edge.is_active_path ? 'var(--accent)' : 'var(--border)',
    },
    style: {
      stroke: edge.is_active_path ? 'var(--accent)' : 'var(--border)',
      strokeWidth: edge.is_active_path ? 2.4 : 1.4,
    },
    animated: edge.is_active_path,
    data: edge,
  }))

  return { flowNodes, flowEdges }
}

function MessageTreeNode({ data }) {
  const { node, isActivePath, isSearchMatch } = data
  const isUser = node.role === 'user'
  const Icon = isUser ? User : Bot
  const markerCount = node.branch_markers?.length || 0

  return (
    <div
      className="h-full rounded-lg px-3 py-2 text-left shadow-sm transition"
      style={{
        background: isActivePath ? 'color-mix(in srgb, var(--accent-subtle) 58%, var(--bg-surface))' : 'var(--bg-surface)',
        border: `1px solid ${isSearchMatch ? 'var(--accent)' : isActivePath ? 'var(--accent-border)' : 'var(--border)'}`,
        color: 'var(--text-primary)',
        boxShadow: node.is_current_leaf ? '0 0 0 2px var(--accent-border)' : undefined,
      }}
    >
      <div className="flex items-center gap-2">
        <div
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md"
          style={{ background: isUser ? 'var(--bubble-bg)' : 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
        >
          <Icon className="h-3.5 w-3.5" />
        </div>
        <span className="truncate text-xs font-semibold">{roleLabel(node.role)} #{node.id}</span>
        {node.sibling_count > 1 && (
          <span className="ml-auto text-[11px]" style={{ color: 'var(--text-muted)' }}>
            {node.sibling_index}/{node.sibling_count}
          </span>
        )}
      </div>
      <p className="mt-2 line-clamp-2 text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
        {compactPreview(node.preview)}
      </p>
      <div className="mt-2 flex items-center gap-1 overflow-hidden text-[11px]" style={{ color: 'var(--text-muted)' }}>
        {node.is_current_leaf && <span className="shrink-0 rounded px-1.5 py-0.5" style={{ background: 'var(--accent-subtle)' }}>leaf</span>}
        {markerCount > 0 && <span className="shrink-0 rounded px-1.5 py-0.5" style={{ background: 'var(--bg-elevated)' }}>{markerCount} 标记</span>}
        {node.status !== 'completed' && <span className="shrink-0 rounded px-1.5 py-0.5" style={{ background: 'var(--error-bg)', color: 'var(--error-text)' }}>{node.status}</span>}
      </div>
    </div>
  )
}

const nodeTypes = { messageNode: MessageTreeNode }

function MessageDetail({
  conversationId,
  node,
  detail,
  loading,
  error,
  activating,
  openingBranch,
  onActivatePath,
  onOpenBranch,
}) {
  if (!node) {
    return (
      <aside
        className="hidden w-80 shrink-0 border-l p-4 lg:block"
        style={{ borderColor: 'var(--border)', background: 'var(--bg-surface)' }}
      >
        <div className="flex h-full items-center justify-center text-sm" style={{ color: 'var(--text-muted)' }}>
          选择一个节点查看详情
        </div>
      </aside>
    )
  }

  const canActivatePath = Boolean(node.is_leaf)

  return (
    <aside
      className="w-full shrink-0 border-t p-4 lg:w-96 lg:border-l lg:border-t-0"
      style={{ borderColor: 'var(--border)', background: 'var(--bg-surface)' }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
            {roleLabel(node.role)} #{node.id}
          </p>
          <p className="mt-1 text-xs" style={{ color: 'var(--text-muted)' }}>
            {formatTime(node.updated_at || node.created_at)}
          </p>
        </div>
        {node.model && (
          <span className="max-w-36 truncate rounded-md px-2 py-1 text-xs" style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)' }}>
            {node.model}
          </span>
        )}
      </div>

      {node.branch_markers?.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {node.branch_markers.map(marker => (
            <span
              key={`${marker.id}-${marker.marker_type}`}
              className="rounded-md px-2 py-1 text-xs"
              style={{
                background: marker.is_current_branch ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
                color: marker.is_current_branch ? 'var(--text-primary)' : 'var(--text-muted)',
              }}
            >
              {markerLabel(marker)}
            </span>
          ))}
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        {canActivatePath && (
          <button
            type="button"
            onClick={() => onActivatePath(node.id)}
            disabled={activating}
            className="flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm transition disabled:opacity-50"
            style={{ background: 'var(--accent)', color: 'var(--text-primary)' }}
          >
            {activating ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
            切换到此 path
          </button>
        )}
        <button
          type="button"
          onClick={() => onOpenBranch(node)}
          disabled={node.role !== 'assistant' || openingBranch}
          className="flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm transition disabled:opacity-50"
          style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
          title={node.role === 'assistant' ? '打开分支对话' : '只有助理消息可以创建分支'}
        >
          {openingBranch ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitBranch className="h-4 w-4" />}
          打开分支
        </button>
      </div>
      {!canActivatePath && (
        <p className="mt-2 text-xs" style={{ color: 'var(--text-muted)' }}>
          仅叶子节点可以切换到此 path
        </p>
      )}

      <div
        className="mt-4 max-h-[48vh] overflow-y-auto rounded-lg border p-3 text-sm lg:max-h-[calc(100vh-260px)]"
        style={{ borderColor: 'var(--border)', background: 'var(--bg-base)', color: 'var(--text-primary)' }}
      >
        {loading ? (
          <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--text-muted)' }}>
            <Loader2 className="h-4 w-4 animate-spin" />
            加载消息内容
          </div>
        ) : error ? (
          <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--error-text)' }}>
            <AlertCircle className="h-4 w-4" />
            {error}
          </div>
        ) : detail?.role === 'assistant' ? (
          <div className="prose-chat">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
              {detail.content || ''}
            </ReactMarkdown>
          </div>
        ) : (
          <div className="whitespace-pre-wrap break-words">{detail?.content || node.preview}</div>
        )}
      </div>
      <p className="mt-2 text-xs" style={{ color: 'var(--text-muted)' }}>
        会话 {conversationId} · children {node.child_count}
      </p>
    </aside>
  )
}

function branchDisplayTitle(branch) {
  return branch?.title || branch?.auto_title || '未命名分支'
}

function isMainBranch(branch) {
  return branch?.parent_branch_id == null && branch?.forked_from_message_id == null
}

function BranchRowDisplay({
  branch,
  isCurrent,
  isMain,
  busy,
  confirming,
  iconButton,
  onStartEdit,
  onActivate,
  onArchive,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
  onLocate,
}) {
  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        onClick={onLocate}
        disabled={!branch.forked_from_message_id}
        className="flex min-w-0 flex-1 items-center gap-2 text-left disabled:cursor-default"
        title={branch.forked_from_message_id ? '在树中定位分叉点' : undefined}
      >
        <GitBranch className="h-3.5 w-3.5 shrink-0" style={{ color: isCurrent ? 'var(--accent)' : 'var(--text-muted)' }} />
        <span className="truncate text-sm" style={{ color: 'var(--text-primary)', fontWeight: isCurrent ? 600 : 400 }}>
          {branchDisplayTitle(branch)}
        </span>
        {isMain && (
          <span className="shrink-0 rounded px-1 text-[10px]" style={{ background: 'var(--bg-elevated)', color: 'var(--text-muted)' }}>主</span>
        )}
        {isCurrent && (
          <span className="shrink-0 rounded px-1 text-[10px]" style={{ background: 'var(--accent)', color: 'var(--text-primary)' }}>当前</span>
        )}
      </button>

      {confirming ? (
        <div className="flex items-center gap-1">
          <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>删除?</span>
          <button type="button" onClick={onConfirmDelete} disabled={busy} className={iconButton} style={{ background: 'var(--error-bg)', color: 'var(--error-text)' }} title="确认删除">
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
          </button>
          <button type="button" onClick={onCancelDelete} disabled={busy} className={iconButton} style={{ color: 'var(--text-muted)' }} title="取消">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-0.5">
          {!isCurrent && (
            <button type="button" onClick={onActivate} disabled={busy} className={iconButton} style={{ color: 'var(--text-secondary)' }} title="切换到此分支">
              <ArrowRight className="h-3.5 w-3.5" />
            </button>
          )}
          <button type="button" onClick={onStartEdit} disabled={busy} className={iconButton} style={{ color: 'var(--text-secondary)' }} title="重命名">
            <Pencil className="h-3.5 w-3.5" />
          </button>
          {!isMain && (
            <button type="button" onClick={onArchive} disabled={busy || isCurrent} className={iconButton} style={{ color: 'var(--text-secondary)' }} title={isCurrent ? '不能归档当前分支' : '归档'}>
              <Archive className="h-3.5 w-3.5" />
            </button>
          )}
          {!isMain && (
            <button type="button" onClick={onRequestDelete} disabled={busy} className={iconButton} style={{ color: 'var(--error-text)' }} title="删除分支">
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function BranchRow({
  branch,
  isCurrent,
  isMain,
  busy,
  editing,
  draftTitle,
  confirming,
  onDraftChange,
  onStartEdit,
  onCancelEdit,
  onSubmitEdit,
  onActivate,
  onArchive,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
  onLocate,
}) {
  const iconButton = 'flex h-7 w-7 items-center justify-center rounded-md transition disabled:opacity-40'
  return (
    <div
      className="rounded-lg px-2 py-2"
      style={{ background: isCurrent ? 'var(--accent-subtle)' : 'transparent' }}
    >
      {editing ? (
        <form onSubmit={event => { event.preventDefault(); onSubmitEdit() }} className="flex items-center gap-1">
          <input
            autoFocus
            value={draftTitle}
            onChange={event => onDraftChange(event.target.value)}
            className="h-7 min-w-0 flex-1 rounded-md border bg-transparent px-2 text-sm outline-none"
            style={{ borderColor: 'var(--border)', color: 'var(--text-primary)' }}
          />
          <button type="submit" className={iconButton} style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }} title="保存">
            <Check className="h-3.5 w-3.5" />
          </button>
          <button type="button" onClick={onCancelEdit} className={iconButton} style={{ color: 'var(--text-muted)' }} title="取消">
            <X className="h-3.5 w-3.5" />
          </button>
        </form>
      ) : (
        <BranchRowDisplay
          branch={branch}
          isCurrent={isCurrent}
          isMain={isMain}
          busy={busy}
          confirming={confirming}
          iconButton={iconButton}
          onStartEdit={onStartEdit}
          onActivate={onActivate}
          onArchive={onArchive}
          onRequestDelete={onRequestDelete}
          onCancelDelete={onCancelDelete}
          onConfirmDelete={onConfirmDelete}
          onLocate={onLocate}
        />
      )}
    </div>
  )
}

function BranchManager({ branches, currentBranchId, loading, onActivate, onRename, onArchive, onDelete, onLocate }) {
  const [editingId, setEditingId] = useState(null)
  const [draftTitle, setDraftTitle] = useState('')
  const [confirmingId, setConfirmingId] = useState(null)
  const [busyId, setBusyId] = useState(null)

  const run = useCallback(async (branchId, action) => {
    setBusyId(branchId)
    try {
      await action()
    } finally {
      setBusyId(null)
    }
  }, [])

  const startEdit = useCallback((branch) => {
    setConfirmingId(null)
    setEditingId(branch.id)
    setDraftTitle(branch.title || '')
  }, [])

  const submitEdit = useCallback((branch) => {
    const next = draftTitle.trim()
    setEditingId(null)
    if (!next || next === (branch.title || '')) return
    void run(branch.id, () => onRename(branch.id, next))
  }, [draftTitle, onRename, run])

  if (!branches.length && !loading) {
    return (
      <div className="flex h-full items-center justify-center px-3 text-center text-xs" style={{ color: 'var(--text-muted)' }}>
        暂无分支
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-0.5 overflow-y-auto p-2">
      {branches.map(branch => {
        const main = isMainBranch(branch)
        return (
          <BranchRow
            key={branch.id}
            branch={branch}
            isCurrent={branch.id === currentBranchId}
            isMain={main}
            busy={busyId === branch.id}
            editing={editingId === branch.id}
            draftTitle={editingId === branch.id ? draftTitle : ''}
            confirming={confirmingId === branch.id}
            onDraftChange={setDraftTitle}
            onStartEdit={() => startEdit(branch)}
            onCancelEdit={() => setEditingId(null)}
            onSubmitEdit={() => submitEdit(branch)}
            onActivate={() => run(branch.id, () => onActivate(branch.id))}
            onArchive={() => run(branch.id, () => onArchive(branch.id))}
            onRequestDelete={() => setConfirmingId(branch.id)}
            onCancelDelete={() => setConfirmingId(null)}
            onConfirmDelete={() => run(branch.id, async () => { await onDelete(branch.id); setConfirmingId(null) })}
            onLocate={() => onLocate(branch)}
          />
        )
      })}
    </div>
  )
}

export default function MessageTreePanel({
  open,
  conversationId,
  conversationTitle,
  refreshToken = 0,
  branches = [],
  currentBranchId = null,
  branchesLoading = false,
  onClose,
  onActivatePath,
  onOpenBranch,
  onActivateBranch,
  onRenameBranch,
  onArchiveBranch,
  onDeleteBranch,
}) {
  const [tree, setTree] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [deferredQuery, setDeferredQuery] = useState('')
  const [selectedNodeId, setSelectedNodeId] = useState(null)
  const [messageDetails, setMessageDetails] = useState({})
  const [detailLoadingId, setDetailLoadingId] = useState(null)
  const [detailError, setDetailError] = useState('')
  const [activatingId, setActivatingId] = useState(null)
  const [openingBranchId, setOpeningBranchId] = useState(null)
  const [positionOverrides, setPositionOverrides] = useState({})
  const [branchRailOpen, setBranchRailOpen] = useState(true)
  const flowInstanceRef = useRef(null)

  const handleFlowInit = useCallback((instance) => {
    flowInstanceRef.current = instance
  }, [])

  const selectedNode = useMemo(() => (
    tree?.nodes?.find(node => node.id === selectedNodeId) || null
  ), [selectedNodeId, tree])

  const selectedDetail = selectedNodeId ? messageDetails[selectedNodeId] : null

  const loadTree = useCallback(async () => {
    if (!open || !conversationId) return
    setLoading(true)
    setError('')
    try {
      const data = await api.getMessageTree(conversationId)
      setTree(data)
      setSelectedNodeId(current => (
        current && data?.nodes?.some(node => node.id === current)
          ? current
          : data?.current_leaf_message_id || data?.active_path?.at?.(-1) || data?.nodes?.[0]?.id || null
      ))
      queueMicrotask(() => {
        flowInstanceRef.current?.fitView?.({ padding: 0.18, duration: 240 })
      })
    } catch (err) {
      setError(err.message || '加载消息树失败')
    } finally {
      setLoading(false)
    }
  }, [conversationId, open])

  useEffect(() => {
    if (!open) return undefined
    queueMicrotask(() => {
      void loadTree()
    })
    return undefined
  }, [loadTree, open, refreshToken])

  useEffect(() => {
    if (!open) return undefined
    const handleKeyDown = event => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose, open])

  useEffect(() => {
    const timer = setTimeout(() => setDeferredQuery(query), 180)
    return () => clearTimeout(timer)
  }, [query])

  // Detail content is cached by message id; a refresh (edit/regenerate/delete)
  // or conversation switch can change a message in place, so drop the cache to
  // avoid showing stale content. Reset during render (the React-recommended
  // alternative to setState-in-effect) guarded by the previous key.
  const detailCacheKey = `${conversationId ?? ''}:${refreshToken}`
  const [prevDetailCacheKey, setPrevDetailCacheKey] = useState(detailCacheKey)
  if (detailCacheKey !== prevDetailCacheKey) {
    setPrevDetailCacheKey(detailCacheKey)
    setMessageDetails({})
    setDetailError('')
  }

  // Manual drag positions are keyed by node id and should survive tree
  // recomputes (e.g. search) within a conversation; only drop them when the
  // conversation itself changes.
  const [prevConversationId, setPrevConversationId] = useState(conversationId)
  if (conversationId !== prevConversationId) {
    setPrevConversationId(conversationId)
    setPositionOverrides({})
  }

  useEffect(() => {
    if (!open || !conversationId || !selectedNodeId || messageDetails[selectedNodeId]) return undefined

    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      setDetailLoadingId(selectedNodeId)
      setDetailError('')
      api.getMessage(conversationId, selectedNodeId)
        .then(message => {
          if (!cancelled) {
            setMessageDetails(prev => ({ ...prev, [selectedNodeId]: message }))
          }
        })
        .catch(err => {
          if (!cancelled) setDetailError(err.message || '加载消息失败')
        })
        .finally(() => {
          if (!cancelled) setDetailLoadingId(null)
        })
    })

    return () => {
      cancelled = true
    }
  }, [conversationId, messageDetails, open, selectedNodeId])

  const { flowNodes, flowEdges } = useMemo(() => buildLayout(tree), [tree])
  const matches = useMemo(() => computeMatches(tree, deferredQuery), [deferredQuery, tree])
  const selectedFlowNodes = useMemo(() => (
    flowNodes.map(node => {
      const override = positionOverrides[node.id]
      return {
        ...node,
        position: override || node.position,
        selected: Number(node.id) === selectedNodeId,
        data: {
          ...node.data,
          isSearchMatch: matches.has(Number(node.id)),
        },
      }
    })
  ), [flowNodes, matches, positionOverrides, selectedNodeId])

  const focusActivePath = useCallback(() => {
    if (!tree?.active_path?.length || !flowInstanceRef.current) return
    flowInstanceRef.current.fitView({
      nodes: tree.active_path.map(id => ({ id: String(id) })),
      padding: 0.28,
      duration: 260,
    })
  }, [tree])

  const focusSelected = useCallback(() => {
    if (!selectedNodeId || !flowInstanceRef.current) return
    flowInstanceRef.current.fitView({
      nodes: [{ id: String(selectedNodeId) }],
      maxZoom: 1.25,
      padding: 0.42,
      duration: 220,
    })
  }, [selectedNodeId])

  const activatePath = useCallback(async (messageId) => {
    if (!messageId || !onActivatePath) return
    const targetNode = tree?.nodes?.find(item => item.id === messageId)
    if (!targetNode?.is_leaf) return
    setActivatingId(messageId)
    try {
      await onActivatePath(messageId)
      await loadTree()
    } finally {
      setActivatingId(null)
    }
  }, [loadTree, onActivatePath, tree])

  const openBranch = useCallback(async (message) => {
    if (!message || !onOpenBranch) return
    setOpeningBranchId(message.id)
    try {
      // The detail request may not have resolved yet; fetch on demand so the
      // branch can be opened the moment a node is selected.
      let fullMessage = messageDetails[message.id]
      if (!fullMessage) {
        try {
          fullMessage = await api.getMessage(conversationId, message.id)
          setMessageDetails(prev => ({ ...prev, [message.id]: fullMessage }))
        } catch {
          fullMessage = message
        }
      }
      await onOpenBranch(fullMessage)
    } finally {
      setOpeningBranchId(null)
    }
  }, [conversationId, messageDetails, onOpenBranch])

  const handleNodeDragStop = useCallback((_, node) => {
    setPositionOverrides(prev => ({ ...prev, [node.id]: node.position }))
  }, [])

  const activateBranch = useCallback(async (branchId) => {
    if (!onActivateBranch) return
    await onActivateBranch(branchId)
    await loadTree()
  }, [loadTree, onActivateBranch])

  const renameBranch = useCallback(async (branchId, title) => {
    if (!onRenameBranch) return
    await onRenameBranch(branchId, title)
  }, [onRenameBranch])

  const archiveBranch = useCallback(async (branchId) => {
    if (!onArchiveBranch) return
    await onArchiveBranch(branchId)
    await loadTree()
  }, [loadTree, onArchiveBranch])

  const deleteBranch = useCallback(async (branchId) => {
    if (!onDeleteBranch) return
    await onDeleteBranch(branchId)
    await loadTree()
  }, [loadTree, onDeleteBranch])

  const locateBranchFork = useCallback((branch) => {
    const targetId = branch?.forked_from_message_id
    if (!targetId) return
    setSelectedNodeId(targetId)
    flowInstanceRef.current?.fitView?.({
      nodes: [{ id: String(targetId) }],
      maxZoom: 1.25,
      padding: 0.42,
      duration: 220,
    })
  }, [])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col"
      style={{ background: 'color-mix(in srgb, var(--bg-base) 94%, transparent)', color: 'var(--text-primary)' }}
      role="dialog"
      aria-modal="true"
      aria-label="消息树"
    >
      <header
        className="flex shrink-0 flex-wrap items-center gap-3 border-b px-4 py-3"
        style={{ borderColor: 'var(--border)', background: 'var(--bg-surface)' }}
      >
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg" style={{ background: 'var(--bg-elevated)' }}>
            <Network className="h-4 w-4" style={{ color: 'var(--text-secondary)' }} />
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">消息树</p>
            <p className="truncate text-xs" style={{ color: 'var(--text-muted)' }}>
              {conversationTitle || '当前会话'}
            </p>
          </div>
        </div>

        <div className="relative w-full sm:w-72">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2" style={{ color: 'var(--text-muted)' }} />
          <input
            type="search"
            value={query}
            onChange={event => setQuery(event.target.value)}
            placeholder="搜索摘要"
            className="h-9 w-full rounded-lg border bg-transparent pl-9 pr-3 text-sm outline-none"
            style={{ borderColor: 'var(--border)', color: 'var(--text-primary)' }}
          />
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={loadTree}
            disabled={loading}
            className="flex h-9 w-9 items-center justify-center rounded-lg transition disabled:opacity-50"
            style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
            title="刷新"
            aria-label="刷新"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button
            type="button"
            onClick={() => flowInstanceRef.current?.fitView?.({ padding: 0.18, duration: 220 })}
            className="flex h-9 w-9 items-center justify-center rounded-lg transition"
            style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
            title="适应视图"
            aria-label="适应视图"
          >
            <LocateFixed className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={focusActivePath}
            className="flex h-9 w-9 items-center justify-center rounded-lg transition"
            style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
            title="居中 active path"
            aria-label="居中 active path"
          >
            <Crosshair className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => setBranchRailOpen(open => !open)}
            className="flex h-9 w-9 items-center justify-center rounded-lg transition"
            style={{
              background: branchRailOpen ? 'var(--accent-subtle)' : 'var(--bg-elevated)',
              color: branchRailOpen ? 'var(--text-primary)' : 'var(--text-secondary)',
            }}
            title="分支管理"
            aria-label="分支管理"
            aria-pressed={branchRailOpen}
          >
            <GitBranch className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={onClose}
            className="flex h-9 w-9 items-center justify-center rounded-lg transition"
            style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)' }}
            title="关闭"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {branchRailOpen && (
          <aside
            className="flex w-full shrink-0 flex-col border-b lg:w-64 lg:border-b-0 lg:border-r"
            style={{ borderColor: 'var(--border)', background: 'var(--bg-surface)' }}
          >
            <div className="flex shrink-0 items-center justify-between px-3 py-2.5" style={{ borderBottom: '1px solid var(--border)' }}>
              <span className="text-xs font-semibold" style={{ color: 'var(--text-secondary)' }}>分支</span>
              {branchesLoading && <Loader2 className="h-3.5 w-3.5 animate-spin" style={{ color: 'var(--text-muted)' }} />}
            </div>
            <div className="min-h-0 flex-1 lg:overflow-y-auto">
              <BranchManager
                branches={branches}
                currentBranchId={currentBranchId}
                loading={branchesLoading}
                onActivate={activateBranch}
                onRename={renameBranch}
                onArchive={archiveBranch}
                onDelete={deleteBranch}
                onLocate={locateBranchFork}
              />
            </div>
          </aside>
        )}
        <main className="relative min-h-[420px] flex-1">
          {loading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center" style={{ background: 'color-mix(in srgb, var(--bg-base) 70%, transparent)' }}>
              <div className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm" style={{ background: 'var(--bg-surface)', color: 'var(--text-muted)' }}>
                <Loader2 className="h-4 w-4 animate-spin" />
                加载消息树
              </div>
            </div>
          )}
          {error ? (
            <div className="flex h-full items-center justify-center p-6">
              <div className="max-w-sm rounded-lg border p-4 text-sm" style={{ borderColor: 'var(--error-border)', background: 'var(--error-bg)', color: 'var(--error-text)' }}>
                <div className="flex items-center gap-2 font-medium">
                  <AlertCircle className="h-4 w-4" />
                  {error}
                </div>
              </div>
            </div>
          ) : (
            <ReactFlowProvider>
              <ReactFlow
                nodes={selectedFlowNodes}
                edges={flowEdges}
                nodeTypes={nodeTypes}
                minZoom={0.2}
                maxZoom={1.8}
                fitView
                fitViewOptions={{ padding: 0.18 }}
                onInit={handleFlowInit}
                onNodeClick={(_, node) => setSelectedNodeId(Number(node.id))}
                onNodeDragStop={handleNodeDragStop}
                nodesDraggable
                nodesConnectable={false}
                elementsSelectable
                proOptions={{ hideAttribution: true }}
              >
                <Background gap={22} size={1} color="var(--border-subtle)" />
                <Controls showInteractive={false} />
                <MiniMap
                  pannable
                  zoomable
                  nodeColor={node => (node.data?.isActivePath ? 'var(--accent)' : 'var(--bg-elevated)')}
                  maskColor="rgba(0,0,0,0.18)"
                />
              </ReactFlow>
            </ReactFlowProvider>
          )}
          {query.trim() && (
            <div
              className="absolute bottom-4 left-4 rounded-lg px-3 py-2 text-xs"
              style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}
            >
              匹配 {matches.size} 个节点
            </div>
          )}
          {selectedNodeId && (
            <button
              type="button"
              onClick={focusSelected}
              className="absolute bottom-4 right-4 rounded-lg px-3 py-2 text-xs transition"
              style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
            >
              定位选中节点
            </button>
          )}
        </main>

        <MessageDetail
          conversationId={conversationId}
          node={selectedNode}
          detail={selectedDetail}
          loading={detailLoadingId === selectedNodeId}
          error={detailError}
          activating={activatingId === selectedNodeId}
          openingBranch={openingBranchId === selectedNodeId}
          onActivatePath={activatePath}
          onOpenBranch={openBranch}
        />
      </div>
    </div>
  )
}
