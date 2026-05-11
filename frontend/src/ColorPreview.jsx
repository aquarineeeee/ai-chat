import { MessageSquare, Send, Sparkles } from 'lucide-react'

const OPTIONS = [
  {
    name: '降饱和砖玫瑰',
    desc: '现在的红调低饱和版，像旧砖或陶土，安静',
    accent: '#b87060',
    accentHover: '#c98070',
    bubble: '#9e5c4a',
    bubbleText: '#fdf6f0',
    subtle: 'rgba(184,112,96,0.15)',
    border: 'rgba(184,112,96,0.35)',
  },
  {
    name: '暖琥珀',
    desc: '偏黄的暖色，像蜡烛或旧书页，完全脱离警告感',
    accent: '#c49a5a',
    accentHover: '#d4aa6a',
    bubble: '#a87d42',
    bubbleText: '#fdf6f0',
    subtle: 'rgba(196,154,90,0.15)',
    border: 'rgba(196,154,90,0.35)',
  },
  {
    name: '灰紫 / 薰衣草',
    desc: '偏灰的紫，像黄昏或干花，有点文学气质',
    accent: '#9b8ab4',
    accentHover: '#ab9ac4',
    bubble: '#7d6a96',
    bubbleText: '#f5f0ff',
    subtle: 'rgba(155,138,180,0.15)',
    border: 'rgba(155,138,180,0.35)',
  },
  {
    name: '鼠尾草绿',
    desc: '偏灰的绿，像苔藓或旧铜，和暖棕背景很搭',
    accent: '#7a9e8a',
    accentHover: '#8aae9a',
    bubble: '#5e8270',
    bubbleText: '#f0faf4',
    subtle: 'rgba(122,158,138,0.15)',
    border: 'rgba(122,158,138,0.35)',
  },
]

const BG = {
  base: '#1c1917',
  surface: '#292524',
  elevated: '#3c3330',
  textPrimary: '#f5f0eb',
  textSecondary: '#a8a29e',
  textMuted: '#78716c',
  border: '#44403c',
}

function MiniCard({ opt }) {
  return (
    <div
      className="rounded-2xl overflow-hidden flex flex-col"
      style={{ background: BG.surface, border: `1px solid ${BG.border}`, width: 260 }}
    >
      {/* Mini header */}
      <div
        className="flex items-center gap-2 px-4 py-3"
        style={{ borderBottom: `1px solid ${BG.border}` }}
      >
        <div
          className="w-6 h-6 rounded-lg flex items-center justify-center"
          style={{ background: opt.accent }}
        >
          <MessageSquare size={13} color={opt.bubbleText} />
        </div>
        <span className="text-sm font-medium" style={{ color: BG.textPrimary }}>{opt.name}</span>
      </div>

      {/* Mini messages */}
      <div className="flex flex-col gap-2 px-4 py-4 flex-1">
        {/* AI message */}
        <div className="flex gap-2 items-start">
          <div
            className="w-6 h-6 rounded-full flex items-center justify-center shrink-0 text-[10px] font-bold"
            style={{ background: opt.accent, color: opt.bubbleText }}
          >
            AI
          </div>
          <div className="text-xs leading-relaxed" style={{ color: BG.textPrimary }}>
            你好，有什么可以帮你的？
          </div>
        </div>

        {/* User message */}
        <div className="flex justify-end">
          <div
            className="text-xs px-3 py-2 rounded-xl rounded-tr-sm max-w-[80%]"
            style={{ background: opt.bubble, color: opt.bubbleText }}
          >
            帮我写一首关于秋天的诗
          </div>
        </div>

        {/* AI reply */}
        <div className="flex gap-2 items-start">
          <div
            className="w-6 h-6 rounded-full flex items-center justify-center shrink-0 text-[10px] font-bold"
            style={{ background: opt.accent, color: opt.bubbleText }}
          >
            AI
          </div>
          <div className="text-xs leading-relaxed" style={{ color: BG.textPrimary }}>
            秋风吹落叶，<br />
            一片寄相思。
          </div>
        </div>
      </div>

      {/* Mini input */}
      <div
        className="mx-4 mb-4 flex items-center gap-2 rounded-xl px-3 py-2"
        style={{ background: BG.base, border: `1px solid ${opt.border}` }}
      >
        <span className="flex-1 text-xs" style={{ color: BG.textMuted }}>发送消息…</span>
        <div
          className="w-6 h-6 rounded-lg flex items-center justify-center"
          style={{ background: opt.accent }}
        >
          <Send size={11} color={opt.bubbleText} />
        </div>
      </div>

      {/* Description */}
      <div
        className="px-4 py-3 text-xs"
        style={{ borderTop: `1px solid ${BG.border}`, color: BG.textMuted }}
      >
        {opt.desc}
      </div>
    </div>
  )
}

export default function ColorPreview() {
  return (
    <div
      className="min-h-screen flex flex-col items-center justify-center p-8 gap-8"
      style={{ background: BG.base }}
    >
      <div className="text-center">
        <h1 className="text-xl font-semibold mb-1" style={{ color: BG.textPrimary }}>配色方案预览</h1>
        <p className="text-sm" style={{ color: BG.textMuted }}>背景色不变，只对比强调色</p>
      </div>
      <div className="flex flex-wrap gap-4 justify-center">
        {OPTIONS.map(opt => <MiniCard key={opt.name} opt={opt} />)}
      </div>
    </div>
  )
}
