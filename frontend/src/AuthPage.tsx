import React, {useState} from 'react';
import {login, register, User} from './api';

type Mode='login'|'register';

export function AuthPage({onAuthenticated,notice}:{onAuthenticated:(user:User)=>void;notice?:string}){
 const [mode,setMode]=useState<Mode>('login'),[email,setEmail]=useState(''),[password,setPassword]=useState(''),[confirm,setConfirm]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const isRegister=mode==='register';
 const switchMode=(next:Mode)=>{setMode(next);setError('');setPassword('');setConfirm('');};
 const submit=async(e:React.FormEvent)=>{
  e.preventDefault();
  if(isRegister&&password!==confirm){setError('Passwords do not match');return;}
  setBusy(true);setError('');
  try{onAuthenticated(isRegister?await register(email.trim(),password):await login(email.trim(),password));}
  catch(err){setError(err instanceof Error?err.message:String(err));setBusy(false);}
 };
 return <main><header><span className="brand">◆ DataForge</span><span>Data import & validation</span></header>
  <section className="panel auth">
   <h2>{isRegister?'Create your account':'Sign in'}</h2>
   {notice&&!error&&<p className="notice" role="status">{notice}</p>}
   <form onSubmit={submit}>
    <label>Email<input type="email" autoComplete="email" required value={email} onChange={e=>setEmail(e.target.value)}/></label>
    <label>Password<input type="password" autoComplete={isRegister?'new-password':'current-password'} required minLength={isRegister?8:undefined} maxLength={128} value={password} onChange={e=>setPassword(e.target.value)}/></label>
    {isRegister&&<label>Confirm password<input type="password" autoComplete="new-password" required value={confirm} onChange={e=>setConfirm(e.target.value)}/></label>}
    {isRegister&&<small>At least 8 characters.</small>}
    {error&&<p className="error" role="alert">{error}</p>}
    <button type="submit" disabled={busy}>{busy?'Please wait…':isRegister?'Create account':'Sign in'}</button>
   </form>
   <p className="switch">{isRegister?'Already have an account? ':'New to DataForge? '}
    <button type="button" className="link" onClick={()=>switchMode(isRegister?'login':'register')}>{isRegister?'Sign in':'Create an account'}</button></p>
  </section>
 </main>;
}
