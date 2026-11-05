import { useState, type FormEvent } from 'react'
import { api } from './api'
import { useToast } from './ui'

type Route = { model: string; label: string }
type Check = { ok: boolean; detail: string }

export function DeepSeekSettings({ configured, routes, onSaved }: {
  configured: boolean; routes: Record<string, Route>; onSaved: () => void
}) {
  const toast = useToast()
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState('')
  const [check, setCheck] = useState<Check | null>(null)

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!key.trim() || busy) return
    setBusy('save'); setCheck(null)
    try {
      await api.put('/api/settings/secrets', { deepseek_api_key: key.trim() })
      setKey(''); onSaved(); toast('DeepSeek 密钥已保存，请检查连接')
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy('') }
  }

  async function connect() {
    setBusy('check'); setCheck(null)
    try { setCheck(await api.post<Check>('/api/settings/deepseek/check', {})) }
    catch (e) { setCheck({ ok: false, detail: (e as Error).message }) } finally { setBusy('') }
  }

  return <div className="deepseek-settings stack">
    <dl className="model-routes" aria-label="DeepSeek 模型分工">{Object.entries(routes).map(([task, route]) => (
      <div key={task}><dt>{route.label}</dt><dd><span className={`model-label ${route.model === 'deepseek-flash' ? 'flash' : 'pro'}`}>{route.model === 'deepseek-flash' ? 'Flash' : 'Pro'}</span><code>{route.model}</code></dd></div>
    ))}</dl>
    <form className="stack" onSubmit={save}>
      <label className="field"><span>DeepSeek 官方 API Key <span className="muted">{configured ? '已保存' : '待填写'}</span></span>
        <input className="input" type="password" value={key} autoComplete="off" spellCheck={false} autoCapitalize="none" maxLength={512}
          placeholder={configured ? '填写新密钥可替换，原密钥不回显' : '填写 DeepSeek 官方平台的 API Key'}
          onChange={e => { setKey(e.target.value); setCheck(null) }} /></label>
      <div className="row wrap"><button className="btn" type="submit" disabled={!key.trim() || !!busy}>{busy === 'save' ? '保存中…' : '保存密钥'}</button>
        <button className="btn" type="button" disabled={!configured || !!key.trim() || !!busy} onClick={connect}>{busy === 'check' ? '检查中…' : '检查连接'}</button>
        <a className="small" href="https://platform.deepseek.com/api_keys" target="_blank" rel="noopener noreferrer">获取官方密钥</a></div>
    </form>
    <p className="small muted">使用 DeepSeek 官方接口。检查连接仅验证密钥和可用型号；分类、拆解和写作按使用量扣费。填写新密钥后请先保存。</p>
    {check && <p className={`callout small ${check.ok ? 'model-check-ok' : ''}`} role={check.ok ? 'status' : 'alert'}>{check.detail}</p>}
  </div>
}
