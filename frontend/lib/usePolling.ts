'use client';
import {useCallback, useEffect, useRef, useState} from 'react';
import {api} from './api';

/** Read-only polling: one request per resource, stale responses ignored, no POST retry. */
export function usePolling<T>(path:string|null, interval=3000, read:(path:string)=>Promise<T>=api) {
  const [state,setState]=useState<{path:string|null;data:T|null;error:string}>({path,data:null,error:''});
  const context=useRef<{path:string|null;alive:boolean;pending:Promise<T>|null;failures:number}>({path,alive:false,pending:null,failures:0});
  const refresh=useCallback(async():Promise<T>=>{
    const current=context.current;
    if(!path||current.path!==path||!current.alive)throw new Error('读取页面已切换。');
    if(current.pending)return current.pending;
    current.pending=read(path).then(data=>{
      if(current.alive){current.failures=0;setState({path,data,error:''});}return data;
    }).catch(e=>{if(current.alive){current.failures++;setState(old=>({path,data:old.path===path?old.data:null,error:(e as Error).message}));}throw e;}).finally(()=>{current.pending=null;});
    return current.pending;
  },[path,read]);
  useEffect(()=>{
    const current={path,alive:true,pending:null as Promise<T>|null,failures:0};context.current=current;
    if(!path)return()=>{current.alive=false;};
    let timer:ReturnType<typeof setTimeout>;let running=false;
    const tick=async()=>{
      clearTimeout(timer);
      if(!current.alive||document.hidden||running)return;
      running=true;
      try{await refresh();}catch{/* surfaced with previous content */}
      running=false;
      if(current.alive&&!document.hidden)timer=setTimeout(tick,Math.min(30000,interval*2**Math.min(current.failures,4)));
    };
    const visible=()=>{clearTimeout(timer);if(!document.hidden)void tick();};
    void tick();document.addEventListener('visibilitychange',visible);window.addEventListener('online',visible);
    return()=>{current.alive=false;clearTimeout(timer);document.removeEventListener('visibilitychange',visible);window.removeEventListener('online',visible);};
  },[path,interval,refresh]);
  return {data:state.path===path?state.data:null,error:state.path===path?state.error:'',refresh};
}
