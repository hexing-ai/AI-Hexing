'use client';

import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';

export default function WindowLogin({loggedIn}:{loggedIn:boolean}) {
  const [code,setCode]=useState('');
  const [request,setRequest]=useState<{code:string;expiresAt:number}|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const lock=useRef(false);

  useEffect(()=>{
    if(!request)return;
    let stopped=false; let polling=false;
    const poll=async()=>{
      if(polling||stopped)return;
      if(Date.now()>=request.expiresAt){setRequest(null);setError('配对码已过期，请重新生成。');return;}
      polling=true;
      try {
        const result=await api<{status:string}>('/auth/window/complete',{});
        if(!stopped&&result.status==='connected')window.location.reload();
      } catch(e) {
        if(!stopped){setRequest(null);setError((e as Error).message);}
      } finally {polling=false;}
    };
    void poll();const timer=setInterval(()=>void poll(),2000);
    return()=>{stopped=true;clearInterval(timer);};
  },[request]);

  async function act(fn:()=>Promise<void>){
    if(lock.current)return;
    lock.current=true;setBusy(true);setError('');setNotice('');
    try{await fn();}catch(e){setError((e as Error).message);}finally{lock.current=false;setBusy(false);}
  }

  return <details className="muted-box window-login"><summary>{loggedIn?'连接另一个窗口':'已在其他窗口登录？'}</summary>
    {loggedIn?<>
      <p>输入目标窗口显示的配对码，允许该窗口使用你的账户访问稿件、声音和创作功能。仅确认你本人正在连接的窗口。</p>
      <label>目标窗口配对码<input value={code} maxLength={12} autoComplete="off" placeholder="XXXX-XXXX" onChange={e=>setCode(e.target.value.toUpperCase())}/></label>
      <button type="button" disabled={busy||code.replace(/[-\s]/g,'').length!==8} onClick={()=>void act(async()=>{
        await api('/auth/window/approve',{code:code.trim(),accepted:true});setCode('');setNotice('已授权，目标窗口将自动进入工作台。');
      })}>确认连接这个窗口</button>
    </>:<>
      <p>在已登录的工作台展开“连接另一个窗口”，输入下方配对码并确认。无需退出或再次输入密码。</p>
      {request?<>
        <p>本窗口配对码：<strong data-testid="window-pair-code">{request.code}</strong></p>
        <p role="status">等待已登录窗口确认，5 分钟内有效。授权后本窗口自动进入工作台。</p>
        <button type="button" disabled={busy} onClick={()=>void act(async()=>{await api('/auth/window/cancel',{});setRequest(null);})}>取消窗口连接</button>
      </>:<button type="button" disabled={busy} onClick={()=>void act(async()=>{
        const value=await api<{code:string;expires_in:number}>('/auth/window/request',{});
        setRequest({code:value.code,expiresAt:Date.now()+value.expires_in*1000});
      })}>生成本窗口配对码</button>}
    </>}
    {notice&&<p role="status" className="notice">{notice}</p>}{error&&<p role="alert" className="error">{error}</p>}
  </details>;
}
