import { useCallback, useRef, useState } from 'react'

/** Local recovery is not a business save or an approval. */
export function useLocalDraft<T>(key: string, initial: T) {
  const read=():T=>{try { const saved=JSON.parse(localStorage.getItem(key)||'null'); return saved && typeof saved==='object' && !Array.isArray(saved) ? {...initial,...saved} : initial } catch { return initial }}
  const [state,setState]=useState(()=>({key,value:read()}))
  const value=state.key===key?state.value:read()
  if(state.key!==key)setState({key,value})
  const current=useRef(value)
  current.current=value
  const resetValue=useRef(initial)
  resetValue.current=initial
  const update=useCallback((next: T | ((old: T) => T)) => {
    const result=typeof next==='function' ? (next as (old:T)=>T)(current.current) : next
    current.current=result
    try { localStorage.setItem(key,JSON.stringify(result)) } catch { window.dispatchEvent(new Event('cw-recovery-error')) }
    setState({key,value:result})
  },[key])
  const clear=useCallback(()=>{try { localStorage.removeItem(key) } catch { /* Storage unavailable. */ } current.current=resetValue.current;setState({key,value:resetValue.current})},[key])
  return [value,update,clear] as const
}
