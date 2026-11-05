import { useState } from 'react'
import { api } from './api'
import { go } from './App'
import { Dialog } from './ui'
import type { Benchmark } from './benchmarkTypes'

type Candidate={title:string;angle:string;audience:string;problem:string;missing_materials:string}
export function BenchmarkIdea({source,quote,onClose}:{source:Benchmark;quote:string;onClose:()=>void}){
 const [items,setItems]=useState<Candidate[]>([]),[chosen,setChosen]=useState<Candidate>({title:'',angle:'',audience:'',problem:'',missing_materials:''})
 const [busy,setBusy]=useState(false),[error,setError]=useState('')
 const analysis=source.stale?null:source.analysis
 const evidence=`参考：${source.title.slice(0,100)}\n来源链接保存在关联对标中\n选中原文（外部作者）：\n${quote}\n已有拆解版本：${analysis?.id||'尚无有效拆解'}\nAI拆解摘录（最多800字，完整内容见对标库，待核对）：${analysis?JSON.stringify({overview:analysis.overview,paragraph:analysis.paragraphs.find(p=>source.passages.find(s=>s.key===p.key)?.text.includes(quote)),reviews:source.reviews}).slice(0,800):'无'}\n我的判断：尚未填写`
 async function suggest(){setBusy(true);setError('');try{const r=await api.post<{candidates:Candidate[]}>(`/api/benchmarks/${source.id}/idea-candidates`,{quote,analysis_id:analysis?.id??null});setItems(r.candidates)}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function save(){setBusy(true);setError('');try{await api.post('/api/ideas',{...chosen,thesis:'',angle:`AI建议，待我确认：${chosen.angle}`,available_materials:evidence,benchmark_ids:[source.id],readiness:chosen.missing_materials?'needs_material':'unreviewed'});window.dispatchEvent(new Event('cw-refresh-status'));onClose();go('ideas')}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <Dialog open wide title="拿来做选题" onClose={()=>{if(!busy)onClose()}} footer={<><button className="btn" disabled={busy} onClick={onClose}>取消</button><button className="btn primary" disabled={busy||!chosen.title.trim()} onClick={()=>void save()}>保存选题，继续确认方向</button></>}><div className="stack">
 <p className="small muted">1 选材料 → 2 选择或填写方向 → 3 保存后补充本人观点。此处不会生成初稿。</p>
 <details><summary>已选原文与来源 · {quote.length}字</summary><blockquote style={{whiteSpace:'pre-wrap'}}>{quote}</blockquote><p>{source.title}</p><a href={source.url} target="_blank" rel="noreferrer">打开来源</a></details>
 <p className="small muted">{analysis?`复用拆解版本 #${analysis.id}，不会重新拆解全文。`:'尚无有效拆解：仅依据所选原文建议方向。'}</p>
 <button className="btn" disabled={busy} onClick={()=>void suggest()}>{busy?'正在处理…':'AI 提三个候选方向'}</button>
 <p className="small muted">使用工作台当前模型，会产生一次模型调用；也可以直接手动填写。</p>
 {error&&<p role="alert" className="callout">{error}</p>}
 {items.map((item,i)=><button className="btn" style={{whiteSpace:'normal',textAlign:'left',display:'block'}} key={i} onClick={()=>setChosen(item)}><strong>{item.title}</strong><p className="small">{item.angle}</p></button>)}
 {([['title','选题标题'],['angle','切入角度（AI建议需自行确认）'],['audience','写给谁'],['problem','解决什么问题'],['missing_materials','还需补什么材料']] as const).map(([key,label])=><label className="field" key={key}><span>{label}</span><textarea className="textarea" rows={2} maxLength={key==='title'?100:500} value={chosen[key]} onChange={e=>setChosen({...chosen,[key]:e.target.value})}/></label>)}
 </div></Dialog>
}
