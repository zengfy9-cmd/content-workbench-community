import { useState } from 'react'
import { api, type VideoOverview } from '../api'
import { go } from '../App'
import { ago, Empty, usePoll, useToast } from '../ui'
import { fmtDur } from './Video'
import { XiloHandoff } from '../XiloHandoff'
import * as Ic from '../icons'

const started = (r: VideoOverview) => r.scenes > 0 || !!(r.planned_at || r.status || r.video || r.error)

function where(r: VideoOverview): { text: string; tone?: 'accent' | 'ok' | 'warn' | 'danger' } {
  if (r.status === 'planning') return { text: '正在写脚本', tone: 'accent' }
  if (r.status === 'rendering') return { text: `出片中 ${r.pct || 0}%`, tone: 'accent' }
  if (r.error) return { text: '上次失败了', tone: 'danger' }
  if (r.video && r.edited_at && r.edited_at > r.video.rendered_at) return { text: '改过，还没重新出片', tone: 'warn' }
  if (r.video) return { text: '已出片', tone: 'ok' }
  return { text: '还没出片' }
}

/** Keep existing projects accessible while new production starts from a draft handoff. */
export function Videos() {
  const toast = useToast()
  const [rows, setRows] = useState<VideoOverview[] | null>(null)
  usePoll(() => { api.get<VideoOverview[]>('/api/videos').then(setRows).catch(e => toast((e as Error).message, 'error')) }, 5000)

  const made = (rows || []).filter(started)

  return (
    <div className="page narrow">
      <div className="page-head">
        <div>
          <h1>旧视频项目</h1>
          <p>查看以前的长文转视频项目。新视频从稿件或成稿交给本机 Codex。</p>
        </div>
      </div>

      <div className="card card-pad"><XiloHandoff scope="video"/></div>

      {rows === null ? (
        <div className="card stack card-pad">{[0, 1, 2].map(i => <div key={i} className="skeleton" style={{ height: 52 }} />)}</div>
      ) : !made.length ? (
        <div className="card"><Empty icon={<Ic.Film />} title="没有旧视频项目"><a href="#/final-drafts">打开成稿箱</a></Empty></div>
      ) : null}

      {made.length > 0 && (
        <section className="vlist">
          <h2 className="section-title">做过的</h2>
          <div className="card list-rows">
            {made.map(r => {
              const w = where(r)
              return (
                <button key={r.id} className="list-row" onClick={() => go(`videos/${r.id}`)}>
                  <span className="vlist-thumb">
                    {r.video ? <video src={`/api/drafts/${r.id}/video.mp4?v=${Math.round(r.video.rendered_at)}#t=1`} muted playsInline preload="metadata" />
                      : <Ic.Film size={16} />}
                  </span>
                  <div className="grow">
                    <div className="t">{r.title || '（还没有标题）'}</div>
                    <div className="sub">
                      {r.status === 'rendering' ? r.stage || '排队中'
                        : r.error && !r.status ? r.error
                          : `${r.scenes} 个镜头 · ${r.video ? fmtDur(r.video.duration) : `约 ${fmtDur(r.seconds)}`}`
                            + (r.video ? ` · ${ago(r.video.rendered_at)} 出片` : '')}
                    </div>
                  </div>
                  <span className={'pill' + (w.tone ? ` ${w.tone}` : '')}>
                    {(r.status === 'planning' || r.status === 'rendering') && <Ic.Loader size={12} />} {w.text}
                  </span>
                </button>
              )
            })}
          </div>
        </section>
      )}

    </div>
  )
}
