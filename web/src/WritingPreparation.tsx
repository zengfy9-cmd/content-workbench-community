import { WritingSources, type AutoSources } from './WritingSources'
import { PromptPreview } from './AiRules'
import { AssetSearch } from './AssetLinks'
import { WritingModelPicker, useWritingModel } from './WritingModelPicker'
import { useEffect, useState } from 'react'
import { api, type Idea } from './api'
import { go } from './App'
import { Dialog } from './ui'
import { PLATFORMS, type ContentPlatform } from './platforms'
import { BriefFields } from './IdeaBrief'
import { briefOf, type Brief } from './ideaBriefModel'

type Source={analysis_id?:number|null;id:number;title:string;preview:string;author?:string;source?:string;status?:string;archived?:number;length?:number}
type Method={id:number;title:string;content:string;validation:string}
export type SourceUse={kind:string;ref:string;where:string;note:string}
export type WritingContext={sources?:SourceUse[];auto_sources?:AutoSources;methods?:Method[];title:string;brief:Brief&{angle:string;own_facts:string};style:{identity:string;guide:string;manual:string;habits:string;working_rules?:string};materials:Source[];benchmarks:Source[];created_at:number;images?:{id:number;name:string}[]}
type Prep={methods:Method[];idea:Idea;selected:{material_ids:number[];benchmark_ids:number[]};style:WritingContext['style'];materials:Source[];benchmarks:Source[]}
function SourcePicker({kind,items,selected,onChange}:{kind:'materials'|'benchmarks';items:Source[];selected:number[];onChange:(v:number[])=>void}){
 const [open,setOpen]=useState(false),[q,setQ]=useState(''),[limit,setLimit]=useState(20)
 const label=kind==='materials'?'素材':'对标',route=kind==='materials'?'materials':'benchmarks'
 const shown=items.filter(i=>!selected.includes(i.id)&&`${i.title} ${i.preview}`.toLowerCase().includes(q.trim().toLowerCase()))
 function row(item:Source){const chosen=selected.includes(item.id);return <label className="prep-source" key={item.id}><input type="checkbox" disabled={!chosen&&(selected.length>=6||!!item.status&&item.status!=='ready')} checked={chosen} onChange={e=>onChange(e.target.checked?[...selected,item.id]:selected.filter(id=>id!==item.id))}/><span><strong>{item.title}</strong><small>{item.source==='media'?'外部导入 · 保留原作者归属':item.source?'素材 · 来源归属以正文为准':`参考文章${item.author?' · '+item.author:''}`}{item.archived?' · 已归档':''}{item.status&&item.status!=='ready'?' · 尚未处理完成':''}</small><small>{item.preview}</small>{(item.length||0)>8000&&<small>长材料：本次采用前8000字符；关键内容在后面时，请先单独整理为素材。</small>}<a href={`#/${route}/${item.id}`} target="_blank" rel="noreferrer">查看{label} ↗</a></span></label>}
 return <section className="prep-source-section"><h3>{kind==='materials'?'采用的素材':'参考文章'} <span className="muted small">已选 {selected.length}/6</span></h3>{selected.length?selected.map(id=>items.find(i=>i.id===id)).filter((i):i is Source=>!!i).map(row):<p className="small muted">尚未选择{label}</p>}<button className="btn sm" onClick={()=>setOpen(v=>!v)}>{open?'收起选择':`＋ 选择${label}`}</button>{open&&<div className="prep-source-search"><input className="input" aria-label={`搜索${label}`} placeholder={`搜索${label}标题与摘要`} value={q} onChange={e=>{setQ(e.target.value);setLimit(20)}}/>{shown.slice(0,limit).map(row)}{!shown.length&&<p className="small muted">没有其他符合条件的{label}</p>}{shown.length>limit&&<button className="btn sm" onClick={()=>setLimit(v=>v+20)}>查看更多</button>}</div>}</section>
}
export function WritingPreparation({idea,platform,onClose}:{idea:Idea;platform:ContentPlatform;onClose:()=>void}){
 const [confirmed,setConfirmed]=useState(false)
 const writingModel=useWritingModel()
 const [title,setTitle]=useState(idea.title),[savedTitle,setSavedTitle]=useState(idea.title),[titleSaving,setTitleSaving]=useState(false)
 const [methods,setMethods]=useState<number[]>(idea.method_ids||[])
 const [data,setData]=useState<Prep|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false)
 const [brief,setBrief]=useState(briefOf(idea)),[angle,setAngle]=useState(idea.angle||''),[facts,setFacts]=useState(''),[materials,setMaterials]=useState<number[]>([]),[benchmarks,setBenchmarks]=useState<number[]>([])
 useEffect(()=>{let alive=true;api.get<Prep>(`/api/ideas/${idea.id}/preparation`).then(d=>{if(!alive)return;setData(d);setTitle(d.idea.title);setSavedTitle(d.idea.title);setBrief(briefOf(d.idea));setAngle(d.idea.angle||'');setMaterials(d.selected.material_ids);setBenchmarks(d.selected.benchmark_ids)}).catch(e=>{if(alive)setError(e.message)});return()=>{alive=false}},[idea.id])
 async function saveTitle(){
  if(!title.trim()){setError('请填写选题标题');return}
  setTitleSaving(true);setError('')
  try{await api.patch(`/api/ideas/${idea.id}`,{title:title.trim()});setSavedTitle(title.trim());setTitle(title.trim());setConfirmed(false)}
  catch(e){setError((e as Error).message)}finally{setTitleSaving(false)}
 }
 async function start(){setBusy(true);setError('');try{const preparation={...brief,angle,own_facts:facts,material_ids:materials,benchmark_ids:benchmarks,method_ids:methods};await api.patch(`/api/ideas/${idea.id}`,{title:title.trim(),...brief,angle,material_ids:materials,benchmark_ids:benchmarks,method_ids:methods});const r=await api.post<{id:number}>(`/api/ideas/${idea.id}/draft`,{platform,preparation,writing_model:writingModel.value});window.dispatchEvent(new Event('cw-refresh-status'));onClose();go(`drafts/${r.id}`)}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <Dialog open wide title={`确认方向 · ${PLATFORMS[platform]}`} onClose={()=>{if(!busy&&!titleSaving)onClose()}} footer={<><button className="btn" disabled={busy||titleSaving} onClick={onClose}>返回补材料</button><label className="small"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/> 我已核对方向和材料缺口</label><button className="btn primary" disabled={busy||titleSaving||writingModel.loading||!data||!confirmed||!title.trim()} onClick={()=>void start()}>{busy?'正在准备…':'确认方向，写初稿'}</button></>}>
 {error&&<p className="callout" role="alert">{error}</p>}{!data?<p className="muted">{error?'写作依据未加载，请关闭后重试。':'正在读取选题和材料…'}</p>:<div className="stack writing-preparation"><WritingModelPicker model={writingModel} disabled={busy}/><label className="field"><span>选题标题</span><textarea aria-label="选题标题" className="textarea" rows={2} maxLength={300} value={title} disabled={busy||titleSaving} onChange={e=>{setTitle(e.target.value);setConfirmed(false)}} placeholder="填写这篇内容的选题标题"/></label><div className="row wrap"><button type="button" className="btn sm" disabled={busy||titleSaving||!title.trim()||title.trim()===savedTitle} onClick={()=>void saveTitle()}>{titleSaving?'保存中…':'保存标题'}</button><span className="small muted">{title.trim()===savedTitle?'标题已保存':'标题有修改；起草时也会一并保存。'}</span></div><p className="small muted">以你手选的第一篇对标为主要骨架，其余对标补充。自动从素材库、对标库和内容灵感中筛选相关资料；他人经历不写成你的经历。下方可免费预览本次检索与完整提示词。</p><dl className="brief-summary">{Object.entries({写给谁:brief.audience,为什么写:brief.problem,我的观点:brief.thesis}).map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value||'尚未填写'}</dd></div>)}</dl><details className="brief-more"><summary>修改读者、观点与材料说明</summary><BriefFields value={brief} onChange={setBrief}/><label className="field"><span>本篇切入角度</span><textarea className="textarea" rows={2} maxLength={6000} value={angle} onChange={e=>setAngle(e.target.value)}/></label></details><AssetSearch query={title+' '+angle} onPick={item=>{const add=(old:number[])=>old.includes(item.id)?old:[...old,item.id];if(item.kind==='materials')setMaterials(add);else if(item.kind==='benchmarks')setBenchmarks(add);else setMethods(add)}}/><details className="brief-more"><summary>本篇试用的方法 · 已选 {methods.length}</summary><p className="small muted">仅本篇使用，不自动变成长期规则。来源和方法内容随起稿保存。</p>{data.methods.map(m=><label className="prep-source" key={m.id}><input type="checkbox" checked={methods.includes(m.id)} onChange={e=>setMethods(e.target.checked?[...methods,m.id]:methods.filter(id=>id!==m.id))}/><span><strong>{m.title}</strong><small>{m.validation} · {m.content}</small></span></label>)}</details><SourcePicker kind="benchmarks" items={data.benchmarks} selected={benchmarks} onChange={setBenchmarks}/><SourcePicker kind="materials" items={data.materials} selected={materials} onChange={setMaterials}/><label className="field"><span>补充我的真实经历、案例或判断（可选）</span><textarea className="textarea" rows={3} maxLength={6000} value={facts} onChange={e=>setFacts(e.target.value)} placeholder="只填写你实际经历或确认过的内容。没有可以留空。"/></label><div className="idea-image-grid">{data.idea.image_ids?.map(id=><a key={id} href={`/api/images/${id}`} target="_blank" rel="noreferrer"><img src={`/api/images/${id}`} alt="选题图片附件"/></a>)}</div><StylePreview style={data.style}/><PromptPreview url={`/api/ideas/${idea.id}/prompt-preview`} body={()=>({...brief,title,platform,angle,own_facts:facts,material_ids:materials,benchmark_ids:benchmarks,method_ids:methods})}/><div className="prep-missing"><strong>材料缺口</strong><p>{brief.missing_materials||(!materials.length&&!facts.trim()?'尚未提供本人实操或案例；如需亲历内容，留待补充。':'未填写具体缺口；不代表事实已经验证。')}</p><small>缺少的事实、实测和案例会留在备注或批注中，不由 AI 补造。</small></div></div>}</Dialog>
}
function StylePreview({style}:{style:WritingContext['style']}){
 return <details className="brief-more"><summary>我的写法 · 查看本次采用的规则</summary><p className="small">身份：{style.identity}</p>{([['guide','文风要求'],['habits','已启用的改稿习惯'],['manual','个人背景说明'],['working_rules','工作准则']] as const).map(([k,l])=><div key={k}><strong className="small">{l}</strong><pre className="prep-rule">{style[k]||'尚未配置，保持空白。'}</pre></div>)}</details>
}
const normSrc=(s:string)=>(s||'').replace(/[\s*_`#>~\-—–·【】“”"']/g,'')
function SourceUseTable({sources,body,known}:{sources:SourceUse[];body:string;known:Set<number>}){
 const nb=normSrc(body)
 const rows=sources.map(s=>{
   const num=parseInt((s.ref||'').replace(/\D/g,''),10)
   const isSelf=s.kind==='自述'||/本人补充/.test(s.ref||'')
   const inBody=isSelf?true:!!normSrc(s.where)&&nb.includes(normSrc(s.where))
   const refOk=isSelf||!num||known.has(num)
   return {...s,state:(inBody?(refOk?'ok':'warn'):'bad') as 'ok'|'warn'|'bad'}
 })
 const bad=rows.filter(r=>r.state==='bad').length, warn=rows.filter(r=>r.state==='warn').length
 const color={ok:'#3a7d5d',warn:'#b07d18',bad:'#b4362f'}
 return <div style={{marginTop:12}}>
   <p className="small"><strong>逐段来源 · {rows.length} 条</strong>　起草模型自述，发布前逐行核对
     {bad>0&&<span style={{color:color.bad}}>　· {bad} 条在正文里搜不到</span>}
     {warn>0&&<span style={{color:color.warn}}>　· {warn} 条来源不在本篇资料里</span>}
     {!bad&&!warn&&<span style={{color:color.ok}}>　· 全部对得上</span>}</p>
   <div style={{border:'1px solid #e6e3da',borderRadius:10,overflow:'auto'}}><table style={{width:'100%',borderCollapse:'collapse',fontSize:13}}>
     <thead><tr>{['正文这段','来源','用了什么'].map(h=><th key={h} style={{textAlign:'left',padding:'7px 10px',background:'#f7f7f3',borderBottom:'1px solid #e6e3da',fontWeight:500,color:'#7b8078'}}>{h}</th>)}</tr></thead>
     <tbody>{rows.map((s,i)=><tr key={i} style={s.state==='ok'?undefined:{background:s.state==='bad'?'#fdf2f1':'#fdf8ec'}}>
       <td style={{padding:'8px 10px',borderBottom:'1px solid #f0eee7',color:'#414741',maxWidth:220}}>
         {s.where}
         {s.state!=='ok'&&<div style={{fontSize:11,color:color[s.state],marginTop:2}}>{s.state==='bad'?'正文里搜不到这段':'这条资料不在本篇里'}</div>}
       </td>
       <td style={{padding:'8px 10px',borderBottom:'1px solid #f0eee7',whiteSpace:'nowrap',color:color[s.state]}}>{s.kind} {s.ref}</td>
       <td style={{padding:'8px 10px',borderBottom:'1px solid #f0eee7',color:'#414741'}}>{s.note}</td>
     </tr>)}</tbody>
   </table></div>
 </div>
}

export function WritingContextSummary({context,body=''}:{context:WritingContext;body?:string}){
 return <details className="writing-context-summary"><summary>本篇写作依据 · 已保存</summary><dl className="brief-summary">{Object.entries({写给谁:context.brief.audience,为什么写:context.brief.problem,我的观点:context.brief.thesis,材料缺口:context.brief.missing_materials}).map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value||'尚未填写'}</dd></div>)}</dl><p className="small">手选对标（第一篇为主）：{context.benchmarks.map(s=>s.title+(s.analysis_id?`（研究版本 ${s.analysis_id}）`:'' )).join('、')||'未选'}</p><p className="small">本篇试用方法：{context.methods?.map(m=>m.title+' · '+m.validation).join('、')||'未选'}</p><p className="small">采用素材：{context.materials.map(s=>s.title).join('、')||'未选'}</p>{context.brief.own_facts&&<p className="small">本人补充：{context.brief.own_facts}</p>}<div className="idea-image-grid">{context.images?.map(i=><a key={i.id} href={`/api/images/${i.id}`} target="_blank" rel="noreferrer"><img src={`/api/images/${i.id}`} alt={i.name}/></a>)}</div>{context.sources&&context.sources.length>0
 ?<SourceUseTable sources={context.sources} body={body} known={new Set<number>([...(context.benchmarks||[]).map(b=>b.id),...(context.materials||[]).map(m=>m.id),...(context.methods||[]).map(m=>m.id)])}/>
 :<p className="small muted">这篇没有逐段来源（旧版起草流程生成的）。重新起草一次就会有。</p>}<WritingSources sources={context.auto_sources}/><StylePreview style={context.style}/></details>
}
