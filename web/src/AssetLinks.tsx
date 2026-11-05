import { useState } from 'react'
import { api } from './api'
import { Dialog, useToast } from './ui'
import { useLocalDraft } from './useLocalDraft'
import type { Benchmark } from './benchmarkTypes'

type Match={id:number;kind:'materials'|'benchmarks'|'tips';title:string;preview:string;reason:string}
export function AssetSearch({query,onPick}:{query:string;onPick:(item:Match)=>void}){
 const [q,setQ]=useState(query),[items,setItems]=useState<Match[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState('')
 async function find(){setBusy(true);try{setItems(await api.get<Match[]>('/api/assets/search?q='+encodeURIComponent(q)));setError('')}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <details className="brief-more"><summary>找已有研究、素材和方法</summary><p className="small muted">本地按内容与相关词查找，不调用模型。点击采用后才进入本篇。</p><div className="row"><input className="input grow" aria-label="查找相关资产" value={q} onChange={e=>setQ(e.target.value)}/><button className="btn" disabled={busy||!q.trim()} onClick={()=>void find()}>{busy?'查找中':'查找'}</button></div>{error&&<p role="alert">{error}</p>}{items.map(i=><div className="setting" key={i.kind+i.id}><div className="body"><strong>{i.title}</strong><p className="small muted">{i.reason} · {i.kind==='tips'?'方法':i.kind==='benchmarks'?'对标研究':'素材'}</p><p className="small">{i.preview}</p></div><button className="btn sm" onClick={()=>onPick(i)}>采用</button></div>)}</details>
}

export function MethodCapture({source}:{source:Benchmark}){
 const [open,setOpen]=useState(false),[busy,setBusy]=useState(false);const toast=useToast()
 const [form,setForm,clear]=useLocalDraft(`cw-method-${source.analysis?.id}`,{title:'',content:''})
 async function save(){setBusy(true);try{await api.post(`/api/benchmarks/${source.id}/methods`,{...form,analysis_id:source.analysis?.id});clear();setOpen(false);toast('已存到内容灵感技巧，保持 AI 建议、待验证')}catch(e){toast((e as Error).message,'error')}finally{setBusy(false)}}
 return <>{source.analysis&&!source.stale&&<button className="btn sm" onClick={()=>setOpen(true)}>保存方法候选</button>}{open&&<Dialog open title="保存到内容灵感技巧" onClose={()=>setOpen(false)} footer={<button className="btn primary" disabled={busy||!form.title.trim()||!form.content.trim()} onClick={()=>void save()}>保存候选</button>}><p className="small muted">保留本次研究版本与来源；保存不代表认可，更不会自动成为个人铁律。</p><label className="field">方法名称<input className="input" value={form.title} onChange={e=>setForm({...form,title:e.target.value})}/></label><label className="field">具体方法、依据与适用边界<textarea className="textarea" rows={5} value={form.content} onChange={e=>setForm({...form,content:e.target.value})}/></label><details><summary>查看本次写作准备建议</summary><p>{source.analysis?.research?.writing_prep||source.analysis?.basic?.techniques||'请从本次研究中选择值得保留的具体方法。'}</p></details></Dialog>}</>
}
