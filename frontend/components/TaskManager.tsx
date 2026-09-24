'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type List, type Task } from '../lib/api';
import ConfirmDialog from './ConfirmDialog';

type Life = {task_id:string;revision:number;state:string;purge_after:string|null;can_restore:boolean;cleanup_status:string|null};
type Preview = {task_id:string;expected_revision:number;preview_token:string;text_versions:number;audio_versions:number;files:number;bytes:number;unfinished_jobs:number;recovery_days:number;kept:string[]};
type ReadonlyText = {versions:{id:string;kind:string;content:unknown;confirmed_at:string|null}[]};
type ReadonlyAudio = {versions:{id:string;asset_id?:string;duration_ms?:number}[];exports:{id:string;download_path?:string;available:boolean}[]};
const cleanupLabels:Record<string,string>={recovery_period:'恢复期内，到期后清理',waiting_for_result_reconciliation:'调用结果尚未核清，延后清理',shared_asset_reference:'存在共享文件引用，待核对后清理',file_cleanup_failed:'文件清理失败，后台将重试',completed:'已清理'};

export default function TaskManager({onChanged}:{onChanged:(taskId:string)=>Promise<void>}) {
  const [open,setOpen]=useState(false);
  return <details className="panel" onToggle={e=>setOpen(e.currentTarget.open)}><summary>任务管理 · 归档与回收站</summary>{open&&<Manager onChanged={onChanged}/>}</details>;
}

function Manager({onChanged}:{onChanged:(taskId:string)=>Promise<void>}) {
  const [bucket,setBucket]=useState('active');
  const [cursor,setCursor]=useState<string|null>(null); const [previous,setPrevious]=useState<(string|null)[]>([]);
  const [data,setData]=useState<List<Task>|null>(null);
  const [busy,setBusy]=useState(false); const lock=useRef(false); const sequence=useRef(0);
  const invalidate=useCallback(()=>{sequence.current++;},[]);
  const [error,setError]=useState(''); const [notice,setNotice]=useState('');
  const [preview,setPreview]=useState<Preview|null>(null);
  const [view,setView]=useState<{id:string;text:ReadonlyText;audio:ReadonlyAudio}|null>(null);
  const [reload,setReload]=useState(0);
  const refresh=useCallback(async()=>{
    const seq=++sequence.current;
    const value=await api<List<Task>>(`/tasks?lifecycle=${bucket}&limit=10${cursor?'&cursor='+encodeURIComponent(cursor):''}`);
    if(seq===sequence.current)setData(value);
  },[bucket,cursor]);
  useEffect(()=>{let stopped=false;void refresh().catch(e=>{if(!stopped)setError(e.message);});return()=>{stopped=true;invalidate();};},[refresh,reload,invalidate]);
  async function run(fn:()=>Promise<void>){
    if(lock.current)return;lock.current=true;setBusy(true);setError('');setNotice('');
    try{await fn();}catch(e){setError((e as Error).message);}finally{lock.current=false;setBusy(false);}
  }
  function assertEditorClosed(id:string){
    if(sessionStorage.getItem('hexing-draft-'+id))throw new Error('请先保存并结束这个任务的文本编辑，再操作归档或删除。');
  }
  async function changed(id:string,message:string){
    setPreview(null);setView(null);setNotice(message);setReload(v=>v+1);await onChanged(id);
  }
  async function transition(id:string,action:'archive'|'restore'){
    if(action==='archive')assertEditorClosed(id);
    const latest=await api<Life>(`/tasks/${id}/lifecycle`);
    await api(`/tasks/${id}/${action}`,{expected_revision:latest.revision});
    await changed(id,action==='archive'?'任务已归档，素材保留。':'任务已恢复；已取消的处理不会自动重启。');
  }
  async function openPreview(id:string){
    assertEditorClosed(id);setView(null);
    setPreview(await api<Preview>(`/tasks/${id}/delete-preview`));
  }
  async function trash(){
    if(!preview)return;
    assertEditorClosed(preview.task_id);
    await api(`/tasks/${preview.task_id}/trash`,{expected_revision:preview.expected_revision,preview_token:preview.preview_token,confirm:true});
    await changed(preview.task_id,'已移入回收站，7 天内可以恢复。');
  }
  async function readArchived(id:string){
    const [text,audio]=await Promise.all([api<ReadonlyText>(`/tasks/${id}/texts`),api<ReadonlyAudio>(`/tasks/${id}/audio`)]);
    setView({id,text,audio});
  }
  return <section aria-label="任务管理"><h2>任务管理</h2><p>归档保留素材；删除进入 7 天回收期，到期后由后台清理。已下载文件、本人音色和公开榜单不会随任务删除。</p>
    <label>任务范围<select value={bucket} disabled={busy} onChange={e=>{sequence.current++;setBucket(e.target.value);setCursor(null);setPrevious([]);setData(null);setPreview(null);setView(null);setError('');}}><option value="active">进行中</option><option value="archived">已归档</option><option value="trashed">回收站</option></select></label>
    <button disabled={busy} onClick={()=>void run(refresh)}>刷新任务管理</button>
    {error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {!data?<p>正在读取任务…</p>:data.items.length===0?<p>这个范围暂无任务。</p>:data.items.map(task=><article className="muted-box" key={task.id} data-testid={'managed-'+task.id}>
      <h3>任务 {task.id.slice(0,8)}</h3><p>{task.audience}</p>
      {bucket==='trashed'&&<><p>恢复截止：{task.lifecycle?.purge_after?new Date(task.lifecycle.purge_after).toLocaleString('zh-CN'):'—'}</p><p>{cleanupLabels[task.lifecycle?.cleanup_status||'']||'等待后台清理'}。后台停机时清理会延后。</p></>}
      <div className="actions">{bucket==='active'?<button disabled={busy} onClick={()=>void run(()=>transition(task.id,'archive'))}>归档任务</button>:<button disabled={busy||!task.lifecycle?.can_restore} onClick={()=>void run(()=>transition(task.id,'restore'))}>恢复任务</button>}
      {bucket==='archived'&&<button disabled={busy} onClick={()=>void run(()=>readArchived(task.id))}>查看已归档素材</button>}
      {bucket!=='trashed'&&<button disabled={busy} onClick={()=>void run(()=>openPreview(task.id))}>预览删除范围</button>}</div>
    </article>)}
    <div className="actions"><button disabled={busy||previous.length===0} onClick={()=>{setCursor(previous[previous.length-1]);setPrevious(previous.slice(0,-1));setData(null);}}>上一页任务</button><button disabled={busy||!data?.next_cursor} onClick={()=>{setPrevious([...previous,cursor]);setCursor(data!.next_cursor);setData(null);}}>下一页任务</button></div>
    {preview&&<ConfirmDialog title="确认任务删除范围" confirmLabel="确认移入回收站" cancelLabel="取消删除" busy={busy} onConfirm={()=>void run(trash)} onCancel={()=>setPreview(null)}>{error&&<p role="alert" className="error">{error}</p>}<p>任务 {preview.task_id.slice(0,8)}：{preview.text_versions} 个文本版本、{preview.audio_versions} 个配音版本、{preview.files} 份文件，约 {(preview.bytes/1024/1024).toFixed(2)} MB。</p><p>{preview.unfinished_jobs} 个未完成处理将停止采用。已发送的调用仍可能计费；恢复任务不会重发。</p><p>确认后进入 {preview.recovery_days} 天回收期，到期清理任务原文和私有素材。保留：{preview.kept.join('、')}。</p></ConfirmDialog>}
    {view&&<section className="muted-box" aria-label="已归档素材"><h3>已归档素材 · {view.id.slice(0,8)}</h3><button onClick={()=>setView(null)}>关闭素材查看</button>{view.text.versions.map(v=><article key={v.id}><h4>{v.kind==='script'?'口播':'文案'} · {v.confirmed_at?'已确认':'草稿'}</h4><pre>{JSON.stringify(v.content,null,2)}</pre><a href={`/api/v1/tasks/${view.id}/texts/${v.id}/download`}>下载文字</a></article>)}{view.audio.versions.filter(v=>v.asset_id).map(v=><p key={v.id}><a href={`/api/v1/assets/${v.asset_id}/content`}>下载配音 {v.id.slice(0,8)}</a></p>)}{view.audio.exports.filter(v=>v.available&&v.download_path).map(v=><p key={v.id}><a href={v.download_path}>下载素材包 {v.id.slice(0,8)}</a></p>)}</section>}
  </section>;
}
