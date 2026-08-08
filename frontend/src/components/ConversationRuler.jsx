import { useMemo } from 'react'
import {
  CHAT_RULER_PREVIEW_LENGTH,
  CHAT_RULER_WINDOW_SIZE,
} from '../config/chat'

function cleanText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim()
}

function truncateText(value, length) {
  const text = cleanText(value)
  if (!text) return ''
  return text.length > length ? `${text.slice(0, length)}…` : text
}

function getWindow(turns, anchorTurnId) {
  const anchorIndex = Math.max(0, turns.findIndex(turn => turn.userMessage.id === anchorTurnId))
  const windowSize = Math.min(CHAT_RULER_WINDOW_SIZE, turns.length)
  const radius = Math.floor(windowSize / 2)
  const start = Math.min(
    Math.max(0, anchorIndex - radius),
    Math.max(0, turns.length - windowSize),
  )
  return turns.slice(start, start + windowSize)
}

export default function ConversationRuler({ turns, anchorTurnId, onJump, position }) {
  const visibleTurns = useMemo(
    () => getWindow(turns, anchorTurnId),
    [anchorTurnId, turns],
  )

  if (turns.length < CHAT_RULER_WINDOW_SIZE || !position) return null

  return (
    <nav className="conversation-ruler" aria-label="对话导航" style={position}>
      <div className="conversation-ruler-track">
        {visibleTurns.map(turn => {
          const isActive = turn.userMessage.id === anchorTurnId
          const userPreview = truncateText(
            turn.userMessage.content,
            CHAT_RULER_PREVIEW_LENGTH,
          ) || '（空消息）'
          const assistantPreview = truncateText(
            turn.assistantMessage?.content,
            CHAT_RULER_PREVIEW_LENGTH,
          ) || (turn.assistantMessage?.status === 'streaming' ? '正在回复…' : '暂无回复')

          return (
            <button
              key={turn.userMessage.id}
              type="button"
              className={`conversation-ruler-tick${isActive ? ' is-active' : ''}`}
              onClick={() => onJump(turn.userMessage.id)}
              aria-label={`跳转到：${userPreview}`}
            >
              <span className="conversation-ruler-tooltip" role="tooltip">
                <strong>{userPreview}</strong>
                <span>{assistantPreview}</span>
              </span>
            </button>
          )
        })}
      </div>
    </nav>
  )
}
