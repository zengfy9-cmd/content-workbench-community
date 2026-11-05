import { ReadingMeta } from './StudyWorkspace'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Benchmark } from '../benchmarkTypes'
import { useToast } from '../ui'
import { TagChips } from '../TagChips'

type ImportItem = { read_count?:number|null;read_count_at?:number|null; id: number; url: string; state: string; error: string; warning: string; benchmark_id: number | null; title: string | null; author: string | null; kind: string | null; tags: string[]; archived: number | null }
type ImportBatch = { id: number; active: boolean; auto_classify: number; items: ImportItem[] }
const states: Record<string, string> = { queued: '等待读取', reading: '正在读取原文', classifying: '正在整理分类', saved: '已导入', duplicate: '库中已有 · 未重复收录', failed: '读取失败' }

export function BenchmarkBatchImport({ onSaved, onChanged }: { onSaved: (item: Benchmark) => void; onChanged: () => void }) {
  const [text, setText] = useState('')
  const [auto, setAuto] = useState(true)
  const [batch, setBatch] = useState<ImportBatch | null>(null)
  const [creating, setCreating] = useState(false)
  const [showInput, setShowInput] = useState(true)
  const starting = useRef(false)
  const sequence = useRef(0)
  const [error, setError] = useState('')
  const [connectUrl, setConnectUrl] = useState('')
  const [connecting, setConnecting] = useState(false)
  const toast = useToast()
  useEffect(() => {
    let alive = true
    const version = sequence.current
    api.get<ImportBatch | null>('/api/benchmark-imports/latest').then(value => { if (alive && sequence.current === version) { setBatch(value); setShowInput(!value) } }).catch(e => { if (alive && sequence.current === version) setError(e.message) })
    return () => { alive = false }
  }, [])
  const refresh = useCallback(async () => {
    if (!batch) return
    setBatch(await api.get<ImportBatch>(`/api/benchmark-imports/${batch.id}`))
    onChanged()
  }, [batch, onChanged])
  const active = !!batch?.active
  const batchId = batch?.id
  useEffect(() => {
    if (!active || !batchId) return
    let alive = true
    const timer = window.setInterval(() => {
      api.get<ImportBatch>(`/api/benchmark-imports/${batchId}`).then(value => { if (alive) { setBatch(value); onChanged() } }).catch(e => { if (alive) setError(e.message) })
    }, 2000)
    return () => { alive = false; window.clearInterval(timer) }
  }, [active, batchId, onChanged])
  async function start(value = text) {
    if (starting.current || active || !value.trim()) return
    starting.current = true; sequence.current += 1; setCreating(true); setError('')
    try {
      setBatch(await api.post<ImportBatch>('/api/benchmark-imports', { text: value, auto_classify: auto }))
      setShowInput(false)
      onChanged()
    } catch (e) { setError((e as Error).message) }
    finally { starting.current = false; setCreating(false) }
  }
  async function retry(itemId?: number) {
    if (!batch || creating) return
    setCreating(true); setError('')
    try { setBatch(await api.post<ImportBatch>(`/api/benchmark-imports/${batch.id}/retry`, itemId ? { item_id: itemId } : {})) }
    catch (e) { setError((e as Error).message) } finally { setCreating(false) }
  }
  async function connect() {
    setConnecting(true)
    try { setConnectUrl((await api.post<{ url: string }>('/api/references/scys/connect')).url) }
    catch (e) { setError((e as Error).message) } finally { setConnecting(false) }
  }
  const counts = batch?.items.reduce((all, item) => ({ ...all, [item.state]: (all[item.state] || 0) + 1 }), {} as Record<string, number>) || {}
  return <div className="stack benchmark-batch">
    {batch && <button className="btn sm" disabled={active || creating} onClick={() => setShowInput(!showInput)}>{showInput ? '收起链接输入' : '继续导入新链接'}</button>}
    {showInput && <>
    <label className="field"><span>粘贴文章链接，每行一条，也可以直接粘贴分享文字</span><textarea className="textarea batch-links" aria-label="批量文章链接" value={text} disabled={active || creating} maxLength={50000} rows={4} placeholder={'https://mp.weixin.qq.com/s/…\nhttps://scys.com/articleDetail/…\n一次最多20条，重复链接自动跳过'} onChange={e => setText(e.target.value)} onPaste={e => {
      const value = e.clipboardData.getData('text')
      if (/https?:\/\//i.test(value) && !active && !creating) { e.preventDefault(); setText(value); void start(value) }
    }} /></label>
    <label className="row small"><input type="checkbox" checked={auto} disabled={active || creating} onChange={e => setAuto(e.target.checked)} />自动整理类型和标签（使用已配置模型）</label>
    <p className="small muted batch-help">粘贴后自动导入。作者/账号从原网页或生财 MCP 读取，缺失时留空。已有文章只补齐空缺资料。关闭弹窗后后台继续，稍后可回来查看。</p>
    <div className="row wrap"><button className="btn primary" disabled={active || creating || !text.trim()} onClick={() => start()}>{creating ? '正在创建导入…' : active ? '后台导入中…' : '开始导入'}</button></div>
    </>}
    {!!counts.failed && <button className="btn" disabled={creating} onClick={() => retry()}>重试失败项（{counts.failed}）</button>}
    {error && <div className="callout" role="alert">{error}</div>}
    {batch && <>
      <div className="batch-summary" role="status"><strong>{active ? '正在导入' : '导入结果'}</strong><span>{batch.items.length}条 · 已导入{counts.saved || 0} · 已有{counts.duplicate || 0} · 失败{counts.failed || 0}</span></div>
      <div className="batch-results">{batch.items.map(item => <BatchItem key={`${item.id}-${item.state}`} item={item} onRetry={() => retry(item.id)} retryBusy={creating} onChanged={refresh} onOpen={async () => {
        try { onSaved(await api.get<Benchmark>(`/api/benchmarks/${item.benchmark_id}`)) } catch (e) { toast((e as Error).message, 'error') }
      }} />)}</div>
      {batch.items.some(i => i.state === 'failed' && /https?:\/\/(www\.)?scys\.com/i.test(i.url)) && <div className="row wrap"><button className="btn sm" disabled={connecting} onClick={connect}>连接生财 MCP</button>{connectUrl && <a href={connectUrl} target="_blank" rel="noopener noreferrer">打开授权页</a>}<span className="small muted">授权后点击“重试失败项”。</span></div>}
    </>}
  </div>
}

function BatchItem({ item, onOpen, onChanged, onRetry, retryBusy }: { item: ImportItem; onOpen: () => void; onChanged: () => void; onRetry: () => void; retryBusy: boolean }) {
  const [author, setAuthor] = useState(item.author || '')
  const [kind, setKind] = useState(item.kind || '未分类')
  const [tags, setTags] = useState(item.tags.join('，'))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  async function save() {
    setSaving(true); setError('')
    try { await api.put(`/api/benchmarks/${item.benchmark_id}`, { author, kind, tags: tags.split(/[,，]/).map(t => t.trim()).filter(Boolean) }); onChanged() }
    catch (e) { setError((e as Error).message) } finally { setSaving(false) }
  }
  return <section className={'batch-result ' + item.state}>
    <div className="row wrap"><strong className="grow">{item.title || '等待识别标题'}</strong><span className="badge">{states[item.state] || item.state}</span></div>
    <div className="small muted batch-url">{item.url}</div>
    {item.benchmark_id && <><div className="row wrap small"><span>{item.author || '作者/账号未识别'}</span><span className="badge">{item.kind}</span>{item.archived === 1 && <span>已归档</span>}</div><ReadingMeta source={item}/><TagChips tags={item.tags} />
      <div className="row wrap"><button className="btn ghost sm" onClick={onOpen}>查看文章</button><details className="batch-edit"><summary>修改资料</summary><div className="stack">
        <label className="field"><span>作者/账号</span><input className="input" value={author} maxLength={120} onChange={e => setAuthor(e.target.value)} /></label><label className="field"><span>内容类型</span><select className="select" value={kind} onChange={e => setKind(e.target.value)}>{['未分类', '教程', '案例', '观点', '故事', '清单', '其他'].map(k => <option key={k}>{k}</option>)}</select></label><label className="field"><span>标签</span><input className="input" value={tags} maxLength={1000} onChange={e => setTags(e.target.value)} /></label><button className="btn sm" disabled={saving} onClick={save}>{saving ? '保存中…' : '保存资料'}</button>{error && <span className="small" role="alert">{error}</span>}
      </div></details></div></>}
    {item.error && <p className="small" role="alert">{item.error}</p>}{item.state === 'failed' && <button className="btn sm" disabled={retryBusy} onClick={onRetry}>重试这条链接</button>}{item.warning && <p className="small muted">{item.warning}</p>}
  </section>
}
