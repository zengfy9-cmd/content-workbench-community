import { useEffect, useRef, useState, type DragEvent } from 'react'
import { api, type Status } from '../api'
import { go } from '../App'
import { Dialog, todayLabel, usePoll, useToast } from '../ui'
import { Composer } from './Composer'
import { PlanDialog, planDay, type Plan, type PlanSource } from './WritingPlans'
import { TipDialog, type Tip } from './ContentTips'
import { TagChips } from '../TagChips'
import MarkdownIt from 'markdown-it'
import './WritingWorkspace.css'

const newsMarkdown=new MarkdownIt({html:false,linkify:false}).enable('table')
newsMarkdown.renderer.rules.link_open=(tokens,index,options,_env,renderer)=>{
  tokens[index].attrSet('target','_blank');tokens[index].attrSet('rel','noopener noreferrer')
  return renderer.renderToken(tokens,index,options)
}
type News={rank:number;title:string;url:string;story_id:string|null;detail:string;body?:string}
type AddState={day:string;mode:PlanSource;kind:'planned'|'extra';existing?:Plan;seed?:News}

export function WritingDashboard({status}:{status:Status|null}) {
  const toast=useToast(), storyRequest=useRef(0)
  const [plans,setPlans]=useState<Plan[]>([]), [tips,setTips]=useState<Tip[]>([])
  const [hot,setHot]=useState<{items:News[];updated_at:number|null;stale:boolean}>({items:[],updated_at:null,stale:true})
  const [story,setStory]=useState<News|null>(null), [storyBusy,setStoryBusy]=useState(false), [newsBusy,setNewsBusy]=useState(false)
  const [adding,setAdding]=useState<AddState|null>(null), [tipOpen,setTipOpen]=useState(false), [capture,setCapture]=useState(false)
  const [tipIndex,setTipIndex]=useState(0)
  const [error,setError]=useState(''), [starting,setStarting]=useState<number|null>(null)
  const load=()=>Promise.all([api.get<Plan[]>('/api/plans'),api.get<typeof hot>('/api/news/hot'),api.get<Tip[]>('/api/tips')])
    .then(([p,n,t])=>{setPlans(p);setHot(n);setTips(t.filter(t=>!t.archived));setError('')}).catch(e=>setError(e.message))
  usePoll(load,15000)
  useEffect(()=>{const listener=()=>setCapture(true);window.addEventListener('cw-capture',listener);return()=>window.removeEventListener('cw-capture',listener)},[])
  async function edit(plan:Plan,patch:object){try{await api.patch(`/api/plans/${plan.id}`,patch);await load()}catch(e){toast((e as Error).message,'error')}}
  async function start(plan:Plan){
    if(starting!==null)return
    setStarting(plan.id)
    try{const r=await api.post<{id:number}>(`/api/plans/${plan.id}/start`,{});go(`drafts/${r.id}`)}catch(e){toast((e as Error).message,'error')}finally{setStarting(null)}
  }
  async function refresh(){setNewsBusy(true);try{setHot(await api.post('/api/news/refresh',{}))}catch(e){toast((e as Error).message,'error')}finally{setNewsBusy(false)}}
  async function detail(item:News){
    const seq=++storyRequest.current;setStory(item);setStoryBusy(!!item.story_id)
    if(!item.story_id)return
    try{const result=await api.get<News>(`/api/news/stories/${item.story_id}`);if(seq===storyRequest.current)setStory(result)}catch(e){if(seq===storyRequest.current)toast((e as Error).message,'error')}finally{if(seq===storyRequest.current)setStoryBusy(false)}
  }
  function create(day=planDay(),mode:PlanSource='new',kind:'planned'|'extra'='planned',seed?:News){setAdding({day,mode,kind,seed})}
  const shownTip=tips.length?tips[tipIndex%tips.length]:null
  const active=plans.filter(p=>!['archived','done'].includes(p.state))
  const today=active.filter(p=>p.day<=planDay()&&p.kind==='planned'), tomorrow=active.filter(p=>p.day===planDay(1)&&p.kind==='planned')
  const future=active.filter(p=>p.day>planDay(1)&&p.kind==='planned'), extra=active.filter(p=>p.kind==='extra')
  const completed=plans.filter(p=>['archived','done'].includes(p.state))
  function addMenu(date:string,label:string){return <details className="action-menu"><summary className="btn sm" aria-label={label}>＋ 添加</summary><div className="action-menu-panel">{([['new','新建写作计划'],['idea','从选题库选择'],['draft','从已有稿件添加']] as const).map(([mode,text])=><button key={mode} onClick={e=>{e.currentTarget.closest('details')?.removeAttribute('open');create(date,mode)}}>{text}</button>)}</div></details>}
  function cards(items:Plan[],nextDay=false){return <div className="writing-task-list">{items.map((p,i)=><article className="writing-task" key={p.id} draggable onDragStart={e=>e.dataTransfer.setData('text/plain',String(p.id))} onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault();e.stopPropagation();const moving=plans.find(x=>x.id===Number(e.dataTransfer.getData('text/plain')));if(moving&&moving.id!==p.id)void edit(moving,{day:p.day,position:p.position-1})}}>
    <div className="task-meta"><span className={'task-state '+(p.state==='writing'?' writing':'')}>{p.state==='writing'?'写作中':'待开始'}</span>{p.day!==planDay()&&<time>{p.day<planDay()?'待续 · ':''}{p.day}</time>}</div>
    <h3>{p.draft_title||p.title}</h3><p>{p.angle||'写下自己的切入角度，补齐真实材料。'}</p>
    <span className="task-references">{p.benchmark_ids.length?`已关联 ${p.benchmark_ids.length} 篇对标`:'还未关联对标'}{p.draft_id?' · 已关联原稿':''}</span>
    <footer><button className={'btn sm '+(p.state==='writing'?'primary':'')} disabled={starting!==null} onClick={()=>void start(p)}>{starting===p.id?'打开中…':p.draft_id?'继续写作':nextDay?'准备写作':'开始写作'}</button>
      <div className="task-secondary">{nextDay&&<button className="text-action" onClick={()=>void edit(p,{day:planDay()})}>移到今天</button>}<button className="text-action" onClick={()=>setAdding({day:p.day,mode:'new',kind:p.kind,existing:p})}>改期</button>
      <details className="action-menu"><summary className="btn ghost sm" aria-label={`${p.title}的更多操作`}>···</summary><div className="action-menu-panel"><button onClick={()=>setAdding({day:p.day,mode:'new',kind:p.kind,existing:p})}>编辑计划与对标</button><button disabled={i===0} onClick={()=>void edit(p,{position:items[i-1].position-1})}>上移</button><button onClick={()=>void edit(p,{day:planDay(nextDay?0:1)})}>{nextDay?'移到今天':'移到明天'}</button><button onClick={()=>void edit(p,{state:'done'})}>标记完成</button><button onClick={()=>void edit(p,{state:'archived'})}>归档计划</button></div></details></div>
    </footer></article>)}</div>}
  function drop(date:string,e:DragEvent){e.preventDefault();const p=plans.find(x=>x.id===Number(e.dataTransfer.getData('text/plain')));if(p)void edit(p,{day:date})}
  return <div className="page writing-dashboard-v2">
    <header className="writing-welcome"><div><h1>今天，先把这篇写出来</h1><p>{todayLabel()}</p></div><div className="row wrap"><button className="btn" onClick={()=>create(planDay(),'draft')}>从稿件添加</button><button className="btn primary" onClick={()=>create()}>＋ 新建计划</button></div></header>
    {error&&<p role="alert" className="callout">{error}</p>}
    <div className="writing-home-layout"><main className="writing-planning-area"><div className="writing-lanes">
      <section className="writing-lane today" onDragOver={e=>e.preventDefault()} onDrop={e=>drop(planDay(),e)}><header><h2>今日写作 <span>{today.length}</span></h2>{addMenu(planDay(),'添加今日写作')}</header>
        {cards(today)}{!today.length&&<div className="writing-lane-empty"><strong>今天准备推进哪一篇？</strong><p>选一篇已有稿件继续写，或安排一个新选题。</p><button className="btn" onClick={()=>create(planDay(),'draft')}>从已有稿件添加</button></div>}
        <button className="lane-add" onClick={()=>create()}>＋ 添加今天要写的内容</button>
      </section>
      <section className="writing-lane tomorrow" onDragOver={e=>e.preventDefault()} onDrop={e=>drop(planDay(1),e)}><header><h2>明天写作计划 <span>{tomorrow.length}</span></h2>{addMenu(planDay(1),'添加明天写作计划')}</header>
        {cards(tomorrow,true)}{!tomorrow.length&&<div className="writing-lane-empty"><strong>给明天留一个好开头</strong><p>先选题、定角度、选好对标素材，明天打开就能接着写。</p><button className="btn" onClick={()=>create(planDay(1),'idea')}>挑一个选题</button></div>}
      </section></div>
      <section className="writing-extra"><header><div><h2>热点加更</h2><span>临时选题单独安排，原计划保留</span></div><button className="btn sm" onClick={()=>create(planDay(),'new','extra')}>＋ 添加加更</button></header>{extra.length>0&&cards(extra)}</section>
      {future.length>0&&<details className="writing-fold"><summary>之后的计划 · {future.length} 篇</summary>{cards(future,true)}</details>}
    </main><aside className="writing-assist" aria-label="热点与内容技巧">
      <section className="writing-news"><header><h2>AIHOT 热点前五</h2><button className="text-action" disabled={newsBusy} onClick={()=>void refresh()}>{newsBusy?'更新中…':'刷新'}</button></header>
        <p className="assist-caption">{hot.updated_at?`${new Date(hot.updated_at*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})} 更新 · 每小时更新`:'正在获取热点，可点击刷新重试'}</p>
        <ol>{hot.items.map(n=><li key={n.url}><span className="news-rank">{n.rank}</span><button className="news-title" onClick={()=>void detail(n)}>{n.title}</button><button className="news-add" aria-label={`将热点“${n.title}”加入计划`} onClick={()=>create(planDay(1),'new','planned',n)}>＋</button></li>)}</ol>
      </section>
      <section className="writing-tips-preview"><header><button className="section-link" onClick={()=>go('tips')}><h2>内容灵感技巧 ›</h2></button><div className="row"><button className="btn ghost sm" aria-label="上一条技巧" disabled={tips.length<2} onClick={()=>setTipIndex(i=>(i+tips.length-1)%tips.length)}>←</button><span className="small muted" aria-live="polite">{tips.length?tipIndex%tips.length+1:0}/{tips.length}</span><button className="btn ghost sm" aria-label="下一条技巧" disabled={tips.length<2} onClick={()=>setTipIndex(i=>(i+1)%tips.length)}>→</button></div></header><p className="assist-caption">写不出来时，翻一张自己的方法卡</p>
        {shownTip?<button className="home-tip-note" onClick={()=>go('tips')}><strong>{shownTip.pinned?'⌖ ':''}{shownTip.title}</strong><p>{shownTip.content}</p><TagChips tags={[shownTip.category,shownTip.origin]}/></button>:<div className="home-tip-note"><strong>留住一个对你有用的动作</strong><p>选题、开头、写法和排版，先记一句，下次写作时再用。</p></div>}
        <div className="tip-preview-actions"><button className="btn" onClick={()=>go('tips')}>查看技巧</button><button className="btn primary" onClick={()=>setTipOpen(true)}>＋ 记一条</button></div>
      </section>
    </aside></div>
    <div className="writing-inbox-strip"><div><strong>待处理</strong><button onClick={()=>go('ideas')}>{status?.counts.ideas_new||0} 个选题</button><button onClick={()=>go('materials')}>{status?.counts.materials||0} 条素材</button><button onClick={()=>setCapture(true)}>＋ 记一笔</button></div><span>计划关联原稿，安排日期不会复制文章</span></div>
    <details className="writing-fold"><summary>已完成与归档计划 · {completed.length} 篇</summary>{completed.map(p=><div key={p.id} className="row wrap"><span>{p.day} · {p.title}</span><button className="btn sm" onClick={()=>void edit(p,{state:'todo'})}>恢复计划</button></div>)}</details>
    <PlanDialog open={!!adding} existing={adding?.existing} initialDay={adding?.day} mode={adding?.mode} kind={adding?.kind} seed={adding?.seed} onClose={()=>setAdding(null)} onSaved={()=>{setAdding(null);void load()}}/>
    <TipDialog open={tipOpen} onClose={()=>setTipOpen(false)} onSaved={()=>{setTipOpen(false);void load()}}/>
    <Dialog open={capture} title="记一笔 / 添加素材" onClose={()=>setCapture(false)}><Composer status={status} onSaved={()=>{setCapture(false);void load()}}/></Dialog>
    <Dialog open={!!story} title={story?.title||'热点详情'} onClose={()=>{storyRequest.current++;setStory(null)}} footer={<><button className="btn" onClick={()=>{create(planDay(1),'new','planned',story||undefined);storyRequest.current++;setStory(null)}}>加入明天计划</button><button className="btn primary" onClick={()=>{create(planDay(),'new','extra',story||undefined);storyRequest.current++;setStory(null)}}>加入热点加更</button></>}><article className="hot-story"><p className="small muted">AIHOT 聚合资料，重要事实请回原始信源核对。</p><a href={story?.url} target="_blank" rel="noopener noreferrer">打开 AIHOT 来源</a>{storyBusy?<p role="status">正在读取事件详情…</p>:<div dangerouslySetInnerHTML={{__html:newsMarkdown.render(story?.body||story?.detail||'')}}/>}</article></Dialog>
  </div>
}
