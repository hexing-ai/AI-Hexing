'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import RankingStats from './RankingStats';
import { api, ApiError, type Fragment, type List } from '../lib/api';
import BotAvatar from './ranking/BotAvatar';
import {shapeOptions,colorOptions,type Avatar} from '../lib/botAppearance';
import BotPlayground from './ranking/BotPlayground';
import './ranking/ranking.css';

type Content = {name:string;bot_avatar:Avatar;highlights:{text:string;evidence_refs:string[]}[];github_url:string;source_snapshot_id:string;analysis_id:string;category:string;one_line_use:string};
type PublicTool = {id:string;version:number;name:string;bot_avatar:Avatar&{path:string};highlights:string[];github_url:string;last_verified_at:string|null;verification_status:string;category:string|null;one_line_use:string|null};
export type RankingEntry = {id:string;public_id:string;state:'draft'|'published'|'withdrawn';revision:number;data_mode:string;draft_version:number;content:Content;has_unpublished_changes:boolean;published:PublicTool|null;issues:string[];evidence:Fragment[];history:{version:number;created_at:string;published_at:string|null;content:PublicTool}[]};
type Preview = {tool:PublicTool;draft_revision_id:string;entry_revision:number;content_hash:string;issues:string[]};
const states = {draft:'草稿',published:'已公开',withdrawn:'已下架'};
const verification:Record<string,string> = {verified:'最近来源核验有效；当前访问情况以 GitHub 为准',unavailable:'最近核验暂不可访问',unknown:'尚未核实'};

function PublicCard({tool}:{tool:PublicTool}) {
  return <article className="ranking-preview-card">
    <BotAvatar avatar={tool.bot_avatar} mood="happy" still/>
    <h3>{tool.name||'待填写工具名'}</h3>
    {tool.one_line_use&&<p>{tool.one_line_use}</p>}
    <ul>{tool.highlights.map((h,i)=><li key={i}>{h||'待填写亮点'}</li>)}</ul>
    {tool.category&&<p>分类：{tool.category}</p>}
    {tool.github_url&&<a href={tool.github_url} target="_blank" rel="noopener noreferrer">{tool.github_url} ↗</a>}
    <small>{verification[tool.verification_status]}<br/>最后核实：{tool.last_verified_at?new Date(tool.last_verified_at).toLocaleString('zh-CN'):'未知'}</small>
  </article>;
}

export default function RankingManager({entryId}:{entryId?:string}) {
  const [management,setManagement]=useState(!!entryId);
  const [items,setItems]=useState<RankingEntry[]>([]);
  const [cursor,setCursor]=useState<string|null>(null);
  const [entry,setEntry]=useState<RankingEntry|null>(null);
  const [content,setContent]=useState<Content|null>(null);
  const [preview,setPreview]=useState<Preview|null>(null);
  const [approved,setApproved]=useState(false);
  const [busy,setBusy]=useState(false);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const [conflicted,setConflicted]=useState(false);
  const lock=useRef(false);
  const pending=useRef<{signature:string;key:string}|null>(null);
  const dirty=!!entry&&!!content&&JSON.stringify(entry.content)!==JSON.stringify(content);

  const readList=useCallback(async(next?:string)=>{
    const data=await api<List<RankingEntry>>('/ranking/entries'+(next?'?cursor='+encodeURIComponent(next):''));
    setItems(old=>next?[...old,...data.items.filter(i=>!old.some(e=>e.id===i.id))]:data.items);
    setCursor(data.next_cursor);
  },[]);

  const accept=useCallback((data:RankingEntry)=>{
    setEntry(data);setContent(data.content);setPreview(null);setApproved(false);setConflicted(false);
  },[]);

  useEffect(()=>{
    let active=true;
    async function load(){
      try {
        const [list,data]=await Promise.all([api<List<RankingEntry>>('/ranking/entries'),entryId?api<RankingEntry>('/ranking/entries/'+entryId):Promise.resolve(null)]);
        if(active){setItems(list.items);setCursor(list.next_cursor);if(data)accept(data);}
      }catch(e){if(active)setError((e as Error).message);}
      finally{if(active)setLoading(false);}
    }
    void load();return()=>{active=false;};
  },[entryId,accept]);

  useEffect(()=>{
    if(!dirty)return;
    const unload=(event:BeforeUnloadEvent)=>event.preventDefault();
    const navigate=(event:MouseEvent)=>{
      if(event.target instanceof Element&&event.target.closest('a[href]')&&!event.target.closest('a[target="_blank"]')&&!window.confirm('还有未保存修改，离开会丢失这些修改。确定离开？')){
        event.preventDefault();event.stopPropagation();
      }
    };
    window.addEventListener('beforeunload',unload);document.addEventListener('click',navigate,true);
    return()=>{window.removeEventListener('beforeunload',unload);document.removeEventListener('click',navigate,true);};
  },[dirty]);

  function change(value:Content){setContent(value);setPreview(null);setApproved(false);setNotice('');}
  async function act(fn:()=>Promise<void>){
    if(lock.current)return;lock.current=true;setBusy(true);setError('');setNotice('');
    try{await fn();}catch(e){
      const err=e as ApiError;
      setError((err.status===0?'连接中断，结果可能已保存。请先读取服务器版本核对；原编辑内容仍保留。':err.message)+(err.requestId?' · 请求编号 '+err.requestId:''));
      if(err.status===409||err.status===0)setConflicted(true);
    }finally{lock.current=false;setBusy(false);}
  }
  async function write(path:string,body:unknown,method='POST'){
    const signature=JSON.stringify([path,body,method]);
    if(pending.current?.signature!==signature)pending.current={signature,key:crypto.randomUUID()};
    const result=await api<RankingEntry>(path,body,method,pending.current.key);
    pending.current=null;accept(result);
    // A failed list refresh must not disguise an already successful publication.
    setItems(old=>[result,...old.filter(i=>i.id!==result.id)]);
    return result;
  }

  const sceneTools=items.map(item=>({id:item.id,version:item.draft_version,first_published_at:null,name:item.content.name,bot_avatar:item.content.bot_avatar,highlights:item.content.highlights.map(h=>h.text),github_url:item.content.github_url,last_verified_at:null,verification_status:'unknown' as const,category:item.content.category,one_line_use:item.content.one_line_use}));
  return <section className={`ranking-manager ${management?'is-managing':'is-showing-bots'}`}>
    <header className="ranking-scene-header"><div><span className="ranking-brand">AI-Hexing <span>COLLECTION</span></span><h1>工具封神榜</h1></div><div className="ranking-scene-actions"><a href="/display" target="_blank" rel="noopener noreferrer">公开浏览 ↗</a><button onClick={()=>setManagement(!management)}>{management?'返回 Bot 场景':'管理工具'}</button></div></header>
    {!management&&<BotPlayground tools={sceneTools} loading={loading} onManage={id=>{if(id){const item=items.find(i=>i.id===id);if(item)accept(item);}setManagement(true);}}/>}
    {management&&<div className="ranking-management-intro"><p>保存草稿并核对后，再决定公开。</p><Link href="/discover" className="button">从资料卡加入工具 →</Link></div>}
    {loading&&<p role="status">正在读取榜单…</p>}
    {error&&<p className="error" role="alert">{error}</p>}
    {notice&&<p className="notice" role="status">{notice}</p>}
    {management&&<div className="ranking-layout"><aside className="panel ranking-list" aria-label="榜单条目">
      <div className="panel-heading"><h2>条目</h2><button disabled={busy} onClick={()=>void act(()=>readList())}>刷新列表</button></div>
      {!loading&&!items.length&&<p>还没有榜单草稿。先从工具资料卡加入，不需要先完成配音。</p>}
      {items.map(item=><Link className={`ranking-list-item ${entry?.id===item.id?'selected':''}`} key={item.id} href={'/ranking/'+item.id}><strong>{item.content.name||'未命名工具'}</strong><small>{states[item.state]}{item.has_unpublished_changes?' · 有未发布修改':''}</small></Link>)}
      {cursor&&<button disabled={busy} onClick={()=>void act(()=>readList(cursor))}>加载更多条目</button>}
    </aside><div>
    {!entry&&!loading&&<div className="panel empty"><h2>选择一个条目开始编辑</h2><p>私有稿件和声音不会随工具公开。</p></div>}
    {entry&&content&&<>
      <section className="panel ranking-editor"><div className="panel-heading"><h2>编辑公开资料</h2><span className="badge">{states[entry.state]}{entry.has_unpublished_changes?' · 有未发布修改':''}</span></div>
      <p>{dirty?'有未保存修改':'草稿已保存'} · 第 {entry.draft_version} 版</p>
      {conflicted&&<div className="muted-box"><p>本地编辑内容已保留。可先复制需要保留的文字，再载入服务器版本核对。</p><button disabled={busy} onClick={()=>void act(async()=>{if(!dirty||window.confirm('载入服务器版本将替换当前未保存编辑，确定继续？')){accept(await api<RankingEntry>('/ranking/entries/'+entry.id));pending.current=null;setNotice('已载入服务器版本，请重新核对。');}})}>读取服务器版本</button></div>}
      <fieldset disabled={busy} className="ranking-fields">
      <label>工具名<input maxLength={100} value={content.name} onChange={e=>change({...content,name:e.target.value})}/></label>
      <div className="appearance-live"><BotAvatar avatar={content.bot_avatar} mood="happy" still/><div><strong>{content.name||'你的工具伙伴'}</strong><p>形象预览随选择变化，保存并公开后才会更新到榜单。</p></div></div>
      <div className="ranking-two"><label>Bot 形象<select value={content.bot_avatar.preset} onChange={e=>change({...content,bot_avatar:{...content.bot_avatar,preset:e.target.value as Avatar['preset']}})}>{shapeOptions.map(s=><option key={s.value} value={s.value}>{s.label}</option>)}</select></label><label>Bot 配色<select value={content.bot_avatar.palette} onChange={e=>change({...content,bot_avatar:{...content.bot_avatar,palette:e.target.value as Avatar['palette']}})}>{colorOptions.map(c=><option key={c.value} value={c.value}>{c.label}</option>)}</select></label></div>
      <label>GitHub 链接<input maxLength={1000} value={content.github_url} onChange={e=>change({...content,github_url:e.target.value})}/></label>
      <div className="ranking-two"><label>分类（可选）<input maxLength={40} value={content.category} onChange={e=>change({...content,category:e.target.value})}/></label><label>一句话用途（可选）<input maxLength={160} value={content.one_line_use} onChange={e=>change({...content,one_line_use:e.target.value})}/></label></div>
      <h3>公开亮点</h3><p>每条亮点保留必要限制，并勾选支持它的原文。没有依据的承诺请删除。</p>
      {content.highlights.map((h,i)=><div key={i} className="ranking-highlight">
        <label>亮点 {i+1}<textarea rows={3} maxLength={300} value={h.text} onChange={e=>change({...content,highlights:content.highlights.map((v,n)=>n===i?{...v,text:e.target.value}:v)})}/></label>
        <details><summary>核对来源（已选 {h.evidence_refs.length} 段）</summary>{entry.evidence.map(f=><label className="ranking-evidence" key={f.id}><input type="checkbox" checked={h.evidence_refs.includes(f.id)} onChange={e=>change({...content,highlights:content.highlights.map((v,n)=>n===i?{...v,evidence_refs:e.target.checked?[...v.evidence_refs,f.id]:v.evidence_refs.filter(r=>r!==f.id)}:v)})}/><span><strong>{f.id}</strong><pre>{f.text}</pre></span></label>)}</details>
        <button type="button" onClick={()=>change({...content,highlights:content.highlights.filter((_,n)=>n!==i)})}>移除此亮点</button>
      </div>)}
      {content.highlights.length<3&&<button type="button" onClick={()=>change({...content,highlights:[...content.highlights,{text:'',evidence_refs:[]}]})}>添加亮点</button>}
      </fieldset>
      <div className="actions"><button className="primary" disabled={busy||!dirty} onClick={()=>void act(async()=>{await write('/ranking/entries/'+entry.id,{expected_revision:entry.revision,content},'PATCH');setNotice('草稿已保存。公开内容尚未改变。');})}>保存草稿</button>
      <button disabled={busy||dirty} onClick={()=>void act(async()=>{setPreview(await api<Preview>('/ranking/entries/'+entry.id+'/preview'));setApproved(false);setNotice('预览已就绪，请核对下方内容。');})}>预览公开内容</button></div>
      {dirty&&<small>先保存草稿，再预览准确版本。</small>}
      </section>
      {preview&&<section className="panel ranking-publish" aria-label="发布预览"><h2>即将公开的内容</h2><p>公开后，这个 Bot 将出现在工具封神榜；访客点按它查看下方介绍，再自行打开 GitHub。</p><PublicCard tool={preview.tool}/>
        {preview.issues.length>0?<div className="muted-box">{preview.issues.map(p=><p key={p}>{p}</p>)}</div>:<><label className="radio-label"><input type="checkbox" checked={approved} disabled={busy} onChange={e=>setApproved(e.target.checked)}/>内容核对无误，可以公开</label><button className="primary" disabled={busy||!approved} onClick={()=>void act(async()=>{await write('/ranking/entries/'+entry.id+'/publish',{expected_revision:preview.entry_revision,draft_revision_id:preview.draft_revision_id,content_hash:preview.content_hash,content_reviewed:true});setNotice('发布成功，公开副本已更新。');})}>{entry.state==='published'?'发布更新':entry.state==='withdrawn'?'重新公开':'公开'}</button></>}
      </section>}
      {entry.published&&<section className="panel"><h2>{entry.state==='withdrawn'?'上次公开内容（已下架）':'当前公开内容'}</h2><PublicCard tool={entry.published}/>{entry.state==='published'&&<button disabled={busy} onClick={()=>void act(async()=>{if(window.confirm('下架后，访客将无法从公开接口读取此工具。确定下架？')){await write('/ranking/entries/'+entry.id+'/withdraw',{expected_revision:entry.revision});setNotice('已下架，草稿与历史版本保留。');}})}>下架此工具</button>}</section>}
      <RankingStats entryId={entry.id}/>
      <details className="panel"><summary>历史版本（{entry.history.length}）</summary>{entry.history.map(h=><details key={h.version}><summary>第 {h.version} 版 · {h.published_at?'曾公开':'草稿'} · {new Date(h.created_at).toLocaleString('zh-CN')}</summary><PublicCard tool={h.content}/></details>)}</details>
    </>}
    </div></div>}
  </section>;
}
