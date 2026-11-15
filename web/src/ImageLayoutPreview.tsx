import { useEffect, useRef, useState } from 'react'
import type { Editor } from '@tiptap/core'
import MarkdownIt from 'markdown-it'
import { api, ApiError, type DraftImage } from './api'
import { RichEditor } from './RichEditor'
import { ArticlePreview } from './ArticlePreview'
import { useToast } from './ui'
import './ImageLayoutPreview.css'

type Asset = { task_id:number; image_id:number; panel:number; name:string }
type Cover = { image_id:number; field:'cover_wechat'|'cover_x' }
type Layout = {
  draft_id:number; title:string; platform:string; wechat_theme:string; body:string
  expected_updated_at:number; assets:Asset[]; cover:Cover|null; warnings:string[]; auto_placed:number
  current_image_id:number
}
const imageMarkdown=new MarkdownIt('commonmark',{html:false})
function hasImage(body:string,id:number){return imageMarkdown.parse(body,{}).some(t=>t.children?.some(c=>c.type==='image'&&c.attrGet('src')===`/api/images/${id}`))}

export function ImageLayoutPreview({ taskId, draftId, onClose, onSaved }: {
  taskId:number; draftId:number; onClose:()=>void; onSaved:()=>void
}) {
  const toast=useToast(),dialog=useRef<HTMLDialogElement>(null),editor=useRef<Editor|null>(null)
  const [layout,setLayout]=useState<Layout|null>(null),[body,setBody]=useState('')
  const [html,setHtml]=useState(''),[rendered,setRendered]=useState<string|null>(null)
  const [error,setError]=useState(''),[previewError,setPreviewError]=useState('')
  const [busy,setBusy]=useState(false),[conflict,setConflict]=useState(false),[raw,setRaw]=useState(false)
  const [image,setImage]=useState<number|''>(''),[position,setPosition]=useState(''),[useCover,setUseCover]=useState(true)
  const saveKey=useRef(crypto.randomUUID())

  useEffect(()=>{dialog.current?.showModal()},[])
  useEffect(()=>{
    let alive=true
    api.post<Layout>('/api/article-images/layout-preview',{task_id:taskId,draft_id:draftId}).then(row=>{
      if(!alive)return
      setLayout(row);setBody(row.body);setImage(row.assets[0]?.image_id||row.cover?.image_id||'')
    }).catch(e=>{if(alive)setError(e.message)})
    return()=>{alive=false}
  },[taskId,draftId])
  useEffect(()=>{
    if(!layout)return
    let alive=true
    const timer=window.setTimeout(()=>{
      api.post<{html?:string;thread?:{text:string}[]}>('/api/preview',{
        draft_id:layout.draft_id,platform:layout.platform,title:layout.title,body,wechat_theme:layout.wechat_theme,
      }).then(result=>{
        if(!alive)return
        setHtml(result.html||'');setRendered(body);setPreviewError('')
      }).catch(e=>{if(alive){setPreviewError(e.message);setRendered(null)}})
    },250)
    return()=>{alive=false;window.clearTimeout(timer)}
  },[body,layout])

  function update(value:string) {setBody(value);saveKey.current=crypto.randomUUID()}
  async function upload(files:File[]) {
    if(!layout)return []
    const form=new FormData();files.forEach(file=>form.append('files',file))
    try {return (await api.upload<DraftImage[]>(`/api/drafts/${layout.draft_id}/images`,form)).map(row=>row.id)}
    catch(e){toast((e as Error).message,'error');return []}
  }
  async function move() {
    if(!layout||!image)return
    setBusy(true);setError('')
    try {
      const result=await api.post<{body:string}>('/api/article-images/layout-move',{
        draft_id:layout.draft_id,body,image_id:image,target:position,
      })
      update(result.body)
    }catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  async function autoPlace() {
    if(!layout)return
    setBusy(true);setError('')
    try {
      let next=body
      const items=[...layout.assets,...(layout.cover?[{task_id:taskId,image_id:layout.cover.image_id,panel:0,name:'本次封面'}]:[])]
      const steps=[2,4,6,8]
      for(let i=0;i<items.length;i++){
        const item=items[i]
        if(hasImage(next,item.image_id))continue
        const result=await api.post<{body:string}>('/api/article-images/layout-move',{
          draft_id:layout.draft_id,body:next,image_id:item.image_id,target:`第${steps[i]||2+i*2}段后`,
        })
        next=result.body
      }
      update(next)
      toast('已自动放进正文，可直接拖动微调')
    }catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  async function save() {
    if(!layout||rendered!==body)return
    const placed=[...layout.assets,...(layout.cover?[{image_id:layout.cover.image_id}]:[])].map(a=>a.image_id).filter(id=>hasImage(body,id))
    if(!placed.length)return
    setBusy(true);setError('')
    try {
      await api.post('/api/article-images/layout-save',{
        draft_id:layout.draft_id,body,cover:useCover?layout.cover:null,
        expected_updated_at:layout.expected_updated_at,confirm:true,request_key:saveKey.current,
        required_image_id:placed[0],
      })
      toast('排版已保存到原稿');onSaved();onClose()
      try{localStorage.removeItem(`cw-draft-recovery-v1:${layout.draft_id}`)}catch{/* 本机恢复稿作废，避免下次打开覆盖新正文 */}
    }catch(e){setError((e as Error).message);if(e instanceof ApiError&&e.status===409)setConflict(true)}
    finally{setBusy(false)}
  }
  const assets=layout?[...layout.assets,...(layout.cover?[{task_id:taskId,image_id:layout.cover.image_id,panel:0,name:'本次封面'}]:[])]:[]
  const placedIds=layout?assets.map(a=>a.image_id).filter(id=>hasImage(body,id)):[]
  const imagePlaced=placedIds.length>0
  return <dialog ref={dialog} className="image-layout-dialog" aria-labelledby="image-layout-title" onCancel={e=>{e.preventDefault();if(!busy)onClose()}}>
    <header className="image-layout-head">
      <div><h2 id="image-layout-title">预览插图排版</h2><p className="small muted">{layout?.title||'正在载入原稿'} · 确认前，正文只在这里预览</p></div>
      <button className="btn" disabled={busy} onClick={onClose}>关闭预览</button>
    </header>
    {error&&<p className="image-layout-error" role="alert">{error}</p>}
    {layout&&<>
      <div className="image-layout-tools">
        <div className="image-layout-assets" aria-label="本次图片附件">
          {assets.map(asset=><div className="image-layout-asset" key={asset.image_id} draggable onDragStart={event=>{
            const alt=asset.panel?`配图 ${asset.panel}`:'封面'
            // 整块磁贴都能拖：只抓缩略图时拖动经常落空，用户以为已经放进去了
            event.dataTransfer.setData('text/html',`<img src="/api/images/${asset.image_id}" alt="${alt}">`)
            event.dataTransfer.setData('text/plain',`![${alt}](/api/images/${asset.image_id})`)
            event.dataTransfer.effectAllowed='copy'
          }}>
            <img src={`/api/images/${asset.image_id}`} alt={asset.name}/>
            <span className="small">{asset.panel?`配图 ${asset.panel}`:'封面'}</span>
            <button className="btn sm" disabled={raw||busy} onMouseDown={event=>event.preventDefault()} onClick={()=>{
              if(body.includes(`](/api/images/${asset.image_id})`)){toast('这张图已在正文里，可直接拖动，或输入新位置','info');return}
              editor.current?.chain().focus().setImage({src:`/api/images/${asset.image_id}`,alt:asset.name}).run()
            }}>插到光标处</button>
          </div>)}
        </div>
        <p className="small muted">图片已存回本篇附件。可从这里拖入左侧正文；正文里的图片也能拖动。右侧显示当前主题的排版效果。</p>
        {!!layout.warnings.length&&<p className="small image-layout-warning" role="status">{layout.warnings.join(' ')}</p>}
        {!imagePlaced&&<div className="row" style={{gap:8,alignItems:'center'}}>
          <p className="small image-layout-warning" role="status" style={{margin:0}}>图片还没进正文：把上面的图拖到左边（整块都能拖），或点「自动放进去」。</p>
          <button className="btn primary sm" disabled={busy} onClick={()=>void autoPlace()}>自动放进去</button>
        </div>}
        <details className="image-layout-position-details"><summary className="small muted">或指定位置（第3段后 / 正文里的一句话）</summary>
        <form className="image-layout-position" onSubmit={e=>{e.preventDefault();void move()}}>
          <select className="select" aria-label="调整位置的图片" value={image} onChange={e=>setImage(Number(e.target.value))}>{assets.map(asset=><option value={asset.image_id} key={asset.image_id}>{asset.panel?`配图 ${asset.panel}`:'封面图片'}</option>)}</select>
          <input className="input" aria-label="图片放置位置" placeholder="第3段后，或粘贴正文中的一句话" value={position} onChange={e=>setPosition(e.target.value)}/>
          <button className="btn" disabled={busy||!image||!position.trim()}>放到这个位置</button>
        </form>
        </details>
        {layout.cover&&<label className="image-layout-cover"><input type="checkbox" checked={useCover} onChange={e=>{setUseCover(e.target.checked);saveKey.current=crypto.randomUUID()}}/>同时设为文章封面；图片已放在正文开头，可拖动调整</label>}
      </div>
      <div className="image-layout-columns">
        <section className="image-layout-edit" aria-label="可编辑排版">
          <div className="image-layout-column-head"><strong>调整正文和图片位置</strong><button className="btn sm" onClick={()=>setRaw(!raw)}>{raw?'返回可视编辑':'编辑 Markdown'}</button></div>
          {raw?<textarea className="textarea image-layout-markdown" aria-label="排版 Markdown" value={body} disabled={busy} onChange={e=>update(e.target.value)}/>:<RichEditor value={body} onChange={update} onFiles={upload} editorRef={editor} editable={!busy}/>}
        </section>
        <section className="image-layout-render" aria-label="插图后的排版效果">
          <div className="image-layout-column-head"><strong>排版效果</strong><span className="small muted" role="status">{rendered===body?'预览已更新':'正在更新预览…'}</span></div>
          {previewError&&<p role="alert" className="image-layout-error">{previewError}</p>}
          <div className="phone image-layout-phone"><div className="phone-bar">{layout.platform==='wechat'?'公众号文章预览':'文章排版预览'}</div><div className="phone-body">{!layout.wechat_theme&&<h1>{layout.title}</h1>}{layout.platform==='x'?<div style={{whiteSpace:'pre-wrap'}}>{body}</div>:<ArticlePreview html={html}/>}</div></div>
        </section>
      </div>
      <footer className="image-layout-foot"><span className="small muted">{conflict?'原稿已在别处改过，请关闭后重新载入再排版':layout&&rendered!==body?'正在更新右侧预览，稍等再确认':layout&&!imagePlaced?'本次图片还没进正文，所以还不能确认':'确认后保存到原稿，预览和移动不调用模型。'}</span>{conflict&&<button className="btn" onClick={()=>{void navigator.clipboard.writeText(body).then(()=>toast('当前排版已复制，可关闭后重新载入原稿'))}}>复制当前排版</button>}<button className="btn primary" disabled={busy||rendered!==body||conflict||!imagePlaced} onClick={()=>void save()}>{busy?'正在处理…':'确认排版，保存到原稿'}</button></footer>
    </>}
  </dialog>
}
