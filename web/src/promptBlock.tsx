import { Node, mergeAttributes, type Editor } from '@tiptap/core'
import { NodeViewContent, NodeViewWrapper, ReactNodeViewRenderer, type NodeViewProps } from '@tiptap/react'
import { useState } from 'react'

export async function copyPromptText(text: string) {
  if (navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(text); return } catch { /* selection fallback */ }
  }
  const field = document.createElement('textarea')
  field.value = text
  field.style.cssText = 'position:fixed;left:-10000px;top:0'
  const focus = document.activeElement as HTMLElement | null
  const selection = window.getSelection()
  const range = selection?.rangeCount ? selection.getRangeAt(0).cloneRange() : null
  document.body.append(field)
  try {
    field.select()
    if (!document.execCommand('copy')) throw new Error('复制失败，请检查剪贴板权限后重试')
  } finally {
    field.remove(); focus?.focus({ preventScroll: true })
    if (range) { selection?.removeAllRanges(); selection?.addRange(range) }
  }
}

function PromptView({ node }: NodeViewProps) {
  const [status, setStatus] = useState('复制提示词')
  return <NodeViewWrapper className="prompt-block">
    <div className="prompt-block-head" contentEditable={false}><span>提示词</span>
      <button type="button" className="btn sm" onClick={async () => {
        try { await copyPromptText(node.textContent); setStatus('已复制') }
        catch { setStatus('复制失败，请重试') }
      }}>{status}</button></div>
    <NodeViewContent className="prompt-content" />
  </NodeViewWrapper>
}

export const PromptBlock = Node.create({
  name: 'promptBlock', group: 'block', content: 'text*', marks: '', code: true, defining: true,
  parseHTML() { return [{ tag: 'pre', priority: 60, preserveWhitespace: 'full',
    getAttrs: el => el.hasAttribute('data-prompt') || el.querySelector('code.language-prompt') ? {} : false }] },
  renderHTML({ HTMLAttributes }) { return ['pre', mergeAttributes(HTMLAttributes, { 'data-prompt': 'true' }), ['code', { class: 'language-prompt' }, 0]] },
  addNodeView() { return ReactNodeViewRenderer(PromptView) },
})

export function togglePromptBlock(editor: Editor) {
  if (editor.isActive('promptBlock')) return editor.chain().focus().setParagraph().run()
  const { $from, $to } = editor.state.selection
  if ($from.depth !== 1 || $to.depth !== 1) return false
  const from = $from.before(1), to = $to.after(1)
  const text = editor.state.doc.textBetween(from, to, '\n', node => node.type.name === 'hardBreak' ? '\n' : '')
  const node = editor.schema.nodes.promptBlock.create(null, text ? editor.schema.text(text) : undefined)
  return editor.chain().focus().insertContentAt({ from, to }, node.toJSON()).run()
}
