import { useEffect, useRef } from 'react'
import { useEditor, EditorContent, useEditorState, type Editor as TiptapEditor } from '@tiptap/react'
import { BubbleMenu } from '@tiptap/react/menus'
import { anchorKey, CommentAnchors, type Anchor } from './commentAnchors'
import StarterKit from '@tiptap/starter-kit'
import Highlight from '@tiptap/extension-highlight'
import Image from '@tiptap/extension-image'
import Placeholder from '@tiptap/extension-placeholder'
import MarkdownIt from 'markdown-it'
import type { JSONContent } from '@tiptap/core'
import { Extension } from '@tiptap/core'
import * as Ic from './icons'
import { ReviewIssues, issueKey } from './reviewIssues'
import { isolateSelection } from './selectionFormat'
import { PromptBlock, togglePromptBlock } from './promptBlock'

/* The draft is stored as Markdown (## 标题, **加粗**, ==高亮==, ![](/api/images/N)).
   The editor shows it as formatted text with the pictures in place and writes
   Markdown back on every change. */

const md = new MarkdownIt('commonmark', { html: false, linkify: false }).enable('table')
md.core.ruler.before('inline', 'legacy_editor_breaks', state => {
  for (const token of state.tokens) {
    if (token.type === 'inline') token.content = token.content.replace(/(?:\\\n)*\n?\\$/, match => match.includes('\n') || token.content === '\\' ? '' : match)
  }
})
md.block.ruler.before('fence', 'alignment', (state, start, end, silent) => {
  const marker = state.src.slice(state.bMarks[start] + state.tShift[start], state.eMarks[start]).trim()
  if (marker !== ':::center' && marker !== ':::left') return false
  let last = start + 1
  while (last < end && state.src.slice(state.bMarks[last] + state.tShift[last], state.eMarks[last]).trim() !== ':::') last++
  if (last >= end) return false
  if (silent) return true
  const first = state.tokens.length
  const limit = state.lineMax
  state.lineMax = last
  try { state.md.block.tokenize(state, start + 1, last) }
  finally { state.lineMax = limit }
  for (const token of state.tokens.slice(first)) {
    if (token.type === 'paragraph_open' || token.type === 'heading_open') token.attrSet('style', `text-align:${marker.slice(3)}`)
  }
  state.line = last + 1
  return true
})
// Match the server rule: highlight can wrap bold text without CommonMark's
// punctuation-flanking restriction turning the delimiters into visible text.
md.core.ruler.before('text_join','workbench_highlight',state=>{
  for(const token of state.tokens){
    const kids=token.children
    if(token.type!=='inline'||!kids)continue
    const out:typeof kids=[];let opening:number|null=null
    for(const child of kids){
      if(child.type!=='text'||!child.content.includes('==')){out.push(child);continue}
      child.content.split('==').forEach((part,i)=>{
        if(i){out.push(new state.Token(opening===null?'mark_open':'mark_close','mark',opening===null?1:-1));opening=opening===null?out.length-1:null}
        if(part){const text=new state.Token('text','',0);text.content=part;out.push(text)}
      })
    }
    if(opening!==null){const text=new state.Token('text','',0);text.content='==';out[opening]=text}
    token.children=out
  }
})

export function markdownToHtml(markdown: string): string {
  // the title is its own field, so the body's top level is ##
  const tokens = md.parse(normalizeEmphasis(markdown), {})
  for (const token of tokens) if (token.tag === 'h1') token.tag = 'h2'
  return md.renderer.render(tokens, md.options, {})
}

/* Same rule as textutil.normalize_emphasis on the server: CommonMark won't close
   `**…。**放` or open `放**「…`, so punctuation at the edge of a bold/highlight
   that touches a letter moves just outside it. */
const PUNCT = '。！？，；：、“”‘’「」『』（）《》〈〉【】…—～·.,!?;:\'"()[]<>'
const isSpace = (c: string) => /\s/.test(c)

function fixPairs(line: string, delim: string): string {
  const pos: number[] = []
  for (let i = line.indexOf(delim); i >= 0; i = line.indexOf(delim, i + delim.length)) pos.push(i)
  if (pos.length < 2) return line
  const n = delim.length
  let out = '', last = 0
  for (let k = 0; k + 1 < pos.length; k += 2) {
    const o = pos[k], c = pos[k + 1]
    let inner = line.slice(o + n, c)
    const before = o > 0 ? line[o - 1] : ' '
    const after = c + n < line.length ? line[c + n] : ' '
    let lead = '', trail = ''
    if (!isSpace(before) && !PUNCT.includes(before)) {
      let j = 0
      while (j < inner.length && PUNCT.includes(inner[j])) j++
      lead = inner.slice(0, j); inner = inner.slice(j)
    }
    if (!isSpace(after) && !PUNCT.includes(after)) {
      let j = inner.length
      while (j > 0 && PUNCT.includes(inner[j - 1])) j--
      trail = inner.slice(j); inner = inner.slice(0, j)
    }
    if (!inner) continue
    out += line.slice(last, o) + lead + delim + inner + delim + trail
    last = c + n
  }
  return out + line.slice(last)
}

export function normalizeEmphasis(markdown: string): string {
  let fence = '', size = 0
  return markdown.split('\n').map(line => {
    const marker = line.match(/^\s{0,3}(`{3,}|~{3,})(.*)$/)
    if (fence) {
      if (marker && marker[1][0] === fence && marker[1].length >= size && !marker[2].trim()) fence = ''
      return line
    }
    if (marker) { fence = marker[1][0]; size = marker[1].length; return line }
    if (line.includes('`')) return line
    return fixPairs(fixPairs(line.replace(/\*\*==([^\n]*?)==\*\*/g,'==**$1**=='), '**'), '==')
  }).join('\n')
}

// ------------------------------------------------------------------ JSON → Markdown

type Mark = { type: string; attrs?: Record<string, unknown> }
const ORDER = ['link', 'highlight', 'bold', 'italic', 'code']

function open(m: Mark): string {
  if (m.type === 'bold') return '**'
  if (m.type === 'highlight') return '=='
  if (m.type === 'italic') return '*'
  if (m.type === 'code') return '`'
  if (m.type === 'link') return '['
  return ''
}
function close(m: Mark): string {
  if (m.type === 'link') return `](${String(m.attrs?.href || '')})`
  return open(m)
}

function escapeText(t: string): string {
  return t.replace(/([\\`*_[\]])/g, '\\$1').replace(/==/g, '\\=\\=')
}

function inline(nodes: JSONContent[] | undefined): string {
  let out = ''
  let stack: Mark[] = []
  const closeTo = (n: number) => { while (stack.length > n) out += close(stack.pop()!) }
  for (const node of nodes || []) {
    if (node.type === 'hardBreak') { closeTo(0); out += '  \n'; continue }
    if (node.type !== 'text') continue
    const marks = ((node.marks || []) as Mark[]).filter(m => ORDER.includes(m.type))
      .sort((a, b) => ORDER.indexOf(a.type) - ORDER.indexOf(b.type))
    // keep the common prefix of open marks, close the rest, open the new ones
    let keep = 0
    while (keep < stack.length && keep < marks.length && stack[keep].type === marks[keep].type
      && JSON.stringify(stack[keep].attrs || {}) === JSON.stringify(marks[keep].attrs || {})) keep++
    closeTo(keep)
    for (const m of marks.slice(keep)) { out += open(m); stack.push(m) }
    const text = node.text || ''
    out += marks.some(m => m.type === 'code') ? text : escapeText(text)
  }
  closeTo(0)
  stack = []
  return out
}

function lineStart(text: string): string {
  // stop a paragraph from turning into a heading, quote or list
  return text.replace(/^(#{1,6}\s|>|[-+]\s)/gm, m => '\\' + m).replace(/^(\d+)([.)])(\s)/gm, '$1\\$2$3')
}

function block(node: JSONContent, indent = ''): string {
  const kids = node.content || []
  switch (node.type) {
    case 'paragraph': { const text=indent + lineStart(inline(kids)).replace(/\n/g, '\n' + indent); return node.attrs?.textAlign ? ':::'+node.attrs.textAlign+'\n'+text+'\n:::' : text }
    case 'heading': { const text='#'.repeat(Number(node.attrs?.level || 2)) + ' ' + inline(kids); return node.attrs?.textAlign ? ':::'+node.attrs.textAlign+'\n'+text+'\n:::' : text }
    case 'blockquote': return kids.map(k => block(k)).join('\n\n').split('\n').map(l => (l ? '> ' + l : '>')).join('\n')
    case 'bulletList':
    case 'orderedList': {
      let n = Number(node.attrs?.start || 1)
      return kids.map(item => {
        const marker = node.type === 'bulletList' ? '- ' : `${n++}. `
        const parts = (item.content || []).map(k => block(k))
        const pad = ' '.repeat(marker.length)
        return marker + parts.join('\n\n').split('\n').map((l, i) => (i === 0 || !l ? l : pad + l)).join('\n')
      }).join('\n')
    }
    case 'horizontalRule': return '---'
    case 'image': return `![${String(node.attrs?.alt || '')}](${String(node.attrs?.src || '')})`
    case 'promptBlock':
    case 'codeBlock': {
      const text = kids.map(k => k.text || '').join('')
      const longest = Math.max(0, ...(text.match(/`+/g) || []).map(s => s.length))
      const fence = '`'.repeat(Math.max(3, longest + 1))
      return fence + (node.type === 'promptBlock' ? 'prompt' : String(node.attrs?.language || '')) + '\n' + text + '\n' + fence
    }
    default: return inline(kids)
  }
}

export function docToMarkdown(doc: JSONContent): string {
  return normalizeEmphasis((doc.content || []).map(n => block(n)).filter(s => s.trim() !== '').join('\n\n') + '\n')
}

// ------------------------------------------------------------------ editor

export type Selection = { quote: string; prefix: string; suffix: string }

export function RichEditor({ value, onChange, onFiles, placeholder, editorRef, anchors, activeAnchor, onAnchorClick, onComment, reviewIssues, onReviewIssues, editable = true }: {
  value: string
  onChange: (markdown: string) => void
  onFiles: (files: File[]) => Promise<number[]>
  placeholder?: string
  editorRef?: React.MutableRefObject<TiptapEditor | null>
  anchors?: Anchor[]
  activeAnchor?: number | null
  onAnchorClick?: (id: number) => void
  onComment?: (sel: Selection) => void
  editable?: boolean
  reviewIssues?: Anchor[]
  onReviewIssues?: (issues: Anchor[]) => void
}) {
  const lastEmitted = useRef(value)
  const selectionRef=useRef<{from:number;to:number}|null>(null)
  const onFilesRef = useRef(onFiles)
  onFilesRef.current = onFiles
  const clickRef = useRef<(id: number) => void>(() => {})
  clickRef.current = id => onAnchorClick?.(id)
  const issuesCallback=useRef(onReviewIssues)
  issuesCallback.current=onReviewIssues

  const editor = useEditor({
    extensions: [
      Extension.create({name:'paragraphAlignment',addGlobalAttributes(){return [{types:['paragraph','heading'],attributes:{textAlign:{default:null,parseHTML:el=>['center','left'].includes(el.style.textAlign)?el.style.textAlign:null,renderHTML:attrs=>attrs.textAlign?{style:`text-align:${attrs.textAlign}`}:{}}}}]}}),
      PromptBlock,
      StarterKit.configure({ heading: { levels: [2, 3, 4] }, underline: false, strike: false,
        link: { openOnClick: false, autolink: true } }),
      Highlight.configure({ multicolor: false }),
      Image.configure({ inline: false, allowBase64: false }),
      Placeholder.configure({ placeholder: placeholder || '从这里开始写' }),
      CommentAnchors.configure({ onClick: clickRef }),
      ReviewIssues.configure({onChange:(issues:Anchor[])=>issuesCallback.current?.(issues)}),
    ],
    editable,
    onSelectionUpdate:({editor:e})=>{selectionRef.current={from:e.state.selection.from,to:e.state.selection.to}},
    content: markdownToHtml(value),
    editorProps: {
      attributes: { class: 'rich-body', spellcheck: 'false' },
      handlePaste: (view, event) => {
        const files = [...(event.clipboardData?.files || [])].filter(f => f.type.startsWith('image/'))
        if (!files.length) return false
        event.preventDefault()
        insertUploaded(view.state.selection.from, files)
        return true
      },
      handleDrop: (view, event) => {
        const files = [...(event.dataTransfer?.files || [])].filter(f => f.type.startsWith('image/'))
        if (!files.length) return false
        event.preventDefault()
        const pos = view.posAtCoords({ left: event.clientX, top: event.clientY })?.pos ?? view.state.selection.from
        insertUploaded(pos, files)
        return true
      },
    },
    onUpdate: ({ editor }) => {
      const next = docToMarkdown(editor.getJSON())
      lastEmitted.current = next
      onChange(next)
    },
  })

  async function insertUploaded(pos: number, files: File[]) {
    const ids = await onFilesRef.current(files)
    if (!editor || !ids.length) return
    editor.chain().focus().insertContentAt(pos, ids.map(id => ({ type: 'image', attrs: { src: `/api/images/${id}` } }))).run()
  }

  // outside changes (restore a version, AI draft arriving) replace the content
  useEffect(() => {
    if (!editor || value === lastEmitted.current) return
    lastEmitted.current = value
    editor.commands.setContent(markdownToHtml(value), { emitUpdate: false })
  }, [value, editor])

  useEffect(() => { if (editorRef) editorRef.current = editor }, [editor, editorRef])
  useEffect(()=>{if(editor && reviewIssues && JSON.stringify(issueKey.getState(editor.state)||[])!==JSON.stringify(reviewIssues))editor.view.dispatch(editor.state.tr.setMeta(issueKey,reviewIssues))},[editor,reviewIssues])
  useEffect(() => { editor?.setEditable(editable, false) }, [editor, editable])

  // comment highlights follow the comment list and the selected thread
  useEffect(() => {
    if (!editor) return
    editor.view.dispatch(editor.state.tr.setMeta(anchorKey, { anchors: anchors || [], active: activeAnchor ?? null }))
  }, [editor, anchors, activeAnchor])

  function commentOnSelection() {
    if (!editor || !onComment) return
    const { from, to } = editor.state.selection
    const doc = editor.state.doc
    const quote = doc.textBetween(from, to, '\n').trim().slice(0, 500)
    if (!quote) return
    onComment({ quote, prefix: doc.textBetween(Math.max(0, from - 30), from, '\n'), suffix: doc.textBetween(to, Math.min(doc.content.size, to + 30), '\n') })
  }

  if (!editor) return null
  return (
    <div className="rich">
      {editable && <Toolbar editor={editor} selection={selectionRef} onPick={files => insertUploaded(editor.state.selection.from, files)} />}
      <BubbleMenu editor={editor} className="bubble" appendTo={()=>document.body} options={{strategy:'fixed',placement:'top',offset:8}} shouldShow={({ editor: e, from, to }) => from !== to && !e.isActive('image')}>
        {editable && <>
          <button type="button" onMouseDown={e => e.preventDefault()} onClick={() => editor.chain().focus().toggleBold().run()} aria-label="加粗"><b>B</b></button>
          <button type="button" onMouseDown={e => e.preventDefault()} onClick={() => editor.chain().focus().toggleHighlight().run()} aria-label="高亮"><span className="hl">高亮</span></button>
          <button type="button" onMouseDown={e=>e.preventDefault()} onClick={()=>toggleReviewIssue(editor)} aria-label="选段标记待修改"><span className="issue-label">待修改</span></button>
          <span className="sep" />
        </>}
        {onComment && <button type="button" className="cm" onMouseDown={e => e.preventDefault()} onClick={commentOnSelection}><Ic.Note size={14} /> 批注</button>}
      </BubbleMenu>
      <EditorContent editor={editor} />
    </div>
  )
}

function toggleReviewIssue(editor:TiptapEditor){
  const {from,to}=editor.state.selection
  if(from===to)return
  const doc=editor.state.doc,quote=doc.textBetween(from,to,'\n')
  const old=issueKey.getState(editor.state)||[]
  const exists=old.some(a=>a.quote===quote && a.prefix===doc.textBetween(Math.max(0,from-30),from,'\n'))
  editor.view.dispatch(editor.state.tr.setMeta(issueKey,exists?old.filter(a=>!(a.quote===quote&&a.prefix===doc.textBetween(Math.max(0,from-30),from,'\n'))):[...old,{id:Date.now(),quote,prefix:doc.textBetween(Math.max(0,from-30),from,'\n'),suffix:doc.textBetween(to,Math.min(doc.content.size,to+30),'\n')}]))
}

function Toolbar({ editor, onPick,selection }: { editor: TiptapEditor; onPick: (files: File[]) => void;selection:React.RefObject<{from:number;to:number}|null> }) {
  const fileRef = useRef<HTMLInputElement>(null)
  const s = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      h2: e.isActive('heading', { level: 2 }), h3: e.isActive('heading', { level: 3 }), h4: e.isActive('heading', { level: 4 }),
      bold: e.isActive('bold'), mark: e.isActive('highlight'), quote: e.isActive('blockquote'), prompt: e.isActive('promptBlock'),
      center: e.isActive({textAlign:'center'}), left: e.isActive({textAlign:'left'}),
      ul: e.isActive('bulletList'), ol: e.isActive('orderedList'),
      undo: e.can().undo(), redo: e.can().redo(),
      characters: Array.from(e.state.doc.textContent.replace(/\s/g, '')).length,
    }),
  })
  const c = (blockStyle=false) => {
    const range=selection.current
    if(range && range.to<=editor.state.doc.content.size)editor.commands.setTextSelection(range)
    if(blockStyle)isolateSelection(editor)
    return editor.chain().focus()
  }
  function issue(){
    toggleReviewIssue(editor)
  }
  const B = ({ on, label, title, run, children }: { on?: boolean; label: string; title?: string; run: () => void; children: React.ReactNode }) => (
    <button type="button" className={'tb' + (on ? ' on' : '')} aria-pressed={!!on} aria-label={label} title={title || label}
      onMouseDown={e => e.preventDefault()} onClick={run}>{children}</button>
  )
  return (
    <div className="rich-toolbar" role="toolbar" aria-label="排版">
      <B label="正文" on={!s.h2 && !s.h3 && !s.h4 && !s.quote} run={() => c(true).setParagraph().run()}>正文</B>
      <B label="一级标题" on={s.h2} run={() => c(true).toggleHeading({ level: 2 }).run()}>标题1</B>
      <B label="二级标题" on={s.h3} run={() => c(true).toggleHeading({ level: 3 }).run()}>标题2</B>
      <B label="三级标题" on={s.h4} run={() => c(true).toggleHeading({ level: 4 }).run()}>标题3</B>
      <span className="sep" />
      <B label="加粗" title="加粗（⌘B）" on={s.bold} run={() => c().toggleBold().run()}><b>B</b></B>
      <B label="高亮" title="高亮（⌘⇧H）" on={s.mark} run={() => c().toggleHighlight().run()}><span className="hl">高亮</span></B>
      <span className="sep" />
      <B label="标记待修改" title="红色审核标记：选中文字后点击，再次点击取消；随稿件保存，不进入发布正文" run={()=>{c();issue()}}><span className="issue-label">待修改</span></B>
      <B label="引用" on={s.quote} run={() => c(true).toggleBlockquote().run()}>引用</B>
      <B label="提示词块" title="选中文字设为提示词块，可一键复制；再次点击恢复正文" on={s.prompt} run={()=>{c(true);togglePromptBlock(editor)}}>提示词</B>
      <B label="选中段落居中" on={s.center} run={()=>c(true).updateAttributes('paragraph',{textAlign:'center'}).updateAttributes('heading',{textAlign:'center'}).run()}>居中</B>
      <B label="选中段落左对齐" on={s.left} run={()=>c(true).updateAttributes('paragraph',{textAlign:'left'}).updateAttributes('heading',{textAlign:'left'}).run()}>左对齐</B>
      <B label="无序列表" on={s.ul} run={() => c(true).toggleBulletList().run()}>• 列表</B>
      <B label="有序列表" on={s.ol} run={() => c(true).toggleOrderedList().run()}>1. 列表</B>
      <B label="分割线" run={() => c().setHorizontalRule().run()}>—</B>
      <B label="插入图片" run={() => fileRef.current?.click()}><Ic.Image size={15} /></B>
      <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden
        onChange={e => { onPick([...(e.target.files || [])]); e.target.value = '' }} />
      <span className="sep" />
      <B label="撤销" run={() => c().undo().run()}><Ic.Undo size={15} /></B>
      <B label="重做" run={() => c().redo().run()}><span style={{ display: 'inline-flex', transform: 'scaleX(-1)' }}><Ic.Undo size={15} /></span></B>
      <span className="rich-word-count" aria-label={`正文字数：${s.characters}`} title="正文字符数：含标点，不含空白、图片和单独的文章标题">{s.characters.toLocaleString('zh-CN')} 字</span>
    </div>
  )
}
