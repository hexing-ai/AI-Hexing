'use client';

import Link from 'next/link';
import {useEffect,useRef, type ReactNode} from 'react';
import WindowLogin from './WindowLogin';
import {DialogFocusProvider} from './ConfirmDialog';

const navigation = [
  {href:'/tasks', label:'创作任务', icon:'file'},
  {href:'/discover', label:'发现工具', icon:'discover'},
  {href:'/voices', label:'我的声音', icon:'voice'},
  {href:'/ranking', label:'工具封神榜', icon:'trophy'},
] as const;
const utilities = [
  {href:'/costs', label:'费用记录', icon:'wallet'},
  {href:'/activity', label:'处理记录', icon:'clock'},
  {href:'/validation', label:'接入设置', icon:'settings'},
] as const;

export function WorkspaceIcon({name}:{name:string}) {
  const paths:Record<string,ReactNode> = {
    discover:<><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
    file:<><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6M8 13h8M8 17h6"/></>,
    voice:<path d="M4 10v4m4-8v12m4-15v18m4-15v12m4-8v4"/>,
    trophy:<><path d="M7 3h10v6a5 5 0 0 1-10 0ZM7 5H3v3a4 4 0 0 0 5 4m9-7h4v3a4 4 0 0 1-5 4M12 14v7m-5 0h10"/></>,
    wallet:<><rect x="3" y="5" width="18" height="15" rx="3"/><path d="M3 9h18m-5 5h5M7 15h3"/></>,
    clock:<><circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/></>,
    settings:<><circle cx="12" cy="12" r="4"/><path d="m9 3-1 3-3 1v4l-2 1 2 2v3l3 1 1 3h5l1-3 3-1v-3l3-2-3-2V7l-3-1-1-3Z"/></>,
    arrow:<path d="M19 12H5m6-6-6 6 6 6"/>,
    plus:<path d="M12 5v14M5 12h14"/>,
  };
  return <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]||paths.file}</svg>;
}

export default function WorkspaceFrame({page,mode,onLogout,children}:{page:string;mode?:string;onLogout:()=>void;children:ReactNode}) {
  const menu=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const media=matchMedia('(min-width: 851px)');const closeOnDesktop=()=>{if(media.matches)menu.current?.close();};media.addEventListener('change',closeOnDesktop);return()=>media.removeEventListener('change',closeOnDesktop);},[]);
  const close=()=>menu.current?.close();
  const title=[...navigation,...utilities].find(item=>item.href==='/'+page)?.label||'创作工作台';
  const links=(items:readonly {href:string;label:string;icon:string}[])=>items.map(item=><Link key={item.href} href={item.href} className={item.href==='/'+page?'active':''} aria-current={item.href==='/'+page?'page':undefined} onClick={close}><WorkspaceIcon name={item.icon}/>{item.label}</Link>);
  const brand=<Link href="/tasks" className="workspace-brand" onClick={close}><span className="brand-symbol" aria-hidden="true">H</span>AI-Hexing</Link>;
  const sidebar=<>{brand}<span className="nav-caption">创作空间</span><nav aria-label="工作台导航">{links(navigation)}</nav><div className="workspace-utilities"><nav aria-label="辅助导航">{links(utilities)}</nav><div className="workspace-account"><span className="account-avatar" aria-hidden="true">H</span><span>我的工作台</span><button className="text-button" onClick={onLogout}>退出</button></div></div></>;
  return <DialogFocusProvider><div className="shell hexing-workspace">
    <a className="skip-link" href="#workspace-main">跳到主要内容</a>
    <aside className="sidebar desktop-sidebar">{sidebar}</aside>
    <dialog className="mobile-navigation" ref={menu} aria-label="工作台菜单" onKeyDown={e=>{if(e.key!=='Tab')return;const nodes=Array.from(e.currentTarget.querySelectorAll<HTMLElement>('a[href],button:not(:disabled)'));e.preventDefault();const i=nodes.indexOf(document.activeElement as HTMLElement);nodes[(i+(e.shiftKey?-1:1)+nodes.length)%nodes.length]?.focus();}} onClick={e=>{if(e.target===e.currentTarget)close();}}><div className="mobile-navigation-inner"><button className="menu-close" onClick={close} aria-label="关闭导航">×</button>{sidebar}</div></dialog>
    <main className="workspace" id="workspace-main" tabIndex={-1}>
      <header className="topbar"><div className="topbar-location"><button className="mobile-menu-button" onClick={()=>menu.current?.showModal()} aria-label="打开导航">☰</button><span>{title}</span>{page==='tasks'&&<span className="breadcrumb-tail">/ 创作工作台</span>}</div><div className="topbar-actions"><span className={`mode ${mode==='mock'?'mock':''}`}><span className="mode-dot"/>{mode==='mock'?'模拟模式':'真实服务'}</span><details className="connection-menu"><summary>窗口连接</summary><WindowLogin loggedIn/></details></div></header>
      <div className={`content workspace-page-${page}`}>{children}</div>
    </main>
  </div></DialogFocusProvider>;
}
