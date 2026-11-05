import { useRef, useState } from 'react'

type Pane = {
  size: number; minimum: number; maximum: number
  resize: (value: number, persist?: boolean) => number; reset: () => void
}

/** An overlay handle keeps the existing grid and independently scrolling panes. */
export function PaneResizer({ label, controls, edge, pane, className = '' }: {
  label: string; controls: string; edge: 'start' | 'end'; pane: Pane; className?: string
}) {
  const drag = useRef<{ pointer: number; x: number; initial: number; last: number } | null>(null)
  const [dragging, setDragging] = useState(false)
  const direction = edge === 'end' ? 1 : -1
  function finish() {
    const current = drag.current
    drag.current = null
    setDragging(false)
    if (current) pane.resize(current.last)
  }
  return (
    <div className={`pane-resize-group at-${edge} ${className}`} data-dragging={dragging || undefined}>
      <div className="pane-resizer" role="separator" tabIndex={0} aria-orientation="vertical"
        aria-label={label} aria-controls={controls} aria-valuemin={pane.minimum} aria-valuemax={pane.maximum}
        aria-valuenow={pane.size} aria-valuetext={`${pane.size} 像素`}
        title={`${label}：拖动调整，双击恢复默认；方向键微调`}
        onPointerDown={e => {
          if (!e.isPrimary || e.button !== 0) return
          e.preventDefault()
          e.currentTarget.focus()
          e.currentTarget.setPointerCapture(e.pointerId)
          drag.current = { pointer: e.pointerId, x: e.clientX, initial: pane.size, last: pane.size }
          setDragging(true)
        }}
        onPointerMove={e => {
          const current = drag.current
          if (current?.pointer === e.pointerId) current.last = pane.resize(current.initial + (e.clientX - current.x) * direction, false)
        }}
        onPointerUp={e => {
          if (drag.current?.pointer !== e.pointerId) return
          finish()
          if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
        }}
        onPointerCancel={finish} onLostPointerCapture={finish} onDoubleClick={pane.reset}
        onKeyDown={e => {
          const step = e.shiftKey ? 40 : 12
          if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
            e.preventDefault(); pane.resize(pane.size + (e.key === 'ArrowRight' ? step : -step) * direction)
          } else if (e.key === 'Home' || e.key === 'End') {
            e.preventDefault(); pane.resize(e.key === 'Home' ? pane.minimum : pane.maximum)
          }
        }}><span aria-hidden="true" /></div>
      <div className="pane-resize-tools" role="group" aria-label={`${label}设置`}>
        <button type="button" aria-label={`缩窄${label}`} title="缩窄" disabled={pane.size <= pane.minimum} onClick={() => pane.resize(pane.size - 24)}>−</button>
        <button type="button" aria-label={`加宽${label}`} title="加宽" disabled={pane.size >= pane.maximum} onClick={() => pane.resize(pane.size + 24)}>＋</button>
        <button type="button" aria-label={`恢复${label}默认宽度`} title="恢复默认宽度" onClick={pane.reset}>复原</button>
      </div>
    </div>
  )
}
