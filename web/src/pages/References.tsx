import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import MarkdownIt from 'markdown-it'
import { api, type DraftReference } from '../api'
import { Dialog, Empty } from '../ui'
import * as Ic from '../icons'
import { ReferenceReading } from './ReferenceReading'

const markdown = new MarkdownIt({ html: false, linkify: true, breaks: true })
const defaultValidate = markdown.validateLink.bind(markdown)
markdown.validateLink = url => /^https?:\/\//i.test(url) && defaultValidate(url)
markdown.renderer.rules.link_open = (tokens, index, options, _env, renderer) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return renderer.renderToken(tokens, index, options)
}
const renderImage = markdown.renderer.rules.image!
markdown.renderer.rules.image = (tokens, index, options, env, renderer) => {
  tokens[index].attrSet('referrerpolicy', 'no-referrer')
  tokens[index].attrSet('loading', 'lazy')
  tokens[index].attrSet('decoding', 'async')
  return renderImage(tokens, index, options, env, renderer)
}

function storedIds(key: string): number[] | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) || 'null')
    return Array.isArray(value) ? [...new Set(value.filter((n): n is number => Number.isInteger(n) && n > 0))] : null
  } catch { return null }
}

function ArticleBody({ article }: { article: DraftReference }) {
  const html = useMemo(() => markdown.render(article.body), [article.body])
  return <ReferenceReading article={article} html={html} />
}

export function ReferencesPanel({ draftId, left, onPosition }: { draftId: number; left: boolean; onPosition: () => void }) {
  const key = `cw-reference-panes-${draftId}`
  const [articles, setArticles] = useState<DraftReference[]>([])
  const [openIds, setOpenIds] = useState<number[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [adding, setAdding] = useState(false)
  const [mode, setMode] = useState<'link' | 'paste'>('link')
  const [url, setUrl] = useState('')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)
  const [connectUrl, setConnectUrl] = useState('')
  const panes = useRef<HTMLDivElement>(null)
  const drag = useRef<{ y:number; sizes:Record<number,number>; first:number; second:number } | null>(null)
  const sizeKey = `cw-reference-sizes-${draftId}`
  const [sizes, setSizes] = useState<Record<number,number>>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(sizeKey) || '{}')
      return Object.fromEntries(Object.entries(saved).filter(([id,n])=>Number(id)>0 && typeof n==='number' && Number.isFinite(n) && n>0).map(([id,n])=>[id,Number(n)]))
    } catch { return {} }
  })
  function resize(first: number, second: number, delta: number, initial?:Record<number,number>) {
    const measured = initial || Object.fromEntries(Array.from(panes.current?.querySelectorAll<HTMLElement>(':scope > .reference-pane') || []).map(el=>[Number(el.dataset.referenceId),el.getBoundingClientRect().height]))
    const total = measured[first]+measured[second]
    const minimum = Math.min(110,total/3)
    const top = Math.max(minimum,Math.min(total-minimum,measured[first]+delta))
    const next = {...measured,[first]:top,[second]:total-top}
    setSizes(next)
    try { localStorage.setItem(sizeKey,JSON.stringify(next)) } catch { /* resizing remains available */ }
  }

  async function connectScys() {
    setBusy(true); setError(''); setConnectUrl('')
    try {
      const result = await api.post<{ url: string }>('/api/references/scys/connect', {})
      setConnectUrl(result.url)
    } catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }

  function remember(ids: number[]) {
    setOpenIds(ids)
    try { localStorage.setItem(key, JSON.stringify(ids)) } catch { /* reading remains available */ }
  }

  useEffect(() => {
    let active = true
    api.get<DraftReference[]>(`/api/drafts/${draftId}/references`).then(items => {
      if (!active) return
      setArticles(items)
      const saved = storedIds(key)
      setOpenIds(saved === null ? items.map(item => item.id) : saved.filter(id => items.some(item => item.id === id)))
    }).catch(e => { if (active) setLoadError((e as Error).message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [draftId, key])

  async function add() {
    setBusy(true); setError('')
    try {
      const content = mode === 'link'
        ? await api.post<{ title: string; url: string; body: string }>(`/api/drafts/${draftId}/references/read`, { url: url.trim() })
        : { title: title.trim(), body: body.trim(), url: url.trim() }
      const article = await api.post<DraftReference>(`/api/drafts/${draftId}/references`, { ...content, source: mode === 'link' ? 'web' : 'paste' })
      setArticles(items => [...items, article])
      remember([...openIds, article.id])
      setExpanded(null); setAdding(false); setUrl(''); setTitle(''); setBody('')
    } catch (e) {
      setError((e as Error).message)
    } finally { setBusy(false) }
  }

  const open = openIds.map(id => articles.find(article => article.id === id)).filter((article): article is DraftReference => !!article)
  const closed = articles.filter(article => !openIds.includes(article.id))
  const links = url.match(/https?:\/\/[^\s<>"）]+/g) || []
  const originalUrl = links.length === 1 ? links[0].replace(/[，。；)\]]+$/, '') : ''
  const validUrl = !!originalUrl

  return <div className="reference-workspace">
    <div className="reference-tools">
      <div className="reference-heading"><Ic.Doc size={17} /><b>对标原文</b><span className="small muted">{open.length > 0 ? `${open.length} 篇并排参考` : '边看边写'}</span></div>
      <div className="reference-actions">
        <button className="btn ghost sm" onClick={onPosition} title={left ? '把参考区放到右边' : '把参考区放到左边'}>{left ? <Ic.Right size={14} /> : <Ic.Back size={14} />}{left ? '放右边' : '放左边'}</button>
        <button className="btn sm" onClick={() => { setError(''); setAdding(true) }} disabled={loading || !!loadError}><Ic.Plus size={14} /> 添加原文</button>
      </div>
      {closed.length > 0 && <label className="reference-reopen">打开已收起的原文
        <select aria-label="打开已收起的原文" value="" onChange={e => { remember([...openIds, Number(e.target.value)]); setExpanded(null) }}>
          <option value="" disabled>选择文章（{closed.length} 篇）</option>
          {closed.map(article => <option key={article.id} value={article.id}>{article.title}</option>)}
        </select>
      </label>}
    </div>
    {loading ? <div className="reference-message muted" role="status">正在载入对标原文…</div>
      : loadError ? <div className="reference-message callout" role="alert"><Ic.Alert size={16} />{loadError}</div>
      : open.length === 0 ? <div className="reference-message"><Empty icon={<Ic.Doc />} title={articles.length ? '原文已收起' : '把对标文章放在旁边'}>
        {articles.length ? '从上方列表重新打开，或添加另一篇。' : '粘贴文章链接或正文。多打开一篇，参考区就多一格，每篇都能独立滚动。'}
      </Empty><button className="btn primary" onClick={() => setAdding(true)}><Ic.Plus size={15} /> 添加对标文章</button></div>
      : <div ref={panes} className="reference-panes" aria-label="对标文章分屏">
        {open.map((article,index) => <Fragment key={article.id}>
          {index > 0 && expanded === null && <div className="reference-divider" role="separator" tabIndex={0} aria-orientation="horizontal" aria-label={`调整第 ${index} 篇和第 ${index+1} 篇高度`} aria-controls={`reference-pane-${open[index-1].id}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(100*(sizes[open[index-1].id] || 1)/((sizes[open[index-1].id] || 1)+(sizes[article.id] || 1)))} title="上下拖动调整高度；方向键微调，双击平分"
            onPointerDown={e=>{
              if (e.button!==0) return
              e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId)
              drag.current={y:e.clientY,first:open[index-1].id,second:article.id,sizes:Object.fromEntries(Array.from(panes.current!.querySelectorAll<HTMLElement>(':scope > .reference-pane')).map(el=>[Number(el.dataset.referenceId),el.getBoundingClientRect().height]))}
            }}
            onPointerMove={e=>{ const d=drag.current; if(d) resize(d.first,d.second,e.clientY-d.y,d.sizes) }}
            onPointerUp={e=>{ drag.current=null; if(e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId) }}
            onPointerCancel={()=>{drag.current=null}} onLostPointerCapture={()=>{drag.current=null}}
            onDoubleClick={()=>{ const a=panes.current!.querySelector<HTMLElement>(`#reference-pane-${open[index-1].id}`)!; const b=panes.current!.querySelector<HTMLElement>(`#reference-pane-${article.id}`)!; resize(open[index-1].id,article.id,(b.getBoundingClientRect().height-a.getBoundingClientRect().height)/2) }}
            onKeyDown={e=>{ if(e.key==='ArrowUp'||e.key==='ArrowDown') {e.preventDefault();resize(open[index-1].id,article.id,e.key==='ArrowUp'?-24:24)} }}><span /></div>}
          <section id={`reference-pane-${article.id}`} data-reference-id={article.id} style={expanded===null ? {flexGrow:sizes[article.id] || (Object.values(sizes).length ? Object.values(sizes).reduce((a,b)=>a+b,0)/Object.values(sizes).length : 1)} : undefined} className={'reference-pane' + (expanded !== null && expanded !== article.id ? ' reference-hidden' : '')} aria-label={`对标原文：${article.title}`}>
          <header className="reference-pane-top">
            <div className="reference-pane-name"><b title={article.title}>{article.title}</b><span className="small muted">{article.source === 'web' ? '网页正文' : '粘贴原文'}{article.url && <> · <a href={article.url} target="_blank" rel="noopener noreferrer">打开原网页 <Ic.External size={11} /></a></>}</span></div>
            {open.length > 1 && <button className="btn ghost icon sm" aria-label={expanded === article.id ? '恢复全部分屏' : `放大原文：${article.title}`} title={expanded === article.id ? '恢复全部分屏' : '暂时放大这一篇'} onClick={() => setExpanded(expanded === article.id ? null : article.id)}><Ic.Eye size={15} /></button>}
            <button className="btn ghost icon sm" aria-label={`收起原文：${article.title}`} title="收起这篇，仍保留在参考列表" onClick={() => { remember(openIds.filter(id => id !== article.id)); setExpanded(null) }}><Ic.Close size={15} /></button>
          </header>
          <ArticleBody article={article} />
        </section></Fragment>)}
      </div>}

    <Dialog open={adding} title="添加对标原文" onClose={() => { if (!busy) setAdding(false) }} footer={<>
      <button className="btn" disabled={busy} onClick={() => setAdding(false)}>取消</button>
      <button className="btn primary" disabled={busy || (mode === 'link' ? !validUrl : !body.trim())} onClick={add}>{busy ? <><Ic.Loader size={15} /> 正在读取并打开…</> : mode === 'link' ? '读取并打开原文' : '保存并打开原文'}</button>
    </>}>
      <div className="reference-form stack">
        <div className="reference-modes" role="group" aria-label="添加原文的方式">
          <button className={'btn sm' + (mode === 'link' ? ' primary' : '')} aria-pressed={mode === 'link'} disabled={busy} onClick={() => { setMode('link'); setError('') }}><Ic.LinkI size={14} /> 文章链接</button>
          <button className={'btn sm' + (mode === 'paste' ? ' primary' : '')} aria-pressed={mode === 'paste'} disabled={busy} onClick={() => { setMode('paste'); setError('') }}><Ic.Doc size={14} /> 粘贴原文</button>
        </div>
        <label>原文链接{mode === 'paste' && <span className="muted">（可选）</span>}<input aria-label="原文链接" type="text" value={url} disabled={busy} placeholder="公众号文章、图片帖或小红书分享链接" onChange={e => setUrl(e.target.value)} /></label>
        {mode === 'link' && <><p className="small muted">公众号支持普通文章和图片帖；小红书可以粘贴整段分享文案，请保留链接中的分享参数。生财文章通过生财 MCP 读取。</p>
          {/https:\/\/(?:www\.)?scys\.com\//i.test(url) && <div><button className="btn sm" disabled={busy} onClick={connectScys}>连接生财 MCP</button>{connectUrl && <p><a className="btn primary sm" href={connectUrl} target="_blank" rel="noopener noreferrer">前往生财授权 <Ic.External size={13} /></a><span className="small muted"> 授权完成后，回来重新读取原文。</span></p>}</div>}
        </>}
        {mode === 'paste' && <>
          <label>文章标题<input aria-label="对标文章标题" value={title} disabled={busy} maxLength={300} placeholder="方便你识别这篇参考" onChange={e => setTitle(e.target.value)} /></label>
          <label>原文正文<textarea aria-label="原文正文" value={body} disabled={busy} maxLength={200000} rows={10} placeholder="把原文粘贴到这里，支持 Markdown 和图片链接" onChange={e => setBody(e.target.value)} /></label>
        </>}
        <p className="small muted">每篇原文单独保存到当前稿件，打开新文章会保留前面已打开的参考。</p>
        {error && <div className="callout" role="alert"><Ic.Alert size={16} /><div>{error}{mode === 'link' && <div className="reference-error-actions"><button className="btn sm" onClick={() => { setMode('paste'); setUrl(originalUrl); setError('') }}>改为粘贴原文</button>{validUrl && <a className="btn sm" href={originalUrl} target="_blank" rel="noopener noreferrer">打开原网页 <Ic.External size={13} /></a>}</div>}</div></div>}
      </div>
    </Dialog>
  </div>
}
