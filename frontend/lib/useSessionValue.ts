'use client';
import {useCallback, useSyncExternalStore} from 'react';
const event='hexing-session-value';
function subscribe(notify:()=>void){window.addEventListener('storage',notify);window.addEventListener(event,notify);return()=>{window.removeEventListener('storage',notify);window.removeEventListener(event,notify);};}
export function useSessionValue(key:string){
  const value=useSyncExternalStore(subscribe,useCallback(()=>{try{return sessionStorage.getItem(key);}catch{return null;}},[key]),()=>null);
  const set=useCallback((next:string|null)=>{if(next===null)sessionStorage.removeItem(key);else sessionStorage.setItem(key,next);window.dispatchEvent(new Event(event));},[key]);
  return [value,set] as const;
}
