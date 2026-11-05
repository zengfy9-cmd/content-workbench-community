import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useToast } from '../ui'
import './ArticleImages.css'

type Task={id:number;title:string;kind:'preview'|'final';count:number;panel:number|null;parent_id:number|null;state:string;error:string;article?:string;notes?:string;artifacts?:{sha256:string}[]}
type Draft={id:number;title:string}
const states:Record<string,string>={running:'生成中',completed:'已生成',failed:'生成失败',cancelled:'已停止',interrupted:'中断 · 待核对'}

export function ArticleImages({draftId}:{draftId?:number}){
  const toast=useToast(),[title,setTitle]=useState(''),[article,setArticle]=useState(''),[notes,setNotes]=useState(''),[count,setCount]=useState(4)
  const [tasks,setTasks]=useState<Task[]>([]),[current,setCurrent]=useState<Task|null>(null),[busy,setBusy]=useState(false),[drafts,setDrafts]=useState<Draft[]>([]),[target,setTarget]=useState<number|''>(draftId||'')
  const requestKey=useRef(crypto.randomUUID()),selected=useRef<number|null>(null)
  function changed(){requestKey.current=crypto.randomUUID()}
  async function load(){
    const list=await api.get<Task[]>('/api/article-images');setTasks(list)
    const id=selected.current||list[0]?.id
    if(id){const row=await api.get<Task>(`/api/article-images/${id}`);if(selected.current===null||selected.current===id)setCurrent(row)}
  }
  const readDraft=useCallback(async(id:number)=>{const row=await api.get<{title:string;body:string}>(`/api/drafts/${id}`);setTitle(row.title);setArticle(row.body);requestKey.current=crypto.randomUUID()},[])
  useEffect(()=>{let alive=true;const tick=()=>{if(alive)void load().catch(()=>{})};tick();const timer=window.setInterval(tick,2500);return()=>{alive=false;window.clearInterval(timer)}},[])
  useEffect(()=>{void api.get<Draft[]>('/api/drafts').then(setDrafts).catch(()=>{});if(draftId){setTarget(draftId);void readDraft(draftId).catch(e=>toast(e.message,'error'))}},[draftId,readDraft,toast])
  async function start(){setBusy(true);try{const r=await api.post<{id:number}>('/api/article-images',{request_key:requestKey.current,title:title.trim()||Array.from(article.trim().split(/\r?\n/)[0]).slice(0,36).join(''),article,count,notes});selected.current=r.id;await load()}catch(e){toast((e as Error).message,'error')}finally{setBusy(false)}}
  async function final(panel:number,parentId=current?.id){if(!current)return;setBusy(true);try{const r=await api.post<{id:number}>(`/api/article-images/${parentId}/final`,{panel,confirm:true,request_key:crypto.randomUUID()});selected.current=r.id;await load()}catch(e){toast((e as Error).message,'error')}finally{setBusy(false)}}
  async function action(kind:string){if(!current)return;setBusy(true);try{await api.post(`/api/article-images/${current.id}/${kind}`);await load()}catch(e){toast((e as Error).message,'error')}finally{setBusy(false)}}
  async function attach(){if(!current||!target)return;setBusy(true);try{await api.post(`/api/article-images/${current.id}/attach`,{draft_id:target});toast('已加入稿件附件，正文保持原样')}catch(e){toast((e as Error).message,'error')}finally{setBusy(false)}}
  return <div className="page illustration-page">
    <div className="page-head"><div><h1>正文配图</h1><p>放入正文，先看一组编号预览，选定后再生成独立大图。</p></div></div>
    <div className="illustration-layout">
      <section className="card card-pad stack">
        <div className="illustration-style"><strong>自己的角色与画风</strong><p className="small muted">在补充要求里描述风格；也可将自己的参考图放进私有数据目录 article-images/references（最多 5 张）。未配置参考图时按正文与补充要求设计。</p></div>
        <label>已有稿件（可选）<select className="select" value={target} onChange={e=>setTarget(e.target.value?Number(e.target.value):'')}><option value="">直接粘贴正文</option>{drafts.map(d=><option key={d.id} value={d.id}>{d.title||`稿件 ${d.id}`}</option>)}</select></label>
        {target&&<button className="btn sm" onClick={()=>void readDraft(Number(target)).catch(e=>toast(e.message,'error'))}>载入这篇正文</button>}
        <label>文章标题（选填）<input className="input" maxLength={200} value={title} onChange={e=>{setTitle(e.target.value);changed()}} placeholder="留空时用正文首行作为任务名称"/></label>
        <label>正文<textarea className="textarea" rows={12} maxLength={80000} value={article} onChange={e=>{setArticle(e.target.value);changed()}} placeholder="粘贴已经确定的正文，Codex 只为它配图。"/></label>
        <div className="inline-form"><label>预览张数<select className="select" value={count} onChange={e=>{setCount(Number(e.target.value));changed()}}>{[2,3,4,5,6].map(n=><option key={n} value={n}>{n} 张</option>)}</select></label><span className="small muted">集中在一张预览图里，方便比较</span></div>
        <label>补充要求（可选）<textarea className="textarea" rows={2} maxLength={3000} value={notes} onChange={e=>{setNotes(e.target.value);changed()}} placeholder="例如：字再少一点，第三段适合做流程图"/></label>
        <button className="btn primary" disabled={busy||!article.trim()||tasks.some(t=>t.state==='running')} onClick={()=>void start()}>生成整组预览</button>
        <p className="small muted">使用本机 Codex 的内置生图工具与 ChatGPT 账号额度。正文用于配图理解，原稿不会被改写。额度不足时保留错误，不自动转用图片 API。</p>
      </section>
      <section className="illustration-results"><h2>预览与成图</h2>
        {!tasks.length&&<div className="card card-pad"><p>生成的图片会显示在这里。</p><p className="small muted">先选画面和信息重点，再出大图。</p></div>}
        {tasks.length>0&&<select className="select" aria-label="配图任务历史" value={current?.id||''} onChange={e=>{selected.current=Number(e.target.value);void load().catch(e=>toast(e.message,'error'))}}>{tasks.map(t=><option value={t.id} key={t.id}>{t.title} · {t.kind==='preview'?'整组预览':`第${t.panel}张大图`} · {states[t.state]||t.state}</option>)}</select>}
        {current&&<article className="card card-pad stack" style={{marginTop:16}}><h3>{current.title}</h3><p className="small muted">{current.kind==='preview'?`${current.count} 张编号缩略预览`:`第 ${current.panel} 张独立大图`} · {states[current.state]||current.state}</p>
          {current.error&&<p className="illustration-error" role="alert">{current.error}</p>}
          {current.state==='running'&&<p role="status">正在理解正文并生成图片，通常需要几分钟。完成后会自动显示。</p>}
          {current.state==='completed'&&<><a href={`/api/article-images/${current.id}/image`} target="_blank" rel="noreferrer"><img className="illustration-output" src={`/api/article-images/${current.id}/image`} alt={current.kind==='preview'?'整组编号预览':'已选编号的独立大图'}/></a><a className="btn" href={`/api/article-images/${current.id}/image?download=true`}>下载{current.kind==='preview'?'预览图':'大图'}</a>
          {current.kind==='preview'?<><p>选择满意的编号，再生成独立大图：</p><div className="actions">{Array.from({length:current.count},(_,i)=><button className="btn" disabled={busy||tasks.some(t=>t.state==='running')} key={i} onClick={()=>void final(i+1)}>确认 {String(i+1).padStart(2,'0')}，生成大图</button>)}</div></>:<><label>添加到稿件附件<select className="select" value={target} onChange={e=>setTarget(e.target.value?Number(e.target.value):'')}><option value="">请选择稿件</option>{drafts.map(d=><option key={d.id} value={d.id}>{d.title||`稿件 ${d.id}`}</option>)}</select></label><button className="btn" disabled={busy||!target} onClick={()=>void attach()}>保存到这篇稿件的附件</button></>}</>}
          <div className="actions">{current.state==='running'?<button className="btn" disabled={busy} onClick={()=>void action('cancel')}>停止生成</button>:current.state!=='completed'&&<><button className="btn" disabled={busy} onClick={()=>void action('recover')}>读取原任务图片</button>{current.kind==='final'&&current.parent_id&&current.panel?<button className="btn" disabled={busy||tasks.some(t=>t.state==='running')} onClick={()=>void final(current.panel!,current.parent_id!)}>重新生成第 {current.panel} 张大图</button>:<button className="btn" disabled={busy} onClick={()=>{setTitle(current.title);setArticle(current.article||'');setCount(current.count);setNotes(current.notes||'');changed();toast('已载入原输入，点击生成整组预览将发起新任务')}}>载入原输入，准备重试</button>}</>}</div>
          <details><summary>查看本次输入正文</summary><p className="illustration-source">{current.article}</p></details>
        </article>}
      </section>
    </div>
  </div>
}
