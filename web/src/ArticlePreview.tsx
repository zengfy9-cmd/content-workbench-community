import { useEffect, useRef } from 'react'
import { copyPromptText } from './promptBlock'
import { useToast } from './ui'

/** Buttons belong only to the workbench preview, never to the exported HTML. */
export function ArticlePreview({ html }: { html: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const toast = useToast()
  useEffect(() => {
    const root = ref.current
    if (!root) return
    const buttons: HTMLButtonElement[] = []
    root.querySelectorAll<HTMLElement>('pre[data-prompt]').forEach(pre => {
      // A fenced Markdown block adds one terminator newline during rendering.
      const text = (pre.querySelector('code')?.textContent || '').replace(/\n$/, '')
      const button = document.createElement('button')
      button.type = 'button'; button.className = 'btn sm prompt-preview-copy'
      button.textContent = '复制提示词'
      button.onclick = async () => {
        try { await copyPromptText(text); toast('提示词已复制') }
        catch (e) { toast((e as Error).message, 'error') }
      }
      pre.before(button); buttons.push(button)
    })
    return () => buttons.forEach(button => button.remove())
  }, [html, toast])
  return <div ref={ref} dangerouslySetInnerHTML={{ __html: html }} />
}
