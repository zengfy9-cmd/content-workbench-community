import { useLocalDraft } from '../useLocalDraft'
import { go } from '../App'
import { useState } from 'react'
import { api } from '../api'
import type { Benchmark } from '../benchmarkTypes'
import { Dialog } from '../ui'
import { BenchmarkBatchImport } from './BenchmarkBatchImport'

export function BenchmarkImport({ open, onClose, onSaved, onChanged }: { open: boolean; onClose: () => void; onSaved: (item: Benchmark) => void; onChanged: () => void }) {
  const [mode, setMode] = useState<'link' | 'paste'>('link')
  const [form,setForm,clearForm]=useLocalDraft('cw-benchmark-import',{url:'',title:'',body:'',author:'',kind:'未分类',tags:''})
  const {url,title,body,author,kind,tags}=form
  const setUrl=(url:string)=>setForm(v=>({...v,url}))
  const setTitle=(title:string)=>setForm(v=>({...v,title}))
  const setBody=(body:string)=>setForm(v=>({...v,body}))
  const setAuthor=(author:string)=>setForm(v=>({...v,author}))
  const setKind=(kind:string)=>setForm(v=>({...v,kind}))
  const setTags=(tags:string)=>setForm(v=>({...v,tags}))
  const [original,setOriginal]=useState<{original_html?:string;read_count?:number|null}>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [connectUrl, setConnectUrl] = useState('')
  async function read() {
    setBusy(true); setError('')
    try {
      const result = await api.post<{ title: string; body: string; url: string; author?: string; tags?: string[]; original_html?:string;read_count?:number|null }>('/api/benchmarks/read', { url: url.trim() })
      setOriginal({original_html:result.original_html,read_count:result.read_count}); setTitle(result.title); setBody(result.body); setUrl(result.url)
      setAuthor(result.author || ''); setTags((result.tags || []).join('，'))
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  async function save() {
    setBusy(true); setError('')
    try {
      const result = await api.post<Benchmark>('/api/benchmarks', { ...original, title, body, url, author, kind, tags: tags.split(/[，,]/).map(t => t.trim()).filter(Boolean), source: mode === 'link' ? 'web' : 'paste' })
      clearForm(); onSaved(result)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  async function connect() {
    setBusy(true)
    try { setConnectUrl((await api.post<{ url: string }>('/api/references/scys/connect')).url) }
    catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return <Dialog open={open} title="导入对标文章" onClose={onClose} wide footer={mode === 'link' ? <button className="btn" onClick={onClose}>完成</button> : <>
    <span className="small muted">保留原文，收藏后由你选择何时拆解。</span>
    <button className="btn primary" disabled={busy || !title.trim() || !body.trim()} onClick={save}>{busy ? '处理中…' : '保存到对标库'}</button>
  </>}>
    <div className="stack">
      <button className="btn" onClick={()=>{onClose();go('imports')}}>导入音视频 · 使用小D整理</button>
      <div className="segmented"><button aria-pressed={mode === 'link'} onClick={() => setMode('link')}>链接批量导入</button><button aria-pressed={mode === 'paste'} onClick={() => setMode('paste')}>粘贴正文</button></div>
      {mode === 'link' ? <BenchmarkBatchImport onSaved={onSaved} onChanged={onChanged} /> : <>
      <label className="field"><span>来源链接（可选）</span><input className="input" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://…" /></label>
      {url.trim() && <button className="btn" disabled={busy} onClick={read}>从链接补充原文与作者</button>}
      <label className="field"><span>原文标题</span><input className="input" value={title} maxLength={300} onChange={e => setTitle(e.target.value)} /></label>
      <div className="row wrap">
        <label className="field grow"><span>作者（不确定可留空）</span><input className="input" value={author} maxLength={120} onChange={e => setAuthor(e.target.value)} /></label>
        <label className="field"><span>内容类型</span><select className="select" value={kind} onChange={e => setKind(e.target.value)}>{['未分类', '教程', '案例', '观点', '故事', '清单', '其他'].map(t => <option key={t}>{t}</option>)}</select></label>
      </div>
      <label className="field"><span>标签，用逗号分开</span><input className="input" value={tags} onChange={e => setTags(e.target.value)} placeholder="AI实战，工具体验…" /></label>
      <label className="field"><span>原文正文</span><textarea className="textarea" rows={10} value={body} onChange={e => setBody(e.target.value)} placeholder="粘贴取得的原文正文，保留图片和链接…" /></label>
      {error && <div className="callout" role="alert">{error}</div>}
      {error && url.includes('scys.com') && <div className="row wrap"><button className="btn sm" disabled={busy} onClick={connect}>连接工作台的生财 MCP</button>{connectUrl && <a href={connectUrl} target="_blank" rel="noopener noreferrer">打开生财授权页</a>}</div>}
      </>}
    </div>
  </Dialog>
}
