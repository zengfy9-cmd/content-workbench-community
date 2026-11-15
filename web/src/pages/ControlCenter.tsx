import MarkdownIt from 'markdown-it'
import { useRef, useState, type ClipboardEvent } from 'react'
import { api } from '../api'
import { go } from '../App'
import { usePoll, useToast } from '../ui'
import { PlanDialog } from './WritingPlans'

type Source = { id: number; kind: string; title: string; excerpt?: string }
type Turn = { id: number; images?: number[]; message: string; answer: string; state: string; error: string; context: { mode?: string; related?: Source[] } }
type Data = { turns: Turn[]; overview: {
  plans: { id: number; title: string; day: string; draft_id: number | null }[]
  sources: { id: number; title: string; current_analysis_id: number | null }[]
  drafts: { id: number; title: string }[]
  open_comments: number
} }

const markdown = new MarkdownIt('commonmark', { html: false, linkify: false }).disable('image')

function sourceHref(source: Source) {
  if (source.kind === 'plans') return '#/today'
  if (source.kind === 'tips' || source.kind === 'ideas') return `#/${source.kind}`
  return `#/${source.kind}/${source.id}`
}

function Answer({ turn }: { turn: Turn }) {
  const sources = turn.context.related || []
  const quotes = turn.context.mode === 'quotes'
  const final = turn.context.mode === 'final'
  const pending = ['queued', 'running'].includes(turn.state)
  return <>
    <div className="control-answer">
      {turn.answer && quotes
        ? <><p>{turn.answer}</p>{sources.map(source => <figure className="control-quote" key={source.id}>
            <blockquote>{source.excerpt}</blockquote>
            <figcaption><a href={sourceHref(source)} title={source.title}>素材 #{source.id}</a></figcaption>
          </figure>)}</>
        : turn.answer && final
          ? <><p>{turn.answer}</p>{!!sources.length && <ul>{sources.map(source =>
              <li key={source.id}><a href={sourceHref(source)}>{source.title || '未命名成稿'}</a></li>)}</ul>}</>
          : turn.answer && !['ideas','today'].includes(turn.context.mode || '')
          ? <div className="control-markdown" dangerouslySetInnerHTML={{ __html: markdown.render(turn.answer) }} />
          : turn.answer || (pending ? '正在处理…' : turn.error || '没有收到成功结果')}
    </div>
    {!!sources.length && !quotes && !final && !pending && <details className="control-sources">
      <summary>来源</summary>
      <ul>{sources.slice(0, 2).map(source => <li key={source.kind + source.id}>
        <a href={sourceHref(source)}>{source.title}</a>
      </li>)}</ul>
    </details>}
  </>
}

type Screenshot = { id: string; url: string }
const imageTypes = ['image/png','image/jpeg','image/webp']

function readScreenshot(file: File): Promise<Screenshot> {
  return new Promise((resolve,reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve({ id: crypto.randomUUID(), url: String(reader.result) })
    reader.onerror = () => reject(new Error('无法读取截图，请重新粘贴'))
    reader.readAsDataURL(file)
  })
}

export function ControlCenter() {
  const toast = useToast()
  const [data, setData] = useState<Data | null>(null), [error, setError] = useState(''), [text, setText] = useState('')
  const [key, setKey] = useState(() => crypto.randomUUID()), [busy, setBusy] = useState(false), [plan, setPlan] = useState(false)
  const [pictures, setPictures] = useState<Screenshot[]>([])
  const [reading, setReading] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  async function attach(files: File[]) {
    if (busy || reading || !files.length) return
    if (files.length + pictures.length > 2) { toast('每条消息最多放 2 张截图','error'); return }
    if (files.some(f=>!imageTypes.includes(f.type) || f.size > 4*1024*1024)) {
      toast('支持 PNG、JPEG 和 WebP，每张不超过 4 MB','error'); return
    }
    setReading(true)
    try {
      const next=await Promise.all(files.map(readScreenshot))
      setPictures(old=>[...old,...next]);setKey(crypto.randomUUID())
    } catch (e) { toast((e as Error).message,'error') }
    finally { setReading(false) }
  }
  function paste(e: ClipboardEvent<HTMLTextAreaElement>) {
    const items=Array.from(e.clipboardData.items).filter(item=>item.kind==='file').map(item=>item.getAsFile())
      .filter((file): file is File => !!file)
    const files=(items.length?items:Array.from(e.clipboardData.files)).filter(f=>f.type.startsWith('image/'))
    if (!files.length) return
    e.preventDefault()
    const pastedText=e.clipboardData.getData('text/plain')
    if (pastedText && !busy) {
      const {selectionStart,selectionEnd}=e.currentTarget
      setText(old=>(old.slice(0,selectionStart)+pastedText+old.slice(selectionEnd)).slice(0,4000));setKey(crypto.randomUUID())
    }
    void attach(files)
  }
  const load = () => api.get<Data>('/api/control').then(d => { setData(d); setError('') }).catch(e => setError(e.message))
  usePoll(load, 3000)
  const running = busy || data?.turns.some(t => ['queued','running'].includes(t.state))
  async function send() {
    if (running || reading || (!text.trim() && !pictures.length)) return
    setBusy(true)
    try {
      await api.post('/api/control', { message: text, request_key: key, images: pictures.map(p=>p.url.split(',')[1]) })
      setText('');setPictures([])
      setKey(crypto.randomUUID())
      await load()
    } catch (e) { toast((e as Error).message, 'error') }
    finally { setBusy(false) }
  }

  return <div className="page control-page">
    <header className="page-head"><div><h1>中控</h1><p>GPT-6.1 Sol · 高 · 本机 Codex 账号额度</p></div><button className="btn" onClick={() => setPlan(true)}>安排任务</button></header>
    {error && <p className="callout" role="alert">{error}</p>}
    <div className="control-grid">
      <section className="control-conversation" aria-label="中控对话">
        {!data?.turns.length && <div className="card card-pad"><h2>今天想查什么？</h2><p>查素材原话、看今日任务，或挑一个值得写的选题。</p></div>}
        {data?.turns.map(turn => <article key={turn.id} className="control-turn"><div className="control-user">{turn.message && <div>{turn.message}</div>}{!!turn.images?.length && <div className="control-pictures">{turn.images.map((index) => {
          const url=`/api/control/${turn.id}/images/${index}`
          return <a key={index} href={url} target="_blank" rel="noreferrer"><img src={url} alt={`已发送的截图 ${index}`} loading="lazy" /></a>
        })}</div>}</div><Answer turn={turn} /></article>)}
        <form className="control-composer" onSubmit={e => { e.preventDefault(); void send() }}>
          <label htmlFor="control-text">交代任务</label>
          <textarea id="control-text" className="textarea" value={text} maxLength={4000} onChange={e => { setText(e.target.value); setKey(crypto.randomUUID()) }} onPaste={paste} disabled={busy} rows={2} placeholder="输入问题，或直接 Ctrl+V 粘贴截图" />
          {!!pictures.length && <div className="control-pictures">{pictures.map((p,i)=><div key={p.id} className="control-picture">
            <img src={p.url} alt={`待发送的截图 ${i+1}`} />
            <button type="button" className="btn sm" disabled={busy||reading} aria-label={`移除截图 ${i+1}`} onClick={()=>{setPictures(old=>old.filter(a=>a.id!==p.id));setKey(crypto.randomUUID())}}>×</button>
          </div>)}</div>}
          <div className="row wrap">
            <input type="file" hidden ref={fileInput} accept={imageTypes.join(',')} multiple onChange={e=>{void attach(Array.from(e.currentTarget.files||[]));e.currentTarget.value=''}}/>
            <button type="button" className="btn sm" disabled={busy||reading} onClick={()=>fileInput.current?.click()}>添加截图</button>
            <button className="btn primary" disabled={!!running || reading || (!text.trim()&&!pictures.length)}>{running?'处理中…':reading?'读取截图…':'发送'}</button>
          </div>
        </form>
      </section>
      <aside className="control-overview">
        <section className="card card-pad"><h2>手上的任务</h2>{data?.overview.plans.map(p => <p key={p.id}><button className="text-action" onClick={() => go(p.draft_id ? `drafts/${p.draft_id}` : 'today')}>{p.title}</button><small className="muted"> · {p.day}</small></p>)}{!data?.overview.plans.length && <p>没有未完成的计划。</p>}<p className="small">{data?.overview.open_comments || 0} 条批注待处理</p><a href="#/today" className="small">工作总览</a></section>
        <section className="card card-pad"><h2>最近收到的对标</h2>{data?.overview.sources.map(s => <p key={s.id}><a href={`#/benchmarks/${s.id}`}>{s.title}</a><small className="muted"> · {s.current_analysis_id ? '已有拆解' : '待拆解'}</small></p>)}<button className="btn sm" onClick={() => go('benchmarks')}>全部对标与判断</button></section>
        <section className="card card-pad"><h2>继续改稿</h2>{data?.overview.drafts.map(d => <p key={d.id}><a href={`#/drafts/${d.id}`}>{d.title || '未命名稿件'}</a></p>)}</section>
      </aside>
    </div>
    <PlanDialog open={plan} onClose={() => setPlan(false)} onSaved={() => { setPlan(false); void load() }} />
  </div>
}
