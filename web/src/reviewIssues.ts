import { Extension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'
import { locate, type Anchor } from './commentAnchors'

export const issueKey=new PluginKey<Anchor[]>('review-issues')
export const ReviewIssues=Extension.create({
  name:'reviewIssues',
  addOptions(){return {onChange: (issues:Anchor[])=>{void issues}}},
  addProseMirrorPlugins(){
    const storageKey=`cw-review-issues:${location.pathname}${location.hash}`
    let initial:Anchor[]=[]
    try { const saved=JSON.parse(localStorage.getItem(storageKey)||'[]'); if(Array.isArray(saved))initial=saved.filter(a=>typeof a.quote==='string'&&typeof a.id==='number') } catch { /* Empty recovery. */ }
    return [new Plugin({key:issueKey,state:{init:()=>initial,apply(tr,old){
      const next=tr.getMeta(issueKey) as Anchor[]|undefined
      if(next){try{localStorage.setItem(storageKey,JSON.stringify(next))}catch{/* Current session still works. */}}
      return next||old
    }},view:()=>({update:(view,previous)=>{const next=issueKey.getState(view.state)||[];if(next!==issueKey.getState(previous))this.options.onChange(next)}}),props:{decorations(state){
      const marks:Decoration[]=[]
      for(const range of locate(state.doc,issueKey.getState(state)||[]).values())if(range)marks.push(Decoration.inline(range[0],range[1],{class:'review-issue',title:'待核实／待修改（仅本机审核标记，不发布）'}))
      return DecorationSet.create(state.doc,marks)
    }}})]
  }
})
