export const API='http://127.0.0.1:8000';

export type User={id:string;email:string;created_at:string};

// The JWT is kept in localStorage so a page refresh keeps you signed in.
// Trade-off (agreed for this local-first app): any script running on the page
// could read it, so never add untrusted third-party scripts.
const TOKEN_KEY='dataforge.token';
const EXPIRES_KEY='dataforge.tokenExpiresAt';

export function getToken():string|null{
 const token=localStorage.getItem(TOKEN_KEY);
 const expiresAt=Number(localStorage.getItem(EXPIRES_KEY));
 if(!token||!expiresAt||Date.now()>=expiresAt){clearToken();return null;}
 return token;
}
export function tokenExpiresAt():number|null{return getToken()?Number(localStorage.getItem(EXPIRES_KEY)):null;}
function saveToken(token:string,expiresInSeconds:number){
 localStorage.setItem(TOKEN_KEY,token);
 localStorage.setItem(EXPIRES_KEY,String(Date.now()+expiresInSeconds*1000));
}
export function clearToken(){localStorage.removeItem(TOKEN_KEY);localStorage.removeItem(EXPIRES_KEY);}
/** Fires when another browser tab logs in or out. */
export function onTokenChangedElsewhere(handler:()=>void){
 const listener=(e:StorageEvent)=>{if(e.key===TOKEN_KEY||e.key===null)handler();};
 window.addEventListener('storage',listener);
 return ()=>window.removeEventListener('storage',listener);
}

// Set by the app shell: called when the API rejects our token (expired, revoked, user deleted).
let sessionExpiredHandler:()=>void=()=>{};
export function setSessionExpiredHandler(handler:()=>void){sessionExpiredHandler=handler;}

export const UNREACHABLE='Cannot reach the DataForge server. Is the backend running?';
/** fetch() that reports a stopped/unreachable backend clearly instead of "Failed to fetch". */
async function send(url:string,init:RequestInit):Promise<Response>{
 try{return await fetch(url,init);}catch{throw Error(UNREACHABLE);}
}

export class SessionExpiredError extends Error{constructor(){super('Your session has expired. Please sign in again.');}}

/** fetch() for authenticated API calls: adds the bearer token and handles 401. */
export async function apiFetch(path:string,init:RequestInit={}):Promise<Response>{
 const token=getToken();
 if(!token){sessionExpiredHandler();throw new SessionExpiredError();}
 const headers=new Headers(init.headers);
 headers.set('Authorization',`Bearer ${token}`);
 const r=await send(`${API}${path}`,{...init,headers});
 if(r.status===401){clearToken();sessionExpiredHandler();throw new SessionExpiredError();}
 return r;
}

/** Turns FastAPI error bodies (string detail or 422 validation list) into one message. */
async function errorMessage(r:Response,fallback:string):Promise<string>{
 try{
  const body=await r.json();
  if(typeof body.detail==='string')return body.detail;
  if(Array.isArray(body.detail))return body.detail.map((d:{loc?:unknown[];msg?:string})=>{
   const field=d.loc?.[d.loc.length-1];
   return typeof field==='string'&&field!=='body'?`${field}: ${d.msg}`:String(d.msg);
  }).join('; ');
 }catch{/* not JSON */}
 return fallback;
}

export async function login(email:string,password:string):Promise<User>{
 // OAuth2 password form: the email goes in the "username" field.
 const r=await send(`${API}/api/auth/login`,{method:'POST',body:new URLSearchParams({username:email,password})});
 if(!r.ok)throw Error(await errorMessage(r,'Sign in failed'));
 const data=await r.json();
 saveToken(data.access_token,data.expires_in);
 return currentUser();
}

export async function register(email:string,password:string):Promise<User>{
 const r=await send(`${API}/api/auth/register`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password})});
 if(!r.ok)throw Error(await errorMessage(r,'Registration failed'));
 return login(email,password);
}

export async function currentUser():Promise<User>{
 const r=await apiFetch('/api/auth/me');
 if(!r.ok)throw Error(await errorMessage(r,'Cannot load your account'));
 return r.json();
}

/** Report downloads need the Authorization header, which a plain <a href> cannot send. */
export async function downloadReport(importId:string){
 const r=await apiFetch(`/api/imports/${importId}/report.csv`);
 if(!r.ok)throw Error(await errorMessage(r,'Cannot download report'));
 const url=URL.createObjectURL(await r.blob());
 const a=document.createElement('a');
 a.href=url;a.download=`dataforge-${importId}.csv`;
 document.body.appendChild(a);a.click();a.remove();
 setTimeout(()=>URL.revokeObjectURL(url),1000);
}
