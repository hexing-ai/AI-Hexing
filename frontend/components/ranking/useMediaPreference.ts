'use client';
import {useCallback,useSyncExternalStore} from 'react';
export function useMediaPreference(query:string,serverValue=false){
  const subscribe=useCallback((notify:()=>void)=>{const media=matchMedia(query);media.addEventListener('change',notify);return()=>media.removeEventListener('change',notify);},[query]);
  return useSyncExternalStore(subscribe,useCallback(()=>matchMedia(query).matches,[query]),()=>serverValue);
}
