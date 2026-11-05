import { useEffect, useState } from 'react'
import { api, type ContentGroup } from './api'
import { Dialog, useToast } from './ui'
import * as Ic from './icons'

export function GroupFilter({ groups, value, onChange, onCreated }: {
  groups: ContentGroup[]; value: string; onChange: (value: string) => void; onCreated: () => void
}) {
  const [creating, setCreating] = useState(false)
  return <>
    <label className="group-filter">
      <span>分组</span>
      <select className="input" aria-label="按分组筛选" value={value} onChange={e => onChange(e.target.value)}>
        <option value="all">全部分组</option>
        <option value="none">未分组</option>
        {groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
      </select>
    </label>
    <button className="btn sm" onClick={() => setCreating(true)}><Ic.Plus size={14} /> 新建分组</button>
    {creating && <GroupDialog createOnly onClose={() => setCreating(false)} onSave={async () => { onCreated() }} />}
  </>
}

export function ContentActions({ kind, id, title, groupId, onChange, disabled = false }: {
  kind: 'ideas' | 'drafts'; id: number; title: string; groupId: number | null
  onChange: () => void; disabled?: boolean
}) {
  const toast = useToast()
  const [dialog, setDialog] = useState<'group' | 'delete' | null>(null)
  const [deleting, setDeleting] = useState(false)
  const label = kind === 'ideas' ? '选题' : '稿件'
  const displayTitle = title || '（还没有标题）'

  async function remove() {
    if (deleting) return
    setDeleting(true)
    try {
      await api.del(`/api/${kind}/${id}`)
      setDialog(null)
      onChange()
      window.dispatchEvent(new Event('cw-refresh-status'))
      toast(`${label}已删除`)
    } catch (e) { toast((e as Error).message, 'error') }
    finally { setDeleting(false) }
  }

  return <>
    <span className="content-actions">
      <button className="btn sm" aria-label={`分组${label}：${displayTitle}`} onClick={() => setDialog('group')}>分组</button>
      <button className="btn sm danger" aria-label={`删除${label}：${displayTitle}`} disabled={disabled}
        title={disabled ? '请等当前任务结束后再删除' : `删除${label}`} onClick={() => setDialog('delete')}>
        <Ic.Trash size={13} /> 删除
      </button>
    </span>
    {dialog === 'group' && <GroupDialog currentId={groupId} onClose={() => setDialog(null)} onSave={async group_id => {
      await api.patch(`/api/${kind}/${id}`, { group_id })
      onChange()
    }} />}
    <Dialog open={dialog === 'delete'} title={`删除这条${label}？`} onClose={() => { if (!deleting) setDialog(null) }}
      footer={<>
        <button className="btn" disabled={deleting} onClick={() => setDialog(null)}>取消</button>
        <button className="btn danger" disabled={deleting} onClick={remove}>{deleting ? '删除中…' : '确认删除'}</button>
      </>}>
      <p className="delete-content-title">{displayTitle}</p>
      <p className="muted">{kind === 'ideas'
        ? '只删除这条选题，保留来源素材和已经写出的稿件。删除后无法撤销。'
        : '删除本机这篇稿件、历史版本、批注和配图，无法撤销。已经存到平台或飞书的内容需要在对应平台管理。'}</p>
    </Dialog>
  </>
}

function GroupDialog({ currentId, createOnly = false, onClose, onSave }: {
  currentId?: number | null; createOnly?: boolean; onClose: () => void
  onSave: (groupId: number | null) => Promise<void>
}) {
  const toast = useToast()
  const [groups, setGroups] = useState<ContentGroup[]>([])
  const [selected, setSelected] = useState(createOnly ? 'new' : currentId == null ? '' : String(currentId))
  const [name, setName] = useState('')
  const [loading, setLoading] = useState(!createOnly)
  const [loadFailed, setLoadFailed] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (createOnly) return
    let ignore = false
    api.get<ContentGroup[]>('/api/content-groups').then(items => { if (!ignore) setGroups(items) })
      .catch(e => { if (!ignore) { setError(e.message); setLoadFailed(true) } })
      .finally(() => { if (!ignore) setLoading(false) })
    return () => { ignore = true }
  }, [createOnly])

  async function save() {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      let groupId = selected ? Number(selected) : null
      if (selected === 'new') {
        const group = await api.post<ContentGroup>('/api/content-groups', { name: name.trim() })
        groupId = group.id
      }
      await onSave(groupId)
      toast(createOnly ? '分组已创建' : groupId === null ? '已移到未分组' : '分组已保存')
      onClose()
    } catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }

  return <Dialog open title={createOnly ? '新建分组' : '设置分组'} onClose={() => { if (!busy) onClose() }}
    footer={<>
      <button className="btn" disabled={busy} onClick={onClose}>取消</button>
      <button className="btn primary" disabled={loading || loadFailed || busy || (selected === 'new' && !name.trim())}
        onClick={save}>{busy ? '保存中…' : createOnly ? '创建分组' : '保存分组'}</button>
    </>}>
    <div className="stack">
      {!createOnly && <label className="field"><span>选择分组</span>
        <select className="input" value={selected} disabled={loading || busy} onChange={e => setSelected(e.target.value)}>
          <option value="">未分组</option>
          {groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
          <option value="new">＋ 新建分组…</option>
        </select>
      </label>}
      {selected === 'new' && <label className="field"><span>分组名称</span>
        <input className="input" autoFocus maxLength={40} value={name} disabled={busy}
          onChange={e => setName(e.target.value)} placeholder="例如：示例项目、示例产品、个人分享" />
      </label>}
      <p className="small muted">选题和稿件共用分组名称。每条内容可单独调整；从选题起稿时沿用该选题的分组。</p>
      {loading && <span className="small muted">正在读取分组…</span>}
      {error && <div className="err" role="alert">{error}</div>}
    </div>
  </Dialog>
}
