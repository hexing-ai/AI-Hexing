'use client';

import Link from 'next/link';
import {useRouter} from 'next/navigation';
import {useEffect, useRef, useState} from 'react';
import {api, type Task, type Snapshot, type Receipt} from '../lib/api';
import TaskManager from './TaskManager';
import TaskArtifacts from './TaskArtifacts';
import TaskContextPanel from './TaskContextPanel';
import TextStudio from './TextStudio';
import AudioStudio from './AudioStudio';
import {WorkspaceIcon} from './WorkspaceFrame';

function contentText(value:unknown,field:string) {
  if(!value||typeof value!=='object'||!('content' in value))return '';
  const content=value.content;
  if(!content||typeof content!=='object'||!(field in content))return '';
  const result=(content as Record<string,unknown>)[field];
  return typeof result==='string'?result:'';
}

export default function TaskWorkspace({tasks,loaded,error,taskId,path,onOpen,onRefresh,onChanged,authorize}:{tasks:Task[];loaded:boolean;error:string;taskId?:string;path:string;onOpen:(id:string)=>void;onRefresh:()=>Promise<void>;onChanged:(id:string)=>Promise<void>;authorize:(action:string,input:Record<string,unknown>)=>Promise<string|undefined>}) {
  const router=useRouter();
  const [repository,setRepository]=useState('');
  const [importing,setImporting]=useState(false);
  const [importError,setImportError]=useState('');
  const importLock=useRef(false);
  const importPanel=useRef<HTMLDetailsElement>(null);
  const repositoryInput=useRef<HTMLInputElement>(null);
  function openImport(){if(importPanel.current){importPanel.current.open=true;repositoryInput.current?.focus();}}
  async function importRepository(){
    if(importLock.current)return;
    importLock.current=true;setImporting(true);setImportError('');
    try{const result=await api<Receipt>('/imports',{repository_url:repository.trim()});router.push('/discover/jobs/'+result.job_id);}
    catch(e){setImportError((e as Error).message);}
    finally{importLock.current=false;setImporting(false);}
  }
  const [sources,setSources]=useState<Record<string,Snapshot>>({});
  const [sourceError,setSourceError]=useState(false);
  const [sourceRetry,setSourceRetry]=useState(0);
  const sourceKey=JSON.stringify([...new Set(tasks.filter(t=>!taskId||t.id===taskId).map(t=>t.snapshot_id))].sort());
  const entry=useRef<HTMLElement>(null);
  useEffect(()=>{
    let active=true;
    const ids=JSON.parse(sourceKey) as string[];
    void Promise.allSettled(ids.map(id=>api<Snapshot>(`/snapshots/${id}`))).then(results=>{
      if(!active)return;
      const loaded:Record<string,Snapshot>={};
      results.forEach((result,index)=>{if(result.status==='fulfilled'&&result.value?.metadata?.full_name)loaded[ids[index]]=result.value;});
      setSources(loaded);setSourceError(results.some(r=>r.status==='rejected'));
    });
    return()=>{active=false;};
  },[sourceKey,sourceRetry]);
  useEffect(()=>{if(taskId&&loaded){entry.current?.focus({preventScroll:true});}},[taskId,loaded]);
  const task=tasks.find(t=>t.id===taskId);
  const source=task?sources[task.snapshot_id]:undefined;
  const title=(t:Task)=>contentText(t.current_copy,'title')||sources[t.snapshot_id]?.metadata.full_name||`创作任务 · ${t.id.slice(0,8)}`;
  const openDetails=(id:string)=>{const element=document.getElementById(id);if(element instanceof HTMLDetailsElement){element.open=true;const summary=element.querySelector('summary');summary?.focus();element.scrollIntoView({block:'start'});}};
  return <>
    {error&&<div className="error" role="alert">任务暂时无法更新，已加载内容保留。{error}<button onClick={()=>void onRefresh().catch(()=>{})}>重新读取任务</button></div>}
    {!taskId?<>
      <div className="page-title task-home-title"><div><span className="eyebrow">把值得分享的工具，写成下一条内容</span><h1>创作任务</h1><p>导入已有工具，或按主题寻找，再复核亮点、完成口播与素材。</p></div><Link className="button primary" href="/discover"><WorkspaceIcon name="plus"/>发现工具</Link></div>
      <div className="creation-entrypoints"><Link href="/discover#discovery-search" className="creation-entry"><WorkspaceIcon name="discover"/><span><strong>寻找下一个工具</strong><small>按主题发现，再复核值得讲的亮点</small></span><span aria-hidden="true">↗</span></Link><button type="button" className="creation-entry" aria-controls="repository-import" onClick={openImport}><WorkspaceIcon name="plus"/><span><strong>导入 GitHub 链接</strong><small>已有工具？带着仓库资料开始创作</small></span><span aria-hidden="true">↓</span></button></div>
      <details ref={importPanel} id="repository-import" className="panel import-panel task-import">
        <summary>导入 GitHub 链接</summary>
        <label htmlFor="repository">GitHub 仓库地址</label>
        <form onSubmit={e=>{e.preventDefault();void importRepository();}}>
          <input ref={repositoryInput} id="repository" type="url" placeholder="https://github.com/owner/repository" required disabled={importing} value={repository} onChange={e=>setRepository(e.target.value)}/>
          <button className="primary" disabled={importing||!repository.trim()}>{importing?'正在提交…':'导入并读取资料 →'}</button>
        </form>
        <small>读取完成后，分析并核对要讲的亮点，再创建创作任务。</small>
        {importError&&<p className="error" role="alert">{importError} <Link href="/activity">查看处理记录</Link></p>}
      </details>
      <div className="section-title"><h2>最近创作</h2><span className="muted">每份稿件与素材，随时继续</span></div>
      {!loaded?<p role="status">{error?'等待重新读取任务。':'正在读取任务…'}</p>:tasks.length?<div className="task-list">{tasks.map(t=><article className="panel task-row" key={t.id}><div className="task-card-mark"><WorkspaceIcon name="file"/></div><div className="task-card-body"><small>{sources[t.snapshot_id]?.metadata.full_name||'已复核工具资料'}</small><h3>{title(t)}</h3><p className="task-excerpt">{contentText(t.current_script,'pain')||t.audience}</p><TaskArtifacts task={t}/></div><Link className="button" href={`/tasks/${t.id}`} onClick={()=>onOpen(t.id)}>继续创作 →</Link></article>)}</div>:<div className="panel empty large"><span className="empty-mark"><WorkspaceIcon name="file"/></span><h2>还没有创作任务</h2><p>先找到一个工具，复核资料与亮点，<br/>再开始第一份口播。</p><Link className="button primary" href="/discover">发现值得分享的工具 →</Link></div>}
      <TaskManager onChanged={onChanged}/>
    </>:<>
      <Link className="back-to-tasks" href="/tasks"><WorkspaceIcon name="arrow"/>返回任务</Link>
      {!loaded?<p role="status">{error?'等待重新读取任务。':'正在读取任务…'}</p>:!task?<div className="panel empty"><h1>暂时无法打开这个任务</h1><p>任务可能已归档或移入回收站，请返回列表查看任务管理。</p><Link className="button" href="/tasks">返回创作任务</Link></div>:<>
        <div className="page-title task-detail-title"><div><h1>{title(task)}</h1><div className="task-source-line"><span>{source?.metadata.full_name||`任务 ${task.id.slice(0,8)}`}</span>{source?.canonical_url?.startsWith('https://github.com/')&&<a href={source.canonical_url} target="_blank" rel="noreferrer">GitHub ↗</a>}<span>版本 {task.revision}</span></div></div><button onClick={()=>openDetails('task-processing')}><WorkspaceIcon name="clock"/>历史与问题</button></div>
        <TaskArtifacts task={task}/>
        {sourceError&&<small className="source-retry">部分工具名称暂时无法读取，已保存稿件仍可使用。<button className="text-button" onClick={()=>setSourceRetry(v=>v+1)}>重读工具资料</button></small>}
        <section id="text-creation" className="studio-entry" ref={entry} tabIndex={-1} aria-label={`任务 ${task.id.slice(0,8)} 的文本创作`}>
          <div className="task-editor-layout"><div className="manuscript-column"><TextStudio key={task.id} taskId={task.id} authorize={authorize} focusGroupId={path.split('/')[3]==='generations'?path.split('/')[4]:undefined}/><TaskContextPanel key={'context-'+task.id} taskId={task.id} onChanged={onRefresh}/></div><aside className="materials-column" aria-label="配音与素材"><AudioStudio key={'audio-'+task.id} taskId={task.id} authorize={authorize} focusId={['audio','exports'].includes(path.split('/')[3])?path.split('/')[4]:undefined} focusType={path.split('/')[3]}/></aside></div>
        </section>
      </>}
    </>}
  </>;
}
