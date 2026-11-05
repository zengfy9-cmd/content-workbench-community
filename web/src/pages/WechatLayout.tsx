import * as Ic from '../icons'

export type WechatTheme = { id: string; name: string; accent: string; description: string }

export function WechatLayoutControls({ themes, theme, busy, canCopy, inferredHeadings, onTheme, onCopy }: {
  themes: WechatTheme[]; theme: string; busy: boolean; canCopy: boolean
  inferredHeadings: number
  onTheme: (value: string) => void; onCopy: () => void
}) {
  if (!theme) return null
  return (
    <section className="wechat-layout-controls" aria-label="公众号排版设置" aria-busy={busy}>
      <div className="wechat-layout-heading"><Ic.Check size={14} /><strong>通用 公众号排版</strong></div>
      <div className="wechat-layout-actions">
        <label className="sr-only" htmlFor="wechat-layout-theme">公众号排版主题</label>
        <select id="wechat-layout-theme" value={theme} disabled={busy} onChange={e => onTheme(e.target.value)}>
          {themes.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
        <button type="button" className="btn sm" disabled={busy || !canCopy} onClick={onCopy}><Ic.Copy size={14} /> 复制排版</button>
        <button type="button" className="btn ghost sm" disabled={busy} onClick={() => onTheme('')}>恢复原排版</button>
      </div>
      <p className="small">{themes.find(item => item.id === theme)?.description}</p>
      <p className="small muted" role="status">{inferredHeadings > 0 ? `已识别 ${inferredHeadings} 处小标题，仅用于排版；正文原文不变。` : '沿用正文中的标题、引用和列表结构，样式随稿件保存。'}</p>
    </section>
  )
}
