import { ResearchControls, ResearchTrace, ResearchJudgment, useResearchChoice } from '../ResearchControls'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Benchmark, BenchmarkCard, BenchmarkSummary, ResearchReport, BasicResearch } from '../benchmarkTypes'
import { Dialog, useToast } from '../ui'
import { BenchmarkReader } from './BenchmarkParts'
import { BenchmarkImport } from './BenchmarkImport'
import { TagChips } from '../TagChips'
import { TipCapture } from './ContentTips'

export function ReadingMeta({ source,editable=false }: { editable?:boolean;source: { id?:number; read_count?: number | null; read_count_at?: number | null } }) {
  const [value,setValue]=useState('');const [manual,setManual]=useState<number|null>(null);const toast=useToast()
  useEffect(()=>{setManual(null);setValue('')},[source.id,source.read_count])
  const count=manual??source.read_count
  async function save(){try{await api.put(`/api/benchmarks/${source.id}`,{read_count:Number(value)});setManual(Number(value));setValue('');toast('阅读量已补录')}catch(e){toast((e as Error).message,'error')}}
  return <div className="reading-meta">阅读量：<b>{count == null ? '暂未获取' : count.toLocaleString('zh-CN')}</b>{source.read_count_at && <span> · {new Date(source.read_count_at * 1000).toLocaleDateString('zh-CN')} 获取</span>}{editable && source.id && <details><summary>补录阅读量</summary><div className="row"><input className="input" type="number" min="0" max="10000000000" aria-label="手动补录阅读量" value={value} onChange={e=>setValue(e.target.value)}/><button className="btn sm" disabled={!/^\d+$/.test(value)} onClick={()=>void save()}>保存</button></div></details>}</div>
}

export function ResearchArticle({ report, onLocate }: { report: ResearchReport; onLocate?: (key: string) => void }) {
  return <article className="research-report">
    <p>{report.audience}</p>
    <div className="research-lenses">
      {([['核心观点','thesis'],['情绪与读者处境','emotion'],['材料与佐证','evidence']] as const).map(([label,key]) => <section key={key} className={`lens-${key}`}><h3>{label}</h3><p>{report[key]}</p></section>)}
    </div>
    <p><strong>写作目的与推测依据：</strong>{report.purpose}</p>
    <h3>金句与表达</h3>{report.golden_quotes.length ? report.golden_quotes.map(q => <blockquote key={q}>{q}</blockquote>) : <p className="muted">没有提取到可核对的金句。</p>}
    <h3>文章结构与具体写法</h3><div className="research-table-scroll"><table><thead><tr><th>原文位置</th><th>说了什么</th><th>全文作用</th><th>具体写法与局限</th></tr></thead><tbody>{report.sections.map((row,i) => <tr key={i}><td><button className="source-location" onClick={() => onLocate?.(row.paragraph_key)}>{row.location}</button></td><td>{row.summary}</td><td>{row.role}</td><td>{row.technique}</td></tr>)}</tbody></table></div>
    <h3>对我的参考价值</h3><p>{report.reference_value}</p>
    <section className="writing-prep"><h3>我的写作准备</h3><p className="small muted">AI 建议，待我确认；具体经历与结果由我补充。</p><p>{report.writing_prep}</p></section>
  </article>
}

export function DraftOriginal({ draftId, initialSource, source, onSource, locateKey, onCollapse }: {
  draftId: number; initialSource?: number; source: Benchmark | null; onSource: (s: Benchmark | null) => void; locateKey: string; onCollapse?:()=>void
}) {
  const [items,setItems]=useState<BenchmarkSummary[]>([])
  const [selected,setSelected]=useState<number | null>(initialSource || null)
  const [adding,setAdding]=useState(false)
  const [picking,setPicking]=useState(false)
  const [library,setLibrary]=useState<BenchmarkSummary[]>([])
  const [query,setQuery]=useState('')
  const [checked,setChecked]=useState<number[]>([])
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [raw,setRaw]=useState(true)
  const reader=useRef<HTMLDivElement>(null)
  const toast=useToast()
  const load=useCallback(async()=>{
    try {
      const linked=await api.get<{benchmark_id:number}[]>(`/api/drafts/${draftId}/benchmarks`)
      const sources=await Promise.all(linked.map(link=>api.get<Benchmark>(`/api/benchmarks/${link.benchmark_id}`)))
      setItems(sources)
      setSelected(v=>sources.some(s=>s.id===v) ? v : sources.find(s=>s.id===initialSource)?.id || sources[0]?.id || null)
    } catch(e){setError((e as Error).message)}
  },[draftId,initialSource])
  useEffect(()=>{void load()},[load])
  useEffect(()=>{
    if(!selected){onSource(null);return}
    let alive=true
    onSource(null)
    api.get<Benchmark>(`/api/benchmarks/${selected}`).then(s=>{if(alive){onSource(s);setError('')}}).catch(e=>{if(alive)setError(e.message)})
    return()=>{alive=false}
  },[selected,onSource])
  const running=source && ['queued','running'].includes(source.analysis_state)
  useEffect(()=>{
    if(!running || !selected)return
    let alive=true
    const timer=setInterval(()=>api.get<Benchmark>(`/api/benchmarks/${selected}`).then(s=>{if(alive)onSource(s)}).catch(()=>{}),2000)
    return()=>{alive=false;clearInterval(timer)}
  },[running,selected,onSource])
  useEffect(()=>{
    const targetKey=locateKey.split('|')[0]
    const part=source?.passages.find(p=>p.key===targetKey)
    if(!part || !reader.current)return
    const direct=reader.current.querySelector<HTMLElement>(`[data-bm-key="${targetKey}"]`)
    const candidates=Array.from(reader.current.querySelectorAll<HTMLElement>('p,h1,h2,h3,h4,li,blockquote,section'))
    const normalized=(value:string)=>value.replace(/\s+/g,'')
    const text=normalized(part.text).slice(0,45)
    const node=direct || candidates.filter(n=>text && normalized(n.textContent||'').includes(text)).sort((a,b)=>(a.textContent?.length||0)-(b.textContent?.length||0))[0]
    if(node) reader.current.scrollTop+=node.getBoundingClientRect().top-reader.current.getBoundingClientRect().top-16
  },[locateKey,source,raw])
  async function openPicker(){
    setPicking(true);setQuery('');setChecked([]);setBusy(true)
    try {setLibrary(await api.get<BenchmarkSummary[]>('/api/benchmarks'))}
    catch(e){toast((e as Error).message,'error')}
    finally{setBusy(false)}
  }
  async function attach(ids:number[]){
    setBusy(true)
    try {
      for(const value of ids)await api.post(`/api/benchmarks/${value}/draft`,{draft_id:draftId})
      await load();setSelected(ids[0] || null);setPicking(false);setAdding(false)
    }catch(e){await load();toast((e as Error).message,'error')}
    finally{setBusy(false)}
  }
  async function remove(value:number){
    setBusy(true)
    try {await api.del(`/api/drafts/${draftId}/benchmarks/${value}`);await load()}
    catch(e){toast((e as Error).message,'error')}
    finally{setBusy(false)}
  }
  const candidates=library.filter(s=>!items.some(item=>item.id===s.id) && `${s.title} ${s.author} ${s.tags.join(' ')}`.toLowerCase().includes(query.trim().toLowerCase()))
  return <div className="study-original">
    <header className="study-toolbar"><strong>本篇对标（{items.length}）</strong><div className="row"><button className="btn sm" disabled={busy} onClick={()=>void openPicker()}>＋ 添加</button>{onCollapse&&<button className="btn ghost icon sm" aria-label="收起本篇对标" title="收起本篇对标" onClick={onCollapse}>×</button>}</div></header>
    <div className="draft-reference-list" aria-label="本篇已加入的对标">
      {items.map(s=><div className="draft-reference-row" key={s.id}>
        <button className={'btn ghost draft-reference-title'+(selected===s.id?' selected':'')} aria-pressed={selected===s.id} title={s.title} onClick={()=>setSelected(s.id)}>{s.title}{selected===s.id && <span className="small muted">当前查看</span>}</button>
        <button className="btn ghost sm" disabled={busy} aria-label={`从本篇移出：${s.title}`} title="仅从本篇移出，保留对标库原文" onClick={()=>void remove(s.id)}>移出</button>
      </div>)}
      {!items.length && <p className="small muted">还没有加入对标。点“＋ 添加”选择本篇要参考的文章。</p>}
    </div>
    {error && <p className="callout">{error}</p>}
    {source ? <><div className="source-metadata"><ReadingMeta source={source} editable/>{source.media_id && <a href={`#/imports/${source.media_id}`}>查看原始转写与音频</a>}<span>{source.author || '作者待补充'}</span><div className="row wrap"><button className="btn ghost sm" aria-pressed={raw} onClick={()=>setRaw(!raw)}>{raw?'原文样式':'研究分段'} · 切换</button><a href={source.url} target="_blank" rel="noopener noreferrer">打开来源</a></div></div>
      {raw && source.original_html ? <div className="study-source-scroll" ref={reader} tabIndex={0} aria-label="原文样式阅读"><h2>{source.title}</h2><TagChips tags={source.tags}/><div className="source-preserved" dangerouslySetInnerHTML={{__html:source.original_html}}/></div>
        : <BenchmarkReader source={{...source,original_html:''}} active={locateKey} onSelect={()=>{}} root={reader}/>}</>
      : <div className="card-pad muted">选择或导入对标，原文会保留在中间，右侧显示分析。</div>}
    <Dialog open={picking} title="为本篇添加对标" onClose={()=>{if(!busy)setPicking(false)}} footer={<div className="row wrap"><button className="btn" disabled={busy} onClick={()=>{setPicking(false);setAdding(true)}}>导入新原文</button><button className="btn primary" disabled={busy || !checked.length} onClick={()=>void attach(checked)}>加入本篇（{checked.length}）</button></div>}>
      <div className="stack"><input className="input" aria-label="搜索可添加的对标" placeholder="搜索标题、作者、标签" value={query} onChange={e=>setQuery(e.target.value)}/><p className="small muted">这里只显示尚未加入本篇的文章，可以多选。</p><div className="draft-reference-picker">{candidates.map(s=><label key={s.id} className="draft-reference-choice"><input type="checkbox" disabled={busy} checked={checked.includes(s.id)} onChange={e=>setChecked(v=>e.target.checked?[...v,s.id]:v.filter(id=>id!==s.id))}/><span>{s.title}<span className="small muted">{s.author || '作者待补充'} · {s.kind}</span></span></label>)}{!candidates.length && <p className="muted">{busy?'正在读取对标库…':'没有符合条件的文章，可以导入新原文。'}</p>}</div></div>
    </Dialog>
    <BenchmarkImport open={adding} onClose={()=>setAdding(false)} onChanged={()=>{}} onSaved={s=>{void attach([s.id])}}/>
  </div>
}

export function StudyAnalysis({ source, onSource, onLocate, onUse, allowLearning, onLearning, preparationOnly=false }: {
  preparationOnly?:boolean;source:Benchmark|null;onSource:(s:Benchmark)=>void;onLocate:(key:string)=>void;onUse:(c:BenchmarkCard,title:string)=>void;allowLearning:boolean;onLearning:(v:boolean)=>void
}){
  const toast=useToast();const [busy,setBusy]=useState(false)
  const [researchChoice,setResearchChoice]=useResearchChoice()
  const active=source && ['queued','running'].includes(source.analysis_state)
  async function action(pause=false){
    if(!source)return
    setBusy(true)
    try {await api.post(`/api/benchmarks/${source.id}/${pause?'pause':'analyze'}`,pause?{}:{format:'research',...researchChoice});onSource(await api.get<Benchmark>(`/api/benchmarks/${source.id}`))}catch(e){toast((e as Error).message,'error')}finally{setBusy(false)}
  }
  if(!source)return <p className="card-pad muted">在中间选择一篇对标，即可在这里查看分析。</p>
  const report=source.analysis?.research
  if(preparationOnly && report)return <div className="study-analysis-scroll"><h2>我的写作准备</h2><p className="small muted">AI建议待确认，真实事实与结果由你补充。</p><section className="writing-prep"><p style={{whiteSpace:'pre-wrap'}}>{report.writing_prep}</p></section><h3>与我的读者有什么关系</h3><p>{report.reference_value}</p><ResearchJudgment key={source.analysis?.id} source={source}/></div>
  return <div className="study-analysis-scroll"><ResearchControls value={researchChoice} onChange={setResearchChoice} disabled={!!active||busy}/><div className="row wrap"><button className="btn primary sm" disabled={busy||!!active} onClick={()=>void action()}>{active?'正在研究…':report?'重新研究':'开始研究'}</button>{active && <button className="btn sm" disabled={busy} onClick={()=>void action(true)}>暂停拆解</button>}</div>
    <ResearchTrace metadata={source.analysis?.metadata}/><ReadingMeta source={source}/>{source.analysis_progress && <p className="small muted" role="status">{source.analysis_progress}</p>}{source.analysis_error && <p className="callout">{source.analysis_error}</p>}{source.stale && <p className="callout">这份分析对应旧版原文，可重新研究。</p>}
    <h2>{source.title}</h2>
    {report && <TipCapture seed={{title:`${source.title.slice(0,50)} · 写法参考`,content:report.reference_value,steps:report.writing_prep,origin:'AI建议',source_url:source.url||''}} label="把有用写法存为技巧"/>}
    {report ? <><ResearchArticle report={report} onLocate={onLocate}/><ResearchJudgment key={source.analysis?.id} source={source}/><button className="btn" onClick={()=>onUse({key:source.passages[0]?.key||'',role:'全文结构',purpose:report.thesis,technique:report.reference_value,evidence:report.evidence,adaptation:report.writing_prep,needs:['自己的事实、观点与结果证据']},source.title)}>带入我的写作准备</button></>
      : source.analysis?.basic ? <><BasicResearchArticle report={source.analysis.basic} onLocate={onLocate}/><ResearchJudgment key={source.analysis.id} source={source}/></> : source.analysis ? <><p className="callout small">下面是保留的旧版拆解。点击“开始研究”，可升级为观点、情绪、佐证、金句与结构表。</p><div className="research-table-scroll"><table><thead><tr><th>原文位置</th><th>作用与写法</th><th>佐证与仿写</th></tr></thead><tbody>{source.analysis.paragraphs.map(c=><tr key={c.key}><td><button className="source-location" onClick={()=>onLocate(c.key)}>{c.role}</button></td><td><p>{c.purpose}</p><p>{c.technique}</p></td><td><p>{c.evidence}</p><p>{c.adaptation}</p></td></tr>)}</tbody></table></div></> : <p>尚未分析。全文研究会先概括观点、情绪和材料，再按章节整理结构。</p>}
    <label className="draft-learning-option"><input type="checkbox" checked={allowLearning} onChange={e=>onLearning(e.target.checked)}/>允许本篇改稿用于长期写作习惯</label>
  </div>
}

export function BasicResearchArticle({report,onLocate}:{report:BasicResearch;onLocate?:(key:string)=>void}){
 return <article className="research-report basic-research">{([['thesis','核心观点'],['audience','目标受众'],['hook','开头 Hook'],['structure','内容结构'],['techniques','表达技巧'],['evidence','论证与证据'],['further_research','值得进一步研究']] as const).map(([field,label])=><section key={field}><h3>{label}</h3><p>{report[field]}</p>{report.basis.filter(b=>b.field===field).map((basis,i)=><button className="source-location basis-quote" key={i} onClick={()=>onLocate?.(basis.paragraph_key)}>原文依据：「{basis.quote}」 ↗</button>)}</section>)}</article>
}
