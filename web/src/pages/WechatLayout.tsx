import * as Ic from '../icons'
import { useEffect, useState } from 'react'

export type WechatTheme = { id: string; name: string; accent: string; description: string }

export function WechatLayoutControls({ themes, theme, busy, canCopy, inferredHeadings, branding, onNumber, onTheme, onCopy }: {
  themes: WechatTheme[]; theme: string; busy: boolean; canCopy: boolean
  inferredHeadings: number
  branding?: { enabled: boolean; number: number | null; needs_start_number: boolean }
  onNumber: (number: number) => Promise<{ shared_with?: { draft_id: number; title: string }[] }>
  onTheme: (value: string) => void; onCopy: () => void
}) {
  const [articleNumber, setArticleNumber] = useState('')
  const [savingNumber, setSavingNumber] = useState(false)
  const [numberError, setNumberError] = useState('')
  const [sharedWith, setSharedWith] = useState<{ draft_id: number; title: string }[]>([])
  useEffect(() => {
    setArticleNumber(branding?.number?.toString() || '')
    setNumberError('')
    setSharedWith([])
  }, [branding?.number])
  const numberDirty = !!branding?.enabled && articleNumber !== (branding.number?.toString() || '')
  if (!theme) return null
  return (
    <section className="wechat-layout-controls" aria-label="公众号排版设置" aria-busy={busy || savingNumber}>
      <div className="wechat-layout-heading"><Ic.Check size={14} /><strong>xilo 公众号排版</strong></div>
      <div className="wechat-layout-actions">
        <label className="sr-only" htmlFor="wechat-layout-theme">公众号排版主题</label>
        <select id="wechat-layout-theme" value={theme} disabled={busy || savingNumber} onChange={e => onTheme(e.target.value)}>
          {themes.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        <button type="button" className="btn sm" disabled={busy || savingNumber || numberDirty || !canCopy || branding?.needs_start_number} onClick={onCopy}><Ic.Copy size={14} /> 复制排版</button>
        <button type="button" className="btn ghost sm" disabled={busy || savingNumber} onClick={() => onTheme('')}>恢复原排版</button>
      </div>
      <p className="small">{themes.find(item => item.id === theme)?.description}</p>
      <p className="small muted" role="status">{inferredHeadings > 0 ? `已识别 ${inferredHeadings} 处小标题，仅用于排版；正文原文不变。` : '沿用正文中的标题、引用和列表结构，样式随稿件保存。'}</p>
      {branding?.enabled && <p className="small" role="status">固定首尾已自动套用{branding.number != null ? ` · 第 ${branding.number} 篇 AI 原创` : ''}。开头编号、文末名片和居中文案不用手写。</p>}
      {branding?.enabled && <p className="small muted">数字想写几就写几，重复只提示不拦；新稿自动顺延，重排不加号。</p>}
      {branding?.enabled && <form className="wechat-layout-actions" onSubmit={async e => {
        e.preventDefault(); setSavingNumber(true); setNumberError(''); setSharedWith([])
        try {
          const saved = await onNumber(Number(articleNumber))
          setSharedWith(saved?.shared_with || [])
        } catch (error) { setNumberError((error as Error).message) } finally { setSavingNumber(false) }
      }}>
        <label className="small" htmlFor="wechat-article-number">本篇原创编号</label>
        <input className="input" id="wechat-article-number" type="number" min="1" max="1000000" step="1" required disabled={busy || savingNumber} value={articleNumber} onChange={e => setArticleNumber(e.target.value)} />
        <button className="btn sm" disabled={busy || savingNumber || !numberDirty}>保存编号</button>
        {numberDirty && <p className="small" role="status">编号已修改，点击「保存编号」后更新预览。</p>}
        {sharedWith.length > 0 && <p className="small" role="status">第 {branding?.number ?? articleNumber} 篇也会用在{sharedWith.map(item => `《${item.title || `稿件 ${item.draft_id}`}》`).join('、')}上，两篇会显示同一个编号。</p>}
        {numberError && <p role="alert" className="small">{numberError}</p>}
      </form>}
    </section>
  )
}
