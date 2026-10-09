import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

export default function useAttachmentDraft() {
  const [items, setItems] = useState([])
  const [error, setError] = useState('')
  const controllers = useRef(new Map())
  const uploadQueue = useRef([])
  const activeUploads = useRef(0)
  const urls = useRef(new Set())
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    const activeControllers = controllers.current
    const objectUrls = urls.current
    const queuedUploads = uploadQueue.current
    return () => {
      mounted.current = false
      queuedUploads.length = 0
      activeControllers.forEach(controller => controller.abort())
      objectUrls.forEach(url => URL.revokeObjectURL(url))
    }
  }, [])

  function patch(key, values) {
    if (mounted.current) setItems(current => current.map(item => item.key === key ? { ...item, ...values } : item))
  }
  async function executeUpload(item) {
    const controller = new AbortController()
    controllers.current.set(item.key, controller)
    patch(item.key, { state: 'uploading', progress: 0, error: '' })
    try {
      const attachment = await api.uploadAttachment(item.file, {
        signal: controller.signal,
        onProgress: progress => patch(item.key, { progress }),
      })
      if (controller.signal.aborted || !mounted.current) {
        // An upload whose result was discarded remains unclaimed for TTL cleanup.
        return
      }
      patch(item.key, { state: 'ready', attachment, progress: 100 })
    } catch (e) {
      if (e.name !== 'AbortError') patch(item.key, { state: 'failed', error: e.message })
    } finally { controllers.current.delete(item.key) }
  }
  function drainUploads() {
    if (!mounted.current) return
    while (activeUploads.current < 1 && uploadQueue.current.length) {
      const item = uploadQueue.current.shift()
      activeUploads.current += 1
      void executeUpload(item).finally(() => {
        activeUploads.current -= 1
        drainUploads()
      })
    }
  }
  function upload(item) {
    if (item.attachment || controllers.current.has(item.key) || uploadQueue.current.some(candidate => candidate.key === item.key)) return
    patch(item.key, { state: 'pending', progress: 0, error: '' })
    uploadQueue.current.push(item)
    drainUploads()
  }
  function addFiles(files) {
    setError('')
    const valid = []
    const invalid = []
    for (const file of Array.from(files)) {
      let rejection = ''
      if (!/\.(jpe?g|png)$/i.test(file.name) || !['image/jpeg', 'image/png'].includes(file.type)) {
        rejection = '只支持 JPG、PNG 图片'
      }
      if (!file.size || file.size > 1048576) rejection = '每张图片须为非空文件且不超过 1 MiB'
      if (rejection) {
        invalid.push({ key: crypto.randomUUID(), file, url: '', state: 'invalid', error: rejection })
        continue
      }
      const url = URL.createObjectURL(file)
      urls.current.add(url)
      valid.push({ key: crypto.randomUUID(), file, url, state: 'pending', progress: 0 })
    }
    setItems(current => [...current, ...valid, ...invalid])
    valid.forEach(item => { void upload(item) })
  }
  async function remove(item) {
    const queuedIndex = uploadQueue.current.findIndex(candidate => candidate.key === item.key)
    if (queuedIndex >= 0) uploadQueue.current.splice(queuedIndex, 1)
    controllers.current.get(item.key)?.abort()
    if (item.attachment) {
      patch(item.key, { state: 'removing', error: '' })
      try {
        const result = await api.deleteAttachment(item.attachment.id)
        if (result?.status === 'failed' || result?.status === 'deleting') {
          patch(item.key, {
            state: 'remove-failed',
            attachment: { ...item.attachment, ...result },
            error: result.status === 'failed' ? '图片删除失败，请重试移除' : '图片正在删除，请稍后重试移除',
          })
          return
        }
        if (result && result.status !== 'deleted') {
          patch(item.key, { state: 'remove-failed', error: '图片删除尚未确认，请重试移除' })
          return
        }
      }
      catch (e) { patch(item.key, { state: 'remove-failed', error: e.message }); return }
    }
    setItems(current => current.filter(candidate => candidate.key !== item.key))
    URL.revokeObjectURL(item.url)
    urls.current.delete(item.url)
  }
  function clear() {
    items.forEach(item => { URL.revokeObjectURL(item.url); urls.current.delete(item.url) })
    setItems([])
    setError('')
  }
  return { items, error, addFiles, remove, retry: upload, clear,
    ready: items.every(item => item.state === 'ready'),
    attachments: items.filter(item => item.state === 'ready').map(item => item.attachment),
  }
}
