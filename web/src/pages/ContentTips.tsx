import { useState } from 'react'
import { useLocalDraft } from '../useLocalDraft'
import { api } from '../api'
import { Dialog, usePoll, useToast } from '../ui'
import { go } from '../App'
import { TagChips } from '../TagChips'

export const TIP_CATEGORIES=['选题','标题','开头','结构','素材','排版'] as const
export type Tip={source_benchmark_id?:number;source_analysis_id?:number;id:number;title:string;content:string;category:string;origin:string;validation:string;scenario:string;steps:string;example:string;source_url:string;feedback:string;pinned:number;archived:number;updated_at:number}
export type TipSeed=Partial<Tip>

export function TipDialog({open,onClose,onSaved,tip=null,seed}:{open:boolean;onClose:()=>void;onSaved:()=>void;tip?:Tip|null;seed?:TipSeed}) {
  // Mount only when opening so each capture starts from its own current values.
  return <Dialog open={open} title={tip?'编辑内容灵感技巧':'记一条内容灵感技巧'} onClose={onClose}>{open&&<TipForm key={tip?.id||'new'} tip={tip} seed={seed} onSaved={onSaved} onClose={onClose}/>}</Dialog>
}
function TipForm({tip,seed,onSaved,onClose}:{tip:Tip|null;seed?:TipSeed;onSaved:()=>void;onClose:()=>void}) {
  const initial=tip||seed
  const [form,setForm,clearDraft]=useLocalDraft(`cw-tip-edit-${tip?.id||'new'}-${seed?.source_url||''}`,{title:initial?.title||'',content:initial?.content||'',category:initial?.category||'选题',origin:initial?.origin||'我的心得',validation:initial?.validation||'待验证',scenario:initial?.scenario||'',steps:initial?.steps||'',example:initial?.example||'',source_url:initial?.source_url||'',feedback:initial?.feedback||''})
  const [busy,setBusy]=useState(false), [error,setError]=useState('');const toast=useToast()
  function field(key:keyof typeof form,value:string){setForm(f=>({...f,[key]:value}))}
  async function save(){
    if(busy)return
    setBusy(true);setError('')
    try{const values={...form,title:form.title.trim()||form.content.trim().split('\n')[0].slice(0,80)};await(tip?api.patch(`/api/tips/${tip.id}`,values):api.post('/api/tips',values));clearDraft();toast('技巧已保存');onSaved()}catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  return <div className="stack tip-form">
    <p className="small muted">输入会暂存在当前浏览器，关闭或刷新后可继续；点击保存才正式入库。</p>{error&&<p className="callout" role="alert">{error}</p>}
    <label className="field">一句技巧<textarea className="textarea" autoFocus rows={4} value={form.content} maxLength={12000} placeholder="例如：从今天真实遇到的一个问题里找选题，先写错误判断，再写修正动作。" onChange={e=>field('content',e.target.value)}/></label>
    <label className="field">标题（可不填）<input className="input" value={form.title} maxLength={200} onChange={e=>field('title',e.target.value)}/></label>
    <div className="tip-fields">{([['category','分类',TIP_CATEGORIES],['origin','来源类型',['我的心得','他人分享','AI建议']],['validation','使用状态',['待验证','试用中','用过有效']]] as const).map(([key,label,values])=><label className="field" key={key}>{label}<select className="select" value={form[key]} onChange={e=>field(key,e.target.value)}>{values.map(v=><option key={v}>{v}</option>)}</select></label>)}</div>
    <details open={!!tip}><summary>补充场景、步骤、例子和我的反馈</summary><div className="stack tip-more">{([['scenario','什么时候用',3000],['steps','具体步骤',6000],['example','一个例子',6000],['source_url','来源链接',4096],['feedback','我的使用反馈与验证',4000]] as const).map(([key,label,max])=><label key={key} className="field">{label}<textarea className="textarea" rows={key==='source_url'?1:2} value={form[key]} maxLength={max} onChange={e=>field(key,e.target.value)}/></label>)}<p className="small muted">反馈由你填写；尚未填写不代表认可或验证有效。</p></div></details>
    <div className="row" style={{justifyContent:'flex-end'}}><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={busy||!form.content.trim()} onClick={()=>void save()}>{busy?'保存中…':'保存技巧'}</button></div>
  </div>
}

export function TipCapture({seed,label='存为技巧'}:{seed:TipSeed;label?:string}) {
  const [open,setOpen]=useState(false)
  return <><button className="btn sm" onClick={()=>setOpen(true)}>{label}</button><TipDialog open={open} seed={seed} onClose={()=>setOpen(false)} onSaved={()=>setOpen(false)}/></>
}

export function ContentTips(){return <div className="page tips-page"><div className="page-head"><div><h1>内容灵感技巧</h1><p>留下自己的方法、例子和判断，下一次写作时拿来用。</p></div></div><TipsPanel/></div>}

export function TipsPanel({compact=false,onUse}:{compact?:boolean;onUse?:(text:string)=>void}) {
  const [tips,setTips]=useState<Tip[]>([]), [q,setQ]=useState(''), [category,setCategory]=useState('全部'), [archived,setArchived]=useState(false)
  const [editing,setEditing]=useState<Tip|null>(null), [adding,setAdding]=useState(false), [error,setError]=useState(''), [ideaTip,setIdeaTip]=useState<Tip|null>(null)
  const toast=useToast()
  const load=()=>api.get<Tip[]>('/api/tips').then(t=>{setTips(t);setError('')}).catch(e=>setError(e.message))
  usePoll(load,15000)
  async function patch(tip:Tip,body:object){try{await api.patch(`/api/tips/${tip.id}`,body);void load()}catch(e){toast((e as Error).message,'error')}}
  const shown=tips.filter(t=>!!t.archived===archived&&(category==='全部'||t.category===category)&&`${t.title} ${t.content} ${t.steps} ${t.example} ${t.source_url} ${t.feedback}`.toLowerCase().includes(q.trim().toLowerCase()))
  return <div className={'tips-panel'+(compact?' compact':'')}>
    <div className="tips-tools"><input className="input" value={q} onChange={e=>setQ(e.target.value)} placeholder="搜索方法、例子、来源" aria-label="搜索内容灵感技巧"/><button className="btn primary" onClick={()=>{setEditing(null);setAdding(true)}}>＋ 记一条</button></div>
    <div className="tips-filters" aria-label="技巧分类">{['全部',...TIP_CATEGORIES].map(c=><button key={c} aria-pressed={category===c} onClick={()=>setCategory(c)}>{c}</button>)}<button aria-pressed={archived} onClick={()=>setArchived(!archived)}>{archived?'查看使用中':'归档'}</button></div>
    {error&&<p role="alert" className="callout">{error}</p>}
    {!shown.length&&<div className="tips-empty"><strong>{q?'没有找到对应技巧':archived?'没有归档的技巧':'记下第一条自己的方法'}</strong><p>选题时的一点发现，或者一次写作里真正管用的动作，都可以留在这里。</p><button className="btn" onClick={()=>{setEditing(null);setAdding(true)}}>＋ 记一条</button></div>}
    <div className="tip-list">{shown.map(t=><article className={'tip-card'+(t.pinned?' pinned':'')} key={t.id}>
      <header><h3>{!!t.pinned&&<span title="已置顶">⌖ </span>}{t.title}</h3><button className="btn ghost sm" onClick={()=>{setEditing(t);setAdding(true)}}>编辑</button></header>
      <TagChips tags={[t.category,t.origin,t.validation]}/>{t.source_benchmark_id&&<a className="small" href={`#/benchmarks/${t.source_benchmark_id}?analysis=${t.source_analysis_id||''}`}>查看来源研究 · 版本 {t.source_analysis_id}</a>}<p className="tip-content">{t.content}</p>
      {(t.scenario||t.steps||t.example||t.source_url||t.feedback)&&<details><summary>查看例子与使用反馈</summary>{([['适用场景',t.scenario],['具体步骤',t.steps],['例子',t.example],['我的使用反馈',t.feedback]] as const).map(([label,text])=>text&&<div className="tip-detail" key={label}><strong>{label}</strong><p>{text}</p></div>)}{t.source_url&&<a href={t.source_url} target="_blank" rel="noopener noreferrer">查看原始来源</a>}</details>}
      <footer><button className="btn ghost sm" onClick={()=>void patch(t,{pinned:!t.pinned})}>{t.pinned?'取消置顶':'置顶'}</button><button className="btn sm" onClick={()=>setIdeaTip(t)}>用于选题</button>{onUse&&<button className="btn primary sm" onClick={()=>onUse([`参考技巧：${t.title}（${t.origin} · ${t.validation}）`,t.content,t.scenario,t.steps,t.example,t.source_url].filter(Boolean).join('\n'))}>带入写作准备</button>}<button className="btn ghost sm" onClick={()=>void patch(t,{archived:!t.archived})}>{t.archived?'恢复':'归档'}</button></footer>
    </article>)}</div>
    <TipDialog open={adding} tip={editing} onClose={()=>setAdding(false)} onSaved={()=>{setAdding(false);void load()}}/>
    <Dialog open={!!ideaTip} title="用这条技巧准备选题" onClose={()=>setIdeaTip(null)}>{ideaTip&&<TipIdea key={ideaTip.id} tip={ideaTip} onClose={()=>setIdeaTip(null)}/>}</Dialog>
  </div>
}
function TipIdea({tip,onClose}:{tip:Tip;onClose:()=>void}) {
  const [title,setTitle]=useState(''), [angle,setAngle]=useState(''), [busy,setBusy]=useState(false), [error,setError]=useState('')
  async function save(){setBusy(true);try{await api.post('/api/ideas',{title,angle:[angle,`参考技巧：${tip.title}（${tip.origin} · ${tip.validation}）`,tip.content,tip.source_url].filter(Boolean).join('\n'),platforms:['公众号'],method_ids:[tip.id]});window.dispatchEvent(new Event('cw-refresh-status'));onClose();go('ideas')}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  return <div className="stack"><p className="small muted">参考：{tip.title}。写下你自己的选题，保存后进入选题库。</p>{error&&<p role="alert">{error}</p>}<label className="field">我想写的选题<input className="input" value={title} onChange={e=>setTitle(e.target.value)} maxLength={300}/></label><label className="field">我的观点与真实材料<textarea className="textarea" value={angle} onChange={e=>setAngle(e.target.value)} maxLength={4000}/></label><button className="btn primary" disabled={!title.trim()||busy} onClick={()=>void save()}>保存到选题库</button></div>
}
