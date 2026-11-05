import { useEffect, useRef, useState } from 'react'
import { api, type Draft, type DraftImage, type XhsCard, type XhsState } from '../api'
import { AutoTextarea, useToast } from '../ui'
import { imgUrl } from './Longform'
import * as Ic from '../icons'

const TITLE_MAX = 20, BODY_MAX = 1000
type BrowserState = { state: string; message: string; delivery?: { state: string; message: string } }

function caption(s: XhsState) {
  return `${s.title || ''}\n\n${s.body || ''}\n\n${(s.tags || []).map(t => '#' + t).join(' ')}`.trim()
}

/** 小红书 version of a long-form draft: AI rewrite + drawn cards; the author sends it from his phone. */
export function XhsPanel({ draft, onDraft }: { draft: Draft; onDraft: (d: Draft) => void }) {
  const toast = useToast()
  const [s, setS] = useState<XhsState>(draft.xhs || {})
  const [busy, setBusy] = useState('')
  const [cardsOpen, setCardsOpen] = useState(false)
  const [manual, setManual] = useState(false)
  const [browser, setBrowser] = useState<BrowserState | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const fileInput = useRef<HTMLInputElement>(null)
  const latest = useRef(s)
  latest.current = s
  const saveQueue = useRef<Promise<unknown>>(Promise.resolve())
  useEffect(() => { setS(draft.xhs || {}) }, [draft.id, draft.xhs?.generated_at, draft.xhs?.generating, draft.xhs?.card_images?.join(',')])
  useEffect(() => {
    let active = true
    const refresh = async () => {
      try { const next = await api.get<BrowserState>(`/api/xhs/browser?draft_id=${draft.id}`); if (active) setBrowser(next) } catch { /* local editor remains usable */ }
    }
    void refresh()
    const interval = window.setInterval(refresh, 2500)
    return () => { active = false; window.clearInterval(interval) }
  }, [draft.id])
  useEffect(() => { setManual(false) }, [draft.id])

  // poll while the AI is writing it
  useEffect(() => {
    if (!s.generating) return
    const t = window.setInterval(async () => {
      const d = await api.get<Draft>(`/api/drafts/${draft.id}`)
      if (!d.xhs?.generating) { onDraft(d); setS(d.xhs || {}); if (d.xhs?.error) toast(d.xhs.error, 'error') }
    }, 4000)
    return () => window.clearInterval(t)
  }, [s.generating, draft.id, onDraft, toast])

  function patch(next: Partial<XhsState>, now = false) {
    const merged = { ...latest.current, ...next }
    latest.current = merged
    setS(merged)
    window.clearTimeout(timer.current)
    const send = () => { saveQueue.current = saveQueue.current.catch(() => {}).then(() => api.put<XhsState>(`/api/drafts/${draft.id}/xhs`, merged)); void saveQueue.current.catch(e => toast((e as Error).message, 'error')) }
    if (now) send(); else timer.current = window.setTimeout(send, 700)
  }
  async function generate() {
    if (s.title && !window.confirm('按现在的长文重新生成小红书版？你改过的文案和图卡文字会被覆盖。')) return
    try { setS(await api.post<XhsState>(`/api/drafts/${draft.id}/xhs/generate`)) } catch (e) { toast((e as Error).message, 'error') }
  }
  async function rerender() {
    setBusy('render')
    try {
      await api.put(`/api/drafts/${draft.id}/xhs`, { cover: s.cover, cards: s.cards })
      const next = await api.post<XhsState>(`/api/drafts/${draft.id}/xhs/render`)
      setS(next); onDraft(await api.get<Draft>(`/api/drafts/${draft.id}`)); toast('图卡重新画好了')
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy('') }
  }
  async function copy() {
    try { await navigator.clipboard.writeText(caption(s)); toast('文案已复制，标题、正文和话题都在里面') } catch { toast('复制失败，手动选中复制吧', 'error') }
  }
  const order = [...(s.card_images || []), ...(s.extra_images || [])]
  const sending = ['uploading', 'filling', 'saving'].includes(browser?.state || '')
  const blocked = ['saved', 'unknown', 'uploading', 'filling', 'saving'].includes(browser?.delivery?.state || '')
  const connected = browser?.state === 'ready'
  const missing = [!s.title?.trim() && '标题', !s.body?.trim() && '正文', !order.length && '至少1张图片'].filter(Boolean)
  const connectionLabel = connected ? '已连接小红书' : sending ? '正在传送本篇' : browser?.state === 'waiting_login' ? '等待登录' : browser?.state === 'opening' ? '正在打开浏览器' : browser?.state === 'disconnected' || !browser ? '未连接' : '浏览器操作状态见下方'
  async function connectBrowser() {
    setBusy('connect')
    try { setBrowser(await api.post<BrowserState>('/api/xhs/browser/connect')) }
    catch (e) { toast((e as Error).message, 'error') } finally { setBusy('') }
  }
  async function saveWebDraft() {
    if (!window.confirm(`把“${s.title || '未填写标题'}”及${order.length}张图片送入专用Chrome当前账号的小红书网页草稿箱？请先确认该窗口登录的是你的目标账号。`)) return
    setBusy('save-web')
    try {
      window.clearTimeout(timer.current)
      await saveQueue.current.catch(() => {})
      await api.put(`/api/drafts/${draft.id}/xhs`, latest.current)
      setBrowser(await api.post<BrowserState>(`/api/drafts/${draft.id}/xhs/save-web-draft`, { confirm: true }))
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy('') }
  }
  async function uploadImages(files: File[]) {
    if (!files.length) return
    if (order.length + files.length > 9) { toast('小红书图文最多选择9张图片', 'error'); return }
    setBusy('upload')
    try {
      const form = new FormData()
      files.forEach(file => form.append('files', file))
      const uploaded = await api.upload<DraftImage[]>(`/api/drafts/${draft.id}/images`, form)
      patch({ extra_images: [...(latest.current.extra_images || []), ...uploaded.map(image => image.id)] }, true)
      await saveQueue.current
      onDraft(await api.get<Draft>(`/api/drafts/${draft.id}`))
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy('') }
  }
  async function reconcile(resolution: 'saved' | 'not_saved') {
    const message = resolution === 'saved' ? '你已亲自在小红书网页草稿箱找到本篇标题和图片？' : '你已检查专用Chrome编辑页和小红书草稿箱，确认本篇尚未保存？解除后不会自动重送。'
    if (!window.confirm(message)) return
    setBusy('reconcile')
    try { setBrowser(await api.post<BrowserState>(`/api/drafts/${draft.id}/xhs/reconcile-web-draft`, { confirm: true, resolution })) }
    catch (e) { toast((e as Error).message, 'error') } finally { setBusy('') }
  }
  async function share() {
    setBusy('share')
    try {
      await navigator.clipboard.writeText(caption(s)).catch(() => {})
      const files = await Promise.all(order.map(async (id, n) => {
        const blob = await (await fetch(imgUrl(id))).blob()
        return new File([blob], `${String(n + 1).padStart(2, '0')}.${blob.type.includes('png') ? 'png' : 'jpg'}`, { type: blob.type })
      }))
      if (!navigator.canShare || !navigator.canShare({ files })) {
        toast('这台设备不支持直接分享图片，用“下载图片”再到小红书里选', 'error'); return
      }
      await navigator.share({ files })
      toast('文案已经复制好，在小红书里粘贴进正文，存草稿或发布')
    } catch (e) {
      if ((e as Error).name !== 'AbortError') toast((e as Error).message, 'error')
    } finally { setBusy('') }
  }
  function move(list: 'card_images' | 'extra_images', i: number, d: -1 | 1) {
    const arr = [...(s[list] || [])]
    const j = i + d
    if (j < 0 || j >= arr.length) return
    ;[arr[i], arr[j]] = [arr[j], arr[i]]
    patch({ [list]: arr }, true)
  }
  const candidates = (draft.images || []).filter(im => !order.includes(im.id) && !im.name.startsWith('小红书-'))
  const setCard = (i: number, c: XhsCard) => setS(st => ({ ...st, cards: (st.cards || []).map((x, j) => (j === i ? c : x)) }))

  if (s.generating) return <div className="stack"><div className="row muted small"><Ic.Loader size={14} /> 正在把长文改成小红书版、画图卡，一两分钟。</div><div className="skeleton" style={{ height: 160 }} /></div>
  if (!manual && !s.title && !s.body) return (
    <div className="stack">
      <div className="small muted">可以直接写好小红书标题、正文并选择图片，也可以让AI从长文生成文案和图卡。写好后可连接专用Chrome，尝试存入小红书网页草稿箱。</div>
      {s.error && <div className="err small">{s.error}</div>}
      <button className="btn primary" onClick={generate}><Ic.Sparkle size={14} /> 生成小红书版</button>
      <button className="btn" onClick={() => setManual(true)}>直接写小红书版</button>
    </div>
  )

  return (
    <div className="stack xhs">
      <section className="card" style={{ padding: 14 }}>
        <div className="mp-head">存到小红书网页草稿箱 <span className={`pill${connected ? ' accent' : ''}`} role="status" aria-live="polite">{connectionLabel}</span></div>
        <div className="small muted" role="status" aria-live="polite">{connected ? '连接成功，请确认专用Chrome窗口中登录的是目标账号。' : browser?.message || '尚未连接小红书'}{browser?.delivery && browser.delivery.message !== browser.message ? `；本篇：${browser.delivery.message}` : ''}</div>
        {missing.length > 0 && <div className="small muted" style={{ marginTop: 8 }} role="status" aria-live="polite">本篇还缺：{missing.join('、')}。补齐后才能存草稿；左侧长文不会自动填进右侧小红书版。</div>}
        <div className="row" style={{ flexWrap: 'wrap', gap: 8, marginTop: 10 }}>
          <button className="btn sm" onClick={connectBrowser} disabled={!!busy || sending || connected || ['opening', 'waiting_login'].includes(browser?.state || '')}>{connected ? '已连接，无需重复登录' : '连接小红书／打开登录窗口'}</button>
          <button className="btn primary sm" onClick={saveWebDraft} disabled={!!busy || sending || blocked || !order.length || !s.title?.trim() || !s.body?.trim() || !['ready', 'failed', 'saved'].includes(browser?.state || '')}>
            {sending || busy === 'save-web' ? <Ic.Loader size={14} /> : <Ic.Send size={14} />}
            {browser?.delivery?.state === 'saved' ? '已存入草稿箱' : browser?.delivery?.state === 'unknown' ? '请先人工核对' : '存到小红书草稿箱'}
          </button>
        </div>
        {browser?.delivery?.state === 'unknown' && <div className="row" style={{ flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
          <button className="btn sm" disabled={!!busy} onClick={() => reconcile('saved')}>我已找到草稿</button>
          <button className="btn ghost sm" disabled={!!busy} onClick={() => reconcile('not_saved')}>确认未保存，解除重试</button>
        </div>}
        <div className="small faint" style={{ marginTop: 8 }}>首次在专用Chrome登录；仅上传本篇选定图文并存草稿，不点击发布。网页草稿是否同步到手机以小红书实际显示为准。未读到成功回执时会停下等你核对。</div>
      </section>
      <div className="xhs-actions">
        <button className="btn primary sm mobile-share" disabled={!!busy || !order.length} onClick={share}>{busy === 'share' ? <Ic.Loader size={14} /> : <Ic.Send size={14} />} 分享到小红书</button>
        <button className="btn sm" onClick={copy}>复制文案</button>
        <a className="btn sm" href={`/api/drafts/${draft.id}/xhs.zip`}><Ic.Upload size={14} /> 下载图片</a>
        <button className="btn ghost sm" onClick={generate}>重新生成</button>
      </div>
      <div className="small faint">也可复制文案、下载图片自行上传。系统分享图片需要设备和App支持。</div>

      <section>
        <div className="mp-head">标题 <span className={'faint small' + ((s.title || '').length > TITLE_MAX ? ' over' : '')}>{(s.title || '').length}/{TITLE_MAX}</span></div>
        <input className="input" value={s.title || ''} maxLength={TITLE_MAX} onChange={e => patch({ title: e.target.value })} />
      </section>
      <section>
        <div className="mp-head">正文 <span className="faint small">{(s.body || '').length}/{BODY_MAX}</span></div>
        <AutoTextarea className="textarea xhs-body" maxLength={BODY_MAX} value={s.body || ''} onChange={e => patch({ body: e.target.value })} />
      </section>
      <section>
        <div className="mp-head">话题 <span className="faint small">空格隔开</span></div>
        <input className="input" value={(s.tags || []).join(' ')} onChange={e => patch({ tags: e.target.value.split(/[\s,，#]+/).filter(Boolean) })} />
      </section>

      <section>
        <div className="mp-head">图片 <span className="faint small">{order.length}/9 张，按这个顺序发</span>
          <button className="btn sm" style={{ marginLeft: 'auto' }} disabled={!!busy || order.length >= 9} onClick={() => fileInput.current?.click()}><Ic.Upload size={14} /> 添加图片</button>
          <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" multiple hidden aria-label="添加小红书图片" onChange={event => { void uploadImages(Array.from(event.target.files || [])); event.target.value = '' }} />
        </div>
        <div className="xhs-grid">
          {(s.card_images || []).map((id, i) => (
            <div key={id} className="xhs-tile"><img src={imgUrl(id)} alt="" />
              <div className="tile-acts"><button onClick={() => move('card_images', i, -1)} aria-label="往前">‹</button><span>{i === 0 ? '封面' : `卡 ${i}`}</span><button onClick={() => move('card_images', i, 1)} aria-label="往后">›</button></div></div>
          ))}
          {(s.extra_images || []).map((id, i) => (
            <div key={id} className="xhs-tile"><img src={imgUrl(id)} alt="" />
              <div className="tile-acts"><button onClick={() => move('extra_images', i, -1)} aria-label="往前">‹</button>
                <button onClick={() => patch({ extra_images: (s.extra_images || []).filter(x => x !== id) }, true)} aria-label="不要这张">×</button>
                <button onClick={() => move('extra_images', i, 1)} aria-label="往后">›</button></div></div>
          ))}
        </div>
        {candidates.length > 0 && (
          <details className="adv"><summary><Ic.Plus size={12} /> 从长文的图片里加</summary>
            <div className="xhs-grid" style={{ marginTop: 8 }}>
              {candidates.map(im => <button key={im.id} className="xhs-tile add" onClick={() => patch({ extra_images: [...(s.extra_images || []), im.id] }, true)}><img src={imgUrl(im.id)} alt="" /></button>)}
            </div>
          </details>
        )}
      </section>

      <section>
        <button className="btn ghost sm" onClick={() => setCardsOpen(v => !v)}><Ic.Note size={13} /> {cardsOpen ? '收起图卡文字' : '改图卡上的字'}</button>
        {cardsOpen && (
          <div className="stack" style={{ marginTop: 8 }}>
            <div className="small muted">封面</div>
            <input className="input" placeholder="顶上一句引子" value={s.cover?.kicker || ''} onChange={e => setS(st => ({ ...st, cover: { ...st.cover, kicker: e.target.value } }))} />
            <AutoTextarea className="textarea" placeholder="大字，每行一句" value={(s.cover?.lines || []).join('\n')} onChange={e => setS(st => ({ ...st, cover: { ...st.cover, lines: e.target.value.split('\n') } }))} />
            <input className="input" placeholder="大字里要标亮的词" value={s.cover?.highlight || ''} onChange={e => setS(st => ({ ...st, cover: { ...st.cover, highlight: e.target.value } }))} />
            <input className="input" placeholder="副标题" value={s.cover?.subtitle || ''} onChange={e => setS(st => ({ ...st, cover: { ...st.cover, subtitle: e.target.value } }))} />
            {(s.cards || []).map((c, i) => (
              <div key={i} className="stack" style={{ gap: 6 }}>
                <div className="small muted">要点卡 {i + 1}</div>
                <input className="input" value={c.title || ''} onChange={e => setCard(i, { ...c, title: e.target.value })} />
                <AutoTextarea className="textarea" value={(c.points || []).join('\n')} onChange={e => setCard(i, { ...c, points: e.target.value.split('\n') })} />
              </div>
            ))}
            <button className="btn sm" disabled={busy === 'render'} onClick={rerender}>{busy === 'render' ? <Ic.Loader size={14} /> : <Ic.Image size={14} />} 按这些字重新出图</button>
          </div>
        )}
      </section>
    </div>
  )
}
