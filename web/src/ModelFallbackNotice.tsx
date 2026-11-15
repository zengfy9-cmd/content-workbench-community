import { useEffect, useState } from 'react'
import { api } from './api'
import { Dialog, useToast } from './ui'
import './ModelFallbackNotice.css'

type Request = { id: number; title: string; reason: string; source_backend: string; source_model: string; target_model: string; task: string; href: string }
type Pending = { requests: Request[]; deepseek_ready: boolean }

export function ModelFallbackNotice() {
  const [data, setData] = useState<Pending>({ requests: [], deepseek_ready: false })
  const [selected, setSelected] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const toast = useToast()
  useEffect(() => {
    let alive = true
    const load = () => api.get<Pending>('/api/model-fallbacks').then(value => { if (alive) setData(value) }).catch(() => {})
    void load()
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void load() }, 5000)
    return () => { alive = false; window.clearInterval(timer) }
  }, [])
  const current = data.requests.find(request => request.id === selected)
  async function decide(action: 'approve' | 'decline') {
    if (!current) return
    setBusy(true); setError('')
    try {
      await api.post(`/api/model-fallbacks/${current.id}/${action}`, action === 'approve' ? { confirm: true } : {})
      setSelected(null)
      setData(value => ({ ...value, requests: value.requests.filter(request => request.id !== current.id) }))
      toast(action === 'approve' ? '已授权 DeepSeek 接替本项，原文和文件继续保留' : '本项未使用 DeepSeek，可保留原件稍后再试')
      window.dispatchEvent(new Event('cw-refresh-status'))
    } catch (failure) { setError((failure as Error).message) } finally { setBusy(false) }
  }
  return <>
    {data.requests.length > 0 && <aside className="model-fallback-notice" aria-label="模型接替授权" role="status">
      <strong>{data.requests.length} 项任务等待模型授权</strong>
      <span className="small muted">你允许后，才会使用 DeepSeek API。</span>
      <button className="btn sm" onClick={() => { setSelected(data.requests[0].id); setError('') }}>查看并决定</button>
    </aside>}
    <Dialog open={!!current} title="允许 DeepSeek 接替本项？" onClose={() => { if (!busy) setSelected(null) }} footer={<div className="row wrap">
      <button className="btn" disabled={busy} onClick={() => void decide('decline')}>暂不使用 DeepSeek</button>
      <button className="btn primary" disabled={busy || !data.deepseek_ready} onClick={() => void decide('approve')}>{busy ? '正在提交…' : '允许本项使用 DeepSeek API'}</button>
    </div>}>
      {current && <div className="stack">
        <strong>{current.title}</strong>
        <p>待继续：{current.task}。接替模型：{current.target_model}。</p>
        <p className="small muted">原调用：{current.source_backend === 'codex-cli' ? '本机 Codex' : current.source_backend === 'disabled' ? '当前文字 AI 已暂停' : '原模型接口'}{current.source_model ? ' · ' + current.source_model : ''}</p>
        <p className="callout small">{current.reason}</p>
        <p>同意后，会将本项已取得的正文发给 DeepSeek 官方 API，继续未完成的文字整理或研究。内容可能分段处理，按实际 API 调用计费。</p>
        <p className="small muted">本次授权只覆盖这一项及其后续文字步骤，其他素材仍需单独允许。已取得的原文件、转写和原文保留。</p>
        <a href={current.href} onClick={() => setSelected(null)}>查看原任务</a>
        {!data.deepseek_ready && <p role="alert">请先到<a href="#/settings" onClick={() => setSelected(null)}>设置</a>填写 DeepSeek 官方 Key。</p>}
        {data.requests.length > 1 && <label className="field"><span>查看其他待授权项</span><select className="select" value={current.id} onChange={e => { setSelected(Number(e.target.value)); setError('') }}>{data.requests.map(request => <option key={request.id} value={request.id}>{request.title}</option>)}</select></label>}
        {error && <p role="alert" className="callout">{error}</p>}
      </div>}
    </Dialog>
  </>
}
