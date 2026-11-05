import { useEffect, useRef, useState } from 'react'
import { api, when } from './api'
import type { Benchmark, ResearchMetadata } from './benchmarkTypes'
import { useLocalDraft } from './useLocalDraft'
import { useToast } from './ui'

export type ResearchChoice={depth:'basic'|'deep';model:string}
export const useResearchChoice=()=>useLocalDraft<ResearchChoice>('cw-research-choice-v1',{depth:'deep',model:'current'})
type Options={models:{id:string;label:string;available:boolean}[];rule_version:string;rule_hash:string;rules:string;note:string}

export function ResearchControls({value,onChange,disabled=false}:{value:ResearchChoice;onChange:(v:ResearchChoice)=>void;disabled?:boolean}){
  const [options,setOptions]=useState<Options|null>(null),[error,setError]=useState('')
  useEffect(()=>{let alive=true;api.get<Options>('/api/research/options').then(v=>{if(alive)setOptions(v)}).catch(e=>{if(alive)setError(e.message)});return()=>{alive=false}},[])
  return <div className="research-controls"><div className="row wrap">
    <label className="research-choice">研究深度<select className="select" aria-label="研究深度" value={value.depth} disabled={disabled} onChange={e=>onChange({...value,depth:e.target.value as ResearchChoice['depth']})}><option value="basic">基础研究 · 批量阅读</option><option value="deep">深度研究 · 完整报告</option></select></label>
    <label className="research-choice">本次研究模型<select className="select" aria-label="本次研究模型" value={value.model} disabled={disabled||!options} onChange={e=>onChange({...value,model:e.target.value})}>{!options&&<option value={value.model}>正在读取接入…</option>}{options&&!options.models.some(m=>m.id===value.model)&&<option value={value.model}>所选接入已不可用 · 请重新选择</option>}{options?.models.map(m=><option key={m.id} value={m.id} disabled={!m.available}>{m.label}</option>)}</select></label>
  </div>{error&&<p className="callout small">模型选项读取失败：{error}</p>}<details className="research-rule"><summary>共用研究规范 · {options?.rule_version||'读取中'}</summary><p className="small muted">各模型收到同一份规范，实际质量仍需审核；保存原文不会调用研究模型。</p><pre>{options?.rules}</pre><p className="small muted">{options?.note}</p></details></div>
}

export function ResearchTrace({metadata}:{metadata?:ResearchMetadata}){
  if(!metadata)return <p className="small muted">历史研究未记录模型与规范版本，原报告保留。</p>
  return <details className="research-trace"><summary>{metadata.depth==='basic'?'基础研究':metadata.depth==='passages'?'段落研究':'深度研究'} · {metadata.origin==='external-import'?'调用方声明型号':'请求模型'}：{metadata.model} · {metadata.rule_version}</summary><p className="small muted">接入：{metadata.backend||metadata.origin}；原文哈希和研究时间随本版本保存。型号为请求路由记录，未冒充供应商实测质量。</p><p className="small muted">规则校验：{metadata.rule_hash||'未记录'}</p>{metadata.rules&&<pre>{metadata.rules}</pre>}</details>
}

export function ResearchJudgment({source}:{source:Benchmark}){
  const previous=source.reviews.find(r=>r.paragraph_key==='__report__')
  let judgment:{agree?:string;correction?:string;verify?:string}={}
  try{judgment=typeof previous?.judgment==='string'?JSON.parse(previous.judgment):previous?.judgment||{}}catch{/* legacy note retained */}
  const initial={note:previous?.note||'',agree:judgment.agree||'',correction:judgment.correction||'',verify:judgment.verify||'',decision:previous?.reviewed&&(!previous.decision||previous.decision==='unreviewed')?'confirmed':previous?.decision||'unreviewed'}
  const cacheKey=`cw-research-judgment:${source.id}:${source.analysis?.id}`
  const [form,setForm]=useLocalDraft(cacheKey,initial)
  const latestForm=useRef(form);latestForm.current=form
  const [saving,setSaving]=useState(false),[error,setError]=useState(''),[history,setHistory]=useState<{id:number;record:string;created_at:number}[]|null>(null)
  const last=useRef(JSON.stringify(initial)),queue=useRef(Promise.resolve())
  const toast=useToast()
  function save(){
    if(!source.analysis)return
    const captured={...form},signature=JSON.stringify(captured)
    setSaving(true)
    queue.current=queue.current.catch(()=>{}).then(async()=>{if(signature===last.current)return;await api.put(`/api/benchmarks/${source.id}/analyses/${source.analysis!.id}/reviews/__report__`,{note:captured.note,decision:captured.decision,judgment:{agree:captured.agree,correction:captured.correction,verify:captured.verify}});last.current=signature;setError('');setHistory(null);if(JSON.stringify(latestForm.current)===signature){try{localStorage.removeItem(cacheKey)}catch{/* saved on server */}}toast('个人判断已保存，历史记录保留')}).catch(e=>setError(e.message)).finally(()=>setSaving(false))
  }
  async function loadHistory(){try{setHistory(await api.get(`/api/benchmarks/${source.id}/analyses/${source.analysis!.id}/reviews/history`))}catch(e){setError((e as Error).message)}}
  return <details className="research-judgment" open={!!error}><summary>我的判断与验证 · {({unreviewed:'尚未审核',partial:'部分确认',confirmed:'已确认',pending:'待验证'} as Record<string,string>)[form.decision]}</summary><p className="small muted">只记录你的判断。未填写不代表认同，保存不会生成个人铁律。</p>
    {initial.note&&<label className="field">原有判断记录<textarea className="textarea" aria-label="原有判断记录" value={form.note} onChange={e=>setForm(v=>({...v,note:e.target.value}))}/></label>}
    {([['agree','我认同 · 理由与适用范围'],['correction','我要纠正 · 原结论与修正'],['verify','还需验证 · 不确定点与验证动作']] as const).map(([key,label])=><label className="field" key={key}>{label}<textarea className="textarea" aria-label={label} maxLength={8000} value={form[key]} placeholder="尚未填写" onChange={e=>setForm(v=>({...v,[key]:e.target.value}))}/></label>)}
    <div className="row wrap"><label>审核范围 <select className="select" aria-label="审核状态" value={form.decision} onChange={e=>setForm(v=>({...v,decision:e.target.value}))}><option value="unreviewed">尚未审核</option><option value="partial">部分确认</option><option value="confirmed">已确认</option><option value="pending">仍待验证</option></select></label><button className="btn primary sm" disabled={saving} onClick={save}>{saving?'保存中…':'保存我的判断'}</button><button className="btn sm" onClick={()=>void loadHistory()}>审核历史</button></div>
    <p className="small muted">未提交的输入会在本浏览器恢复；点击保存后才形成审核记录。</p>{error&&<p role="alert" className="callout">{error}</p>}
    {history&&<div className="review-history">{history.length?history.map(event=><details key={event.id}><summary>{when(event.created_at)} · 记录 {event.id}</summary><pre>{event.record}</pre><button className="btn sm" onClick={()=>{const v=JSON.parse(event.record);setForm({...form,note:v.note||'',decision:v.decision||'unreviewed',agree:v.judgment?.agree||'',correction:v.judgment?.correction||'',verify:v.judgment?.verify||''})}}>恢复到输入框</button></details>):<p className="small muted">暂无新审核记录，原有判断继续保留。</p>}</div>}
  </details>
}
