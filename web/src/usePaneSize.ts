import { useLayoutEffect, useRef, useState } from 'react'

/** Measure the actual workspace, including changes to a containing app panel. */
export function usePaneContainer(enabled = true) {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    const node = ref.current
    if (!enabled || !node) return
    const update = () => setWidth(node.clientWidth)
    update()
    const observer = new ResizeObserver(update)
    observer.observe(node)
    return () => observer.disconnect()
  }, [enabled])
  return { ref, width }
}

type Options = { key: string; width: number; initial: number; min: number; max: number; remaining: number; ratio?: number }

export function usePaneSize({ key, width, initial, min, max, remaining, ratio }: Options) {
  const [preferred, setPreferred] = useState<number | null>(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(key) || 'null')
      return typeof stored === 'number' && Number.isFinite(stored) ? stored : null
    } catch { return null }
  })
  const maximum = width ? Math.max(min, Math.min(max, width - remaining)) : max
  const clamp = (value: number) => Math.round(Math.max(min, Math.min(maximum, value)))
  const size = clamp(preferred ?? (ratio && width ? width * ratio : initial))
  // Window narrowing clamps the visible size without destroying the saved preference.
  function resize(value: number, persist = true) {
    const next = clamp(value)
    setPreferred(next)
    if (persist) {
      try { localStorage.setItem(key, JSON.stringify(next)) } catch { /* Layout still works without storage. */ }
    }
    return next
  }
  function reset() {
    setPreferred(null)
    try { localStorage.removeItem(key) } catch { /* Keep the in-memory default. */ }
  }
  return { size, minimum: min, maximum, resize, reset }
}
