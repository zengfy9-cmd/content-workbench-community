import { ControlCenter } from './pages/ControlCenter'
import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { api, type Status } from './api'
import { Login } from './Login'
import { PasswordLogin } from './PasswordLogin'
import { Today } from './pages/Today'
import { Materials } from './pages/Materials'
import { Ideas } from './pages/Ideas'
import { Drafts, FinalCopies } from './pages/Drafts'
import { Editor } from './pages/Editor'
import { Publications } from './pages/Publications'
import { SettingsPage } from './pages/Settings'
import { Habits } from './pages/Habits'
import { Videos } from './pages/Videos'
import { VideoStudio } from './pages/VideoStudio'
import { ToastProvider, Dialog } from './ui'
import * as Ic from './icons'
import { PaneResizer } from './PaneResizer'
import { usePaneContainer, usePaneSize } from './usePaneSize'
import { Benchmarks } from './pages/Benchmarks'
import { MediaIntake } from './pages/MediaIntake'
import { ContentTips } from './pages/ContentTips'
import { CodexTasks } from './pages/CodexTasks'
import { ArticleImages } from './pages/ArticleImages'
import { ModelFallbackNotice } from './ModelFallbackNotice'
import { ScreenshotStudio } from './pages/ScreenshotStudio'
import { Composer } from './pages/Composer'

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

/** Open the note composer over the current page. */
export function startCapture() {
  window.dispatchEvent(new Event('cw-capture'))
}

type NavItem = { key: string; label: string; icon: ReactNode; count?: number; hot?: boolean }

export default function App() {
  return <ToastProvider><Root /></ToastProvider>
}

function Root() {
  const [auth, setAuth] = useState<{ registered: boolean; logged_in: boolean; login_mode?:string } | null>(null)
  const [route, setRoute] = useState<Route>(parseHash())
  const [status, setStatus] = useState<Status | null>(null)
  const [captureOpen, setCaptureOpen] = useState(false)
  const [renameOpen,setRenameOpen] = useState(false)
  const [newName,setNewName] = useState('')
  const [nameError,setNameError] = useState('')
  const [nameSaving,setNameSaving] = useState(false)
  const workbenchName = status?.workbench_name || '个人内容工作台'
  useEffect(()=>{document.title=workbenchName},[workbenchName])
  async function saveName(){
    if(!newName.trim()){setNameError('请填写工作台名称');return}
    setNameSaving(true);setNameError('')
    try{
      await api.put('/api/settings',{workbench_name:newName.trim()})
      setStatus(previous=>previous?{...previous,workbench_name:newName.trim()}:previous)
      setRenameOpen(false);window.dispatchEvent(new Event('cw-refresh-status'))
    }catch(e){setNameError((e as Error).message)}finally{setNameSaving(false)}
  }

  const workspace = usePaneContainer(!!auth?.logged_in)
  const navigation = usePaneSize({ key: 'cw-pane-navigation-v1', width: workspace.width, initial: 192, min: 170, max: 360, remaining: 640 })

  const refreshAuth = useCallback(() => {
    api.get<{ registered: boolean; logged_in: boolean; login_mode?:string }>('/api/auth/state').then(setAuth)
      .catch(() => setAuth({ registered: true, logged_in: false }))
  }, [])

  useEffect(() => {
    refreshAuth()
    const onHash = () => { setRoute(parseHash()); window.scrollTo(0, 0) }
    const onCapture = () => setCaptureOpen(true)
    window.addEventListener('hashchange', onHash)
    window.addEventListener('cw-logged-out', refreshAuth)
    window.addEventListener('cw-capture', onCapture)
    return () => { window.removeEventListener('hashchange', onHash); window.removeEventListener('cw-logged-out', refreshAuth); window.removeEventListener('cw-capture', onCapture) }
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
  if (!auth.logged_in) return auth.login_mode==='password'?<PasswordLogin registered={auth.registered} onDone={refreshAuth}/>:<Login registered={auth.registered} onDone={refreshAuth} />

  const nav: NavItem[] = [
    { key: 'today', label: '工作总览', icon: <Ic.Sun /> },
    { key: 'control', label: '中控', icon: <Ic.Sparkle /> },
    { key: 'materials', label: '素材', icon: <Ic.Inbox />, count: status?.counts.materials },
    { key: 'benchmarks', label: '对标库', icon: <Ic.Doc /> },
    { key: 'ideas', label: '选题', icon: <Ic.Bulb />, count: status?.counts.ideas_new, hot: !!status?.counts.ideas_new },
    { key: 'drafts', label: '稿件', icon: <Ic.Doc />, count: status?.counts.drafts },
    { key: 'final-drafts', label: '成稿箱', icon: <Ic.Inbox /> },
    { key: 'illustrations', label: '制图工作台', icon: <Ic.Image /> },
    { key: 'screenshots', label: '截图工作室', icon: <Ic.Image /> },
    { key: 'tips', label: '内容灵感技巧', icon: <Ic.Bulb /> },
    { key: 'videos', label: '视频制作', icon: <Ic.Film /> },
    { key: 'published', label: '发布记录', icon: <Ic.Send /> },
    { key: 'habits', label: '改稿习惯', icon: <Ic.Sparkle /> },
  ]

  const primaryNav = nav.slice(0, 7)
  const toolsNav = nav.filter(n => ['illustrations','screenshots'].includes(n.key))

  let body: ReactNode
  const fullBleed = ((route.page === 'drafts' || route.page === 'videos') && !!route.id) || route.page === 'materials' || route.page === 'benchmarks' || route.page === 'screenshots'
  const studyId = Number(route.query.get('study')) || undefined
  const targetDraft = Number(route.query.get('draft')) || undefined
  if (route.page === 'drafts' && route.id) body = <Editor key={route.id} id={route.id} status={status} studyId={studyId} studyKey={route.query.get('passage') || undefined} />
  else if (route.page === 'control') body = <ControlCenter />
  else if (route.page === 'screenshots') body = <ScreenshotStudio query={route.query}/>
  else if (route.page === 'benchmarks') body = <Benchmarks id={route.id} targetDraft={targetDraft} initialAnalysis={Number(route.query.get('analysis'))||undefined} />
  else if (route.page === 'videos' && route.id) body = <VideoStudio id={route.id} />
  else if (route.page === 'videos') body = <Videos />
  else if (route.page === 'imports') body = <MediaIntake id={route.id} />
  else if (route.page === 'materials') body = <Materials openId={route.id} />
  else if (route.page === 'ideas') body = <Ideas />
  else if (route.page === 'drafts') body = <Drafts />
  else if (route.page === 'final-drafts') body = <FinalCopies id={route.id} version={route.query.get('version')} />
  else if (route.page === 'tips') body = <ContentTips />
  else if (route.page === 'illustrations' || route.page === 'codex') body = <ArticleImages draftId={targetDraft} />
  else if (route.page === 'codex-history') body = <CodexTasks />
  else if (route.page === 'published') body = <Publications />
  else if (route.page === 'habits') body = <Habits />
  else if (route.page === 'settings') body = <SettingsPage query={route.query} status={status} />
  else body = <Today status={status} />

  const current = route.page === 'codex' ? 'illustrations' : route.page
  const title = [...nav, { key: 'settings', label: '设置' }].find(n => n.key === current)?.label || workbenchName

  return (
    <div className="app" ref={workspace.ref} style={{ '--sidebar': `${navigation.size}px` } as CSSProperties}>
      <ModelFallbackNotice />
      <Dialog open={captureOpen} title="记一笔 / 添加素材" onClose={()=>setCaptureOpen(false)}>
        <Composer status={status} onSaved={()=>setCaptureOpen(false)}/>
      </Dialog>
      <Dialog open={renameOpen} title="修改工作台名称" onClose={()=>{if(!nameSaving)setRenameOpen(false)}} footer={<><button className="btn" disabled={nameSaving} onClick={()=>setRenameOpen(false)}>取消</button><button className="btn primary" disabled={nameSaving||!newName.trim()} onClick={()=>void saveName()}>{nameSaving?'保存中…':'保存名称'}</button></>}>
        <label className="field"><span>工作台名称</span><input className="input" autoFocus maxLength={40} value={newName} disabled={nameSaving} onChange={e=>setNewName(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&newName.trim()){e.preventDefault();void saveName()}}}/></label>
        {nameError&&<p className="callout" role="alert">{nameError}</p>}
      </Dialog>
      <aside className="sidebar" id="workbench-navigation">
        <PaneResizer label="左侧导航宽度" controls="workbench-navigation" edge="end" pane={navigation} />
        <div className="brand">
          <span className="brand-mark"><Ic.Pen size={16} /></span>
          <button type="button" className="brand-name" title="点击修改工作台名称" aria-label="修改工作台名称" onClick={()=>{setNewName(workbenchName);setNameError('');setRenameOpen(true)}}>{workbenchName}</button>
        </div>
        <details className="sidebar-capture">
          <summary className="btn primary capture-btn"><Ic.Plus size={16} /> 收集内容</summary>
          <button className="btn" onClick={e=>{e.currentTarget.closest('details')?.removeAttribute('open');startCapture()}}>记一笔</button>
          <button className="btn" onClick={e=>{e.currentTarget.closest('details')?.removeAttribute('open');go('imports')}}><Ic.Upload size={16} /> 导入内容</button>
        </details>
        <nav className="nav" aria-label="主导航">
          {primaryNav.map(n => (
            <button key={n.key} className="nav-item" aria-current={current === n.key ? 'page' : undefined} onClick={() => go(n.key)}>
              {n.icon}<span>{n.label}</span>
              {!!n.count && <span className={'count' + (n.hot ? ' hot' : '')}>{n.count}</span>}
            </button>
          ))}
          <div className="nav-group-label">创作工具</div>
          {toolsNav.map(n => (
            <button key={n.key} className="nav-item" aria-current={current === n.key ? 'page' : undefined} onClick={() => go(n.key)}>
              {n.icon}<span>{n.label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          {status && status.jobs_active > 0 && (
            <div className="busy-line"><Ic.Loader size={14} /> 后台在处理 {status.jobs_active} 项</div>
          )}
          <div className="conn" aria-label="连接状态">
            {status?.x_connected && <div className="conn-row"><Ic.XLogo size={12} /> X<span className="dot on" /></div>}
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
              <button className="btn ghost" onClick={() => go('control')}>中控</button>
              <button className="btn ghost" onClick={() => go('final-drafts')}>成稿箱</button>
              <button className="btn ghost icon" aria-label="设置" onClick={() => go('settings')}><Ic.Gear /></button>
            </div>
          </div>
        )}
        {body}
      </div>

      <nav className="tabbar" aria-label="主导航">
        {['today', 'materials', 'benchmarks'].map(k => <TabButton key={k} n={nav.find(n => n.key === k)!} current={current} />)}
        <button aria-label="记一笔" onClick={startCapture}><span className="fab"><Ic.Plus size={22} /></span></button>
        {['ideas', 'drafts'].map(k => <TabButton key={k} n={nav.find(n => n.key === k)!} current={current === 'videos' ? 'drafts' : current} />)}
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
