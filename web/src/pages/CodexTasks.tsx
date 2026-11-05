import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useToast } from '../ui'

type Task={id:number;title:string;project:string;platform:string;model:string;effort:string;state:string;prompt?:string;result?:string;partial?:string;error?:string;draft_id?:number;usage?:{total?:{inputTokens?:number;outputTokens?:number}}}
type Connection={ok:boolean;detail:string;plan?:string}
const states:Record<string,string>={running:'执行中',completed:'已完成 · 待审核',failed:'调用失败',cancelled:'已停止',interrupted:'执行中断 · 待核对'}

export function CodexTasks(){
  const toast=useToast(), [tasks,setTasks]=useState<Task[]>([]), [current,setCurrent]=useState<Task|null>(null)
  const [connection,setConnection]=useState<Connection|null>(null),[checking,setChecking]=useState(false),[busy,setBusy]=useState(false)
  const [title,setTitle]=useState(''),[project,setProject]=useState(''),[platform,setPlatform]=useState('wechat'),[prompt,setPrompt]=useState('')
  const requestKey=useRef(crypto.randomUUID()),selected=useRef<number|null>(null)
  function changed(){requestKey.current=crypto.randomUUID()}
  async function load(){
    const list=await api.get<Task[]>('/api/codex/tasks');setTasks(list)
    const id=selected.current||list[0]?.id
    if(id){const task=await api.get<Task>(`/api/codex/tasks/${id}`);if(selected.current===null||selected.current===id)setCurrent(task)}
  }
  useEffect(()=>{let alive=true;const tick=()=>{if(alive)void load().catch(()=>{})};tick();const interval=window.setInterval(tick,2000);return()=>{alive=false;window.clearInterval(interval)}},[])
  async function check(){setChecking(true);try{setConnection(await api.get<Connection>('/api/codex/status'))}catch(e){toast((e as Error).message,'error')}finally{setChecking(false)}}
  async function start(){setBusy(true);try{const r=await api.post<{id:number}>('/api/codex/tasks',{title,project,platform,prompt,request_key:requestKey.current});selected.current=r.id;await load()}catch(e){toast((e as Error).message,'error')}finally{setBusy(false)}}
  async function action(kind:string){if(!current)return;setBusy(true);try{if(kind==='adopt'){const r=await api.post<{id:number}>(`/api/codex/tasks/${current.id}/adopt`);window.location.hash=`/drafts/${r.id}`}else{await api.post(`/api/codex/tasks/${current.id}/${kind}`);await load()}}catch(e){toast((e as Error).message,'error')}finally{setBusy(false)}}
  return <div className="page narrow">
    <div className="page-head"><div><h1>Codex 创作</h1><p>把任务和真实材料交给 Codex，结果留在这里，审核后采用到稿件。</p></div></div>
    <div className="card card-pad stack">
      <div className="actions"><strong>GPT-6.1 Sol · 极高</strong><span className="pill">ChatGPT 订阅额度</span><button className="btn sm" disabled={checking} onClick={()=>void check()}>{checking?'检查中…':'检查本机连接'}</button></div>
      <p className="small muted">{connection?.detail||'使用本机 Codex 的 ChatGPT 登录。生成会消耗订阅额度；额度不足或型号不可用时会显示错误。'}{connection?.ok&&'。登录状态可用不代表当前型号已通过生成验证。'}</p>
      <div className="stack">
        <label>成果标题<input className="input" value={title} maxLength={200} onChange={e=>{setTitle(e.target.value);changed()}} placeholder="这次要完成什么？"/></label>
        <div className="inline-form"><label>项目<input className="input" value={project} maxLength={100} onChange={e=>{setProject(e.target.value);changed()}} placeholder="填写所属项目"/></label><label>目标平台<select className="select" value={platform} onChange={e=>{setPlatform(e.target.value);changed()}}><option value="wechat">公众号</option><option value="xhs">小红书</option><option value="douyin">抖音</option><option value="x">推特</option></select></label></div>
        <label>任务要求与材料<textarea className="textarea" rows={8} value={prompt} maxLength={100000} onChange={e=>{setPrompt(e.target.value);changed()}} placeholder="写清要求，并粘贴已有正文、原文或真实经历。链接本身不能代替正文。"/></label>
        <p className="small muted">当前支持文字任务。配图制作、网页采集和本聊天历史尚未接入；只使用本次填写的材料。</p>
        <div className="actions"><button className="btn primary" disabled={busy||!title.trim()||!project.trim()||!prompt.trim()||tasks.some(t=>t.state==='running')} onClick={()=>void start()}>{busy?'提交中…':'交给 Codex'}</button></div>
      </div>
    </div>
    <section style={{marginTop:24}}><h2>任务与结果</h2><div className="actions" aria-label="任务历史">{tasks.map(t=><button className="btn sm" key={t.id} aria-pressed={current?.id===t.id} onClick={()=>{selected.current=t.id;void load().catch(e=>toast(e.message,'error'))}}>{t.title} · {states[t.state]||t.state}</button>)}</div>
      {!tasks.length&&<p className="muted">还没有任务。完成后可在这里查看结果和回执。</p>}
      {current&&<article className="card card-pad stack" style={{marginTop:16}}><h3>{current.title}</h3><p className="small muted">{current.project} · {current.platform} · {current.model} · {current.effort==='xhigh'?'极高':current.effort==='high'?'高':current.effort} · {states[current.state]||current.state}</p>
        {current.error&&<p role="alert" style={{overflowWrap:'anywhere'}}>{current.error}</p>}
        <div style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',lineHeight:1.8}}>{current.result||current.partial||'等待 Codex 返回文字…'}</div>
        {current.usage?.total&&<p className="small muted">输入 {current.usage.total.inputTokens??'未知'} tokens · 输出 {current.usage.total.outputTokens??'未知'} tokens（不表示单独 API 账单）</p>}
        <details><summary>本次任务输入</summary><p style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{current.prompt}</p></details>
        <div className="actions">{current.state==='running'?<button className="btn" disabled={busy} onClick={()=>void action('cancel')}>停止任务</button>:<button className="btn" disabled={busy} onClick={()=>void action('recover')}>读取原任务结果</button>}{current.state==='completed'&&<button className="btn primary" disabled={busy} onClick={()=>void action('adopt')}>{current.draft_id?'打开已采用稿件':'采用到稿件 · 待审'}</button>}</div>
        <p className="small muted">读取原结果不会重新生成；采用到稿件后仍需审稿。</p>
      </article>}
    </section>
  </div>
}
