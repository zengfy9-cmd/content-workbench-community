import { useEffect, useState } from 'react'
import { api } from './api'
import * as Ic from './icons'

export function PasswordLogin({registered,onDone}:{registered:boolean;onDone:()=>void}) {
  const [code,setCode]=useState(()=>new URLSearchParams(window.location.hash.split('?')[1]||'').get('setup')||'')
  const [username,setUsername]=useState(''),[password,setPassword]=useState(''),[again,setAgain]=useState('')
  const [busy,setBusy]=useState(false),[error,setError]=useState('')
  useEffect(()=>{if(window.location.hash.includes('setup='))window.history.replaceState(null,'',window.location.pathname+window.location.search+'#/')},[])
  async function submit() {
    if(!registered&&password!==again){setError('两次密码不一致');return}
    setBusy(true);setError('')
    try {
      await api.post(`/api/auth/password/${registered?'login':'register'}`,{username,password,...(!registered?{setup_code:code.trim()}:{})})
      setCode('');setPassword('');setAgain('');onDone()
    }catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  return <div className="login-wrap"><form className="card login-card" onSubmit={e=>{e.preventDefault();void submit()}}>
    <span className="brand-mark"><Ic.Pen size={22}/></span><h1>内容工作台</h1>
    <p>{registered?'登录你的账号，继续查看和编辑已保存的文章、图片。':'为你的私有内容库创建一个账号。之后换电脑，用同一账号登录即可。'}</p>
    {!registered&&<label className="field"><span>初始化码</span><input className="input" aria-label="初始化码" type="password" autoComplete="off" value={code} onChange={e=>setCode(e.target.value)} required/></label>}
    <label className="field"><span>账号</span><input className="input" aria-label="账号" placeholder="字母、数字或邮箱" autoComplete="username" autoFocus value={username} onChange={e=>setUsername(e.target.value)} minLength={3} maxLength={80} pattern="[A-Za-z0-9_.@\-]+" required/></label>
    <label className="field"><span>密码{!registered?'（至少12个字符）':''}</span><input className="input" aria-label="密码" type="password" autoComplete={registered?'current-password':'new-password'} value={password} onChange={e=>setPassword(e.target.value)} minLength={registered?1:12} maxLength={256} required/></label>
    {!registered&&<label className="field"><span>再次输入密码</span><input className="input" aria-label="再次输入密码" type="password" autoComplete="new-password" value={again} onChange={e=>setAgain(e.target.value)} minLength={12} maxLength={256} required/></label>}
    <button className="btn primary lg" style={{marginTop:16}} disabled={busy}>{busy?'正在处理…':registered?'登录工作台':'创建账号并进入工作台'}</button>
    {error&&<p className="err" role="alert">{error}</p>}
  </form></div>
}
