import { PLATFORMS, type ContentPlatform } from '../platforms'
import { useMemo, useState } from 'react'
import { api, type ContentGroup, type Idea } from '../api'
import { ContentActions, GroupFilter } from '../ContentOrganization'
import { ago, Empty, usePoll, useToast } from '../ui'
import * as Ic from '../icons'
import './IdeaLabels.css'
import { IdeaBriefDialog } from '../IdeaBrief'
import { READINESS, BRIEF_FIELDS } from '../ideaBriefModel'
import { WritingPreparation } from '../WritingPreparation'

const FILTERS: [string, string][] = [['new', '待挑'], ['drafted', '已起草'], ['dropped', '不要了'], ['all', '全部']]

export function Ideas() {
  const toast = useToast()
  const [filter, setFilter] = useState('new')
  const [ideas, setIdeas] = useState<Idea[] | null>(null)
  const [groups, setGroups] = useState<ContentGroup[]>([])
  const [group, setGroup] = useState('all')
  const [adding, setAdding] = useState(false)
  const [q, setQ] = useState('')
  const [platform, setPlatform] = useState<string>('all')
  const [limit, setLimit] = useState(30)

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return (ideas || []).filter(i =>
      (filter === 'all' || i.status === filter) &&
      (group === 'all' || (group === 'none' ? i.group_id == null : String(i.group_id) === group)) &&
      (platform === 'all' || i.platforms.includes(platform) || (platform==='推特' && i.platforms.includes('X'))) &&
      (!needle || `${i.title} ${i.angle} ${i.audience||''} ${i.problem||''} ${i.thesis||''} ${i.available_materials||''} ${i.missing_materials||''} ${i.material_title || ''}`.toLowerCase().includes(needle)))
  }, [ideas, q, platform, filter, group])

  const load = () => Promise.all([api.get<Idea[]>('/api/ideas?status=all'), api.get<ContentGroup[]>('/api/content-groups')])
    .then(([items, list]) => { setIdeas(items); setGroups(list) }).catch(e => toast(e.message, 'error'))
  usePoll(load, 8000)

  return (
    <div className="page narrow">
      <div className="page-head">
        <div>
          <h1>选题</h1>
          <p>保留选题理由与原始来源，按目标平台挑选，再写自己的内容。</p>
        </div>
        <div className="actions"><button className="btn" onClick={() => setAdding(true)}><Ic.Plus size={15} /> 自己加一个</button></div>
      </div>

      <div className="row" style={{ marginBottom: 14 }}>
        <div className="segmented" role="group" aria-label="状态">
          {FILTERS.map(([k, l]) => <button key={k} aria-pressed={filter === k} onClick={() => { setFilter(k); setLimit(30) }}>{l}</button>)}
        </div>
        <div className="segmented" role="group" aria-label="平台">
          {(['all', '公众号', '小红书', '抖音', '推特'] as const).map(k => <button key={k} aria-pressed={platform === k} onClick={() => { setPlatform(k); setLimit(30) }}>{k === 'all' ? '全部平台' : k}</button>)}
        </div>
        <input className="input grow" style={{ minHeight: 34, height: 34, minWidth: 160 }} placeholder="搜选题、角度、来源" value={q} onChange={e => { setQ(e.target.value); setLimit(30) }} />
      </div>
      <div className="row" style={{ marginBottom: 14 }}>
        <GroupFilter groups={groups} value={group} onChange={value => { setGroup(value); setLimit(30) }} onCreated={load} />
      </div>
      {ideas && ideas.length > 0 && <div className="small muted" style={{ margin: '-4px 0 10px 2px' }}>{shown.length} 个选题符合条件</div>}

      {ideas === null ? <SkeletonCards /> : ideas.length === 0 ? (
        <div className="card"><Empty icon={<Ic.Bulb />} title={filter === 'new' ? '没有待挑的选题' : '这里是空的'}>
          记一笔或者导入一段录音，几分钟后这里就会出现新选题。
        </Empty></div>
      ) : shown.length === 0 ? (
        <div className="card"><Empty icon={<Ic.Bulb />} title="没有符合条件的选题">试试切换状态、分组，或清空搜索。</Empty></div>
      ) : (
        <>
          <div className="idea-card-list">{shown.slice(0, limit).map(i => <IdeaCard key={i.id} idea={i} onChange={load} />)}</div>
          {shown.length > limit && (
            <div className="row" style={{ justifyContent: 'center', marginTop: 14 }}>
              <button className="btn" onClick={() => setLimit(l => l + 30)}>再看 30 个（还有 {shown.length - limit} 个）</button>
            </div>
          )}
        </>
      )}

      <>{adding && <IdeaBriefDialog onClose={()=>setAdding(false)} onSaved={load}/>}</>
    </div>
  )
}

export function IdeaCard({ idea, onChange, compact, hideSource }: { idea: Idea; onChange: () => void; compact?: boolean; hideSource?: boolean }) {
  const toast = useToast()
  const [showQuotes, setShowQuotes] = useState(false)
  const [preparing,setPreparing]=useState<ContentPlatform|null>(null)
  const [editing,setEditing]=useState(false)
  const busy=!!preparing
  const overlays=<>{editing&&<IdeaBriefDialog idea={idea} onClose={()=>setEditing(false)} onSaved={onChange}/>} {preparing&&<WritingPreparation idea={idea} platform={preparing} onClose={()=>{setPreparing(null);onChange()}}/>}</>

  async function patch(body: object, msg?: string) {
    try { await api.patch(`/api/ideas/${idea.id}`, body); if (msg) toast(msg); onChange(); window.dispatchEvent(new Event('cw-refresh-status')) }
    catch (e) { toast((e as Error).message, 'error') }
  }
  const writeButtons = <>{Object.entries(PLATFORMS).map(([key,label])=><button className="btn sm" key={key} disabled={busy} onClick={()=>setPreparing(key as ContentPlatform)}>写成{label}</button>)}</>

  if (compact) {
    return (
      <div className="idea-compact">{overlays}
        <div className="t">{idea.group_name && <IdeaLabel name={idea.group_name} />}{idea.starred ? <Ic.Star size={13} filled className="star on" /> : null} {idea.title}</div>
        {idea.angle && <div className="a">{idea.angle}</div>}
        <div className="row" style={{ marginTop: 4 }}>{writeButtons}</div>
      </div>
    )
  }

  return (
    <article className="idea">{overlays}
      <div className="idea-head">
        <h3 className="grow">{idea.group_name && <IdeaLabel name={idea.group_name} />}{idea.title}</h3>
        <button className={'btn ghost icon sm'} aria-label={idea.starred ? '取消星标' : '星标'} aria-pressed={!!idea.starred}
          onClick={() => patch({ starred: !idea.starred })}>
          <Ic.Star size={16} filled={!!idea.starred} className={'star' + (idea.starred ? ' on' : '')} />
        </button>
      </div>
      <div className="brief-status"><span className={'pill '+(idea.readiness||'unreviewed')}>{READINESS[idea.readiness||'unreviewed']||'待判断'}</span><button className="btn ghost sm" onClick={()=>setEditing(true)}>编辑选题卡</button></div>
      {idea.problem && <p className="angle"><strong>为什么写：</strong>{idea.problem.length>140?idea.problem.slice(0,140)+'…':idea.problem}</p>}
      {!idea.problem && idea.angle && <p className="small muted"><strong>原有说明：</strong>{idea.angle.length>140?idea.angle.slice(0,140)+'…':idea.angle}</p>}
      {idea.missing_materials && <p className="small muted"><strong>还缺：</strong>{idea.missing_materials.length>100?idea.missing_materials.slice(0,100)+'…':idea.missing_materials}</p>}
      <details className="brief-more"><summary>展开选题卡</summary><dl className="brief-summary">{BRIEF_FIELDS.map(([key,label])=><div key={key}><dt>{label}</dt><dd>{idea[key]||'尚未填写'}</dd></div>)}{idea.angle&&<div><dt>想怎么讲</dt><dd>{idea.angle}</dd></div>}</dl><div className="idea-image-grid">{idea.image_ids?.map(id=><a key={id} href={`/api/images/${id}`} target="_blank" rel="noreferrer"><img src={`/api/images/${id}`} alt="选题图片"/></a>)}</div><p className="small muted">关联的对标与素材，可在“写作准备”里选择和查看。</p></details>

      {idea.quotes.length > 0 && (
        <>
          <button className="disclosure" style={{ marginTop: 10 }} aria-expanded={showQuotes} onClick={() => setShowQuotes(v => !v)}>
            {showQuotes ? <Ic.Down size={14} /> : <Ic.Right size={14} />} {idea.media_id?'来源原话':'你的原话'} {idea.quotes.length} 句
          </button>
          {showQuotes && <ul className="quotes">{idea.quotes.map((q, n) => <li key={n}>{q}</li>)}</ul>}
        </>
      )}

      {idea.notes && (
        <div className="callout" style={{ marginTop: 12 }}><Ic.Alert size={15} /><span>{idea.notes}</span></div>
      )}

      <div className="actions">
        {idea.status === 'dropped' ? (
          <button className="btn sm" onClick={() => patch({ status: 'new' }, '已放回待挑')}><Ic.Undo size={14} /> 放回待挑</button>
        ) : writeButtons}
        <span className="spacer" />
        <div className="meta" style={{ marginTop: 0 }}>
          {idea.benchmark_id && !hideSource && <a className="src" href={`#/benchmarks/${idea.benchmark_id}`}><Ic.Doc size={13} /> 外部对标灵感 · 查看来源</a>}
          {idea.material_title && !hideSource && (
            <a className="src" href={idea.material_id ? `#/materials/${idea.material_id}` : undefined} title={idea.material_title}>
              <Ic.Inbox size={13} /> {idea.material_title}
            </a>
          )}
          <span>{ago(idea.created_at)}</span>
          {idea.status === 'drafted' && <span className="pill accent">已起草</span>}
        </div>
        {idea.status !== 'dropped' && (
          <button className="btn ghost sm" onClick={() => patch({ status: 'dropped' }, '已移到“不要了”')}>不要了</button>
        )}
        <ContentActions kind="ideas" id={idea.id} title={idea.title} groupId={idea.group_id} onChange={onChange} disabled={!!busy} />
      </div>
    </article>
  )
}

function IdeaLabel({ name }: { name: string }) {
  return <span className={'idea-origin-label' + (name === '热点' ? ' idea-origin-label-hot' : '')} aria-label={`选题分组：${name}`}>{name}</span>
}

export function SkeletonCards({ n = 3 }: { n?: number }) {
  return (
    <div className="card">
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="idea">
          <div className="skeleton" style={{ height: 18, width: '62%' }} />
          <div className="skeleton" style={{ height: 13, width: '92%', marginTop: 12 }} />
          <div className="skeleton" style={{ height: 13, width: '78%', marginTop: 8 }} />
        </div>
      ))}
    </div>
  )
}
