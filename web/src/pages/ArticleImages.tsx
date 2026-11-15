import { PromptPreview, PromptTrace } from '../AiRules'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useToast } from '../ui'
import { ImageLayoutPreview } from '../ImageLayoutPreview'
import './ArticleImages.css'

type Mode = 'cover' | 'article'
type ReferenceMode = 'style' | 'character'
type TaskReference = {reference_key:string;name:string}
const referenceLabels:Record<ReferenceMode,string> = {style:'参考画风和构图（保留默认自选角色）',character:'本次使用上传的 IP 当主角'}
const referenceUrl = (key:string) => `/api/article-images/uploaded-reference/${key}`
type Task = {
  id:number; title:string; kind:'preview'|'final'|'cover'; count:number; panel:number|null
  provider?:string;model?:string;parent_id:number|null; state:string; error:string; article?:string; notes?:string
  cover_platform?:string; cover_style?:string; artifacts?:{sha256:string}[]
  reference_key?:string; reference_mode?:ReferenceMode
  draft_id?:number|null; suggested_draft_id?:number|null; attached_image_id?:number|null; in_draft_body?:boolean; is_draft_cover?:boolean
}
type Draft = {id:number; title:string}
type Option = {value:string; label:string}
type Preferences = {summary:string; reference:string|null; styles:Option[]; formats:Option[]}
const states:Record<string,string> = {queued:'排队中',running:'生成中',completed:'已生成',failed:'生成失败',cancelled:'已停止',interrupted:'中断 · 待核对'}
function taskLabel(task:Task) {
  return task.kind==='cover'?'封面大图':task.kind==='preview'?'整组预览':`第 ${task.panel} 张大图`
}

export function ArticleImages({draftId}:{draftId?:number}) {
  const toast=useToast()
  const [provider,setProvider]=useState('codex'),[confirmApi,setConfirmApi]=useState(false)
  const [mode,setMode]=useState<Mode>(draftId?'article':'cover')
  const [title,setTitle]=useState(''),[article,setArticle]=useState(''),[notes,setNotes]=useState('')
  const [reference,setReference]=useState<TaskReference|null>(null),[referenceMode,setReferenceMode]=useState<ReferenceMode>('style')
  const [uploading,setUploading]=useState(false),[referenceError,setReferenceError]=useState('')
  const [promptRevision,setPromptRevision]=useState(0)
  const [count,setCount]=useState(4),[platform,setPlatform]=useState('wechat'),[style,setStyle]=useState('auto')
  const [tasks,setTasks]=useState<Task[]>([]),[current,setCurrent]=useState<Task|null>(null)
  const [busy,setBusy]=useState(false),[drafts,setDrafts]=useState<Draft[]>([])
  const [picked,setPicked]=useState<number[]>([])
  const [target,setTarget]=useState<number|''>(draftId||''),[preferences,setPreferences]=useState<Preferences|null>(null)
  const [preferenceError,setPreferenceError]=useState('')
  const [destination,setDestination]=useState<number|''>('')
  const [layoutTask,setLayoutTask]=useState<{taskId:number;draftId:number}|null>(null)
  const requestKey=useRef(crypto.randomUUID()),selected=useRef<number|null>(null),draftRead=useRef(0)
  const pendingLayout=useRef<number|null>(null),resultTask=useRef<number|null>(null)
  const applyKey=useRef<{task:number;draft:number;key:string}|null>(null)
  const loadedDraft=useRef<{id:number;title:string;body:string}|null>(null)
  const running=tasks.some(t=>t.state==='running'),cover=mode==='cover'
  const waiting=tasks.filter(t=>t.state==='queued').length
  const ahead=current?tasks.filter(t=>t.id<current.id&&(t.state==='running'||t.state==='queued')).length:0
  const applied=!!current?.in_draft_body&&(current.kind!=='cover'||!!current.is_draft_cover)
  const referenceInput={reference_key:reference?.reference_key||'',reference_mode:referenceMode}
  const canStart=!busy&&!uploading&&!referenceError&&(provider!=='image-api'||confirmApi)&&
    (cover?!!title.trim()&&!preferenceError:!!article.trim())
  function changed(){requestKey.current=crypto.randomUUID();setPromptRevision(value=>value+1)}
  async function uploadReference(file:File) {
    setReferenceError('')
    if(file.size>8*1024*1024){setReferenceError('参考图不能超过 8MB，请换一张图。');return}
    setUploading(true)
    try {
      const form=new FormData();form.append('file',file)
      setReference(await api.upload<TaskReference>('/api/article-images/upload-reference',form))
      changed()
    }catch(e){setReferenceError((e as Error).message)}finally{setUploading(false)}
  }
  function clearReference(){setReference(null);setReferenceMode('style');setReferenceError('');changed()}
  async function load() {
    const list=await api.get<Task[]>('/api/article-images');setTasks(list)
    const id=selected.current||list[0]?.id
    if(id){
      const row=await api.get<Task>(`/api/article-images/${id}`)
      if(selected.current!==null&&selected.current!==id)return
      setCurrent(row)
      if(resultTask.current!==row.id){resultTask.current=row.id;setDestination(row.draft_id||row.suggested_draft_id||'')}
      if(pendingLayout.current===row.id&&row.state==='completed'){
        pendingLayout.current=null
        const did=row.draft_id||row.suggested_draft_id
        if(did)setLayoutTask({taskId:row.id,draftId:did})
      }
    }
  }
  const readDraft=useCallback(async(id:number)=>{
    const ticket=++draftRead.current
    const row=await api.get<{title:string;body:string}>(`/api/drafts/${id}`)
    if(ticket!==draftRead.current)return
    loadedDraft.current={id,title:row.title,body:row.body}
    setTitle(row.title);setArticle(row.body);requestKey.current=crypto.randomUUID()
    setPromptRevision(value=>value+1)
  },[])
  async function refreshDraftInput(id:number) {
    const snapshot=loadedDraft.current,ticket=draftRead.current
    if(target!==id||snapshot?.id!==id)return
    const row=await api.get<{title:string;body:string}>(`/api/drafts/${id}`)
    if(ticket!==draftRead.current)return
    setTitle(value=>value===snapshot.title?row.title:value)
    setArticle(value=>value===snapshot.body?row.body:value)
    loadedDraft.current={id,...row};changed()
  }
  useEffect(()=>{
    let alive=true
    const tick=()=>{if(alive)void load().catch(()=>{})}
    tick();const timer=window.setInterval(tick,2500)
    return()=>{alive=false;window.clearInterval(timer)}
  },[])
  useEffect(()=>{
    void api.get<Draft[]>('/api/drafts').then(setDrafts).catch(()=>{})
    void api.get<Preferences>('/api/article-images/preferences').then(setPreferences).catch(e=>setPreferenceError(e.message))
    if(draftId){setTarget(draftId);setMode('article');void readDraft(draftId).catch(e=>toast(e.message,'error'))}
  },[draftId,readDraft,toast])
  async function start() {
    if(!canStart)return
    setBusy(true)
    try {
      const payload=cover
        ?{request_key:requestKey.current,title:title.trim(),article,notes,cover_platform:platform,cover_style:style}
        :{request_key:requestKey.current,title:title.trim()||Array.from(article.trim().split(/\r?\n/)[0]).slice(0,36).join(''),article,count,notes}
      const result=await api.post<{id:number;queued?:boolean;ahead?:number}>(cover?'/api/article-images/cover':'/api/article-images',{...payload,...referenceInput,draft_id:target||null,provider,confirm_api:confirmApi})
      if(result.queued)toast(`已排到队尾，前面还有 ${result.ahead||0} 个任务，轮到时自动开始`)
      if(cover)pendingLayout.current=result.id
      selected.current=result.id;await load()
    }catch(e){toast((e as Error).message,'error')}finally{setBusy(false)}
  }
  async function final(panel:number,parentId=current?.id) {
    if(!current)return;setBusy(true)
    try {
      const result=await api.post<{id:number}>(`/api/article-images/${parentId}/final`,{panel,confirm:true,confirm_api:current.provider==='image-api'&&confirmApi,request_key:crypto.randomUUID(),draft_id:current.draft_id||destination||current.suggested_draft_id||null})
      pendingLayout.current=result.id
      selected.current=result.id;await load()
    }catch(e){toast((e as Error).message,'error')}finally{setBusy(false)}
  }
  async function finalMany(panels:number[],parentId=current?.id) {
    if(!current||!panels.length)return;setBusy(true)
    try {
      let last=0
      for(const panel of [...panels].sort((a,b)=>a-b)){
        const result=await api.post<{id:number}>(`/api/article-images/${parentId}/final`,{panel,confirm:true,confirm_api:current.provider==='image-api'&&confirmApi,request_key:crypto.randomUUID(),draft_id:current.draft_id||destination||current.suggested_draft_id||null})
        last=result.id
      }
      if(last){pendingLayout.current=last;selected.current=last}
      setPicked([]);await load()
      toast(`已排队 ${panels.length} 张，按顺序生成`)
    }catch(e){toast((e as Error).message,'error')}finally{setBusy(false)}
  }
  async function action(kind:string) {
    if(!current)return;setBusy(true)
    try{await api.post(`/api/article-images/${current.id}/${kind}`);await load()}
    catch(e){toast((e as Error).message,'error')}finally{setBusy(false)}
  }
  async function attach() {
    if(!current||!destination)return;setBusy(true)
    try{await api.post(`/api/article-images/${current.id}/attach`,{draft_id:destination});toast('已加入稿件附件，正文保持原样')}
    catch(e){toast((e as Error).message,'error')}finally{setBusy(false)}
  }
  async function confirmUse() {
    if(!current||!destination)return
    const tid=current.id,did=Number(destination)
    if(applyKey.current?.task!==tid||applyKey.current?.draft!==did)applyKey.current={task:tid,draft:did,key:crypto.randomUUID()}
    setBusy(true)
    try {
      const result=await api.post<{ok:boolean;requires_position?:boolean;draft_id:number;detail?:string}>(`/api/article-images/${tid}/confirm-use`,{draft_id:did,confirm:true,request_key:applyKey.current.key})
      if(result.requires_position){toast(result.detail||'请在预览里选择图片位置','info');setLayoutTask({taskId:tid,draftId:result.draft_id})}
      else {applyKey.current=null;toast(current.kind==='cover'?'已插入正文，并设为文章封面':'图片已插入原稿对应位置');window.dispatchEvent(new Event('cw-refresh-status'));await refreshDraftInput(result.draft_id)}
      await load()
    }catch(e){toast((e as Error).message,'error')}finally{setBusy(false)}
  }
  function restore(task:Task) {
    setMode(task.kind==='cover'?'cover':'article')
    setTitle(task.title);setArticle(task.article||'');setNotes(task.notes||'')
    setReference(task.reference_key?{reference_key:task.reference_key,name:'原任务参考图'}:null)
    setReferenceMode(task.reference_mode||'style');setReferenceError('')
    setTarget(task.draft_id||'')
    if(task.kind==='cover'){setPlatform(task.cover_platform||'wechat');setStyle(task.cover_style||'auto')}
    else setCount(task.count)
    changed();toast(`已载入原输入，点击${task.kind==='cover'?'生成封面大图':'生成整组预览'}将发起新任务`)
  }
  const draftOptions=<><option value="">直接输入，不载入稿件</option>{drafts.map(d=><option key={d.id} value={d.id}>{d.title||`稿件 ${d.id}`}</option>)}</>
  const draftSelect=<>
    <label>载入已有稿件<select className="select" aria-label="载入已有稿件" value={target} onChange={e=>{
      const id=e.target.value?Number(e.target.value):''
      setTarget(id);changed()
      if(id)void readDraft(id).catch(e=>toast(e.message,'error'))
      else draftRead.current++
    }}>{draftOptions}</select></label>
    {target&&<p className="small muted">成图会存回此稿附件。先预览排版，确认后才更新正文或封面。</p>}
  </>
  const source=<>
    <label>{cover?'参考正文（选填）':'正文'}<textarea className="textarea" aria-label={cover?'参考正文（选填）':'正文'} rows={cover?5:12} maxLength={80000} value={article} onChange={e=>{setArticle(e.target.value);changed()}} placeholder={cover?'可不填。已有文章时放入正文，让封面更贴合内容。':'粘贴已经确定的正文，只为它配图。'}/></label>
  </>
  return <div className="page illustration-page">
    <div className="page-head"><div><h1>制图工作台</h1><p>输入标题做封面，放入正文做配图。沿用你的角色与审美。</p></div></div>
    <div className="illustration-modes" role="group" aria-label="图片用途">
      <button className={`btn ${cover?'primary':''}`} aria-pressed={cover} onClick={()=>{setMode('cover');changed()}}>封面大图</button>
      <button className={`btn ${!cover?'primary':''}`} aria-pressed={!cover} onClick={()=>{setMode('article');changed()}}>正文配图</button>
    </div>
    <div className="illustration-layout">
      <form className="card card-pad stack" onSubmit={e=>{e.preventDefault();void start()}}>
        <label>生图方式<select className="select" aria-label="生图方式" value={provider} onChange={e=>{setProvider(e.target.value);setConfirmApi(false);changed()}}><option value="codex">本机 Codex · ChatGPT 账号额度</option><option value="image-api">代兔／兼容生图 API · 独立 API 额度</option></select></label>
        {(provider==='image-api'||current?.provider==='image-api')&&<label className="small"><input type="checkbox" checked={confirmApi} onChange={e=>setConfirmApi(e.target.checked)}/> 允许本次使用已配置的生图 API 额度（预览与大图分别调用）</label>}
        {provider==='image-api'&&<a className="small" href="#/settings">填写或更换生图 API Key → 设置</a>}
        <div className="illustration-style">
          <strong>{cover?'按你的审美，设计新的封面':'正文配图'}</strong>
          <p className="small muted">{cover?(preferences?.summary||'沿用自选角色身份，根据标题设计画面，直接生成一张独立大图。'):'一句短标题，一个核心画面。先看整组编号预览，再选图。'}</p>
          <details><summary>查看风格参考</summary>
            {cover&&preferences?.reference?<img src={`/api/article-images/references/${preferences.reference}`} alt="已选定的封面审美参考"/>:!cover?<img src="/api/article-images/references/style.png" alt="已满意的简洁正文配图参考"/>:<p className="small muted">沿用已有角色参考。</p>}
            <p className="small muted">{cover?'参考画风、角色与文字层级；新标题会重新设计场景和动作。':'根据新正文重新设计内容。'}</p>
          </details>
        </div>
        {preferenceError&&cover&&<p className="illustration-error" role="alert">{preferenceError}</p>}
        <label>{cover?'封面标题':'文章标题（选填）'}
          <textarea className={`textarea ${cover?'illustration-cover-title':''}`} aria-label={cover?'封面标题':'文章标题（选填）'} rows={cover?3:2} required={cover} maxLength={200} value={title} onChange={e=>{setTitle(e.target.value);changed()}} placeholder={cover?'输入你的标题，按已选审美生成封面大图':'留空时用正文首行作为任务名称'}/>
        </label>
        {draftSelect}
        {cover?<>
          <div className="illustration-cover-options">
            <label>画幅<select className="select" aria-label="画幅" value={platform} onChange={e=>{setPlatform(e.target.value);changed()}}>{(preferences?.formats||[{value:'wechat',label:'公众号横版'},{value:'xiaohongshu',label:'小红书竖版'},{value:'square',label:'方形封面'}]).map(o=><option key={o.value} value={o.value}>{o.label}</option>)}</select></label>
            <label>风格<select className="select" aria-label="风格" value={style} onChange={e=>{setStyle(e.target.value);changed()}}>{(preferences?.styles||[{value:'auto',label:'按我的审美'}]).map(o=><option key={o.value} value={o.value}>{o.label}</option>)}</select></label>
          </div>
          <details className="illustration-context"><summary>补充文章内容（可选）{article.trim()?' · 已载入':''}</summary><div className="stack">{source}</div></details>
        </>:<>{source}<div className="inline-form"><label>预览张数<select className="select" value={count} onChange={e=>{setCount(Number(e.target.value));changed()}}>{[2,3,4,5,6].map(n=><option key={n} value={n}>{n} 张</option>)}</select></label><span className="small muted">集中在一张预览图里，方便比较</span></div></>}
        <label>补充要求（可选）<textarea className="textarea" aria-label="补充要求（可选）" rows={2} maxLength={3000} value={notes} onChange={e=>{setNotes(e.target.value);changed()}} placeholder={cover?'例如：突出工具组合，不加副标题':'例如：字再少一点，第三段适合做流程图'}/></label>
        <div className="illustration-reference stack">
          <label>上传参考 IP 图（可选）
            <input className="input" type="file" accept="image/png,image/jpeg,image/webp" disabled={busy||uploading}
              onChange={e=>{const file=e.currentTarget.files?.[0];e.currentTarget.value='';if(file)void uploadReference(file)}}/>
          </label>
          <p className="small muted">PNG、JPG 或 WebP，最多一张、8MB。仅用于本次任务，不修改默认角色。</p>
          {uploading&&<p className="small muted" role="status">正在上传参考图…</p>}
          {reference&&<>
            <div className="illustration-reference-thumb"><img src={referenceUrl(reference.reference_key)} alt="本次上传的参考 IP 图"/><span className="small">{reference.name}</span></div>
            <label>参考图怎么用<select className="select" value={referenceMode} disabled={busy||uploading} onChange={e=>{setReferenceMode(e.target.value as ReferenceMode);changed()}}>
              {Object.entries(referenceLabels).map(([value,label])=><option key={value} value={value}>{label}</option>)}
            </select></label>
          </>}
          {referenceError&&<p className="illustration-error small" role="alert">{referenceError}</p>}
          {(reference||referenceError)&&<button className="btn sm" type="button" disabled={busy||uploading} onClick={clearReference}>{reference?'移除参考图':'继续，不使用参考图'}</button>}
        </div>
        {!uploading&&!referenceError&&<PromptPreview key={promptRevision} url="/api/article-images/prompt-preview" body={()=>({kind:cover?'cover':'preview',title,article,count,notes,cover_platform:platform,cover_style:style,provider,...referenceInput})}/>}
        <button className="btn primary" type="submit" disabled={!canStart}>{cover?'生成封面大图':'生成整组预览'}</button>
        {(running||waiting>0)&&<p className="small muted" role="status">同一时间只生成一张：现在提交会排到队尾{waiting>0?`，前面还有 ${waiting} 个在排队`:''}，按顺序自动开始。</p>}
        <p className="small muted">{provider==='codex'?'使用本机 Codex 内置生图与 ChatGPT 账号额度。':'使用已配置的生图 API；原电脑关机仍可运行。'}原稿保持原样，失败不自动重生或切换接口。</p>
      </form>
      <section className="illustration-results" aria-label="制图结果"><h2>预览与成图</h2>
        {!tasks.length&&<div className="card card-pad"><p>{cover?'填好标题，生成的封面会显示在这里。':'生成的图片会显示在这里。'}</p><p className="small muted">{cover?'生成一张独立大图，可下载或加入稿件附件。':'先选画面和信息重点，再出大图。'}</p></div>}
        {tasks.length>0&&<select className="select" aria-label="制图任务历史" value={current?.id||''} onChange={e=>{selected.current=Number(e.target.value);void load().catch(e=>toast(e.message,'error'))}}>{tasks.map(t=><option value={t.id} key={t.id}>{t.title} · {taskLabel(t)} · {states[t.state]||t.state}</option>)}</select>}
        {current&&<article className="card card-pad stack illustration-result"><h3>{current.title}</h3><p className="small muted">{current.kind==='preview'?`${current.count} 张编号缩略预览`:taskLabel(current)} · {states[current.state]||current.state}</p>
          {current.error&&<p className="illustration-error" role="alert">{current.error}</p>}
          {current.state==='running'&&<p role="status">正在理解{current.kind==='cover'?'标题':'正文'}并生成图片，通常需要几分钟。完成后会自动显示。</p>}
          {current.state==='queued'&&<p role="status">排队中{ahead>0?`，前面还有 ${ahead} 个任务`:''}。前一张生成完会自动开始，可以留着这个页面等。</p>}
          {current.state==='completed'&&<>
            <a href={`/api/article-images/${current.id}/image`} target="_blank" rel="noreferrer"><img className="illustration-output" src={`/api/article-images/${current.id}/image`} alt={current.kind==='preview'?'整组编号预览':current.kind==='cover'?'根据标题生成的封面大图':'已选编号的独立大图'}/></a>
            <a className="btn" href={`/api/article-images/${current.id}/image?download=true`}>下载{current.kind==='preview'?'预览图':'大图'}</a>
            {current.kind==='preview'?<>
              {!current.draft_id&&<label>这组配图对应的原稿<select className="select" aria-label="配图对应原稿" value={destination} onChange={e=>setDestination(e.target.value?Number(e.target.value):'')}>{draftOptions}</select></label>}
              <p>勾选要的编号（可多选），一次排队生成：</p><div className="actions">{Array.from({length:current.count},(_,i)=>{const n=i+1,on=picked.includes(n);return <button className={'btn'+(on?' primary':'')} disabled={busy||(current.provider==='image-api'&&!confirmApi)} key={i} aria-pressed={on} onClick={()=>setPicked(old=>on?old.filter(v=>v!==n):[...old,n])}>{on?'✓ ':''}{String(n).padStart(2,'0')}</button>})}
                <button className="btn primary" disabled={busy||!picked.length||(current.provider==='image-api'&&!confirmApi)} onClick={()=>void finalMany(picked)}>{picked.length?`生成选中的 ${picked.length} 张`:'先勾选编号'}</button></div>
            </>:<>
              <label>存回原稿<select className="select" aria-label="存回原稿" disabled={!!current.draft_id} value={destination} onChange={e=>setDestination(e.target.value?Number(e.target.value):'')}>{draftOptions}</select></label>
              {current.attached_image_id&&<p className="small muted">{current.in_draft_body?'已插入原文，打开稿件即可查看排版。':'图片已保存，尚未插入正文。点击下方确认即可使用。'}</p>}
              <button className="btn primary" disabled={busy||!destination||applied} onClick={()=>void confirmUse()}>{applied?'已插入原稿':current.in_draft_body&&current.kind==='cover'?'确认设为文章封面':'确认使用，插入原稿'}</button>
              <p className="small muted">{current.kind==='cover'?'封面放在正文开头，同时设置为文章封面。':'按生成时对应的（配图）标记插入；没有明确位置时打开预览选位置。'}</p>
              <button className="btn" disabled={busy||!destination} onClick={()=>setLayoutTask({taskId:current.id,draftId:Number(destination)})}>{current.kind==='cover'?'预览封面入稿效果':'预览插图排版'}</button>
              <details><summary>仅保存附件</summary><button className="btn" disabled={busy||!destination} onClick={()=>void attach()}>保存到这篇稿件的附件</button></details>
              {destination&&<a href={`/#/drafts/${destination}`} className="small">打开原稿</a>}
            </>}
          </>}
          <div className="actions">
            {current.state==='running'?<button className="btn" disabled={busy} onClick={()=>void action('cancel')}>停止生成</button>
              :current.state==='queued'?<button className="btn" disabled={busy} onClick={()=>void action('cancel')}>取消排队</button>
              :current.state!=='completed'&&<>
              <button className="btn" disabled={busy} onClick={()=>void action('recover')}>读取原任务图片</button>
              {current.kind==='final'&&current.parent_id&&current.panel?<button className="btn" disabled={busy} onClick={()=>void final(current.panel!,current.parent_id!)}>重新生成第 {current.panel} 张大图</button>:<button className="btn" disabled={busy||uploading} onClick={()=>restore(current)}>载入原输入，准备重试</button>}
            </>}
          </div>
          <PromptTrace key={current.id} kind="images" id={current.id}/><details><summary>查看本次输入{current.reference_key?' · 有参考图':''}</summary>
            {current.reference_key&&<div className="illustration-reference-thumb"><img src={referenceUrl(current.reference_key)} alt="该任务使用的参考 IP 图"/><span className="small">{referenceLabels[current.reference_mode||'style']}</span></div>}
            {current.notes&&<p className="illustration-source">补充要求：{current.notes}</p>}
            <p className="illustration-source">{current.title}{current.article?'\n\n'+current.article:''}</p>
          </details>
        </article>}
      </section>
    </div>
    {layoutTask&&<ImageLayoutPreview key={`${layoutTask.taskId}-${layoutTask.draftId}`} taskId={layoutTask.taskId} draftId={layoutTask.draftId} onClose={()=>setLayoutTask(null)} onSaved={()=>{void load().catch(()=>{});void refreshDraftInput(layoutTask.draftId).catch(()=>{})}}/>}
  </div>
}
