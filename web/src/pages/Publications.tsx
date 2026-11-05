import { PublicationReview } from './PublicationReview'
import { PlatformFilter, PLATFORMS, type ContentPlatform } from '../platforms'
import { useEffect, useState } from 'react'
import { api, type Publication } from '../api'
import { go } from '../App'
import { ago, Empty, PlatformIcon, useToast } from '../ui'
import * as Ic from '../icons'

export function Publications() {
  const [stage,setStage]=useState('all')
  const [review,setReview]=useState<number|null>(null)
  const [platform,setPlatform]=useState<ContentPlatform|'all'>('all')
  const toast = useToast()
  const [items, setItems] = useState<Publication[] | null>(null)
  useEffect(() => { api.get<Publication[]>('/api/publications').then(setItems).catch(e => toast(e.message, 'error')) }, [toast])

  return (
    <div className="page narrow">{review!==null&&<PublicationReview id={review} onClose={()=>setReview(null)}/>}
      <div className="page-head">
        <div>
          <h1>发布记录与复盘</h1>
          <p>区分复制、导出、已进草稿箱与确认发布；导出不等于发布。</p>
        </div>
      </div>
      <div className="row" style={{marginBottom:16}}><PlatformFilter value={platform} onChange={setPlatform}/><select className="select" aria-label="发布阶段" value={stage} onChange={e=>setStage(e.target.value)}>{[['all','全部记录'],['pending','待发布 · 已进草稿箱'],['published','已发布'],['export','复制与导出'],['failed','失败或待核对']].map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></div>
      {items === null ? (
        <div className="card stack card-pad">{[0, 1].map(i => <div key={i} className="skeleton" style={{ height: 42 }} />)}</div>
      ) : items.length === 0 ? (
        <div className="card"><Empty icon={<Ic.Send />} title="暂无发布记录">稿件复制、导出、推送草稿箱及确认发布后，会在这里留下对应记录。</Empty></div>
      ) : (
        <div className="card list-rows">
          {items.filter(p=>(platform==='all'||p.platform===platform) && (stage==='all'||stage==='export'&&['copied','exported'].includes(p.state)||stage==='pending'&&p.state==='sent'&&p.platform==='wechat'||stage==='published'&&(p.state==='manual_published'||p.state==='sent'&&p.platform==='x')||stage==='failed'&&!['copied','exported','sent','manual_published'].includes(p.state))).map(p => {
            const ok = ['sent','manual_published','copied','exported'].includes(p.state)
            return (
              <div key={p.id} className="list-row">
                <PlatformIcon platform={p.platform} />
                <div className="grow">
                  <div className="t"><button className="btn ghost sm" onClick={() => p.draft_id && go(`drafts/${p.draft_id}`)}>{p.title || '（稿子已删除）'}</button></div>
                  <div className="sub">{PLATFORMS[p.platform as ContentPlatform] || p.platform}</div>
                </div>
                <span className={'pill ' + (ok ? 'ok' : p.state === 'unknown' ? 'warn' : 'danger')}>
                  {p.state==='copied'?'已复制':p.state==='exported'?'已导出':p.state==='manual_published'?'已确认发布':ok ? (p.platform === 'x' ? '已发出' : '已进草稿箱') : p.state === 'unknown' ? '结果不确定' : '失败'}
                </span>
                {p.remote_id && (p.state==='manual_published'||p.platform==='x') && (
                  <a className="btn ghost icon sm" aria-label="查看发布内容" href={p.state==='manual_published'?p.remote_id:`https://x.com/i/status/${p.remote_id.split(',')[0]}`} target="_blank" rel="noreferrer"
                    onClick={e => e.stopPropagation()}><Ic.External size={14} /></a>
                )}
                <button className="btn sm" onClick={()=>setReview(p.id)}>复盘</button><span className="when">{ago(p.created_at)}</span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
