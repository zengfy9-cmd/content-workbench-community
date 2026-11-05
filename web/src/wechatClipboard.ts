/** Copy HTML, not its markup text; fallback reports failure instead of pretending. */
export async function copyWechatLayout(html: string): Promise<void> {
  const article = document.createElement('div')
  article.innerHTML = html
  const plain = article.cloneNode(true) as HTMLElement
  plain.querySelectorAll('[aria-hidden="true"]').forEach(node => node.remove())
  plain.querySelectorAll('p,section,blockquote,h1,h2,h3,h4,h5,h6,pre,tr').forEach(node => node.append('\n'))
  const text = plain.textContent?.trim() || ''
  try {
    if (navigator.clipboard?.write && typeof ClipboardItem !== 'undefined') {
      await navigator.clipboard.write([new ClipboardItem({
        'text/html': new Blob([html], { type: 'text/html' }),
        'text/plain': new Blob([text], { type: 'text/plain' }),
      })])
      return
    }
  } catch { /* Try the browser's rich-text selection fallback. */ }
  const selection = window.getSelection()
  const previous = selection && selection.rangeCount ? selection.getRangeAt(0).cloneRange() : null
  const focus = document.activeElement as HTMLElement | null
  article.style.cssText = 'position:fixed;left:-10000px;top:0;max-width:677px;background:white;'
  article.contentEditable = 'true'
  article.setAttribute('aria-hidden', 'true')
  document.body.append(article)
  try {
    const range = document.createRange()
    range.selectNodeContents(article)
    selection?.removeAllRanges(); selection?.addRange(range)
    if (!document.execCommand('copy')) throw new Error('复制未成功，请检查浏览器剪贴板权限后重试')
  } finally {
    article.remove(); selection?.removeAllRanges()
    if (previous) selection?.addRange(previous)
    focus?.focus({ preventScroll: true })
  }
}
