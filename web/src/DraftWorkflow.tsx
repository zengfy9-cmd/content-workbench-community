import { useEffect, useState } from 'react'
import { api, type Draft } from './api'
import { go } from './App'
import { Dialog, usePoll, useToast } from './ui'
import { useWritingModel, WritingModelPicker } from './WritingModelPicker'
import { XiloHandoff } from './XiloHandoff'

type Workflow = {
  draft_status:string;body_ok:boolean; layout_ok:boolean; open_comments:number; marks:number; body_hash:string;layout_hash:string;
  open_threads?:{id:number;body:string;quote:string|null;waiting_ai:boolean}[];
  review_marks?:{id:number;quote:string}[];
  blockers?:string[];
  final_copy?:{saved_at:number;current:boolean}|null;
  habits:{id:string;rule:string}[]; variants:{id:number;kind:string;title:string;status:string}[];
  lineage:null|{kind:string;model:string|null;snapshot:{sources:{id:number;title:string;body:string}[];requirements:string;mode:string;style:{habits?:string}}}
}
const labels:Record<string,string> = {xiaohongshu:'小红书',video:'口播稿',long:'合成长稿',wechat:'公众号',x_article:'X 长文'}

export function DraftWorkflow({id,platform,save,getSavedStamp,onComments,onLocateMark,onClearMark,onPreview,onImages}:{
  id:number;platform:string;save:()=>Promise<boolean>;getSavedStamp:()=>number|null;onComments:(threadId?:number)=>void;onLocateMark:(id:number)=>void;onClearMark:(id:number)=>void;onPreview:()=>void;onImages:()=>void
}){
 const toast=useToast()
 const [data,setData]=useState<Workflow|null>(null)
 const [error,setError]=useState('')
 const [busy,setBusy]=useState(false)
 const [compose,setCompose]=useState(false)
 const [layout,setLayout]=useState(false)
 const shortCopy=platform==='douyin'||platform==='xiaohongshu'
 const load=()=>api.get<Workflow>(`/api/drafts/${id}/workflow`).then(d=>{setData(d);setError('')}).catch(e=>setError(e.message))
 usePoll(load,5000)
 async function approve(stage:'layout'|'final'){
  setBusy(true);setError('')
  try{
   if(!await save())return
   const expected_updated_at=getSavedStamp()
   const current=await api.get<Workflow>(`/api/drafts/${id}/workflow`)
   setData(current)
   if(stage==='final'){
    await api.post(`/api/drafts/${id}/final-copy`,{confirm:true,hash:current.body_hash,expected_updated_at})
    await load();toast('已保存到成稿箱，可供 Codex 调取')
   }else{
    await api.post(`/api/drafts/${id}/approve`,{stage,confirm:true,hash:current.layout_hash})
    await load();toast('当前排版已审核')
   }
  }catch(e){setError((e as Error).message)}finally{setBusy(false)}
 }
 return <section className="draft-workflow">
  <div className="row wrap"><strong>本篇进度</strong><span className={'pill '+(data?.body_ok?'ok':'warn')}>{data?.body_ok?'正文已确认':'正文待审核'}</span>
   {data?.final_copy&&<a className="pill ok" href="#/final-drafts">{data.final_copy.current?'已存成稿箱':'成稿箱有上一版'}</a>}
   {!shortCopy&&<span className={'pill '+(data?.layout_ok?'ok':'warn')}>{data?.layout_ok?'排版已确认':'排版待审核'}</span>}
  </div>
  <div className="row wrap workflow-actions">
   <button className="btn sm primary" disabled={busy||!data} onClick={()=>void approve('final')}>{data?.final_copy?'确认正文，更新成稿箱':'确认正文，存入成稿箱'}</button>
   {(!shortCopy||layout)&&<>
    <button className="btn sm" disabled={!data?.body_ok||busy} onClick={onImages}>继续配图</button>
    <button className="btn sm" onClick={onPreview}>预览排版</button>
    <button className="btn sm workflow-review" disabled={!data?.body_ok||busy} onClick={()=>void approve('layout')}>确认排版审核</button>
   </>}
   <button className="btn sm" disabled={!data?.body_ok||busy} onClick={()=>setCompose(true)}>制作其他版本／合成长文</button>
   {shortCopy&&<button className="btn sm ghost" aria-expanded={layout} onClick={()=>setLayout(!layout)}>{layout?'收起配图与排版':'配图与排版（可选）'}</button>}
  </div>
  {!!(data?.open_comments||data?.marks)&&<div className="review-blockers">
   <strong className="small">还需要处理</strong>
   {!!data?.open_comments&&<button className="text-action small" onClick={()=>onComments(data.open_threads?.[0]?.id)}>打开批注面板 · {data.open_comments} 条未解决</button>}
   <ul>
    {data?.open_threads?.map(thread=><li key={thread.id}>
     <button className="text-action review-locate" onClick={()=>onComments(thread.id)}>
      <span>{thread.body}</span>
      <small>{thread.quote?`原文：「${thread.quote}」`:'全文批注'} · {thread.waiting_ai?'等 AI 处理':'等你确认解决'}</small>
     </button>
    </li>)}
    {data?.review_marks?.map(mark=><li key={`mark-${mark.id}`}>
     <button className="text-action review-locate" onClick={()=>onLocateMark(mark.id)}><span>{mark.quote}</span><small>待改标记 · 定位正文</small></button>
     <button className="btn ghost sm" onClick={()=>onClearMark(mark.id)}>清除标记</button>
    </li>)}
   </ul>
  </div>}
  {data && !data.blockers?.length && <p className="small muted">没有未解决批注或待改标记。</p>}
  {data?.draft_status==='failed'&&data.lineage?.model&&<button className="btn sm" disabled={busy} onClick={async()=>{setBusy(true);try{await api.post(`/api/drafts/${id}/retry-derived`,{confirm:true});window.location.reload()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}}>按原模型重试派生稿</button>}
  {error&&<p role="alert" className="err">{error}</p>}
  <XiloHandoff scope="draft" draftId={id} platform={platform}/>
  <details><summary>本篇启用的写作习惯 · {data?.habits.length||0} 条</summary><p className="small muted">下列是当前已确认启用的规则；生成时采用的版本请看「实际发送记录」。</p>{data?.habits.map(h=><p key={h.id}>{h.rule}</p>)}<button className="btn sm" onClick={()=>go('habits')}>管理改稿习惯</button></details>
  {data?.lineage&&<details><summary>本版本来源 · {labels[data.lineage.kind]} · {data.lineage.snapshot.mode==='ai'?data.lineage.model:'原文复制／编排'}</summary><p>{data.lineage.snapshot.requirements}</p>{data.lineage.snapshot.sources.map((s,i)=><details key={s.id}><summary>{i+1}. {s.title} <a href={`#/drafts/${s.id}`}>打开原稿</a></summary><pre className="prep-rule">{s.body}</pre></details>)}</details>}
  {!!data?.variants.length&&<div className="row wrap small">衍生稿：{data.variants.map(v=><a key={v.id} href={`#/drafts/${v.id}`}>{labels[v.kind]} · {v.title}</a>)}</div>}
  {compose&&<ComposeDraft current={id} onClose={()=>setCompose(false)} beforeCreate={save}/>}
 </section>
}

function ComposeDraft({current,onClose,beforeCreate}:{current:number;onClose:()=>void;beforeCreate:()=>Promise<boolean>}){
 const toast=useToast(), model=useWritingModel()
 const [kind,setKind]=useState('xiaohongshu'),[mode,setMode]=useState('copy')
 const [selected,setSelected]=useState<number[]>([current]),[drafts,setDrafts]=useState<Draft[]>([])
 const [requirements,setRequirements]=useState(''),[key,setKey]=useState(()=>crypto.randomUUID())
 const [busy,setBusy]=useState(false),[error,setError]=useState('')
 useEffect(()=>setKey(crypto.randomUUID()),[model.value])
 usePoll(()=>api.get<Draft[]>('/api/drafts').then(setDrafts).catch(e=>setError(e.message)),12000)
 function move(i:number,delta:number){const next=[...selected];[next[i],next[i+delta]]=[next[i+delta],next[i]];setSelected(next);setKey(crypto.randomUUID())}
 async function create(){
  setBusy(true);setError('')
  try{
   if(!await beforeCreate())return
   const r=await api.post<{id:number}>('/api/drafts/compose',{kind,mode,source_ids:kind==='long'?selected:[current],requirements,writing_model:model.value,request_key:key})
   onClose();go(`drafts/${r.id}`);toast('已建立独立稿件；原稿保持原样')
  }catch(e){setError((e as Error).message)}finally{setBusy(false)}
 }
 return <Dialog open title="制作独立版本" wide onClose={()=>{if(!busy)onClose()}} footer={<button className="btn primary" disabled={busy||(mode==='ai'&&model.loading)} onClick={()=>void create()}>{busy?'正在提交…':'建立新稿件'}</button>}>
 <div className="stack">
  <label className="field">用途<select className="select" value={kind} disabled={busy} onChange={e=>{setKind(e.target.value);setKey(crypto.randomUUID())}}>{Object.entries(labels).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
  {kind==='video'&&<p>约3分钟口播，用试读或录音时长校准。确认正文后，在「视频制作 → 用本机 Codex 继续制作」选择路线。</p>}
  {kind==='long'&&<div><p>按主线选择并排序；各篇来源都需要先确认正文。</p>{selected.map((id,i)=><div className="row wrap" key={id}><span>{i+1}. {drafts.find(d=>d.id===id)?.title||'当前稿件'}</span><button className="btn sm" disabled={!i||busy} onClick={()=>move(i,-1)}>上移</button><button className="btn sm" disabled={i===selected.length-1||busy} onClick={()=>move(i,1)}>下移</button></div>)}<div className="workflow-sources">{drafts.filter(d=>d.status!=='generating').map(d=><label key={d.id}><input type="checkbox" disabled={busy} checked={selected.includes(d.id)} onChange={e=>{setSelected(e.target.checked?[...selected,d.id]:selected.filter(x=>x!==d.id));setKey(crypto.randomUUID())}}/> {d.title||'未命名稿件'}</label>)}</div></div>}
  <label className="field">本版本要求<textarea className="textarea" rows={3} disabled={busy} value={requirements} onChange={e=>{setRequirements(e.target.value);setKey(crypto.randomUUID())}} placeholder="合稿的共同问题、目标读者，或者口播需要补什么。"/></label>
  <label className="field">制作方式<select className="select" disabled={busy} value={mode} onChange={e=>{setMode(e.target.value);setKey(crypto.randomUUID())}}><option value="copy">复制／编排来源，我来改（不调用模型）</option><option value="ai">按原写作方案生成独立版本</option></select></label>
  {mode==='ai'&&<WritingModelPicker model={model} disabled={busy}/>}
  <p className="small muted">新稿保留来源快照，图片复制到新稿名下，修改各自保存。生成不会自动发布；原稿的审核不会沿用到新版本。</p>
  {error&&<p role="alert" className="err">{error}</p>}
 </div></Dialog>
}
