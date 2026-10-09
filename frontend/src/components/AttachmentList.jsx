import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api'

function ImageDialog({ url, filename, onClose }) {
  const close = useRef(null)
  useEffect(() => {
    const previous = document.activeElement
    close.current?.focus()
    const keydown = event => {
      if (event.key === 'Escape') onClose()
      if (event.key === 'Tab') { event.preventDefault(); close.current?.focus() }
    }
    document.addEventListener('keydown', keydown)
    return () => { document.removeEventListener('keydown', keydown); previous?.focus() }
  }, [onClose])
  return createPortal(<div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 p-4" onClick={onClose}>
    <div role="dialog" aria-modal="true" aria-label={`图片预览：${filename}`} className="flex max-h-full max-w-full flex-col items-center gap-2" onClick={event => event.stopPropagation()}>
      <button ref={close} type="button" onClick={onClose} className="self-end rounded px-3 py-2 bg-white text-black">关闭预览</button>
      <img src={url} alt={filename} className="max-h-[80vh] max-w-full object-contain" />
      <span className="text-sm text-white">{filename}</span>
    </div>
  </div>, document.body)
}

function AttachmentCard({ attachment, allowDelete, onChanged }) {
  const [url, setUrl] = useState('')
  const [previewError, setPreviewError] = useState('')
  const [status, setStatus] = useState(attachment.status)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const closeModal = useCallback(() => setOpen(false), [])
  const filename = attachment.filename || '图片'
  useEffect(() => {
    let active = true
    let objectUrl
    if (status === 'ready') {
      api.getAttachmentPreview(attachment.id).then(blob => {
        objectUrl = URL.createObjectURL(blob)
        if (active) setUrl(objectUrl)
        else URL.revokeObjectURL(objectUrl)
      }).catch(e => {
        if (active) setPreviewError(e.data?.error?.code === 'ATTACHMENT_FILE_MISSING' ? '图片文件已丢失' : e.status === 410 ? '该图片已失效' : e.status === 401 ? '登录已失效，请重新登录' : '图片预览加载失败')
      })
    }
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [attachment.id, status])
  useEffect(() => {
    const changed = event => { if (event.detail.id === attachment.id) { setStatus(event.detail.status); setOpen(false) } }
    window.addEventListener('attachment-status-changed', changed)
    return () => window.removeEventListener('attachment-status-changed', changed)
  }, [attachment.id])
  async function remove() {
    if (status === 'ready' && !window.confirm('永久删除这张图片？历史消息将保留删除提示。')) return
    setBusy(true)
    setDeleteError('')
    try {
      const result = await api.deleteAttachment(attachment.id)
      const next = result?.status || 'deleted'
      if (next === 'failed') setDeleteError('图片删除失败，请手动重试')
      setStatus(next)
      setOpen(false)
      window.dispatchEvent(new CustomEvent('attachment-status-changed', { detail: { id: attachment.id, status: next } }))
      onChanged?.()
    } catch (e) {
      setDeleteError(e.message)
      if (e.status !== 409) {
        setStatus('failed')
        window.dispatchEvent(new CustomEvent('attachment-status-changed', { detail: { id: attachment.id, status: 'failed' } }))
      }
      onChanged?.()
    } finally { setBusy(false) }
  }
  return <div className="w-32 rounded-lg border p-1 text-xs" style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}>
    {status === 'ready' && url && !previewError ? <button type="button" onClick={() => setOpen(true)} aria-label={`预览 ${filename}`} className="w-full"><img src={url} alt={filename} onError={() => setPreviewError('图片预览加载失败')} className="h-24 w-full rounded object-cover" /></button>
      : <div className="flex h-24 items-center justify-center text-center" role="status">{status === 'deleted' ? '该图片已删除' : status === 'deleting' ? '正在删除' : status === 'failed' ? '删除失败，可重试' : previewError || '加载图片中'}</div>}
    <p className="truncate" title={filename}>{filename}</p>
    <p>{attachment.width} × {attachment.height}</p>
    {allowDelete && ['ready', 'failed', 'deleting'].includes(status) && <button type="button" disabled={busy} onClick={() => { void remove() }} className="underline disabled:opacity-50">{busy ? '删除中' : status === 'deleting' ? '确认删除状态' : status === 'failed' ? '重试删除' : '删除图片'}</button>}
    {deleteError && <p role="alert" style={{ color: 'var(--error-text)' }}>{deleteError}</p>}
    {open && status === 'ready' && <ImageDialog url={url} filename={filename} onClose={closeModal} />}
  </div>
}

export default function AttachmentList({ attachments = [], allowDelete = false, onChanged }) {
  if (!attachments.length) return null
  return <div className="my-2 flex flex-wrap gap-2">{attachments.map(attachment => <AttachmentCard key={`${attachment.id}-${attachment.status}`} attachment={attachment} allowDelete={allowDelete} onChanged={onChanged} />)}</div>
}
