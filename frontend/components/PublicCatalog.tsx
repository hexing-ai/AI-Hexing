'use client';

import Link from 'next/link';
import {useCallback,useEffect,useRef,useState} from 'react';
import {publicEvent,publicRead,PublicReadError,type PublicPage,type PublicTool} from '../lib/public-api';
import BotScene from './ranking/BotScene';
import BotAvatar from './ranking/BotAvatar';
import {useMediaPreference} from './ranking/useMediaPreference';
import './ranking/ranking.css';

const REFRESH_MS=30000;
const STALE_MS=60000;
const verify={verified:'最近来源核验有效，当前能否访问以 GitHub 为准。',unavailable:'最近核验暂不可访问。',unknown:'尚未核实。'};

export default function PublicCatalog(){
  const [view,setView]=useState<'scene'|'list'>('scene');
  const [paused,setPaused]=useState(false);const [sceneGroup,setSceneGroup]=useState(0);
  const reduced=useMediaPreference('(prefers-reduced-motion: reduce)',true);
  const compact=useMediaPreference('(max-width: 850px)');
  const [motionError,setMotionError]=useState(false);const [arrival,setArrival]=useState('');
  const observed=useRef<{since:number;ids:Set<string>}>({since:0,ids:new Set()});
  const [page,setPage]=useState<PublicPage|null>(null);
  const [cursors,setCursors]=useState<(string|null)[]>([null]);
  const [selected,setSelected]=useState<PublicTool|null>(null);
  const [busy,setBusy]=useState(false);
  const [opening,setOpening]=useState(false);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const [listStale,setListStale]=useState(false);
  const [detailStale,setDetailStale]=useState(false);
  const dialog=useRef<HTMLDialogElement>(null);
  const heading=useRef<HTMLHeadingElement>(null);
  const origin=useRef<HTMLButtonElement|null>(null);
  const selectedId=useRef<string|null>(null);
  const pendingIntro=useRef<string|null>(null);
  const detailRequest=useRef(0);
  const listRequest=useRef(0);
  const lastList=useRef(0);
  const lastDetail=useRef(0);
  const alive=useRef(true);
  const cursor=cursors[cursors.length-1];

  const close=useCallback(()=>{
    selectedId.current=null;pendingIntro.current=null;detailRequest.current++;
    setSelected(null);setOpening(false);dialog.current?.close();
    if(origin.current?.isConnected)origin.current.focus();else heading.current?.focus();
  },[]);

  const refreshDetail=useCallback(async(id:string)=>{
    const seq=++detailRequest.current;
    try{
      const data=await publicRead<PublicTool>('/'+encodeURIComponent(id));
      if(!alive.current||seq!==detailRequest.current||selectedId.current!==id)return;
      lastDetail.current=Date.now();setDetailStale(false);setSelected(data);setOpening(false);
      if(pendingIntro.current===id){pendingIntro.current=null;publicEvent(data,'intro_open');}
    }catch(e){
      if(!alive.current||seq!==detailRequest.current)return;
      if((e as PublicReadError).status===404){
        close();setPage(old=>old?{...old,items:old.items.filter(t=>t.id!==id)}:old);
        setNotice('该工具已下架，介绍已关闭。');
      }else{
        setOpening(false);setDetailStale(true);setNotice('介绍暂时无法核对，请刷新后再查看。');
      }
    }
  },[close]);

  const refresh=useCallback(async()=>{
    const seq=++listRequest.current;setBusy(true);
    try{
      const data=await publicRead<PublicPage>('?limit=30'+(cursor?'&cursor='+encodeURIComponent(cursor):''));
      if(!alive.current||seq!==listRequest.current)return;
      if(observed.current.since){const additions=data.items.filter(t=>!observed.current.ids.has(t.id)&&t.first_published_at&&new Date(t.first_published_at).getTime()>observed.current.since);if(additions.length)setArrival('新伙伴入榜：'+additions.slice(0,3).map(t=>t.name).join('、')+(additions.length>3?' 等 '+additions.length+' 个工具':''));}
      else observed.current.since=Date.now();
      data.items.forEach(t=>observed.current.ids.add(t.id));
      lastList.current=Date.now();setListStale(false);setPage(data);setError('');
    }catch(e){if(alive.current&&seq===listRequest.current)setError((e as Error).message);}
    finally{if(alive.current&&seq===listRequest.current)setBusy(false);}
  },[cursor]);

  useEffect(()=>{
    alive.current=true;
    const invalidate=()=>{alive.current=false;listRequest.current++;detailRequest.current++;};
    const checkAge=()=>{
      const now=Date.now();
      if(lastList.current&&now-lastList.current>=STALE_MS)setListStale(true);
      if(lastDetail.current&&now-lastDetail.current>=STALE_MS)setDetailStale(true);
    };
    const sync=()=>{
      checkAge();
      if(document.visibilityState==='hidden')return;
      void refresh();if(selectedId.current)void refreshDetail(selectedId.current);
    };
    sync();
    const interval=setInterval(sync,REFRESH_MS);
    const expiry=setInterval(checkAge,1000);
    window.addEventListener('focus',sync);window.addEventListener('online',sync);
    const visibility=()=>{
      // A suspended page must not expose old details for a frame when restored.
      if(document.visibilityState==='hidden'){setListStale(true);setDetailStale(true);}
      else sync();
    };
    document.addEventListener('visibilitychange',visibility);
    return()=>{
      invalidate();
      clearInterval(interval);clearInterval(expiry);
      window.removeEventListener('focus',sync);window.removeEventListener('online',sync);
      document.removeEventListener('visibilitychange',visibility);
    };
  },[refresh,refreshDetail]);

  useEffect(()=>{if(selected&&!dialog.current?.open)dialog.current?.showModal();},[selected]);

  function open(tool:PublicTool,button:HTMLButtonElement){
    origin.current=button;selectedId.current=tool.id;pendingIntro.current=tool.id;
    setNotice('');setOpening(true);setSelected(null);setDetailStale(true);
    void refreshDetail(tool.id);
  }
  function turn(next:boolean){
    close();setPage(null);setListStale(false);lastList.current=0;
    setSceneGroup(0);setArrival('');observed.current={since:0,ids:new Set()};
    setCursors(old=>next?[...old,page?.next_cursor??null]:old.slice(0,-1));
  }

  const groupSize=compact?12:30;const group=Math.min(sceneGroup,Math.max(0,Math.ceil((page?.items.length||0)/groupSize)-1));
  const shown=page?.items.slice(group*groupSize,(group+1)*groupSize)||[];
  return <main className="public-catalog bot-catalog">
    <header className="catalog-header"><Link className="catalog-wordmark" href="/display" aria-label="AI-Hexing 工具封神榜">H<span>AI-Hexing</span></Link><span className="catalog-edition">人工精选 · 持续收录</span></header>
    <div className="catalog-title"><div><p className="catalog-kicker">让好工具，被看见。</p><h1 ref={heading} tabIndex={-1}>工具封神榜<span className="title-dot">.</span></h1><p>每一个小 Bot，带你认识一个好工具。</p></div><div className="catalog-controls"><div className="view-switch" aria-label="浏览方式"><button aria-pressed={view==='scene'} onClick={()=>setView('scene')}>Bot 场景</button><button aria-pressed={view==='list'} onClick={()=>setView('list')}>工具列表</button></div><button disabled={reduced||view==='list'} aria-pressed={paused||reduced} onClick={()=>setPaused(!paused)}>{reduced?'已减少动效':paused?'继续漂浮':'暂停漂浮'}</button><button disabled={busy} onClick={()=>{void refresh();if(selectedId.current)void refreshDetail(selectedId.current);}}>刷新工具列表</button></div></div>
    {arrival&&<p className="catalog-arrival" role="status">{arrival}<button aria-label="关闭入榜提示" onClick={()=>setArrival('')}>×</button></p>}
    {motionError&&<p className="catalog-message" role="status">动效未能启动，可点名称或切换工具列表继续浏览。</p>}
    {busy&&!page&&<p role="status">正在读取公开工具…</p>}
    {error&&<p role="alert">{error}</p>}
    {notice&&<p role="status">{notice}</p>}
    {opening&&<p role="status">正在核对最新介绍…</p>}
    {listStale?<p role="status">公开资料已过期，刷新成功后继续查看。</p>:page&&<>
      {page.items.length===0?<section className="catalog-empty"><span className="empty-constellation" aria-hidden="true">✦</span><h2>{cursors.length===1?'好工具，正在集结。':'这一页暂时空了。'}</h2><p>{cursors.length===1?'暂未公开工具。精选并确认后，它们会在这里与你见面。':'本页暂无公开工具，可返回上一页。'}</p></section>:view==='scene'?<><BotScene key={cursor+'-'+groupSize+'-'+group} tools={shown} paused={paused||reduced||!!selected||opening} onOpen={open} onFailure={()=>setMotionError(true)}/>{page.items.length>groupSize&&<div className="scene-pages"><button disabled={group===0} onClick={()=>setSceneGroup(group-1)}>上一组 Bot</button><span>本页第 {group*groupSize+1}–{Math.min((group+1)*groupSize,page.items.length)} 个</span><button disabled={(group+1)*groupSize>=page.items.length} onClick={()=>setSceneGroup(group+1)}>下一组 Bot</button></div>}</>:<ul className="public-tool-list" aria-label="已公开工具">{page.items.map(tool=><li key={tool.id}><button onClick={e=>open(tool,e.currentTarget)} aria-haspopup="dialog"><BotAvatar avatar={tool.bot_avatar} still/><div><strong>{tool.name}</strong>{tool.one_line_use&&<span>{tool.one_line_use}</span>}{tool.category&&<small>{tool.category}</small>}</div><span aria-hidden="true">↗</span></button></li>)}</ul>}
    </>}
    <footer className="catalog-footer"><p>人工精选目录 · 形象与大小不代表排名</p><nav className="actions" aria-label="工具列表翻页"><span>第 {cursors.length} 页{page?' · '+page.items.length+' 个工具':''}</span><button disabled={busy||cursors.length===1} onClick={()=>turn(false)}>上一页</button><button disabled={busy||listStale||!page?.next_cursor} onClick={()=>turn(true)}>下一页</button></nav></footer>
    <dialog ref={dialog} className="public-tool-dialog" aria-labelledby="public-tool-title" onCancel={e=>{e.preventDefault();close();}} onKeyDown={e=>{if(e.key!=='Tab')return;const nodes=Array.from(e.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),a[href]'));e.preventDefault();const i=nodes.indexOf(document.activeElement as HTMLElement);nodes[(i+(e.shiftKey?-1:1)+nodes.length)%nodes.length]?.focus();}}>
      <button className="intro-close" autoFocus onClick={close} aria-label="关闭工具介绍">关闭 ×</button>
      {!detailStale&&selected&&<div className="intro-avatar"><BotAvatar avatar={selected.bot_avatar} mood="happy" still/></div>}
      <h2 id="public-tool-title">{detailStale?'介绍待刷新':selected?.name}</h2>
      {detailStale?<><p>介绍已过期或暂时无法核对，刷新后再打开仓库。</p><button onClick={()=>{if(selectedId.current)void refreshDetail(selectedId.current);}}>刷新工具介绍</button></>:selected&&<>
        {selected.one_line_use&&<p>{selected.one_line_use}</p>}
        <ul>{selected.highlights.map((h,i)=><li key={i}>{h}</li>)}</ul>
        <a href={selected.github_url} target="_blank" rel="noopener noreferrer" onClick={e=>{
          if(Date.now()-lastDetail.current>=STALE_MS){e.preventDefault();setDetailStale(true);void refreshDetail(selected.id);return;}
          publicEvent(selected,'github_click');
        }} onAuxClick={e=>{if(e.button===1){if(Date.now()-lastDetail.current>=STALE_MS){e.preventDefault();setDetailStale(true);}else publicEvent(selected,'github_click');}}}>打开 GitHub 仓库 ↗</a>
        <p className="public-verified">{verify[selected.verification_status]}<br/>最后核实：{selected.last_verified_at?new Date(selected.last_verified_at).toLocaleString('zh-CN'):'未知'}</p>
      </>}
    </dialog>
  </main>;
}
