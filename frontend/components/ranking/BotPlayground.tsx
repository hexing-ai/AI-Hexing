'use client';
import {useEffect,useRef,useState} from 'react';
import type {PublicTool} from '../../lib/public-api';
import {workspaceCollection} from '../../lib/curatedTools';
import BotScene from './BotScene';
import BotAvatar from './BotAvatar';
import {useMediaPreference} from './useMediaPreference';

export default function BotPlayground({tools=[],loading=false,onManage}:{tools?:PublicTool[];loading?:boolean;onManage?:(id?:string)=>void}){
  const [paused,setPaused]=useState(false),[group,setGroup]=useState(0),[selected,setSelected]=useState<PublicTool|null>(null);
  const reduced=useMediaPreference('(prefers-reduced-motion: reduce)',true),compact=useMediaPreference('(max-width:850px)');
  const origin=useRef<HTMLButtonElement|null>(null),dialog=useRef<HTMLDialogElement>(null);
  const list=workspaceCollection(tools),size=compact?12:30;
  const editable=!!selected&&tools.some(tool=>tool.id===selected.id);
  const page=Math.min(group,Math.max(0,Math.ceil(list.length/size)-1));
  useEffect(()=>{if(selected){const node=dialog.current;node?.showModal();node?.querySelector<HTMLElement>('#scene-intro-name')?.focus({preventScroll:true});if(node)node.scrollTop=0;}},[selected]);
  function close(){dialog.current?.close();setSelected(null);origin.current?.focus();}
  return <section className="bot-playground" aria-label="工具 Bot 场景">
    {loading?<div className="scene-loading" role="status">正在召集 Bot…</div>:<BotScene key={size+'-'+page} tools={list.slice(page*size,(page+1)*size)} paused={paused||reduced||!!selected} onOpen={(tool,button)=>{origin.current=button;setSelected(tool);}} onFailure={()=>{}}/>}
    <footer className="bot-scene-footer"><span>精选工具 <b>{list.length}</b></span><div className="scene-footer-actions"><button disabled={reduced} aria-pressed={paused||reduced} onClick={()=>setPaused(!paused)}>{reduced?'已减少动效':paused?'继续漂浮':'暂停漂浮'}</button>{list.length>size&&<><button disabled={page===0} onClick={()=>setGroup(page-1)} aria-label="上一组 Bot">←</button><span>{page+1} / {Math.ceil(list.length/size)}</span><button disabled={(page+1)*size>=list.length} onClick={()=>setGroup(page+1)} aria-label="下一组 Bot">→</button></>}</div></footer>
    <dialog ref={dialog} className="bot-intro-dialog" aria-labelledby="scene-intro-name" onCancel={e=>{e.preventDefault();close();}}>
      {selected&&<><BotAvatar avatar={selected.bot_avatar} mood="curious" still/><h2 id="scene-intro-name" tabIndex={-1}>{selected.name}</h2>
      {selected.one_line_use&&<section className="intro-description"><p>{selected.one_line_use}</p></section>}
      {selected.highlights.length>0&&<section className="intro-highlights"><h3>产品亮点：</h3>{selected.highlights.map((h,i)=><p key={i}>{h}</p>)}</section>}
      {onManage&&editable&&<button className="intro-manage" onClick={()=>{const id=selected.id;close();onManage(id);}}>编辑这个工具</button>}
      <footer className="intro-actions">
        {selected.github_url&&<a className="button" href={selected.github_url} target="_blank" rel="noopener noreferrer">打开 GitHub ↗</a>}
        <button className="intro-close" onClick={close} aria-label="关闭 Bot 介绍">关闭</button>
      </footer></>}
    </dialog>
  </section>;
}
