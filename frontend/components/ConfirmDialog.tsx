'use client';
import {createContext,useContext,useEffect,useRef,type ReactNode,type RefObject} from 'react';
const DialogTriggerContext=createContext<RefObject<HTMLElement|null>|null>(null);
export function DialogFocusProvider({children}:{children:ReactNode}){
  const trigger=useRef<HTMLElement|null>(null);
  useEffect(()=>{const remember=(event:FocusEvent)=>{const node=event.target;if(node instanceof HTMLElement&&node!==document.body&&!node.closest('dialog'))trigger.current=node;};document.addEventListener('focusin',remember);return()=>document.removeEventListener('focusin',remember);},[]);
  return <DialogTriggerContext.Provider value={trigger}>{children}</DialogTriggerContext.Provider>;
}
export default function ConfirmDialog({title,children,confirmLabel,onConfirm,onCancel,busy=false,confirmDisabled=false,cancelLabel='取消'}:{title:string;children:ReactNode;confirmLabel:string;onConfirm:()=>void;onCancel:()=>void;busy?:boolean;confirmDisabled?:boolean;cancelLabel?:string}){
  const dialog=useRef<HTMLDialogElement>(null);
  const remembered=useContext(DialogTriggerContext);
  useEffect(()=>{const active=document.activeElement as HTMLElement|null;const trigger=remembered?.current||(active!==document.body?active:null);const node=dialog.current;node?.showModal();return()=>{node?.close();const restore=()=>{if(trigger?.isConnected&&!trigger.hasAttribute('disabled')){trigger.focus();return document.activeElement===trigger;}return false;};const observer=new MutationObserver(()=>{if(restore())observer.disconnect();});observer.observe(document.body,{attributes:true,subtree:true,attributeFilter:['disabled']});requestAnimationFrame(()=>{if(restore())observer.disconnect();});setTimeout(()=>{observer.disconnect();if(document.activeElement===document.body){const fallback=document.querySelector<HTMLElement>('main h1');if(fallback){fallback.tabIndex=-1;fallback.focus();}}},500);};},[remembered]);
  return <dialog ref={dialog} className="cost-dialog" aria-labelledby="action-confirm-title" onCancel={e=>{e.preventDefault();if(!busy)onCancel();}} onKeyDown={e=>{if(e.key!=='Tab')return;const items=Array.from(e.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),a[href]'));if(!items.length)return;e.preventDefault();const i=items.indexOf(document.activeElement as HTMLElement);items[(i+(e.shiftKey?-1:1)+items.length)%items.length].focus();}}><h2 id="action-confirm-title">{title}</h2>{children}<div className="actions"><button disabled={busy} onClick={onCancel}>{cancelLabel}</button><button disabled={busy||confirmDisabled} onClick={onConfirm}>{busy?'正在处理…':confirmLabel}</button></div></dialog>;
}
