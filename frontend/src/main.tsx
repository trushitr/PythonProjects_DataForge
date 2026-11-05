import React, {useCallback, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';
import {apiFetch, clearToken, currentUser, downloadReport, getToken, onTokenChangedElsewhere, SessionExpiredError, setSessionExpiredHandler, tokenExpiresAt, UNREACHABLE, User} from './api';
import {AuthPage} from './AuthPage';
type Summary={id:string;filename:string;created_at:string;total_rows:number;valid_rows:number;invalid_rows:number;duplicate_rows:number};
type Result={row_number:number;values:Record<string,string>;errors:string[];valid:boolean};
type Detail=Summary & {columns:string[];results:Result[]};
function App({user,onLogout}:{user:User;onLogout:()=>void}){
 const [imports,setImports]=useState<Summary[]>([]),[selected,setSelected]=useState<Detail|null>(null),[file,setFile]=useState<File|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const fileInput=useRef<HTMLInputElement>(null); // cleared after upload so the same file can be chosen again
 const refresh=async()=>{try{const r=await apiFetch('/api/imports');if(!r.ok)throw Error('Cannot load imports');setImports(await r.json());}catch(e){setError(String(e));}};
 useEffect(()=>{void refresh()},[]);
 const open=async(id:string)=>{try{const r=await apiFetch(`/api/imports/${id}`);if(!r.ok)throw Error('Cannot load details');setSelected(await r.json());setError('')}catch(e){setError(String(e))}};
 const upload=async()=>{if(!file)return;setBusy(true);setError('');try{const form=new FormData();form.append('file',file);const r=await apiFetch('/api/imports',{method:'POST',body:form});const data=await r.json();if(!r.ok)throw Error(data.detail||'Upload failed');await refresh();await open(data.id);setFile(null);if(fileInput.current)fileInput.current.value='';}catch(e){setError(String(e))}finally{setBusy(false)}};
 const download=async()=>{if(!selected)return;try{await downloadReport(selected.id);setError('')}catch(e){setError(String(e))}};
 return <main><header><span className="brand">◆ DataForge</span><span>Data import & validation</span><span className="account"><span>{user.email}</span><button className="secondary" onClick={onLogout}>Log out</button></span></header><section className="hero"><h1>Make messy data usable.</h1><p>Upload CSV or Excel files, find missing values and duplicate rows, and download a validation report.</p></section><section className="panel"><h2>New import</h2><div className="upload"><input ref={fileInput} aria-label="Choose CSV or XLSX" type="file" accept=".csv,.xlsx" onChange={e=>setFile(e.target.files?.[0]||null)}/><button disabled={!file||busy} onClick={upload}>{busy?'Validating…':'Upload & validate'}</button></div><small>Maximum 5 MB and 10,000 rows. All columns are required in this baseline version.</small>{error&&<p className="error" role="alert">{error}</p>}</section><section className="panel"><h2>Recent imports</h2>{imports.length===0?<p>No imports yet. Try the sample CSV.</p>:<div className="tablewrap"><table><thead><tr><th>File</th><th>Rows</th><th>Valid</th><th>Invalid</th><th>Duplicates</th><th></th></tr></thead><tbody>{imports.map(item=><tr key={item.id}><td>{item.filename}</td><td>{item.total_rows}</td><td>{item.valid_rows}</td><td>{item.invalid_rows}</td><td>{item.duplicate_rows}</td><td><button className="secondary" onClick={()=>open(item.id)}>View</button></td></tr>)}</tbody></table></div>}</section>{selected&&<section className="panel"><div className="split"><div><h2>{selected.filename}</h2><p>{selected.valid_rows} valid / {selected.invalid_rows} invalid</p></div><button className="download" onClick={download}>Download CSV report</button></div><div className="tablewrap"><table><thead><tr><th>Row</th>{selected.columns.map(c=><th key={c}>{c}</th>)}<th>Validation</th></tr></thead><tbody>{selected.results.slice(0,100).map(r=><tr key={r.row_number}><td>{r.row_number}</td>{selected.columns.map(c=><td key={c}>{r.values[c]}</td>)}<td className={r.valid?'good':'bad'}>{r.valid?'Valid':r.errors.join('; ')}</td></tr>)}</tbody></table></div><small>Showing first 100 rows. Download the report for all results.</small></section>}</main>
}
const EXPIRED='Your session has expired. Please sign in again.';
const MAX_TIMEOUT=2147483647; // setTimeout's upper limit (~24.8 days)
function Root(){
 const [user,setUser]=useState<User|null>(null),[checking,setChecking]=useState(()=>getToken()!==null),[notice,setNotice]=useState('');
 const logout=useCallback((message:string)=>{clearToken();setUser(null);setNotice(message);},[]);
 // Any API call rejected with 401 (expired or invalid token) returns to the sign-in page.
 useEffect(()=>{setSessionExpiredHandler(()=>logout(EXPIRED));},[logout]);
 // Restore a saved session on page load by asking the API who the token belongs to.
 useEffect(()=>{
  if(!getToken())return;
  currentUser().then(setUser).catch(e=>{if(!(e instanceof SessionExpiredError))setNotice(UNREACHABLE);}).finally(()=>setChecking(false));
 },[]);
 // Sign out as soon as the token expires, even if the page is just left open.
 useEffect(()=>{
  const expiresAt=user&&tokenExpiresAt();
  if(!expiresAt)return;
  const timer=setTimeout(()=>logout(EXPIRED),Math.min(Math.max(expiresAt-Date.now(),0),MAX_TIMEOUT));
  return ()=>clearTimeout(timer);
 },[user,logout]);
 // Keep tabs in sync: logging out (or in as someone else) in one tab applies to all.
 useEffect(()=>onTokenChangedElsewhere(()=>{
  if(!getToken()){setUser(null);setNotice('You were signed out in another tab.');return;}
  currentUser().then(setUser).catch(()=>{});
 }),[]);
 if(checking)return <main><header><span className="brand">◆ DataForge</span></header><p>Loading…</p></main>;
 // key={user.id}: a different user always gets a fresh dashboard with no leftover state.
 return user?<App key={user.id} user={user} onLogout={()=>logout('You have been signed out.')}/>:<AuthPage notice={notice} onAuthenticated={u=>{setNotice('');setUser(u);}}/>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><Root/></React.StrictMode>);
