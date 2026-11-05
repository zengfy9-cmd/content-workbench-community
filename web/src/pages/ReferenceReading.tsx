import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type DraftReference } from '../api'

type Highlight = { id: number; start: number; end: number; quote: string }
type Selection = Omit<Highlight, 'id'>

export function ReferenceReading({ article, html, highlightsPath, markLabel = '标记已使用' }: { article: DraftReference; html: string; highlightsPath?: string; markLabel?: string }) {
  const root = useRef<HTMLDivElement>(null)
  const [marks, setMarks] = useState<Highlight[]>([])
  const [selection, setSelection] = useState<Selection | null>(null)
  const [selectedMark, setSelectedMark] = useState<number | null>(null)
  const [ready, setReady] = useState(false)
  const [busy, setBusy] = useState(false)
  const saving = useRef(false)
  const [error, setError] = useState('')
  const path = highlightsPath || `/api/drafts/${article.draft_id}/references/${article.id}/highlights`
  useEffect(() => {
    let active = true
    api.get<Highlight[]>(path).then(items => { if (active) { setMarks(items); setReady(true) } })
      .catch(e => { if (active) setError((e as Error).message) })
    return () => { active = false }
  }, [path])

  const markedHtml = useMemo(() => {
    const doc = document.createElement('div')
    doc.innerHTML = html
    const text = doc.textContent || ''
    const valid = marks.filter(m => text.slice(m.start, m.end) === m.quote)
    const walker = document.createTreeWalker(doc, NodeFilter.SHOW_TEXT)
    const nodes: Text[] = []
    while (walker.nextNode()) nodes.push(walker.currentNode as Text)
    let offset = 0
    for (const node of nodes) {
      const value = node.data
      const start = offset
      offset += value.length
      const hits = valid.filter(m => m.start < offset && m.end > start)
      if (!hits.length) continue
      const points = [...new Set([0, value.length, ...hits.flatMap(m => [Math.max(0, m.start-start), Math.min(value.length,m.end-start)])])].sort((a,b)=>a-b)
      const fragment = document.createDocumentFragment()
      for (let i=0; i<points.length-1; i++) {
        const part = document.createTextNode(value.slice(points[i],points[i+1]))
        const hit = hits.find(m => m.start <= start+points[i] && m.end >= start+points[i+1])
        if (hit) {
          const mark = document.createElement('mark')
          mark.dataset.highlightId = String(hit.id)
          mark.title = '已使用 · 点击后可取消高光'
          mark.append(part); fragment.append(mark)
        } else fragment.append(part)
      }
      node.replaceWith(fragment)
    }
    return doc.innerHTML
  }, [html, marks])

  function readSelection(): Selection | null {
    const selected = window.getSelection()
    const container = root.current
    if (!container || !selected || selected.isCollapsed || !selected.rangeCount) return null
    const range = selected.getRangeAt(0)
    if (!container.contains(range.startContainer) || !container.contains(range.endContainer)) return null
    const before = range.cloneRange()
    before.selectNodeContents(container); before.setEnd(range.startContainer,range.startOffset)
    const start = before.toString().length
    const quote = range.toString()
    return quote.trim() ? { start, end:start+quote.length, quote } : null
  }

  function capture() {
    setSelection(readSelection())
    setSelectedMark(null)
  }

  async function save(chosen: Selection | null = selection) {
    if (!chosen || !ready || saving.current) return
    saving.current = true
    setBusy(true); setError('')
    try {
      const mark = await api.post<Highlight>(path,chosen)
      setMarks(items => [...items.filter(m=>m.id!==mark.id),mark])
      setSelection(null); window.getSelection()?.removeAllRanges()
    } catch (e) { setError((e as Error).message) }
    finally { saving.current = false; setBusy(false) }
  }

  async function remove() {
    if (selectedMark === null || busy) return
    setBusy(true); setError('')
    try { await api.del(`${path}/${selectedMark}`); setMarks(items=>items.filter(m=>m.id!==selectedMark)); setSelectedMark(null) }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }

  return <>
    <div className="reference-highlight-tools">
      <button className="btn ghost sm" disabled={!ready || busy || !selection} onMouseDown={e=>e.preventDefault()} onClick={()=>save()}>{busy ? '保存中…' : markLabel}</button>
      <span className="small muted">{selection ? `选中 ${selection.quote.length} 字 · 右键加高光` : `选中文字后右键加高光${marks.length ? ` · 已标 ${marks.length} 处` : ''}`}</span>
      {selectedMark !== null && <button className="btn ghost sm" disabled={busy} onClick={remove}>取消这处高光</button>}
      {error && <span className="small" role="alert">{error}</span>}
    </div>
    <div ref={root} className="reference-article" tabIndex={0} aria-label={`阅读原文：${article.title}`}
      onMouseUp={capture} onKeyUp={capture} onTouchEnd={capture}
      onMouseDown={e => { if (e.button === 2 && readSelection()) e.preventDefault() }}
      onContextMenu={e => {
        const chosen = readSelection()
        if (!chosen) return
        e.preventDefault()
        void save(chosen)
      }}
      onClick={e => { const mark = (e.target as HTMLElement).closest<HTMLElement>('mark[data-highlight-id]'); if (mark && window.getSelection()?.isCollapsed) setSelectedMark(Number(mark.dataset.highlightId)) }}
      dangerouslySetInnerHTML={{ __html: markedHtml }} />
  </>
}
