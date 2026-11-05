import type { Editor } from '@tiptap/core'
import { TextSelection } from '@tiptap/pm/state'

/** Block styles must not absorb unselected sentences in the same paragraph. */
export function isolateSelection(editor:Editor){
  const {from,to,empty}=editor.state.selection
  if(empty)return
  let tr=editor.state.tr
  const end=tr.doc.resolve(to)
  if(end.depth===1&&end.parent.isTextblock&&end.parentOffset>0&&end.parentOffset<end.parent.content.size)tr=tr.split(to)
  const start=tr.doc.resolve(from)
  if(start.depth===1&&start.parent.isTextblock&&start.parentOffset>0&&start.parentOffset<start.parent.content.size)tr=tr.split(from)
  const mappedFrom=tr.mapping.map(from,1),mappedTo=tr.mapping.map(to,-1)
  if(tr.docChanged){tr.setSelection(TextSelection.create(tr.doc,mappedFrom,mappedTo));editor.view.dispatch(tr)}
}
