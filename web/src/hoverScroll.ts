// Route wheel input to the pane under the pointer, even after a Windows native
// select captured focus. Only cancel the native event when this pane can scroll.
export function enableHoverScroll(root: HTMLElement) {
  const wheel=(event:WheelEvent)=>{
    if(event.ctrlKey || event.metaKey || !event.deltaY)return
    const target=event.target as HTMLElement
    if(target.closest('select,textarea,input,[contenteditable="true"]'))return
    let node:HTMLElement|null=target
    while(node && root.contains(node)){
      const overflow=getComputedStyle(node).overflowY
      if(/auto|scroll/.test(overflow) && node.scrollHeight>node.clientHeight+1){
        const delta=event.deltaY*(event.deltaMode===1?18:event.deltaMode===2?node.clientHeight:1)
        if((delta>0 && node.scrollTop+node.clientHeight<node.scrollHeight-1)||(delta<0 && node.scrollTop>0)){
          event.preventDefault();node.scrollTop+=delta;return
        }
      }
      if(node===root)break
      node=node.parentElement
    }
  }
  const enter=(event:PointerEvent)=>{const current=document.activeElement;if(current instanceof HTMLSelectElement && current!==event.target)current.blur()}
  root.addEventListener('wheel',wheel,{passive:false});root.addEventListener('pointerover',enter)
  return()=>{root.removeEventListener('wheel',wheel);root.removeEventListener('pointerover',enter)}
}
