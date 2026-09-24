'use client';
import Link from 'next/link';
import {useEffect,useRef,useState} from 'react';
import {api,ApiError,type Voice} from '../lib/api';
import {usePolling} from '../lib/usePolling';
import {editableVoiceName,voiceLabel} from '../lib/voiceName';
import MediaAudio from './MediaAudio';
import DownloadButton from './DownloadButton';
import ConfirmDialog from './ConfirmDialog';
export type VoiceEntry=Voice&{region:string;display_name:string;metadata_revision:number;referenced_task_count:number;eligible:boolean;is_default:boolean;preview:{download_path:string;duration_ms:number;kind:string}|null};
export type VoiceCollection={items:VoiceEntry[];default_voice_id:string|null;settings_revision:number};
export const voiceStatus:Record<string,string>={failed:'创建失败',needs_reconciliation:'结果待核对',ready:'可用于配音',awaiting_preview:'待试听确认',cloning:'创建中',disabled:'已停用',deletion_pending:'正在删除',remote_deletion_pending:'远端删除待核对',deleted:'已删除'};
export default function VoiceLibrary(){
  const {data,error:readError,refresh}=usePolling<VoiceCollection>('/voices',5000);
  const [filter,setFilter]=useState('active');const [search,setSearch]=useState('');
  const [error,setError]=useState('');const [notice,setNotice]=useState('');const [busy,setBusy]=useState(false);const lock=useRef(false);
  const [editing,setEditing]=useState<{voice:VoiceEntry;name:string}|null>(null);
  const renameTrigger=useRef<HTMLButtonElement|null>(null);
  const [batch,setBatch]=useState(false);const [selected,setSelected]=useState<string[]>([]);
  const [confirmation,setConfirmation]=useState<{voices:VoiceEntry[];action:'disable'|'remove'}|null>(null);
  const restoreRenameFocus=useRef(false);
  useEffect(()=>{if(!busy&&!editing&&restoreRenameFocus.current){restoreRenameFocus.current=false;renameTrigger.current?.focus();}},[busy,editing]);
  function closeEditor(){restoreRenameFocus.current=true;setEditing(null);}
  async function removeVoices(voices:VoiceEntry[]){
    try{
      const result=await api<{removed_ids:string[]}>('/voices/list-removals',{voice_ids:voices.map(v=>v.id),confirm:true});
      setSelected([]);setConfirmation(null);setEditing(null);
      setNotice(`已从列表移除 ${result.removed_ids.length} 条记录，声音文件和历史配音保留。`);
    }catch(e){
      if(e instanceof ApiError&&(e.status===0||e.status>=500)){
        setConfirmation(null);
        throw new Error('连接中断，请刷新列表确认移除结果。声音文件和历史配音不受影响。');
      }
      throw e;
    }
  }
  async function act(fn:()=>Promise<void>){if(lock.current)return;lock.current=true;setBusy(true);setError('');setNotice('');try{await fn();await refresh();}catch(e){setError((e as Error).message);}finally{lock.current=false;setBusy(false);}}
  const visible=data?.items.filter(v=>(filter==='all'||filter==='ready'&&v.eligible||filter==='active'&&!['deleted','disabled'].includes(v.status))&&voiceLabel(v).toLowerCase().includes(search.toLowerCase()))||[];
  const selectedVoices=visible.filter(v=>selected.includes(v.id));
  const selectable=visible;
  return <section aria-label="我的声音库"><div className="page-title"><div><span className="eyebrow">一次创建，留给每一次表达</span><h1>我的声音</h1><p>试听已保存的声音，选好下一份口播的表达方式。</p></div><Link className="button primary" href="/voices/new">新增声音 →</Link></div>
    <div className="voice-library-guide"><div><strong>{data?data.items.filter(v=>v.eligible).length:'—'}</strong><span>份可用声音</span></div><p>默认声音优先用于尚无配音的任务。已有配音保留原来的声音与版本。</p></div>
    <div className="voice-library-filters"><label>查找声音<input value={search} onChange={e=>{setSearch(e.target.value);setSelected([]);}} placeholder="输入声音名称"/></label><label>显示范围<select value={filter} onChange={e=>{setFilter(e.target.value);setSelected([]);}}><option value="active">可用与待处理</option><option value="ready">仅可用声音</option><option value="all">全部（含停用与删除）</option></select></label></div>
    {readError&&<p role="alert" className="error">声音读取失败，已显示记录保留。{readError}<button onClick={()=>void refresh().catch(()=>{})}>重新读取声音</button></p>}{error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status" className="notice">{notice}</p>}
    {!data&&!readError&&<p role="status">正在读取声音…</p>}{data&&!data.items.length&&<div className="panel empty"><h2>列表中还没有声音</h2><p>上传本人的清晰录音，创建后试听并确认，就可以在多个任务中复用。</p><Link className="button" href="/voices/new">开始设置本人声音 →</Link></div>}{data&&data.items.length>0&&!visible.length&&<p className="panel">没有符合当前筛选的声音。可以切换“全部”或调整名称。</p>}
    <div className="actions voice-batch-toolbar"><button disabled={busy} aria-pressed={batch} onClick={()=>{setBatch(!batch);setSelected([]);}}>{batch?'退出批量管理':'批量管理'}</button>{batch&&<><button disabled={busy||!selectable.length} onClick={()=>setSelected(selectable.map(v=>v.id))}>全选当前结果</button><button disabled={busy||!selected.length} onClick={()=>setSelected([])}>取消选择</button><span>已选 {selectedVoices.length} 份</span><button disabled={busy||!selectedVoices.length} onClick={()=>{setError('');setConfirmation({voices:selectedVoices,action:'remove'});}}>移除所选记录</button></>}</div>
    <div className="voice-library-grid">{visible.map(v=><article key={v.id} className={`panel voice-card ${v.is_default?'default-voice':''}`} aria-label={`声音 ${voiceLabel(v)}`}>{batch&&<label className="check-label"><input type="checkbox" aria-label={`选择 ${voiceLabel(v)}`} disabled={busy} checked={selected.includes(v.id)} onChange={e=>setSelected(old=>e.target.checked?[...old,v.id]:old.filter(id=>id!==v.id))}/><span>选择这份声音</span></label>}<div className="panel-heading"><span className="voice-orb" aria-hidden="true">♫</span><div><h2>{voiceLabel(v)}</h2><button className="text-button" disabled={busy||editing!==null} onClick={e=>{renameTrigger.current=e.currentTarget;setEditing({voice:v,name:editableVoiceName(v)});}}>重命名</button><span className={`badge ${v.eligible?'':'warning'}`}>{voiceStatus[v.status]||'状态待核对'}{v.is_default?' · 默认声音':''}</span></div></div>
      {editing?.voice.id===v.id&&<form className="voice-rename" onSubmit={e=>{e.preventDefault();if(!editing.name.trim()||editing.name.trim()===editableVoiceName(editing.voice))return;void act(async()=>{await api(`/voices/${v.id}/metadata`,{display_name:editing.name.trim(),expected_revision:editing.voice.metadata_revision});closeEditor();setNotice('声音名称已保存，编号保持不变。');});}}><label>声音名称<input autoFocus aria-label={`声音名称 ${v.id.slice(0,8)}`} value={editing.name} maxLength={60} disabled={busy} onChange={e=>setEditing({...editing,name:e.target.value})} onKeyDown={e=>{if(e.key==='Escape'&&!busy){e.preventDefault();closeEditor();}}}/></label><small>只修改名称，编号 {v.id.slice(0,8)} 保持不变。</small><div className="actions"><button type="submit" disabled={busy||!editing.name.trim()||editing.name.trim()===editableVoiceName(editing.voice)}>保存名称</button><button type="button" disabled={busy} onClick={closeEditor}>取消重命名</button></div></form>}
      {v.preview?<><p className="muted">已有{v.preview.kind} · {(v.preview.duration_ms/1000).toFixed(2)} 秒{v.data_mode==='mock'?' · 模拟测试音':''}</p><MediaAudio key={v.preview.download_path} src={v.preview.download_path} label={v.display_name+'试听'}/><DownloadButton url={v.preview.download_path} label="下载这段音频" filename="AI-Hexing-声音.wav"/></>:<p className="muted">暂时没有可播放的已保存音频。</p>}
      <div className="actions"><button disabled={busy||!v.eligible||v.is_default} onClick={()=>void act(async()=>{await api('/voice-preferences',{default_voice_id:v.id,expected_revision:data!.settings_revision});setNotice('已设为默认声音，已有配音保持原版本。');})}>{v.is_default?'已设为默认声音':'设为默认声音'}</button>{v.is_default&&<button disabled={busy} onClick={()=>void act(async()=>{await api('/voice-preferences',{default_voice_id:null,expected_revision:data!.settings_revision});setNotice('已取消默认声音。');})}>取消默认声音</button>}<Link href={v.eligible?'/tasks':'/voices/'+v.id}>{v.eligible?'去创作任务配音 →':'查看状态与试听 →'}</Link></div>
      {v.status==='ready'&&!v.eligible&&<p className="notice">这份声音与当前服务配置不匹配，请核对后再使用。</p>}
      <div className="actions"><button disabled={busy} onClick={()=>{setError('');setConfirmation({voices:[v],action:'remove'});}}>从列表移除</button></div>
      <details><summary>更多声音管理</summary><p className="muted">停用会阻止新配音。从列表移除只整理本页记录，声音文件和历史配音保留。</p><button disabled={busy||!['ready','awaiting_preview'].includes(v.status)} onClick={()=>{setError('');setConfirmation({voices:[v],action:'disable'});}}>停用声音</button><small>{v.region} · {v.target_model} · 记录 {v.id.slice(0,8)}</small><Link href={'/voices/'+v.id}>打开声音详情 →</Link></details>
      {v.status==='remote_deletion_pending'&&<p className="notice">这条记录有一笔旧的远端删除请求待核对，不影响从本列表移除。</p>}
    </article>)}</div>
    {confirmation&&<ConfirmDialog title={confirmation.action==='disable'?'停用这份声音？':`从列表移除这 ${confirmation.voices.length} 条记录？`} confirmLabel={confirmation.action==='disable'?'确认停用':'确认移除'} busy={busy} onCancel={()=>setConfirmation(null)} onConfirm={()=>void act(async()=>{if(confirmation.action==='remove'){await removeVoices(confirmation.voices);}else{const v=confirmation.voices[0];await api(`/probe-voices/${v.id}/disable`,{expected_revision:v.revision});setConfirmation(null);setNotice('声音已停用。');}})}>{error&&<p className="error" role="alert">{error}</p>}<ul className="voice-confirm-list">{confirmation.voices.map(v=><li key={v.id}>{voiceLabel(v)}{v.is_default?(confirmation.action==='disable'?' · 默认声音（将取消默认）':' · 默认声音（继续保留）'):''}{v.referenced_task_count>0?` · 已用于 ${v.referenced_task_count} 个任务`:''}</li>)}</ul><p>{confirmation.action==='disable'?'停用后不能用这份声音生成新配音，历史稿件和配音保留。':'只让记录从“我的声音”页面消失。录音样本、已生成音频和远端音色都保留，已有任务与默认声音设置不受影响。'}</p></ConfirmDialog>}
  </section>;
}
