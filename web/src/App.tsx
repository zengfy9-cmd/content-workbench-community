import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { api, type Status } from './api'
import { Login } from './Login'
import { Today } from './pages/Today'
import { Materials } from './pages/Materials'
import { Ideas } from './pages/Ideas'
import { Drafts } from './pages/Drafts'
import { Editor } from './pages/Editor'
import { Publications } from './pages/Publications'
import { SettingsPage } from './pages/Settings'
import { Habits } from './pages/Habits'
import { Videos } from './pages/Videos'
import { VideoStudio } from './pages/VideoStudio'
import { ToastProvider } from './ui'
import * as Ic from './icons'
import { PaneResizer } from './PaneResizer'
import { usePaneContainer, usePaneSize } from './usePaneSize'
import { Benchmarks } from './pages/Benchmarks'
import { MediaIntake } from './pages/MediaIntake'
import { ContentTips } from './pages/ContentTips'
import { CodexTasks } from './pages/CodexTasks'
import { ArticleImages } from './pages/ArticleImages'

type Route = { page: string; id?: number; query: URLSearchParams }

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#\/?/, '')
  const [path, qs] = raw.split('?')
  const [rawPage, id] = path.split('/')
  const page = rawPage === 'capture' ? 'materials' : rawPage
  return { page: page || 'today', id: id ? Number(id) : undefined, query: new URLSearchParams(qs || '') }
}

export function go(path: string) {
  window.location.hash = '/' + path
}

/** Jump to the composer on the Today page and focus it. */
export function startCapture() {
  if (parseHash().page !== 'today') go('today')
  window.setTimeout(() => window.dispatchEvent(new Event('cw-capture')), 60)
}

type NavItem = { key: string; label: string; icon: ReactNode; count?: number; hot?: boolean }

export default function App() {
  return <ToastProvider><Root /></ToastProvider>
}

function Root() {
  const [auth, setAuth] = useState<{ registered: boolean; logged_in: boolean } | null>(null)
  const [route, setRoute] = useState<Route>(parseHash())
  const [status, setStatus] = useState<Status | null>(null)
  const workspace = usePaneContainer(!!auth?.logged_in)
  const navigation = usePaneSize({ key: 'cw-pane-navigation-v1', width: workspace.width, initial: 236, min: 176, max: 360, remaining: 640 })

  const refreshAuth = useCallback(() => {
    api.get<{ registered: boolean; logged_in: boolean }>('/api/auth/state').then(setAuth)
      .catch(() => setAuth({ registered: true, logged_in: false }))
  }, [])

  useEffect(() => {
    refreshAuth()
    const onHash = () => { setRoute(parseHash()); window.scrollTo(0, 0) }
    window.addEventListener('hashchange', onHash)
    window.addEventListener('cw-logged-out', refreshAuth)
    return () => { window.removeEventListener('hashchange', onHash); window.removeEventListener('cw-logged-out', refreshAuth) }
  }, [refreshAuth])

  useEffect(() => {
    if (!auth?.logged_in) return
    let alive = true
    const tick = () => api.get<Status>('/api/status').then(s => alive && setStatus(s)).catch(() => {})
    tick()
    const t = window.setInterval(() => { if (document.visibilityState === 'visible') tick() }, 5000)
    window.addEventListener('cw-refresh-status', tick)
    return () => { alive = false; window.clearInterval(t); window.removeEventListener('cw-refresh-status', tick) }
  }, [auth?.logged_in])

  if (!auth) return null
  if (!auth.logged_in) return <Login registered={auth.registered} onDone={refreshAuth} />

  const nav: NavItem[] = [
    { key: 'today', label: '今天', icon: <Ic.Sun /> },
    { key: 'materials', label: '素材', icon: <Ic.Inbox />, count: status?.counts.materials },
    { key: 'benchmarks', label: '对标库', icon: <Ic.Doc /> },
    { key: 'ideas', label: '选题', icon: <Ic.Bulb />, count: status?.counts.ideas_new, hot: !!status?.counts.ideas_new },
    { key: 'drafts', label: '稿件', icon: <Ic.Doc />, count: status?.counts.drafts },
    { key: 'illustrations', label: '正文配图', icon: <Ic.Image /> },
    { key: 'tips', label: '内容灵感技巧', icon: <Ic.Bulb /> },
    { key: 'videos', label: '视频制作', icon: <Ic.Film /> },
    { key: 'published', label: '发布记录', icon: <Ic.Send /> },
    { key: 'habits', label: '改稿习惯', icon: <Ic.Sparkle /> },
  ]

  let body: ReactNode
  const fullBleed = ((route.page === 'drafts' || route.page === 'videos') && !!route.id) || route.page === 'materials' || route.page === 'benchmarks'
  const studyId = Number(route.query.get('study')) || undefined
  const targetDraft = Number(route.query.get('draft')) || undefined
  if (route.page === 'drafts' && route.id) body = <Editor key={route.id} id={route.id} status={status} studyId={studyId} studyKey={route.query.get('passage') || undefined} />
  else if (route.page === 'benchmarks') body = <Benchmarks id={route.id} targetDraft={targetDraft} initialAnalysis={Number(route.query.get('analysis'))||undefined} />
  else if (route.page === 'videos' && route.id) body = <VideoStudio id={route.id} />
  else if (route.page === 'videos') body = <Videos />
  else if (route.page === 'imports') body = <MediaIntake id={route.id} />
  else if (route.page === 'materials') body = <Materials openId={route.id} />
  else if (route.page === 'ideas') body = <Ideas />
  else if (route.page === 'drafts') body = <Drafts />
  else if (route.page === 'tips') body = <ContentTips />
  else if (route.page === 'illustrations' || route.page === 'codex') body = <ArticleImages draftId={targetDraft} />
  else if (route.page === 'codex-history') body = <CodexTasks />
  else if (route.page === 'published') body = <Publications />
  else if (route.page === 'habits') body = <Habits />
  else if (route.page === 'settings') body = <SettingsPage query={route.query} status={status} />
  else body = <Today status={status} />

  const current = route.page === 'codex' ? 'illustrations' : route.page
  const title = [...nav, { key: 'settings', label: '设置' }].find(n => n.key === current)?.label || '槐安个人内容工作台'

  return (
    <div className="app" ref={workspace.ref} style={{ '--sidebar': `${navigation.size}px` } as CSSProperties}>
      <aside className="sidebar" id="workbench-navigation">
        <PaneResizer label="左侧导航宽度" controls="workbench-navigation" edge="end" pane={navigation} />
        <div className="brand">
          <span className="brand-mark"><Ic.Pen size={16} /></span>
          <span>槐安个人内容工作台</span>
        </div>
        <button className="btn primary capture-btn" onClick={startCapture}><Ic.Plus size={16} /> 记一笔</button>
        <button className="btn capture-btn" onClick={()=>go('imports')}><Ic.Upload size={16} /> 导入内容</button>
        <nav className="nav" aria-label="主导航">
          {nav.map(n => (
            <button key={n.key} className="nav-item" aria-current={current === n.key ? 'page' : undefined} onClick={() => go(n.key)}>
              {n.icon}<span>{n.label}</span>
              {!!n.count && <span className={'count' + (n.hot ? ' hot' : '')}>{n.count}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          {status && status.jobs_active > 0 && (
            <div className="busy-line"><Ic.Loader size={14} /> 后台在处理 {status.jobs_active} 项</div>
          )}
          <div className="conn" aria-label="连接状态">
            <div className="conn-row"><Ic.XLogo size={12} /> X<span className={'dot ' + (status?.x_connected ? 'on' : 'off')} /></div>
            <div className="conn-row"><Ic.WechatLogo size={13} /> 公众号<span className={'dot ' + (status?.wechat_configured ? 'on' : 'off')} /></div>
            <div className="conn-row"><Ic.Sparkle size={13} /> 起草模型<span className={'dot ' + (status?.llm.ok ? 'on' : 'off')} /></div>
          </div>
          <button className="nav-item" aria-current={current === 'settings' ? 'page' : undefined} onClick={() => go('settings')}>
            <Ic.Gear /><span>设置</span>
          </button>
        </div>
      </aside>

      <div className="main">
        {!fullBleed && (
          <div className="mobile-bar">
            <span className="brand-mark" style={{ width: 26, height: 26 }}><Ic.Pen size={14} /></span>
            <span className="t">{title}</span>
            <div className="right">
              {status && status.jobs_active > 0 && <span className="small muted row"><Ic.Loader size={13} /> {status.jobs_active}</span>}
              <button className="btn ghost icon" aria-label="设置" onClick={() => go('settings')}><Ic.Gear /></button>
            </div>
          </div>
        )}
        {body}
      </div>

      <nav className="tabbar" aria-label="主导航">
        {[nav.find(n => n.key === 'today')!, nav.find(n => n.key === 'ideas')!].map(n => <TabButton key={n.key} n={n} current={current} />)}
        <button aria-label="记一笔" onClick={startCapture}><span className="fab"><Ic.Plus size={22} /></span></button>
        {[nav.find(n => n.key === 'drafts')!, nav.find(n => n.key === 'materials')!].map(n => <TabButton key={n.key} n={n} current={current === 'videos' ? 'drafts' : current} />)}
      </nav>
    </div>
  )
}

function TabButton({ n, current }: { n: NavItem; current: string }) {
  return (
    <button aria-current={current === n.key ? 'page' : undefined} onClick={() => go(n.key)}>
      {n.icon}<span>{n.label}</span>
      {n.hot && !!n.count && <span className="dotn">{n.count}</span>}
    </button>
  )
}
