import { useCallback,useEffect,useState } from 'react'
import { api } from './api'
import { Dialog,useToast } from './ui'
type Note={mid:string;content:string;stamp:string}
type Source={id:number;title:string;url:string}
export function MobilePending(){
 const [notes,setNotes]=useState<Note[]>([]),[open,setOpen]=useState(false),[sources,setSources]=useState<Source[]>([]),[selected,setSelected]=useState<Record<string,string>>({}),[busy,setBusy]=useState('')
 const toast=useToast()
 const load=useCallback(()=>{api.get<Note[]>('/api/mobile-inbox/pending').then(setNotes).catch(e=>toast(e.message,'error'))},[toast])
 useEffect(()=>{load();const timer=window.setInterval(load,15000);return()=>window.clearInterval(timer)},[load])
 return <><button className="btn sm" onClick={()=>{setOpen(true);api.get<Source[]>('/api/benchmarks').then(setSources).catch(e=>toast(e.message,'error'))}}>待关联评价 {notes.length}</button>{open&&<Dialog open wide title="飞书评价 · 选择对应素材" onClose={()=>setOpen(false)}><div className="stack"><p className="small muted">未找到明确回复关系的文字先放在这里，不作为外部对标文章。请选择你评价的那条素材；不确定可以留待处理。</p>{notes.map(n=><div className="card card-pad stack" key={n.mid}><p style={{whiteSpace:'pre-wrap'}}>{n.content}</p><span className="small muted">{n.stamp}</span><select className="input" aria-label={`评价对应素材：${n.content.slice(0,20)}`} value={selected[n.mid]||''} onChange={e=>setSelected({...selected,[n.mid]:e.target.value})}><option value="">选择对应素材…</option>{sources.map(s=><option key={s.id} value={s.id}>{s.title}</option>)}</select><button className="btn" disabled={!!busy||!selected[n.mid]} onClick={async()=>{setBusy(n.mid);try{await api.post(`/api/mobile-inbox/pending/${encodeURIComponent(n.mid)}/link`,{benchmark_id:Number(selected[n.mid])});load();toast('已放入对应素材的“我的判断”')}catch(e){toast((e as Error).message,'error')}finally{setBusy('')}}}>关联到这条素材</button><button className="btn" disabled={!!busy} onClick={async()=>{setBusy(n.mid);try{await api.post(`/api/mobile-inbox/pending/${encodeURIComponent(n.mid)}/material`);load();toast('已存入个人素材，未调用模型')}catch(e){toast((e as Error).message,'error')}finally{setBusy('')}}}>这是独立想法，存为个人素材</button></div>)}{!notes.length&&<p>没有待关联评价。</p>}</div></Dialog>}</>
}
