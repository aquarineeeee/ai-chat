import { useRef } from 'react'
import { ImagePlus, X } from 'lucide-react'

export default function AttachmentPicker({ draft, disabled }) {
  const input = useRef(null)
  return <div className="space-y-2" style={{ color: 'var(--text-secondary)' }}>
    <input ref={input} type="file" multiple accept="image/jpeg,image/png,.jpg,.jpeg,.png" className="hidden"
      onChange={event => { draft.addFiles(event.target.files); event.target.value = '' }} disabled={disabled} />
    <button type="button" disabled={disabled} onClick={() => input.current?.click()}
      className="flex items-center gap-2 rounded-lg px-2 py-1 text-xs disabled:opacity-50" aria-label="添加图片">
      <ImagePlus className="h-4 w-4" />添加图片 · JPG/PNG，每张 ≤ 1 MiB
    </button>
    {draft.items.length > 0 && <div className="flex flex-wrap gap-2 max-h-48 overflow-y-auto">
      {draft.items.map(item => <div key={item.key} className="w-28 rounded-lg border p-1 text-xs" style={{ borderColor: 'var(--border)' }}>
        {item.url ? <img src={item.url} alt={item.file.name} className="h-20 w-full rounded object-cover" /> : <div className="h-20 flex items-center justify-center">图片不可用</div>}
        <div className="flex items-center justify-between gap-1">
          <span className="truncate" title={item.file.name}>{item.file.name}</span>
          <button type="button" onClick={() => { void draft.remove(item) }} disabled={disabled || item.state === 'removing'} aria-label={`移除 ${item.file.name}`}><X className="h-4 w-4" /></button>
        </div>
        <div role="status">{item.state === 'pending' ? '等待上传' : item.state === 'uploading' ? `上传 ${item.progress}%${item.progress === 100 ? ' · 校验中' : ''}` : item.state === 'ready' ? '已上传' : item.state === 'removing' ? '删除中' : item.error}</div>
        <div>{(item.file.size / 1024).toFixed(1)} KiB</div>
        {item.state === 'failed' && <button type="button" disabled={disabled} onClick={() => { void draft.retry(item) }} className="underline">重试上传</button>}
        {item.state === 'remove-failed' && <button type="button" disabled={disabled} onClick={() => { void draft.remove(item) }} className="underline">重试移除</button>}
      </div>)}
    </div>}
    {draft.error && <p role="alert" className="text-xs" style={{ color: 'var(--error-text)' }}>{draft.error}</p>}
  </div>
}
