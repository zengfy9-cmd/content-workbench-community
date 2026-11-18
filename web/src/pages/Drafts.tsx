import { PlatformFilter, PLATFORMS, type ContentPlatform } from '../platforms'
import { useEffect, useMemo, useState } from 'react'
import { api, type ContentGroup, type Draft } from '../api'
import { ContentActions, GroupFilter } from '../ContentOrganization'
import { go } from '../App'
import { ago, Empty, PlatformIcon, usePoll, useToast } from '../ui'
import * as Ic from '../icons'
import { ScheduleDraft, type Plan } from './WritingPlans'
import { XiloHandoff } from '../XiloHandoff'
import { markdownToHtml } from '../RichEditor'
import { ArticlePreview } from '../ArticlePreview'
import { copyPromptText } from '../promptBlock'

type FinalRow = { draft_id: number; platform: ContentPlatform; title: string; preview: string; saved_at: number }
type FinalCopy = Omit<FinalRow, 'preview'> & {
  body: string; has_original: boolean; version: number; versions: {version:number;saved_at:number}[]; image_ids:number[]
}
const timeOf = (seconds:number) => new Date(seconds*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})

function snapshotHtml(copy:FinalCopy) {
  const layout=document.createElement('template')
  layout.innerHTML=markdownToHtml(copy.body,true)
  const pins=new Set(copy.image_ids)
  for(const img of layout.content.querySelectorAll('img')){
    const match=/^\/api\/images\/(\d+)$/.exec(img.getAttribute('src')||'')
    if(match&&pins.has(Number(match[1]))){
      img.src=`/api/final-drafts/${copy.draft_id}/images/${match[1]}`
      img.loading='lazy'
    }else{
      const label=document.createElement('span')
      label.className='final-image-missing'
      label.textContent='确认版本未保存此图片'+(img.alt? `：${img.alt}`:'')
      img.replaceWith(label)
    }
  }
  return layout.innerHTML
}

function FinalReader({id, version}:{id:number;version?:string|null}) {
  const toast=useToast()
  const url=`/api/final-drafts/${id}${version?'?'+new URLSearchParams({version}):''}`
  const [result,setResult]=useState<{url:string;copy?:FinalCopy;error?:string}|null>(null)
  const current=result?.url===url?result:null
  const copy=current?.copy
  useEffect(()=>{
    let live=true
    api.get<FinalCopy>(url).then(copy=>{if(live)setResult({url,copy})})
      .catch(e=>{if(live)setResult({url,error:e.message})})
    return()=>{live=false}
  },[url])
  const html=useMemo(()=>copy?.body?snapshotHtml(copy):'',[copy])
  return <div className="page final-reader">
    <div className="final-reader-nav row wrap"><a className="btn sm" href="#/final-drafts">← 返回成稿箱</a>
      {copy?.has_original&&<a className="btn sm" href={`#/drafts/${id}`}>打开原稿</a>}
      {copy?.body&&<button className="btn primary sm" onClick={async()=>{
        try{await copyPromptText([copy.title,copy.body].filter(Boolean).join('\n\n'));toast('已复制全文')}
        catch{toast('未能复制，可选中文字后手动复制','error')}
      }}>复制全文</button>}
    </div>
    {current?.error?<p role="alert" className="callout">{current.error}</p>:!copy?<p role="status">正在读取确认版本…</p>:
      <article className="card final-reader-article">
        <div className="row wrap"><span className="pill accent">{PLATFORMS[copy.platform]||copy.platform}</span>
          <span className="pill ok">已确认 · V{copy.version}</span>
          <time className="small muted" dateTime={new Date(copy.saved_at*1000).toISOString()}>{timeOf(copy.saved_at)}（北京时间）</time>
        </div>
        <h1>{copy.title||'（还没有标题）'}</h1>
        <details className="final-reader-history"><summary>确认版本信息 · {copy.versions.length} 个版本</summary>
          <ul>{copy.versions.map(v=><li key={v.version}><a aria-current={v.version===copy.version?'true':undefined}
            href={`#/final-drafts/${id}?version=${v.version}`}>V{v.version} · {timeOf(v.saved_at)}{v.version===copy.version?'（当前查看）':''}</a></li>)}</ul>
        </details>
        {copy.body.trim()?<div className="final-reader-body"><ArticlePreview html={html}/></div>:
          <p role="status" className="callout">这次确认没有可恢复的正文快照。可打开原稿核对，但原稿不是历史成稿。</p>}
        {copy.version===copy.versions[0]?.version&&<XiloHandoff scope="final" draftId={id} platform={copy.platform}/>}
      </article>}
  </div>
}

export function FinalCopies({ id, version }: { id?: number; version?:string|null }) {
  const [rows, setRows] = useState<FinalRow[] | null>(null)
  const [platform, setPlatform] = useState<ContentPlatform | 'all'>('all')
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  useEffect(()=>{
    if(id)return
    let live=true
    const load=()=>api.get<FinalRow[]>(`/api/final-drafts?${new URLSearchParams({q:query.trim(),platform:platform==='all'?'':platform})}`)
      .then(r=>{if(live){setRows(r);setError('')}}).catch(e=>{if(live)setError(e.message)})
    void load()
    const timer=window.setInterval(()=>{if(document.visibilityState==='visible')void load()},6000)
    return()=>{live=false;window.clearInterval(timer)}
  },[id,platform,query])
  if(id)return <FinalReader key={id} id={id} version={version}/>
  return <div className="page narrow">
    <div className="page-head"><div><h1>成稿箱</h1><p>这里是已确认的文案。后续改动原稿，须再次确认才会更新这里的版本。</p></div><div className="actions"><a className="btn" href="#/published">发布记录</a><a className="btn" href="#/drafts">返回稿件</a></div></div>
    <div className="row wrap" style={{marginBottom:14}}>
      <label>平台 <select aria-label="筛选成稿平台" className="select" value={platform} onChange={e=>setPlatform(e.target.value as ContentPlatform|'all')}>
        <option value="all">全部</option>{Object.entries(PLATFORMS).map(([key,label])=><option key={key} value={key}>{label}</option>)}
      </select></label>
      <input className="input grow" aria-label="搜索成稿标题" placeholder="搜索成稿标题" maxLength={300} value={query} onChange={e => setQuery(e.target.value)} />
    </div>
    {error && <p role="alert" className="callout">{error}</p>}
    {rows === null ? <p className="muted">正在读取成稿…</p> : rows.length === 0 ? <div className="card"><Empty icon={<Ic.Doc />} title={query.trim()?'没有匹配的成稿':'还没有成稿'}>在写完的稿件里点「确认正文，存入成稿箱」。</Empty></div>
      : <div className="card list-rows">{rows.map(row => <a key={row.draft_id} aria-label={row.title||'（还没有标题）'}
          href={`#/final-drafts/${row.draft_id}`} className="list-row row wrap final-list-link">
          <span className="pill">{PLATFORMS[row.platform]||row.platform}</span>
          <div className="grow"><strong>{row.title || '（还没有标题）'}</strong><div className="sub">{row.preview}</div></div>
          <span className="when">{ago(row.saved_at)}</span>
        </a>)}</div>}
  </div>
}

export function Drafts() {
  const toast = useToast()
  const [drafts, setDrafts] = useState<Draft[] | null>(null)
  const [groups, setGroups] = useState<ContentGroup[]>([])
  const [plans, setPlans] = useState<Plan[]>([])
  const [platform,setPlatform]=useState<ContentPlatform|'all'>('all')
  const [group, setGroup] = useState('all')
  const [filter, setFilter] = useState<'open' | 'published' | 'all'>('open')
  const load = () => Promise.all([api.get<Draft[]>('/api/drafts'), api.get<ContentGroup[]>('/api/content-groups'), api.get<Plan[]>('/api/plans')])
    .then(([items, list, p]) => { setDrafts(items); setGroups(list); setPlans(p) }).catch(e => toast(e.message, 'error'))
  usePoll(load, 6000)

  async function blank(platform: ContentPlatform) {
    try {
      const r = await api.post<{ id: number }>('/api/drafts', { platform, title: '', body: '' })
      go(`drafts/${r.id}`)
    } catch (e) { toast((e as Error).message, 'error') }
  }

  const shown = (drafts || []).filter(d =>
    (platform==='all' || d.platform===platform) &&
    (filter === 'all' || (filter === 'published' ? d.status === 'published' : d.status !== 'published')) &&
    (group === 'all' || (group === 'none' ? d.group_id == null : String(d.group_id) === group)))

  return (
    <div className="page narrow">
      <div className="page-head">
        <div>
          <h1>稿件</h1>
          <p>按目标平台整理稿件，各版独立编辑。审核后复制、导出或使用已接通的发布渠道。</p>
        </div>
        <div className="actions">
          <button className="btn" onClick={() => go('illustrations')}>正文配图</button>
          <select className="select" aria-label="新建平台稿" value="" onChange={e=>{if(e.target.value)void blank(e.target.value as ContentPlatform)}}><option value="" disabled>＋ 新建稿件</option>{Object.entries(PLATFORMS).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select>

        </div>
      </div>

      <div className="row" style={{marginBottom:14}}><PlatformFilter value={platform} onChange={setPlatform}/></div>
      <div className="row" style={{ marginBottom: 14 }}>
        <div className="segmented" role="group" aria-label="筛选">
          {([['open', '在改'], ['published', '已发出'], ['all', '全部']] as const).map(([k, l]) => (
            <button key={k} aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}</button>
          ))}
        </div>
        <GroupFilter groups={groups} value={group} onChange={setGroup} onCreated={load} />
      </div>

      {drafts === null ? (
        <div className="card stack card-pad">{[0, 1, 2].map(i => <div key={i} className="skeleton" style={{ height: 42 }} />)}</div>
      ) : shown.length === 0 ? (
        <div className="card"><Empty icon={<Ic.Doc />} title={group === 'all' ? '没有稿子' : '这个分组还没有稿件'}>
          {group === 'all' ? '在“选题”里挑一个，或在右上角新建一篇空稿。' : '切换到“全部分组”，点击稿件右侧“分组”把稿件放进来。'}
        </Empty></div>
      ) : (
        <div className="card list-rows">
          {shown.map(d => (
            <div key={d.id} className="list-row draft-list-row">
              <a className="draft-open" href={`#/drafts/${d.id}`}>
              <PlatformIcon platform={d.platform} />
              <div className="grow">
                <div className="t">{d.title || '（还没有标题）'}</div>
                <div className="sub">{d.status === 'generating' ? '正在按你的文风起草…' : d.status === 'failed' ? `起草失败：${d.error || ''}` : d.preview || '空稿'}</div>
                {d.group_name && <span className="pill content-group" title={d.group_name}>{d.group_name}</span>}
              </div>
              {d.status === 'generating' && <span className="pill accent"><Ic.Loader size={12} /> 起草中</span>}
              {d.status === 'revising' && <span className="pill accent"><Ic.Loader size={12} /> AI 按批注改稿中</span>}
              {d.status === 'failed' && <span className="pill danger">失败</span>}
              {d.status === 'published' && <span className="pill ok"><Ic.Check size={12} /> 已发出</span>}
              <span className="when">{ago(d.updated_at)}</span>
              </a>
              {d.status !== 'published' && <ScheduleDraft draft={d} plans={plans} onSaved={load} />}
              <ContentActions kind="drafts" id={d.id} title={d.title} groupId={d.group_id} onChange={load}
                disabled={d.status === 'generating' || d.status === 'revising'} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
