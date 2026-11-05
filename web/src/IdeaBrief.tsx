import { useState, type ReactNode } from 'react'
import { api, type Idea } from './api'
import { Dialog } from './ui'
import './IdeaBrief.css'
import { IdeaAttachments } from './IdeaAttachments'

import { READINESS, BRIEF_FIELDS, briefOf, type Brief } from './ideaBriefModel'
export function BriefFields({value,onChange,children}:{value:Brief;onChange:(next:Brief)=>void;children?:ReactNode}){
 return <div className="brief-fields">{BRIEF_FIELDS.map(([key,label,hint,limit])=><div key={key}><label className="field"><span>{label}（可选）</span><textarea className="textarea" rows={2} maxLength={limit} value={value[key]} onChange={e=>onChange({...value,[key]:e.target.value})} placeholder={hint}/></label>{key==='available_materials'&&children}</div>)}</div>
}
export function IdeaBriefDialog({idea,onClose,onSaved}:{idea?:Idea;onClose:()=>void;onSaved:()=>void}){
 const [images,setImages]=useState<number[]>(idea?.image_ids||[]),[benchmarks,setBenchmarks]=useState<number[]>(idea?.sources_selected?idea.benchmark_ids||[]:idea?.benchmark_id?[idea.benchmark_id]:idea?.benchmark_ids||[]),[uploading,setUploading]=useState(false)
 const [title,setTitle]=useState(idea?.title||''),[angle,setAngle]=useState(idea?.angle||'')
 const [brief,setBrief]=useState(idea?briefOf(idea):{audience:'',problem:'',thesis:'',available_materials:'',missing_materials:''})
 const [readiness,setReadiness]=useState(idea?.readiness||'unreviewed'),[busy,setBusy]=useState(false),[error,setError]=useState('')
 async function save(){setBusy(true);setError('');try{const body={title:title.trim(),angle,...brief,readiness,image_ids:images,benchmark_ids:benchmarks,material_ids:idea?.sources_selected?idea.material_ids||[]:idea?.material_id?[idea.material_id]:idea?.material_ids||[]};if(idea)await api.patch(`/api/ideas/${idea.id}`,body);else await api.post('/api/ideas',body);onSaved();window.dispatchEvent(new Event('cw-refresh-status'));onClose()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <Dialog open title={idea?'编辑选题卡':'自己加一个选题'} wide onClose={()=>{if(!busy&&!uploading)onClose()}} footer={<><button className="btn" disabled={busy||uploading} onClick={onClose}>取消</button><button className="btn primary" disabled={busy||uploading||!title.trim()} onClick={()=>void save()}>{busy?'保存中…':idea?'保存选题卡':'加入'}</button></>}>
 <div className="stack" onPaste={e=>{if(Array.from(e.clipboardData.files).some(f=>f.type.startsWith('image/')) && !(e.target as HTMLElement).closest('.idea-attachments')){e.preventDefault();const zone=e.currentTarget.querySelector('.idea-attachments');if(zone)zone.dispatchEvent(new ClipboardEvent('paste',{clipboardData:e.clipboardData,bubbles:true}))}}}><p className="small muted">只写标题也可以加入。其他信息按你自己的判断补充，留空不会被视为已确认。</p>{error&&<p className="callout" role="alert">{error}</p>}<label className="field"><span>选题</span><input className="input" autoFocus maxLength={300} value={title} onChange={e=>setTitle(e.target.value)} placeholder="一句话说清楚要写什么"/></label><label className="field"><span>想怎么讲（可选）</span><textarea className="textarea" rows={2} maxLength={6000} value={angle} onChange={e=>setAngle(e.target.value)} placeholder="切入角度、想举的例子"/></label><details className="brief-more" open={!!idea}><summary>补充读者、观点与材料</summary><BriefFields value={brief} onChange={setBrief}><IdeaAttachments images={images} onImages={setImages} benchmarks={benchmarks} onBenchmarks={setBenchmarks} onBusy={setUploading}/></BriefFields></details><label className="field"><span>当前准备状态</span><select className="select" value={readiness} onChange={e=>setReadiness(e.target.value)}>{Object.entries(READINESS).map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label></div></Dialog>
}
