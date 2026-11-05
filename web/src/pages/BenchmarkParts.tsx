import { useLocalDraft } from '../useLocalDraft'
import { ReadingMeta } from './StudyWorkspace'
import { useRef, useState, type RefObject } from 'react'
import { api, type DraftReference } from '../api'
import type { Benchmark, BenchmarkCard, BenchmarkReview } from '../benchmarkTypes'
import { useToast } from '../ui'
import { ReferenceReading } from './ReferenceReading'
import { TagChips } from '../TagChips'
import { BenchmarkIdea } from '../BenchmarkIdea'

export function BenchmarkReader({ source, active, onSelect, root }: {
  source: Benchmark; active: string; onSelect: (key: string) => void; root: RefObject<HTMLDivElement | null>
}) {
  const [quote,setQuote]=useState(''),[making,setMaking]=useState(false)
  const article: DraftReference = { id: source.id, draft_id: 0, title: source.title, body: source.body, url: source.url, source: 'paste', created_at: source.created_at }
  const html = source.passages.map(p => `<section class="benchmark-passage${active === p.key ? ' selected' : ''}" data-bm-key="${p.key}" data-index="${p.index}" tabindex="0" aria-label="第${p.index}段">${p.html}</section>`).join('')
  function select(target: EventTarget) {
    const node = (target as HTMLElement).closest<HTMLElement>('[data-bm-key]')
    if (node?.dataset.bmKey) onSelect(node.dataset.bmKey)
  }
  return <div ref={root} className="benchmark-reader" onMouseUp={()=>{const s=window.getSelection();if(s&&!s.isCollapsed&&root.current?.contains(s.anchorNode)&&root.current?.contains(s.focusNode))setQuote(s.toString().trim())}} onKeyUp={()=>{const s=window.getSelection();if(s&&!s.isCollapsed&&root.current?.contains(s.anchorNode)&&root.current?.contains(s.focusNode))setQuote(s.toString().trim())}} onPointerEnter={() => {
    // Native selects retain wheel focus on Windows after their popup closes.
    // Release only an external select; never steal focus from a writing field.
    const focused = document.activeElement
    if (focused instanceof HTMLSelectElement && !root.current?.contains(focused)) focused.blur()
  }} onClick={e => select(e.target)} onKeyDown={e => {
    if ((e.key === 'Enter' || e.key === ' ') && (e.target as HTMLElement).matches('[data-bm-key]')) { e.preventDefault(); select(e.target) }
  }}>
    {source.url && <a className="small" href={source.url} target="_blank" rel="noopener noreferrer">打开原始来源</a>}
    {source.media_id && <a className="media-source-link" href={`#/imports/${source.media_id}`}>{source.extraction_only?'原始文件、转写与音频':'小D整理稿 · 查看原始转写与音频'}</a>}
    <h1>{source.title}</h1>
    <p className="small muted">{source.author || '作者待补充'} · {source.kind} · {source.passages.length} 段</p>
    <ReadingMeta source={source} editable/><TagChips tags={source.tags} />
    <div className="row wrap" style={{position:'sticky',top:0,zIndex:2,background:'var(--surface)',padding:'8px 0'}}><button className="btn primary sm" disabled={!quote||quote.length>1800} onMouseDown={e=>e.preventDefault()} onClick={()=>setMaking(true)}>拿来做选题</button><span className="small muted">{quote?`已选 ${quote.length} 字${quote.length>1800?'，请选择1800字以内':''}`:'先在原文中选中有用的一段'}</span></div>
    {making&&<BenchmarkIdea source={source} quote={quote} onClose={()=>setMaking(false)}/>}
    {source.original_html ? <div className="source-preserved" dangerouslySetInnerHTML={{__html:source.original_html}}/> : source.stale
      ? <div className="reference-article" dangerouslySetInnerHTML={{ __html: html }} />
      : <ReferenceReading key={`${source.id}-${source.body_hash}`} article={article} html={html} markLabel="标记高亮" highlightsPath={`/api/benchmarks/${source.id}/highlights`} />}
  </div>
}

export function BenchmarkOverview({ source }: { source: Benchmark }) {
  if (!source.analysis) return null
  const labels: Record<string, string> = { topic: '主题', audience: '读者与处境', thesis: '核心观点', purpose: '写作目的', limits: '依据与边界' }
  return <details className="benchmark-overview"><summary>{source.analysis.scope === 'full' ? '整体概览' : '所选段落概览'}</summary>
    {Object.entries(labels).map(([key, label]) => <div key={key}><strong>{label}</strong><p>{source.analysis!.overview[key] || '未说明'}</p></div>)}
  </details>
}

export function BenchmarkAnalysisCard({ source, card, index, active, onSelect, onUse, onReview }: {
  source: Benchmark; card: BenchmarkCard; index: number; active: boolean; onSelect: () => void
  onUse: () => void; onReview: (review: BenchmarkReview) => void
}) {
  const toast = useToast()
  const [makingIdea,setMakingIdea]=useState(false)
  const passage=source.passages.find(p=>p.key===card.key)
  const review = source.reviews.find(r => r.paragraph_key === card.key)
  const [recovery,setRecovery]=useLocalDraft(`cw-passage-judgment:${source.id}:${source.analysis?.id}:${card.key}`,{note:review?.note||''})
  const note=recovery.note
  const setNote=(note:string)=>setRecovery({note})
  const [pending, setPending] = useState(0)
  const saves = useRef(Promise.resolve())
  async function save(patch: { note?: string; useful?: boolean; reviewed?: boolean }) {
    if (!source.analysis) return
    const path = `/api/benchmarks/${source.id}/analyses/${source.analysis.id}/reviews/${card.key}`
    const payload = { note, ...patch }
    setPending(n => n + 1)
    saves.current = saves.current.then(async () => {
      try { onReview(await api.put<BenchmarkReview>(path, payload)) }
      catch (e) { toast((e as Error).message, 'error') }
      finally { setPending(n => n - 1) }
    })
    await saves.current
  }
  return <section className={'benchmark-analysis-card' + (active ? ' active' : '')} data-card-key={card.key}>
    <button className="benchmark-card-head" aria-expanded={active} onClick={onSelect}><span className="benchmark-number">{index}</span><strong>第 {index} 段 · {card.role}</strong><span>{active ? '−' : '+'}</span></button>
    {active && <div className="benchmark-card-content">
      <label>这一段的作用</label><p>{card.purpose}</p>
      <label>可借鉴的写法</label><p>{card.technique}</p>
      <label>论据与待核实内容</label><p>{card.evidence}</p>
      <label>仿写时怎么用</label><p>{card.adaptation}</p>
      <label>需要你补充</label><ul>{card.needs.map((n, i) => <li key={i}>{n}</li>)}</ul>
      <label htmlFor={`benchmark-note-${source.id}-${card.key}`}>我的判断</label>
      <textarea id={`benchmark-note-${source.id}-${card.key}`} className="textarea" value={note} maxLength={8000} placeholder="尚未填写：我认同什么、哪里要纠正、还需验证什么…" onChange={e => setNote(e.target.value)} onBlur={() => { if (note !== (review?.note || '')) void save({ note }) }} />
      <div className="row wrap small muted" role="status">{pending ? '正在保存判断…' : `AI 分析 · ${review?.reviewed ? '你已标记审阅' : '待你审阅'}`}</div>
      <div className="row wrap">
        <button className="btn sm" aria-pressed={!!review?.useful} onClick={() => save({ useful: !review?.useful })}>{review?.useful ? '已标记有用' : '标记有用'}</button>
        <button className="btn ghost sm" onClick={() => save({ reviewed: !review?.reviewed })}>{review?.reviewed ? '取消审阅标记' : '标记已审阅'}</button>
        <button className="btn primary sm" disabled={!passage?.text||passage.text.length>1800} onClick={()=>setMakingIdea(true)}>拿来做选题</button>
        <button className="btn sm" onClick={onUse}>用于仿写</button>
      </div>
    </div>}
    {makingIdea&&passage&&<BenchmarkIdea source={source} quote={passage.text} onClose={()=>setMakingIdea(false)}/>}
  </section>
}
