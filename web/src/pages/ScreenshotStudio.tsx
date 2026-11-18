import { useEffect, useRef, useState } from 'react'
import { api, type Draft } from '../api'
import { go } from '../App'
import { useToast } from '../ui'
import './ScreenshotStudio.css'

type Project = {id:string;title:string;draft_id:number|null;updated_at:number}
type Candidate = {id:number;name:string;url:string;context:string}

export function ScreenshotStudio({query}:{query:URLSearchParams}) {
  const toast=useToast()
  const [rows,setRows]=useState<Project[]>([]),[drafts,setDrafts]=useState<Draft[]>([])
  const [draftId,setDraftId]=useState(query.get('draft')||'')
  const [candidates,setCandidates]=useState<Candidate[]>([]),[selected,setSelected]=useState<number[]>([])
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[returned,setReturned]=useState<number|null>(null)
  const picker=useRef<HTMLInputElement>(null)
  const pid=query.get('project')||''
  useEffect(()=>{
    let alive=true
    Promise.all([api.get<Project[]>('/api/screenshot-projects'),api.get<Draft[]>('/api/drafts')])
      .then(([p,d])=>{if(alive){setRows(p);setDrafts(d)}}).catch(e=>{if(alive)setError(e.message)})
    return()=>{alive=false}
  },[pid])
  useEffect(()=>{
    let alive=true
    setCandidates([]);setSelected([])
    if(draftId)api.get<{images:Candidate[]}>(`/api/drafts/${draftId}/screenshot-candidates`)
      .then(r=>{if(alive)setCandidates(r.images)}).catch(e=>{if(alive)setError(e.message)})
    return()=>{alive=false}
  },[draftId])
  useEffect(()=>{
    const handler=(event:MessageEvent)=>{
      const frame=document.querySelector<HTMLIFrameElement>('iframe.screenshot-editor')
      if(event.origin!==location.origin||event.source!==frame?.contentWindow)return
      if(event.data?.type==='cw-screenshot-return'&&Number.isInteger(event.data.draftId)){
        setReturned(event.data.draftId);toast('截图已回填原稿，正文其他内容保持原样')
      }
    }
    window.addEventListener('message',handler)
    return()=>window.removeEventListener('message',handler)
  },[toast])
  async function create() {
    setBusy(true);setError('')
    try{
      const p=await api.post<Project>('/api/screenshot-projects',{draft_id:Number(draftId),image_ids:selected})
      go(`screenshots?project=${p.id}`)
    }catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  async function upload(files:FileList|null) {
    if(!files?.length)return
    setBusy(true);setError('')
    try{
      const slides=[]
      for(const file of Array.from(files)){
        if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>20*1024*1024)throw Error('请选择不超过 20 MB 的 PNG、JPG 或 WebP')
        const src=await new Promise<string>((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result));r.onerror=()=>reject(Error('图片读取失败'));r.readAsDataURL(file)})
        slides.push({id:'manual-'+crypto.randomUUID(),name:file.name,image:src,source:file.name,manualAdded:true,references:[],
          context:'独立截图 · 可标注和导出，未绑定原稿',annotations:[],reviewed:false,
          style:{width:1600,height:1000,padding:70,scale:1,offsetX:0,offsetY:0,radius:24,shadow:25,bg:['#087f8c','#b5e8ba'],bgImage:null}})
      }
      const p=await api.post<Project>('/api/screenshot-projects',{project:{version:1,title:files[0].name,defaultWatermark:null,slides,excluded:[]}})
      go(`screenshots?project=${p.id}`)
    }catch(e){setError((e as Error).message)}finally{setBusy(false);if(picker.current)picker.current.value=''}
  }
  if(pid)return <div className="screenshot-studio-embedded">
    <div className="screenshot-studio-bar"><a className="btn sm" href="#/screenshots">截图项目</a>
      {returned&&<a className="btn sm primary" href={`#/drafts/${returned}`}>查看回填后的原稿</a>}
      <a className="btn sm" target="_blank" rel="noopener noreferrer" href={`/api/screenshot-projects/${encodeURIComponent(pid)}/editor`}>全屏编辑</a>
    </div>
    <div className="screenshot-frame-wrap"><iframe key={pid} title="截图工作室" className="screenshot-editor" src={`/api/screenshot-projects/${encodeURIComponent(pid)}/editor`}/></div>
  </div>
  return <div className="page narrow screenshot-projects">
    <div className="page-head"><div><h1>截图工作室</h1><p>框选、马赛克、文字与箭头。项目保存后可继续编辑，确认时再回填原稿。</p></div>
      <button className="btn" disabled={busy} onClick={()=>picker.current?.click()}>＋ 添加截图</button>
      <input ref={picker} hidden type="file" multiple accept="image/png,image/jpeg,image/webp" aria-label="上传截图" onChange={e=>void upload(e.target.files)}/>
    </div>
    {error&&<p role="alert" className="callout">{error}</p>}
    <section className="card card-pad stack"><h2>从稿件选截图</h2>
      <label className="field">原稿<select className="select" value={draftId} onChange={e=>{setDraftId(e.target.value);setError('')}}>
        <option value="">选择已有稿件</option>{drafts.map(d=><option key={d.id} value={d.id}>{d.title||'未命名稿件'}</option>)}
      </select></label>
      {draftId&&!candidates.length&&<p className="small muted">原稿中还没有可编辑的本地图片。可以先粘贴截图到正文，或添加独立截图。</p>}
      <div className="screenshot-candidates">{candidates.map(c=><label className="screenshot-candidate" key={c.id}>
        <input type="checkbox" checked={selected.includes(c.id)} onChange={e=>setSelected(prev=>e.target.checked?[...prev,c.id]:prev.filter(i=>i!==c.id))}/>
        <img src={c.url} alt={c.name}/><span>{c.name}</span>
      </label>)}</div>
      {!!candidates.length&&<><p className="small muted">选中需要标注的截图。生成插图和封面不必一起处理。</p><button className="btn primary" disabled={busy||!selected.length} onClick={()=>void create()}>{busy?'正在准备…':`编辑选中的 ${selected.length} 张截图`}</button></>}
    </section>
    <h2>已保存的项目</h2>
    {!rows.length?<p className="muted">添加截图或从原稿选择图片，开始第一个项目。</p>:<div className="card list-rows">{rows.map(p=><a key={p.id} className="list-row" href={`#/screenshots?project=${p.id}`}><div className="grow"><strong>{p.title}</strong><div className="sub">{p.draft_id?'已绑定原稿':'独立截图项目'}</div></div><span>继续编辑 →</span></a>)}</div>}
  </div>
}
