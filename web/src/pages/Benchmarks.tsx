import { MethodCapture } from '../AssetLinks'
import { VideoEvidence } from './VideoEvidence'
import { ResearchControls, ResearchTrace, ResearchJudgment, useResearchChoice } from '../ResearchControls'
import { enableHoverScroll } from '../hoverScroll'
import { ResearchArticle, BasicResearchArticle, ReadingMeta } from './StudyWorkspace'
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { api, when } from '../api'
import type { Benchmark, BenchmarkReview, BenchmarkSummary } from '../benchmarkTypes'
import { go } from '../App'
import { Dialog, Empty, useToast } from '../ui'
import * as Ic from '../icons'
import { PaneResizer } from '../PaneResizer'
import { usePaneContainer, usePaneSize } from '../usePaneSize'
import { BenchmarkAnalysisCard, BenchmarkOverview, BenchmarkReader } from './BenchmarkParts'
import { BenchmarkImport } from './BenchmarkImport'
import { TagChips } from '../TagChips'

export function Benchmarks({ id, targetDraft, initialAnalysis }: { id?: number; targetDraft?: number; initialAnalysis?:number }) {
  useEffect(()=>{const node=workspace.ref.current;if(node)return enableHoverScroll(node)},[])
  const toast = useToast()
  const [items, setItems] = useState<BenchmarkSummary[]>([])
  const [source, setSource] = useState<Benchmark | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [q, setQ] = useState('')
  const [libraryOpen,setLibraryOpen]=useState(!id)
  useEffect(()=>setLibraryOpen(!id),[id])
  const [researchChoice,setResearchChoice]=useResearchChoice()
  const [kind, setKind] = useState('全部')
  const [sourcePlatform,setSourcePlatform]=useState('')
  const [authorFilter, setAuthorFilter] = useState('')
  const [archived, setArchived] = useState(false)
  const [adding, setAdding] = useState(false)
  const [active, setActive] = useState('')
  const [version, setVersion] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [ideaOpen, setIdeaOpen] = useState(false)
  const [ideaTitle, setIdeaTitle] = useState('')
  const [ideaAngle, setIdeaAngle] = useState('')
  const [editing, setEditing] = useState(false)
  const [metaAuthor, setMetaAuthor] = useState('')
  const [metaKind, setMetaKind] = useState('未分类')
  const [metaTags, setMetaTags] = useState('')
  const reader = useRef<HTMLDivElement>(null)
  const analyses = useRef<HTMLDivElement>(null)
  const workspace = usePaneContainer()
  const libraryWidth = usePaneSize({ key: 'cw-pane-benchmark-library-v1', width: workspace.width, initial: 230, min: 180, max: 360, remaining: 700 })
  const reading = usePaneContainer(!!id)
  const analysisWidth = usePaneSize({ key: 'cw-pane-benchmark-analysis-v1', width: reading.width, initial: 390, min: 300, max: 650, remaining: 340 })
  const list = useCallback(() => api.get<BenchmarkSummary[]>(`/api/benchmarks?archived=${archived}`).then(setItems).catch(e => setError(e.message)), [archived])
  useEffect(() => { void list() }, [list])
  useEffect(() => {
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void list() }, 5000)
    return () => window.clearInterval(timer)
  }, [list])
  useEffect(() => { setVersion(initialAnalysis||null); setSource(null); setActive('') }, [id,initialAnalysis])
  useEffect(() => {
    if (!id) { setLoading(false); return }
    let alive = true
    setLoading(true); setError('')
    const load = () => api.get<Benchmark>(`/api/benchmarks/${id}${version ? `?analysis_id=${version}` : ''}`).then(item => {
      if (!alive) return
      setSource(item); setLoading(false)
      setActive(previous => item.passages.some(p => p.key === previous) ? previous : '')
      if (!['queued', 'running'].includes(item.analysis_state)) void list()
    }).catch(e => { if (alive) { setError(e.message); setLoading(false) } })
    void load()
    return () => { alive = false }
  }, [id, version, list])
  const inProgress = !!source && ['queued', 'running'].includes(source.analysis_state)
  async function analyze(currentOnly: boolean) {
    if (!id || !source || busy) return
    setBusy(true)
    try {
      await api.post(`/api/benchmarks/${id}/analyze`, currentOnly ? { paragraph_keys: [active],model:researchChoice.model } : {format: 'research',...researchChoice})
      setVersion(null)
      const item = await api.get<Benchmark>(`/api/benchmarks/${id}`)
      setSource(item)
      setError('')
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) }
  }
  useEffect(() => {
    if (!inProgress || !id) return
    let alive = true
    const timer = window.setInterval(() => {
      api.get<Benchmark>(`/api/benchmarks/${id}`).then(item => { if (!alive) return; setSource(item); if (!['queued', 'running'].includes(item.analysis_state)) void list() }).catch(e => { if (alive) setError(e.message) })
    }, 2500)
    return () => { alive = false; window.clearInterval(timer) }
  }, [inProgress, id, list])
  async function pause() {
    if (!id || busy) return
    setBusy(true)
    try { setSource(await api.post<Benchmark>(`/api/benchmarks/${id}/pause`, {})); toast('拆解已暂停'); void list(); window.dispatchEvent(new Event('cw-refresh-status')) }
    catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) }
  }
  function select(key: string, from: 'reader' | 'analysis') {
    setActive(key)
    requestAnimationFrame(() => {
      const card = analyses.current?.querySelector<HTMLElement>(`[data-card-key="${key}"]`)
      const scroller = card?.closest<HTMLElement>('.benchmark-cards')
      if (card && scroller) scroller.scrollTop += card.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 12
    })
    const container = from === 'reader' ? analyses.current : reader.current
    const attribute = from === 'reader' ? 'data-card-key' : 'data-bm-key'
    let target=container?.querySelector<HTMLElement>(`[${attribute}="${key}"]`)
    if(!target && from==='analysis' && source?.original_html){
      const clean=(text:string)=>text.replace(/\s+/g,'')
      const text=clean(source.passages.find(p=>p.key===key)?.text||'').slice(0,45)
      target=Array.from(container?.querySelectorAll<HTMLElement>('p,h1,h2,h3,h4,li,section')||[]).filter(n=>text && clean(n.textContent||'').includes(text)).sort((a,b)=>(a.textContent?.length||0)-(b.textContent?.length||0))[0]
    }
    if(target && container)container.scrollTop+=target.getBoundingClientRect().top-container.getBoundingClientRect().top-12
  }
  async function write(paragraphKey?: string) {
    if (!source || busy) return
    setBusy(true)
    try {
      const result = await api.post<{ id: number }>(`/api/benchmarks/${source.id}/draft`, { ...(targetDraft ? { draft_id: targetDraft } : {}), analysis_id: source.analysis?.id ?? null })
      window.dispatchEvent(new Event('cw-refresh-status'))
      go(`drafts/${result.id}?study=${source.id}${paragraphKey ? `&passage=${encodeURIComponent(paragraphKey)}` : ''}`)
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) }
  }
  async function archive() {
    if (!source) return
    try { await api.put(`/api/benchmarks/${source.id}`, { archived: !source.archived }); toast(source.archived ? '已恢复收藏' : '已移入归档，可随时恢复'); go('benchmarks'); await list() }
    catch (e) { toast((e as Error).message, 'error') }
  }
  async function saveIdea() {
    if (!source || busy) return
    setBusy(true)
    try { await api.post(`/api/benchmarks/${source.id}/idea`, { title: ideaTitle, angle: ideaAngle }); setIdeaOpen(false); setIdeaTitle(''); setIdeaAngle(''); toast('已保存为待挑选题，并保留对标来源'); window.dispatchEvent(new Event('cw-refresh-status')) }
    catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) }
  }
  async function saveMetadata() {
    if (!source || busy) return
    setBusy(true)
    try {
      setSource(await api.put<Benchmark>(`/api/benchmarks/${source.id}`, { author: metaAuthor, kind: metaKind, tags: metaTags.split(/[，,]/).map(t => t.trim()).filter(Boolean) }))
      setEditing(false); void list()
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) }
  }
  function onReview(review: BenchmarkReview) { setSource(s => s?.analysis?.id === review.analysis_id ? { ...s, reviews: [...s.reviews.filter(r => r.paragraph_key !== review.paragraph_key), review] } : s) }
  const needle = q.trim().toLowerCase()
  const authors = [...new Set(items.map(item => item.author).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh-CN'))
  const shown = items.filter(item => (!sourcePlatform || item.source_platform===sourcePlatform) && (!authorFilter || item.author === authorFilter) && (kind === '全部' || item.kind === kind) && (!needle || `${item.title} ${item.author} ${item.tags.join(' ')}`.toLowerCase().includes(needle)))
  return <div className={'benchmark-page'+(id&&!libraryOpen?' catalog-collapsed':'')} ref={workspace.ref} style={{ '--benchmark-library': `${libraryWidth.size}px`, '--benchmark-analysis': `${analysisWidth.size}px` } as CSSProperties}>
    <header className="benchmark-top">
      {id && <button className="btn ghost sm benchmark-back" onClick={() => go(`benchmarks${targetDraft ? `?draft=${targetDraft}` : ''}`)}><Ic.Back size={15} />列表</button>}
      <button className="btn sm" aria-expanded={libraryOpen} onClick={()=>setLibraryOpen(v=>!v)}>☰ 对标目录</button><h1>对标库</h1><span className="small muted">原文、拆解与我的判断</span>
      <div className="right row wrap"><button className="btn" onClick={() => go('imports')}><Ic.Plus size={15} />批量导入</button>
        {source && <button className="btn primary" disabled={busy} onClick={() => write()}>{targetDraft ? '关联到当前稿件' : '对照仿写'}</button>}</div>
    </header>
    <div className={'benchmark-body' + (id ? ' has-selection' : '')}>
      <aside className="benchmark-library" id="benchmark-library">
        <PaneResizer label="对标列表宽度" controls="benchmark-library" edge="end" pane={libraryWidth} />
        <div className="benchmark-library-head"><h2>我的对标</h2><input className="input" value={q} onChange={e => setQ(e.target.value)} placeholder="搜索标题、作者、标签" aria-label="搜索对标" />
          <div className="row wrap"><select className="select" aria-label="对标类型" value={kind} onChange={e => setKind(e.target.value)}>{['全部', '未分类', '教程', '案例', '观点', '故事', '清单', '其他'].map(v => <option key={v}>{v}</option>)}</select>
            <button className="btn ghost sm" aria-pressed={archived} onClick={() => { setArchived(!archived); setAuthorFilter('') }}>{archived ? '查看收藏' : '归档'}</button></div>
          <select className="select" aria-label="筛选来源平台" value={sourcePlatform} onChange={e=>setSourcePlatform(e.target.value)}><option value="">全部来源平台</option>{[...new Set(items.map(i=>i.source_platform).filter(Boolean))].map(p=><option key={p}>{p}</option>)}</select>
          <select className="select" aria-label="筛选作者或账号" value={authorFilter} onChange={e => setAuthorFilter(e.target.value)}><option value="">全部作者/账号</option>{authors.map(author => <option key={author}>{author}</option>)}</select></div>
        <div className="benchmark-library-items">{shown.map(item => <button key={item.id} className={'benchmark-list-item' + (id === item.id ? ' selected' : '')} onClick={() => { setActive(''); go(`benchmarks/${item.id}${targetDraft ? `?draft=${targetDraft}` : ''}`) }}>
          <strong>{item.title}</strong>{item.source_platform && <span className="small muted">来源：{item.source_platform}{item.source_kind?` · ${item.source_kind}`:''}</span>}<span className="small muted">{item.author || '作者待补充'}</span><span className="row wrap"><span className="badge">{item.kind}</span><span className="small muted">{item.current_analysis_id ? '已有拆解' : '待拆解'}</span></span>
          <ReadingMeta source={item}/><TagChips tags={item.tags} />
        </button>)}{!shown.length && <div className="benchmark-list-empty muted small">{items.length ? '没有符合条件的对标' : '收藏文章链接或粘贴正文，先保留值得研究的原文。'}</div>}</div>
      </aside>
      <div className="benchmark-detail" ref={reading.ref}>
        {!source ? <div className="benchmark-empty"><Empty icon={<Ic.Doc />} title={loading ? '正在读取对标…' : '从一篇原文开始'}>{error || '先在左侧选择原文，再逐段研究它的写法。'}</Empty><button className="btn primary" onClick={() => setAdding(true)}>收藏第一篇对标</button></div> : <>
          <main className="benchmark-original"><div className="benchmark-column-head"><h2>{source.media_id?(source.extraction_only?'原始转写':'来源整理稿'):'原文'}</h2><div className="row wrap"><button className="btn ghost sm" onClick={() => { setMetaAuthor(source.author); setMetaKind(source.kind); setMetaTags(source.tags.join('，')); setEditing(true) }}>分类与作者</button><button className="btn ghost sm" onClick={archive}>{source.archived ? '恢复收藏' : '归档'}</button></div></div>
            {source.media_id&&<VideoEvidence key={source.id} bid={source.id}/>}<BenchmarkReader source={source} active={active} onSelect={key => select(key, 'reader')} root={reader} />
          </main>
          <aside className="benchmark-analysis" id="benchmark-analysis" ref={analyses}>
            <PaneResizer label="逐段拆解宽度" controls="benchmark-analysis" edge="start" pane={analysisWidth} />
            <div className="benchmark-column-head"><h2>研究与审核</h2><span className="small muted">{source.analysis?.basic ? `基础研究 · 原文 ${source.passages.length} 段` : source.analysis?.research ? `全文研究 · 原文 ${source.passages.length} 段` : source.stale ? `旧版 ${source.analysis?.paragraphs.length || 0} 段 · 原文 ${source.passages.length} 段` : `${source.analysis?.paragraphs.length || 0}/${source.passages.length} 段`}</span></div>
            <div className="benchmark-analysis-actions"><ResearchControls value={researchChoice} onChange={setResearchChoice} disabled={busy||inProgress}/><div className="row wrap">
              <button className="btn primary sm" disabled={busy || inProgress} onClick={() => analyze(false)}>{inProgress ? '拆解中…' : source.analysis ? '重新研究全文' : '研究全文'}</button>
              <button className="btn sm" disabled={busy || inProgress || !active} onClick={() => analyze(true)}>补充拆解所选段</button>{inProgress && <button className="btn sm" disabled={busy} onClick={pause}>暂停拆解</button>}</div>
              <p className="small muted">研究全文保留结构表、具体写法与金句。局部拆解需先点击正文段落，只作补充。</p>
              {inProgress && <p className="small" role="status">{source.analysis_progress || '等待分析…'}</p>}
              {source.analysis_error && <div className="callout small" role="alert">{source.analysis_error}</div>}
              {source.stale && <div className="callout small">原文已更新。这份拆解对应旧版本，可重新拆解。</div>}
              {source.versions.length > 0 && <select className="select" aria-label="拆解版本" disabled={inProgress} value={version || ''} onChange={e => setVersion(e.target.value ? Number(e.target.value) : null)}><option value="">最新拆解</option>{source.versions.map(v => <option key={v.id} value={v.id}>{when(v.created_at)} · 版本 {v.id}</option>)}</select>}
            </div>
            <div className="benchmark-cards"><MethodCapture key={source.analysis?.id} source={source}/>{source.media_id && <p className="callout small">视频研究依据转写整理稿；金句、专名与数字仍需对照原始字幕或音轨核验。</p>}{source.full_report?.analysis && <section><h3>全文报告 · 保留独立审核记录</h3><ResearchTrace metadata={source.full_report.analysis.metadata}/>{source.full_report.analysis.research && <ResearchArticle report={source.full_report.analysis.research} onLocate={key=>select(key,'analysis')}/>} {source.full_report.analysis.basic && <BasicResearchArticle report={source.full_report.analysis.basic} onLocate={key=>select(key,'analysis')}/>}<ResearchJudgment key={source.full_report.analysis.id} source={source.full_report}/><h3>局部补充拆解</h3></section>}<ResearchTrace metadata={source.analysis?.metadata}/>{source.analysis?.basic&&<BasicResearchArticle report={source.analysis.basic} onLocate={key=>select(key,'analysis')}/>}{source.analysis?.research && <ResearchArticle report={source.analysis.research} onLocate={key=>select(key,'analysis')}/>}{!source.analysis?.research&&!source.analysis?.basic&&<BenchmarkOverview source={source} />}{(source.analysis?.research||source.analysis?.basic)&&<ResearchJudgment key={source.analysis.id} source={source}/>}
              {source.analysis?.paragraphs.map(card => <BenchmarkAnalysisCard key={`${source.analysis!.id}-${card.key}`} source={source} card={card} index={source.passages.find(p => p.key === card.key)?.index || Number(card.key.match(/^p(\d+)-/)?.[1]) || 1} active={card.key === active} onSelect={() => select(card.key, 'analysis')} onUse={() => write(card.key)} onReview={onReview} />)}
              {!source.analysis && <div className="benchmark-analysis-empty"><h3>先看原文，再选择拆解范围</h3><p>拆解会分别说明段落作用、表达写法、依据与缺口，以及仿写所需的真实材料。</p><p className="small muted">原文较长时，可以逐段拆解。个人判断始终由你填写。</p></div>}
              {source.analysis && !source.analysis.research && !source.analysis.basic && !source.analysis.paragraphs.some(c => c.key === active) && <p className="small muted">当前段落尚未拆解，可以点击“拆解当前段”。</p>}
              {source.analysis && <button className="btn sm" onClick={() => setIdeaOpen(true)}>另存我的候选选题</button>}
            </div>
          </aside>
        </>}
      </div>
    </div>
    <BenchmarkImport open={adding} onClose={() => { setAdding(false); void list() }} onChanged={list} onSaved={item => { setAdding(false); setSource(item); setArchived(!!item.archived); setAuthorFilter(''); go(`benchmarks/${item.id}${targetDraft ? `?draft=${targetDraft}` : ''}`); void list() }} />
    <Dialog open={editing} title="分类与作者" onClose={() => setEditing(false)} footer={<button className="btn primary" disabled={busy} onClick={saveMetadata}>保存资料</button>}><div className="stack"><label className="field"><span>作者</span><input className="input" value={metaAuthor} maxLength={120} onChange={e => setMetaAuthor(e.target.value)} /></label><label className="field"><span>内容类型</span><select className="select" value={metaKind} onChange={e => setMetaKind(e.target.value)}>{['未分类', '教程', '案例', '观点', '故事', '清单', '其他'].map(t => <option key={t}>{t}</option>)}</select></label><label className="field"><span>标签，用逗号分开</span><input className="input" value={metaTags} onChange={e => setMetaTags(e.target.value)} /></label></div></Dialog>
    <Dialog open={ideaOpen} onClose={() => setIdeaOpen(false)} title="记下我的选题启发" footer={<button className="btn primary" disabled={busy || !ideaTitle.trim()} onClick={saveIdea}>存为待挑选题</button>}>
      <div className="stack"><p className="small muted">来源保留在记录里；填写你自己的选题和角度。</p><label className="field"><span>候选选题</span><input className="input" value={ideaTitle} onChange={e => setIdeaTitle(e.target.value)} maxLength={200} /></label><label className="field"><span>我的角度</span><textarea className="textarea" value={ideaAngle} onChange={e => setIdeaAngle(e.target.value)} maxLength={2000} /></label></div>
    </Dialog>
  </div>
}
