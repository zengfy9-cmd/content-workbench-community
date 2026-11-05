import { useEffect, useState } from 'react'
import { api, type Draft, type Idea } from '../api'
import { Dialog, useToast } from '../ui'
import type { BenchmarkSummary } from '../benchmarkTypes'

export type Plan = { id:number; day:string; title:string; angle:string; kind:'planned'|'extra'; state:string; benchmark_ids:number[]; draft_id:number|null; draft_title?:string; draft_status?:string; position:number }
export function planDay(offset=0) {
  const date=new Date(); date.setDate(date.getDate()+offset)
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`
}
export type PlanSource = 'new'|'idea'|'draft'
type NewsSeed = { title:string; url:string }

export function PlanDialog({open,onClose,onSaved,kind='planned',seed=null,existing=null,initialDay,mode='new',draft=null}: {
  open:boolean;onClose:()=>void;onSaved:()=>void;kind?:'planned'|'extra';seed?:NewsSeed|null;
  existing?:Plan|null;initialDay?:string;mode?:PlanSource;draft?:Draft|null
}) {
  const toast=useToast()
  const [title,setTitle]=useState(''), [angle,setAngle]=useState(''), [date,setDate]=useState(planDay())
  const [ids,setIds]=useState<number[]>([]), [idea,setIdea]=useState(''), [draftId,setDraftId]=useState('')
  const [ideas,setIdeas]=useState<Idea[]>([]), [drafts,setDrafts]=useState<Draft[]>([]), [sources,setSources]=useState<BenchmarkSummary[]>([])
  const [source,setSource]=useState<PlanSource>(mode), [busy,setBusy]=useState(false), [loading,setLoading]=useState(false), [error,setError]=useState('')
  const sourceDraftId=draft?.id, sourceDraftTitle=draft?.title
  useEffect(()=>{
    if(!open)return
    let alive=true
    setTitle(existing?.title||sourceDraftTitle||seed?.title||'');setAngle(existing?.angle||'');setIds(existing?.benchmark_ids||[])
    setIdea('');setDraftId(sourceDraftId?String(sourceDraftId):'');setDate(existing?.day||initialDay||planDay());setSource(sourceDraftId?'draft':mode);setError('');setLoading(true)
    Promise.all([api.get<Idea[]>('/api/ideas?status=all'),api.get<Draft[]>('/api/drafts'),api.get<BenchmarkSummary[]>('/api/benchmarks')])
      .then(([i,d,s])=>{if(alive){setIdeas(i.filter(x=>x.status!=='dropped'));setDrafts(d.filter(x=>x.status!=='published'));setSources(s)}})
      .catch(e=>{if(alive)setError(e.message)}).finally(()=>{if(alive)setLoading(false)})
    return()=>{alive=false}
  },[open,seed,kind,existing,initialDay,mode,sourceDraftId,sourceDraftTitle])
  async function save(){
    if(busy)return
    setBusy(true);setError('')
    try {
      await (existing?api.patch(`/api/plans/${existing.id}`,{title,angle,day:date,benchmark_ids:ids}):api.post('/api/plans',{
        title,angle,day:date,kind,idea_id:source==='idea'&&idea?Number(idea):null,
        draft_id:source==='draft'&&draftId?Number(draftId):null,benchmark_ids:ids,source_url:seed?.url||''
      }))
      onSaved();toast(source==='draft'?'已安排原稿，继续编辑同一篇文章':'已保存写作计划')
    }catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  const valid=title.trim() && date && (existing || source==='new' || (source==='draft'?draftId:idea))
  return <Dialog open={open} title={existing?'调整写作安排':kind==='extra'?'安排热点加更':'添加写作安排'} onClose={onClose} footer={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={busy||!valid||loading} onClick={()=>void save()}>{busy?'保存中…':'保存安排'}</button></>}>
    <div className="stack">
      {error&&<p className="callout" role="alert">{error}</p>}
      {!existing&&!draft&&<div className="segmented" role="group" aria-label="添加方式">{([['new','新建计划'],['idea','从选题添加'],['draft','从稿件添加']] as const).map(([key,label])=><button key={key} aria-pressed={source===key} onClick={()=>{setSource(key);setTitle(seed?.title||'');setAngle('');setIdea('');setDraftId('');setIds([])}}>{label}</button>)}</div>}
      <label className="field">写作日期<input className="input" type="date" value={date} onChange={e=>setDate(e.target.value)}/></label>
      {!existing&&source==='idea'&&<label className="field">选择已有选题<select className="select" value={idea} onChange={e=>{setIdea(e.target.value);const i=ideas.find(x=>x.id===Number(e.target.value));setTitle(i?.title||'');setAngle(i?.angle||'')}}><option value="">请选择选题</option>{ideas.map(i=><option key={i.id} value={i.id}>{i.title}</option>)}</select></label>}
      {!existing&&source==='draft'&&!draft&&<label className="field">选择已有稿件<select className="select" value={draftId} onChange={e=>{setDraftId(e.target.value);setTitle(drafts.find(x=>x.id===Number(e.target.value))?.title||'未命名稿件')}}><option value="">请选择稿件</option>{drafts.map(d=><option key={d.id} value={d.id}>{d.title||'未命名稿件'}</option>)}</select></label>}
      {source==='draft'&&<p className="plan-link-note">安排日期只关联原稿，不复制文章；已有未完成安排会更新到所选日期。</p>}
      <label className="field">{source==='draft'?'关联稿件':'我准备写什么'}<input className="input" value={title} readOnly={source==='draft'} maxLength={300} onChange={e=>setTitle(e.target.value)}/></label>
      <label className="field">下一步 / 我的切入角度<textarea className="textarea" rows={3} value={angle} maxLength={4000} onChange={e=>setAngle(e.target.value)} placeholder="例如：补充一张真实使用截图，再写自己的判断"/></label>
      <details><summary>关联对标素材（{ids.length} 篇）</summary><fieldset className="plan-reference-options"><legend>可多选，原稿已有对标会保留</legend>{sources.map(s=><label key={s.id}><input type="checkbox" checked={ids.includes(s.id)} onChange={e=>setIds(e.target.checked?[...ids,s.id]:ids.filter(i=>i!==s.id))}/>{s.title}</label>)}</fieldset></details>
    </div>
  </Dialog>
}

export function ScheduleDraft({draft,onSaved,plans=[]}:{draft:Draft;onSaved:()=>void;plans?:Plan[]}) {
  const [date,setDate]=useState<string|null>(null)
  const scheduled=plans.find(p=>p.draft_id===draft.id&&!['done','archived'].includes(p.state))
  return <div className="draft-schedule">
    {scheduled&&<span className="schedule-badge">已安排：{scheduled.day===planDay()?'今天':scheduled.day===planDay(1)?'明天':scheduled.day}</span>}
    <details className="action-menu"><summary className="btn sm">安排写作</summary><div className="action-menu-panel">{[[planDay(),'加入今日写作'],[planDay(1),'加入明天计划'],['custom','选择日期…']].map(([value,label])=><button key={value} onClick={e=>{e.currentTarget.closest('details')?.removeAttribute('open');setDate(value==='custom'?planDay():value)}}>{label}</button>)}</div></details>
    <PlanDialog open={date!==null} initialDay={date||undefined} draft={draft} onClose={()=>setDate(null)} onSaved={()=>{setDate(null);onSaved()}}/>
  </div>
}
