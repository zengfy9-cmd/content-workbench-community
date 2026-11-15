import { useEffect, useId, useState } from 'react'
import { api } from './api'

type Choice = { id: string; label: string; available: boolean; detail: string }

export function useWritingModel(active = true) {
  const [value, setValue] = useState('')
  const [options, setOptions] = useState<Choice[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    if (!active) return
    let alive = true
    api.get<{ default: string; models: Choice[] }>('/api/writing/models').then(data => {
      if (!alive) return
      setOptions(data.models); setValue(current => current || data.default); setError('')
    }).catch(e => { if (alive) setError(e.message) })
    return () => { alive = false }
  }, [active])
  return { value, setValue, options, error, loading: !options.length }
}

export function WritingModelPicker({ model, disabled }: { model: ReturnType<typeof useWritingModel>; disabled?: boolean }) {
  const id = useId()
  const selected = model.options.find(item => item.id === model.value)
  return <div className="stack">
    <label className="field" htmlFor={id}><span>本次起草模型</span>
      <select id={id} aria-label="本次起草模型" className="select" value={model.value} disabled={disabled || model.loading} onChange={e => model.setValue(e.target.value)}>
        {!model.options.length && <option value="">正在读取模型…</option>}
        {model.options.map(item => <option key={item.id} value={item.id}>{item.label}{item.available ? '' : ' · 需要配置'}</option>)}
      </select>
    </label>
    <p className="small muted">本次起草及自动文风修订都用所选模型，按对应 API 计费。失败后可换一个重试。</p>
    {selected && !selected.available && <p className="small" role="status">{selected.detail}</p>}
    {model.error && <p className="small" role="alert">{model.error}</p>}
  </div>
}
