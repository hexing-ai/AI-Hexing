'use client';
import { useRef, useState } from 'react';
import { api } from '../lib/api';

type Note = {snapshot_id:string;tested_on:string;environment:string;procedure:string;observation:string;limitations:string;id?:string;source_type?:string};
type Context = {id:string|null;revision:number;analysis_id:string;snapshot_id:string;audience:string;primary_highlight_id:string;secondary_highlight_id:string|null;creator_tests:Note[];created_at:string};
type Source = {id:string;snapshot_id:string;created_at:string;analysis:{audience:string;highlights:{id:string;capability:string;conditions:string;evidence_refs:string[]}[]};snapshot:{metadata:{full_name:string};fragments:{id:string;text:string;source_url:string}[]}};
type Data = {current:Context;source:Source};
type History = {items:Context[];next_before:number|null};
const noteFields = {tested_on:'实测日期（UTC）',environment:'环境与工具版本',procedure:'实测操作',observation:'观察结果',limitations:'限制与未验证项'} as const;

export default function TaskContextPanel({taskId,onChanged}:{taskId:string;onChanged:()=>Promise<void>}) {
  const [draft,setDraft]=useState<Context|null>(null);const [sources,setSources]=useState<Source[]>([]);const [cursor,setCursor]=useState<string|null>(null);
  const [reviewed,setReviewed]=useState(false);const [busy,setBusy]=useState(false);const lock=useRef(false);
  const [notice,setNotice]=useState('');const [error,setError]=useState('');const [history,setHistory]=useState<History|null>(null);
  const pending=useRef<{signature:string;key:string}|null>(null);
  async function act(fn:()=>Promise<void>){if(lock.current)return;lock.current=true;setBusy(true);setError('');try{await fn();}catch(e){setError((e as Error).message);}finally{lock.current=false;setBusy(false);}}
  async function load(){const result=await api<Data>(`/tasks/${taskId}/context`);setDraft(result.current);setSources([result.source]);setReviewed(false);const list=await api<{items:Source[];next_cursor:string|null}>(`/tasks/${taskId}/context/sources`);setSources([result.source,...list.items.filter(s=>s.id!==result.source.id)]);setCursor(list.next_cursor);}
  function update(values:Partial<Context>){setDraft(d=>d?{...d,...values}:d);setReviewed(false);setNotice('');}
  function updateNote(index:number,key:keyof Note,value:string){if(!draft)return;update({creator_tests:draft.creator_tests.map((n,i)=>i===index?{...n,[key]:value}:n)});}
  async function save(){
    if(!draft||!reviewed)return;
    if(sessionStorage.getItem('hexing-draft-'+taskId)||document.querySelector(`.text-studio[data-task-id="${taskId}"][data-editing="true"]`))throw new Error('请先保存并结束这个任务的文本编辑，再调整资料。');
    const body={expected_revision:draft.revision,analysis_id:draft.analysis_id,audience:draft.audience,primary_highlight_id:draft.primary_highlight_id,secondary_highlight_id:draft.secondary_highlight_id,creator_tests:draft.creator_tests.map(n=>({snapshot_id:n.snapshot_id,tested_on:n.tested_on,environment:n.environment,procedure:n.procedure,observation:n.observation,limitations:n.limitations})),facts_reviewed:true};
    const signature=JSON.stringify(body);if(pending.current?.signature!==signature)pending.current={signature,key:crypto.randomUUID()};
    const result=await api<{changed:boolean;current:Context}>(`/tasks/${taskId}/context`,body,'PATCH',pending.current.key);
    pending.current=null;setDraft(result.current);setReviewed(false);setHistory(null);
    setNotice(result.changed?'任务资料已保存。原稿和素材保留，请按当前资料重新确认口播、文案与同稿配音。':'资料未变化，原版本和确认状态已保留。');
    window.dispatchEvent(new CustomEvent('hexing-context-changed',{detail:taskId}));await onChanged();
  }
  const source=sources.find(s=>s.id===draft?.analysis_id);
  return <details className="panel" onToggle={e=>{if(e.currentTarget.open&&!draft&&!lock.current)void act(load);}}><summary>任务资料与实测记录</summary><section aria-label="任务资料与实测记录">
    <p>调整受众、亮点或资料后，原稿不会重写，旧确认将失效。正文仍适用时可重新核对确认；同一口播的原音频可重新试听确认，无需再次合成。</p>
    {error&&<p role="alert" className="error">{error} 输入已保留。</p>}{notice&&<p role="status">{notice}</p>}
    {!draft?<p>正在读取任务资料…</p>:<><fieldset disabled={busy}>
      <label>任务目标受众<textarea maxLength={500} value={draft.audience} onChange={e=>update({audience:e.target.value})}/></label>
      <label>采用的资料分析<select value={draft.analysis_id} onChange={e=>{const next=sources.find(s=>s.id===e.target.value)!;update({analysis_id:next.id,snapshot_id:next.snapshot_id,primary_highlight_id:next.analysis.highlights[0]?.id||'',secondary_highlight_id:null});}}>{sources.map(s=><option key={s.id} value={s.id}>{s.snapshot.metadata.full_name} · {new Date(s.created_at).toLocaleString('zh-CN')} · {s.snapshot_id.slice(0,8)} / {s.id.slice(0,8)}</option>)}</select></label>
      {cursor&&<button onClick={()=>void act(async()=>{const list=await api<{items:Source[];next_cursor:string|null}>(`/tasks/${taskId}/context/sources?cursor=${encodeURIComponent(cursor)}`);setSources(old=>[...old,...list.items.filter(s=>!old.some(v=>v.id===s.id))]);setCursor(list.next_cursor);})}>更多资料分析</button>}
      <small>仅列出同一工具已经完成的分析。需要新资料时先到“发现工具”刷新并分析，任务不会自动切换。</small>
      {source&&<><p>原分析受众：{source.analysis.audience}。更改目标受众后请重新判断以下亮点是否适用。</p>
        <label>任务主亮点<select value={draft.primary_highlight_id} onChange={e=>update({primary_highlight_id:e.target.value})}>{source.analysis.highlights.map(h=><option key={h.id} value={h.id}>{h.capability}</option>)}</select></label>
        <label>任务辅助亮点<select value={draft.secondary_highlight_id||''} onChange={e=>update({secondary_highlight_id:e.target.value||null})}><option value="">不使用辅助亮点</option>{source.analysis.highlights.filter(h=>h.id!==draft.primary_highlight_id).map(h=><option key={h.id} value={h.id}>{h.capability}</option>)}</select></label>
        <details><summary>核对仓库说明与亮点证据</summary>{source.analysis.highlights.filter(h=>[draft.primary_highlight_id,draft.secondary_highlight_id].includes(h.id)).map(h=><article key={h.id}><strong>{h.capability}</strong><p>条件：{h.conditions||'未列出，仍需核对'}</p><p>引用：{h.evidence_refs.join('、')}</p></article>)}{source.snapshot.fragments.map(f=><article key={f.id}><strong>仓库说明 · {f.id}</strong><pre>{f.text}</pre></article>)}</details></>}
      <h3>本人实测记录（最多 5 条）</h3><p>只填写本人已经完成的测试，限制或未验证项也要写明。笔记会随之后经你批准的文字生成提交给模型；保存本身不调用模型。</p>
      {draft.creator_tests.map((note,index)=><fieldset key={index}><legend>实测记录 {index+1}</legend>{note.id&&<p>引用编号：<code>{note.id}</code> · 创作者实测</p>}{note.snapshot_id!==draft.snapshot_id&&<p className="error">这条记录绑定旧快照，请移除后按新资料重新录入。</p>}
        {(Object.keys(noteFields) as (keyof typeof noteFields)[]).map(key=><label key={key}>{noteFields[key]}{key==='tested_on'?<input type="date" value={note[key]} onChange={e=>updateNote(index,key,e.target.value)}/>:<textarea maxLength={key==='procedure'||key==='observation'?600:300} value={note[key]} onChange={e=>updateNote(index,key,e.target.value)}/>}</label>)}
        <button onClick={()=>update({creator_tests:draft.creator_tests.filter((_,i)=>i!==index)})}>移除实测记录 {index+1}</button></fieldset>)}
      <button disabled={draft.creator_tests.length>=5} onClick={()=>update({creator_tests:[...draft.creator_tests,{snapshot_id:draft.snapshot_id,tested_on:'',environment:'',procedure:'',observation:'',limitations:''}]})}>添加实测记录</button>
      <label className="check-label"><input type="checkbox" checked={reviewed} onChange={e=>setReviewed(e.target.checked)}/><span>我已核对所选资料、受众、亮点和本人实测记录，同意保存后重新复核原稿与素材。</span></label>
      <div className="actions"><button disabled={!reviewed} onClick={()=>void act(save)}>保存任务资料</button><button onClick={()=>void act(async()=>{const latest=await api<Data>(`/tasks/${taskId}/context`);setDraft({...draft,revision:latest.current.revision});setReviewed(false);setNotice('已读取最新版本号，当前输入保留。请对照下方服务器当前资料，核对差异后再保存。');setHistory({items:[latest.current],next_before:null});})}>读取最新版本并保留输入</button>
      <button onClick={()=>void act(async()=>setHistory(await api<History>(`/tasks/${taskId}/context/history`)))}>查看资料修改历史</button></div>
    </fieldset>{history&&<section aria-label="资料修改历史"><h3>资料修改历史</h3>{!history.items.length&&<p>尚未调整过任务资料。</p>}{history.items.map(c=><details key={c.id||'baseline'}><summary>{c.revision===0?'初始资料':`任务版本 ${c.revision}`} · {c.audience} · {c.creator_tests.length} 条实测</summary><p>资料快照 {c.snapshot_id}；主亮点 {c.primary_highlight_id}；辅助亮点 {c.secondary_highlight_id||'无'}</p>{c.creator_tests.map((n,i)=><pre key={i}>{JSON.stringify(n,null,2)}</pre>)}</details>)}{history.next_before!==null&&<button disabled={busy} onClick={()=>void act(async()=>{const next=await api<History>(`/tasks/${taskId}/context/history?before=${history.next_before}`);setHistory({items:[...history.items,...next.items],next_before:next.next_before});})}>更早资料版本</button>}</section>}</>}
  </section></details>;
}
