'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import ConfirmDialog from './ConfirmDialog';
import {useSessionValue} from '../lib/useSessionValue';
import {usePolling} from '../lib/usePolling';
import JobProgress, {jobPending, type JobDetail} from './JobProgress';
import { api, ApiError, type Receipt, type Task, type List } from '../lib/api';
import ManuscriptPreview from './ManuscriptPreview';
import PlatformCheck, {platformStatus, type PlatformReport} from './PlatformCheck';

type Issue = {path:string;message:string;severity:string};
type Version = {platform_limits?:PlatformReport;platform_limits_saved?:{policy_hash?:string|null};context_stale?:boolean;id:string;kind:'script'|'copy';generation_id?:string;content:Record<string,unknown>|null;script_id:string|null;valid:boolean;confirmed_at:string|null;evidence_map:Record<string,string[]>;issues:Issue[]};
type StudioTask = Task & {current_script:Version|null;current_copy:Version|null;copy_stale:boolean};
type Data = {platform_rules?:PlatformReport;task:StudioTask;template_intro:string;versions:Version[];groups:{id:string;job_id:string;output:{raw_text:string;issues:Issue[]}|null}[]};
type CleanupPreview = {expected_revision:number;preview_token:string;removable_count:number;protected_count:number;content_bytes:number;note:string};
type Draft = {kind:'script'|'copy';content:Record<string,unknown>;evidence:string;parent_id:string;script_id:string|null;revision:number};
const names:Record<string,string>={pain:'痛点',intro:'栏目句',feature:'核心亮点',example:'使用示例',body:'发布正文',title:'标题',tags:'标签（用逗号分隔）'};

async function readAttention(path:string):Promise<List<JobDetail>> {
  const items:JobDetail[]=[];let cursor:string|null=null;const cursors=new Set<string>();
  do{
    const page:List<JobDetail>=await api<List<JobDetail>>(path+(cursor?'&cursor='+encodeURIComponent(cursor):''));
    items.push(...page.items);cursor=page.next_cursor;
    if(cursor){if(cursors.has(cursor))throw new Error('处理记录分页暂时无法继续，请重新读取。');cursors.add(cursor);}
  }while(cursor);
  return {items,next_cursor:null};
}

export default function TextStudio({taskId, authorize,focusGroupId,focusRequest=0}:{taskId:string;focusGroupId?:string;focusRequest?:number;authorize:(action:string,input:Record<string,unknown>)=>Promise<string|undefined>}) {
  const {data,error:readError,refresh}=usePolling<Data>(`/tasks/${taskId}/texts`,2500);
  const {data:activeJobs,error:jobsError}=usePolling<List<JobDetail>>(`/jobs?task_id=${taskId}&bucket=attention&limit=50`,3000,readAttention);
  const [uncertainValue,setUncertainValue]=useSessionValue('hexing-generation-unknown-'+taskId);
  const uncertain=useMemo(()=>{try{return uncertainValue?JSON.parse(uncertainValue) as string[]:null;}catch{return null;}},[uncertainValue]);
  function setUncertain(value:string[]|null){setUncertainValue(value===null?null:JSON.stringify(value));}
  const recentJob=data?.groups[0]?.job_id;
  const {data:latestJob}=usePolling<JobDetail>(recentJob?'/jobs/'+recentJob:null,3000);
  const generationBusy=(!!recentJob&&jobPending(latestJob?.status))||!activeJobs||!!jobsError||activeJobs.items.some(j=>j.kind==='generation'&&jobPending(j.status))||!!uncertain;
  useEffect(()=>{if(uncertain&&data?.groups.some(g=>!uncertain.includes(g.job_id)))setUncertainValue(null);},[data,uncertain,setUncertainValue]);
  const [tabSelection,setTabSelection]=useState<{kind:'script'|'copy';focusKey:string}>({kind:'script',focusKey:''});
  const focusKey=(focusGroupId||'')+':'+focusRequest;
  const focusedVersion=focusGroupId?data?.versions.find(v=>v.generation_id===focusGroupId):undefined;
  const tab=tabSelection.focusKey!==focusKey&&focusedVersion?focusedVersion.kind:tabSelection.kind;
  function setTab(kind:'script'|'copy'){setTabSelection({kind,focusKey});}
  const root=useRef<HTMLElement|null>(null);const focusedResult=useRef('');
  const [draft,setDraft]=useState<Draft|null>(null);const draftRef=useRef<Draft|null>(null);
  const [dirty,setDirty]=useState(false);const [needsSave,setNeedsSave]=useState(false);const editCounter=useRef(0);
  const [status,setStatus]=useState('');const [error,setError]=useState('');const [busy,setBusy]=useState(false);
  const saving=useRef(false);const actionLock=useRef(false);
  const [instruction,setInstruction]=useState('');const [section,setSection]=useState('all');
  const [reviewed,setReviewed]=useState(false);const reviewedRevision=useRef<number|null>(null);
  const [cleanup,setCleanup]=useState<CleanupPreview|null>(null);
  const pendingSave=useRef<{signature:string;key:string}|null>(null);
  useEffect(()=>{
    let alive=true;
    void refresh().then(()=>{
      if(!alive)return;
      const saved=sessionStorage.getItem('hexing-draft-'+taskId);
      if(saved){try{const value=JSON.parse(saved) as Draft;draftRef.current=value;setDraft(value);setTabSelection(current=>({...current,kind:value.kind}));setNeedsSave(true);setStatus('已恢复浏览器草稿，请对照当前版本后保存。');}catch{setStatus('草稿恢复失败，请查看历史版本。');}}
    }).catch(()=>{});
    return()=>{alive=false;};
  },[taskId,refresh]);
  useEffect(()=>{
    if(!focusGroupId||!data)return;
    const key=focusGroupId+':'+focusRequest;if(focusedResult.current===key)return;
    const version=data.versions.find(v=>v.generation_id===focusGroupId);
    const element=document.getElementById(version?'text-version-'+version.id:'generation-'+focusGroupId);
    if(!element||!root.current?.contains(element))return;
    let parent=element.parentElement;
    while(parent&&parent!==root.current){if(parent instanceof HTMLDetailsElement)parent.open=true;parent=parent.parentElement;}
    if(element instanceof HTMLDetailsElement)element.open=true;
    element.focus({preventScroll:true});element.scrollIntoView({block:'start'});focusedResult.current=key;
  },[data,focusGroupId,focusRequest,tab]);
  useEffect(()=>{const changed=()=>{setReviewed(false);void refresh().catch(()=>{});};window.addEventListener('hexing-context-changed',changed);return()=>window.removeEventListener('hexing-context-changed',changed);},[refresh]);
  function remember(value:Draft){draftRef.current=value;setDraft(value);try{sessionStorage.setItem('hexing-draft-'+taskId,JSON.stringify(value));}catch{setError('浏览器无法保存草稿，请复制内容留存。');}}
  const save=useCallback(async()=>{
    const value=draftRef.current;if(!value||saving.current||actionLock.current)return;
    saving.current=true;setStatus('保存中…');setError('');
    const count=editCounter.current;
    try{
      const evidence=JSON.parse(value.evidence);
      const body={kind:value.kind,content:value.content,evidence_map:evidence,parent_id:value.parent_id,script_id:value.script_id,expected_revision:value.revision};
      const signature=JSON.stringify(body);
      if(pendingSave.current?.signature!==signature)pendingSave.current={signature,key:crypto.randomUUID()};
      const result=await api<{version:Version;task:StudioTask;changed:boolean}>(`/tasks/${taskId}/texts`,body,'POST',pendingSave.current.key);
      pendingSave.current=null;
      const next={...draftRef.current!,parent_id:result.version.id,revision:result.task.revision};
      draftRef.current=next;setDraft(next);
      if(count===editCounter.current){setDirty(false);setNeedsSave(false);sessionStorage.removeItem('hexing-draft-'+taskId);setStatus(!result.changed?'内容未变化，已保留当前版本和确认状态。':result.version.valid?'已保存为当前草稿，尚未确认。':'已保存，仍有内容问题待修正。');}
      else{sessionStorage.setItem('hexing-draft-'+taskId,JSON.stringify(next));setStatus('继续保存新的修改…');}
      await refresh();window.dispatchEvent(new CustomEvent('hexing-text-changed',{detail:taskId}));
    }catch(e){setDirty(false);setNeedsSave(true);setStatus('保存失败，编辑内容已保留，可复制或重试。');setError((e as Error).message);}
    finally{saving.current=false;}
  },[taskId,refresh]);
  useEffect(()=>{if(!dirty||busy)return;const timer=setTimeout(()=>void save(),900);return()=>clearTimeout(timer);},[dirty,draft,busy,save]);
  function edit(v:Version){if(dirty||saving.current)return;setTab(v.kind);const value:Draft={kind:v.kind,content:{...(v.content||{}),...(v.kind==='script'?{intro:data!.template_intro}:{})},evidence:JSON.stringify(v.evidence_map,null,2),parent_id:v.id,script_id:v.kind==='copy'?(data?.task.current_script?.id||v.script_id):null,revision:data!.task.revision};remember(value);setStatus(v.kind==='copy'?'正在对照当前口播编辑文案，保存后重新确认。':'正在编辑，修改后自动另存版本。');setReviewed(false);}
  function update(key:string,value:string){const current=draftRef.current!;remember({...current,content:{...current.content,[key]:key==='tags'?value.split(/[,，]/).map(v=>v.trim()).filter(Boolean):value}});editCounter.current++;setDirty(true);setNeedsSave(true);setReviewed(false);}
  async function perform(fn:()=>Promise<void>){if(actionLock.current||saving.current)return;actionLock.current=true;setBusy(true);setError('');try{await fn();await refresh();setReviewed(false);}catch(e){setError((e as Error).message);}finally{actionLock.current=false;setBusy(false);}}
  async function generate(mode:'joint'|'script'|'copy'){
    if(generationBusy)throw new Error('请先查看本任务正在处理的生成或核对未知结果。');
    const latest=await refresh();const body={task_id:taskId,expected_revision:latest.task.revision,mode,base_script_id:latest.task.current_script?.id||null,rewrite_instruction:instruction,rewrite_section:section};
    const permission=await authorize('generation',body);
    const baseline=latest.groups.map(g=>g.job_id);
    sessionStorage.setItem('hexing-generation-unknown-'+taskId,JSON.stringify(baseline));setUncertain(baseline);
    try{
      await api<Receipt>(`/tasks/${taskId}/generations`,{...body,cost_authorization_id:permission});
      setStatus('生成已受理，结果另存候选，不覆盖当前稿。');
    }catch(e){
      if(e instanceof ApiError&&e.status>=400&&e.status<500){sessionStorage.removeItem('hexing-generation-unknown-'+taskId);setUncertain(null);}
      throw e;
    }
  }
  async function transition(v:Version,action:'adopt'|'confirm'){
    await api(`/tasks/${taskId}/texts/${v.id}/${action}`,{expected_revision:action==='confirm'?reviewedRevision.current:data!.task.revision,...(action==='confirm'?{facts_reviewed:true}:{})});
    window.dispatchEvent(new CustomEvent('hexing-text-changed',{detail:taskId}));
    setTab(v.kind);
    setStatus(action==='adopt'?'已采用候选，请核对后分别确认。':'已确认当前版本。');draftRef.current=null;setDraft(null);sessionStorage.removeItem('hexing-draft-'+taskId);
  }
  async function confirmCleanup(){
    if(!cleanup)return;
    const result=await api<{deleted_count:number}>(`/tasks/${taskId}/texts/cleanup`,{expected_revision:cleanup.expected_revision,preview_token:cleanup.preview_token,confirm:true});
    setCleanup(null);setStatus(`已清理 ${result.deleted_count} 条旧版本，受保护版本和生成原文已保留。`);
  }
  function copyText(text:string,label:string){setStatus('');setError('');void navigator.clipboard.writeText(text).then(()=>setStatus(`已复制${label}。`)).catch(()=>setError('复制失败，请手动选择正文复制。'));}
  if(!data)return <section className="panel text-studio">{readError?<><p role="alert" className="error">稿件读取失败：{readError}</p><button onClick={()=>void refresh().catch(()=>{})}>重新读取稿件</button></>:<p role="status">正在读取稿件…</p>}</section>;
  const currentIds=[data.task.current_script?.id,data.task.current_copy?.id];
  const unsaved=dirty||needsSave;
  const renderVersion=(v:Version)=><article className="manuscript-card" hidden={currentIds.includes(v.id)&&v.kind!==tab} key={v.id} id={'text-version-'+v.id} tabIndex={-1}><h4>{v.kind==='script'?'口播':'文案'} · {currentIds.includes(v.id)?'当前版本':'候选／历史'} · {v.context_stale?'资料已变 · 待复核':v.confirmed_at?'已确认':'草稿'} · {v.id.slice(0,8)}</h4>
      {v.kind==='copy'&&<small>关联口播 {v.script_id?.slice(0,8)}{v.script_id!==data.task.current_script?.id?' · 尚未关联当前口播':''}</small>}
      <ManuscriptPreview kind={v.kind} content={v.content} onCopy={copyText}/><details className="manuscript-raw"><summary>查看结构化原文</summary><pre>{JSON.stringify(v.content,null,2)}</pre></details>{v.kind==='copy'&&v.platform_limits&&<PlatformCheck report={v.platform_limits} savedHash={v.platform_limits_saved?.policy_hash}/>}<ul>{v.issues.map((i,n)=><li key={n}>{i.message}</li>)}</ul>
      <div className="actions"><button disabled={busy||unsaved} onClick={()=>edit(v)}>{v.kind==='copy'?'对照当前稿编辑':'编辑口播'}</button>{!currentIds.includes(v.id)&&<button disabled={busy||unsaved||!v.valid} onClick={()=>void perform(()=>transition(v,'adopt'))}>采用此{v.kind==='script'?'口播':'文案'}</button>}{!v.confirmed_at&&<button disabled={busy||unsaved||!reviewed||!v.valid||!currentIds.includes(v.id)||!!v.confirmed_at||(v.kind==='copy'&&(!data.task.current_script?.confirmed_at||v.script_id!==data.task.current_script.id))} onClick={()=>void perform(()=>transition(v,'confirm'))}>确认{v.kind==='script'?'口播':'文案'}</button>}<button onClick={()=>copyText(v.kind==='script'?['pain','intro','feature','example'].map(k=>typeof v.content?.[k]==='string'?v.content[k]:'').join('\n'):typeof v.content?.body==='string'?v.content.body:'',v.kind==='script'?'连续正文':'发布正文')}>复制{v.kind==='script'?'连续正文':'发布正文'}</button></div>
    </article>;
  return <section className="panel text-studio" ref={root} data-task-id={taskId} data-editing={!!draft}><div className="editor-heading"><h2>正式文本创作</h2><span className="editor-save-state">{draft?(unsaved?'待保存':'已保存'):'已读取保存版本'}</span></div>
    <div className="manuscript-tabs" role="tablist" aria-label="稿件类型">{(['script','copy'] as const).map(kind=><button key={kind} id={'tab-'+kind} role="tab" aria-selected={tab===kind} aria-controls="current-manuscript" tabIndex={tab===kind?0:-1} onClick={()=>setTab(kind)} onKeyDown={e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?'script':e.key==='End'?'copy':tab==='script'?'copy':'script';setTab(next);document.getElementById('tab-'+next)?.focus();}}}>{kind==='script'?'口播':'发布文案'}</button>)}</div>
    {data.task.copy_stale&&<p className="mode-note">当前文案基于旧稿，需对照当前口播编辑并重新确认。</p>}

    {readError&&<p className="error" role="alert">暂时无法更新稿件，已显示的内容保留。{readError}<button onClick={()=>void refresh().catch(()=>{})}>重新读取稿件</button></p>}{status&&<p role="status">{status}</p>}{error&&<p role="alert" className="error">{error}</p>}
    {jobsError&&<p className="error" role="alert">无法核对生成状态，暂缓新生成。{jobsError}</p>}
    {uncertain&&<section className="mode-note"><h3>提交结果待核对</h3><p>正在核对这次提交，服务端可能已受理。系统会继续读取记录，不会自动重新生成。</p><Link href="/activity">先查看处理记录 →</Link><button disabled={busy||!activeJobs||!!jobsError||activeJobs.items.some(j=>j.kind==='generation'&&jobPending(j.status))} onClick={()=>{sessionStorage.removeItem('hexing-generation-unknown-'+taskId);setUncertain(null);setStatus('已结束本次提交核对。重新生成仍需单独确认费用。');}}>已核对未创建新记录，解除提交锁定</button></section>}
    {recentJob&&<details className="generation-progress" open={!!latestJob&&jobPending(latestJob.status)}><summary>最近一次生成 · {latestJob?.status_label||'读取进度中'}</summary><JobProgress jobId={recentJob}/></details>}
    {data.groups[0]&&data.versions.some(v=>v.generation_id===data.groups[0].id&&!currentIds.includes(v.id))&&<div className="candidate-ready"><p>最近一次候选已留存。对照来源后，选择要采用的口播与文案。</p><Link className="button" href={`/tasks/${taskId}/generations/${data.groups[0].id}`}>查看本次候选 →</Link></div>}
    {draft&&<div className="muted-box draft-editor" hidden={draft.kind!==tab}><h3>{draft.kind==='script'?'口播编辑':'文案编辑'}</h3>{(draft.kind==='script'?['pain','intro','feature','example']:['body','title','tags']).map(key=><label key={key}>{names[key]}<textarea aria-label={names[key]} value={Array.isArray(draft.content[key])?(draft.content[key] as string[]).join(', '):String(draft.content[key]??'')} readOnly={key==='intro'} onChange={e=>update(key,e.target.value)}/></label>)}
      <details><summary>来源引用</summary><textarea aria-label="来源引用 JSON" value={draft.evidence} onChange={e=>{remember({...draft,evidence:e.target.value});editCounter.current++;setDirty(true);setNeedsSave(true);}}/></details>
      <div className="actions"><button disabled={busy} onClick={()=>void save()}>保存／重试</button><button onClick={()=>void navigator.clipboard.writeText(JSON.stringify(draft.content,null,2)).then(()=>setStatus('已复制编辑内容。')).catch(()=>setError('复制失败，请手动选择文本复制。'))}>复制编辑内容</button><button disabled={busy||unsaved} onClick={()=>{setDraft(null);draftRef.current=null;sessionStorage.removeItem('hexing-draft-'+taskId);setStatus('已结束编辑。');}}>结束编辑</button><button disabled={busy||dirty} onClick={()=>{remember({...draft,revision:data.task.revision});setStatus('已对照最新版本，可点击保存。');}}>已对照最新版本</button></div>
    </div>}
    <div id="current-manuscript" role="tabpanel" aria-labelledby={'tab-'+tab}><h3 className="sr-only">当前稿件与历史候选</h3>{data.versions.length===0&&<p>还没有正式稿件，请先生成候选。</p>}
    {data.versions.filter(v=>currentIds.includes(v.id)).sort((a,b)=>a.kind===b.kind?0:a.kind==='script'?-1:1).map(renderVersion)}
    {data.versions.length>0&&!data.versions.some(v=>currentIds.includes(v.id))&&<p>尚未采用稿件，请展开历史候选选择口播和文案。</p>}
    {!data.versions.some(v=>currentIds.includes(v.id)&&v.kind===tab)&&data.versions.length>0&&<p className="muted">{tab==='script'?'尚未采用口播。':'尚未采用发布文案。'}可在历史候选中选择，或展开下方生成入口。</p>}
    </div>
    {(tab==='script'?!data.task.current_script?.confirmed_at:!data.task.current_copy?.confirmed_at)&&<label className="check-label"><input type="checkbox" checked={reviewed} disabled={busy||unsaved} onChange={e=>{setReviewed(e.target.checked);reviewedRevision.current=data.task.revision;}}/><span>我已核对来源，确认痛点、所选亮点、示例及必要条件准确；文案与当前口播一致。</span></label>}
    <details className="rewrite-panel" open={!data.versions.length}><summary>{data.versions.length?'继续优化 · 生成与改写':'开始创作 · 生成候选'}</summary>
    <label>改写要求（可填写压缩、扩写或指定段落）<input value={instruction} onChange={e=>setInstruction(e.target.value)} maxLength={1000}/></label>
    <label>改写范围<select value={section} onChange={e=>setSection(e.target.value)}><option value="all">整体（可压缩或扩写）</option><option value="pain">只改痛点</option><option value="feature">只改核心亮点</option><option value="example">只改使用示例</option></select></label><div className="actions">{(['joint','script','copy'] as const).map(mode=><button key={mode} disabled={busy||unsaved||generationBusy||(mode==='copy'&&!data.task.current_script)||(section!=='all'&&(mode!=='script'||!data.task.current_script))} onClick={()=>void perform(()=>generate(mode))}>{mode==='joint'?'生成口播与文案':mode==='script'?'只生成／改写口播':'只生成／改写文案'}</button>)}</div>
    </details>
    <div className="actions"><button disabled={busy||unsaved||!!draft} onClick={()=>void perform(async()=>setCleanup(await api<CleanupPreview>(`/tasks/${taskId}/texts/cleanup-preview`)))}>清理历史版本</button></div>
    {!!draft&&<small>请先保存并结束编辑，再预览清理范围。</small>}
    {cleanup&&<ConfirmDialog title="确认历史清理范围" confirmLabel={`确认清理 ${cleanup.removable_count} 条旧版本`} cancelLabel="取消清理" busy={busy} confirmDisabled={unsaved||!!draft||cleanup.removable_count===0} onConfirm={()=>void perform(confirmCleanup)} onCancel={()=>setCleanup(null)}>{error&&<p role="alert" className="error">{error}</p>}<p>可清理 {cleanup.removable_count} 条，保留 {cleanup.protected_count} 条。待清理正文约 {(cleanup.content_bytes/1024).toFixed(1)} KB。</p><p>{cleanup.note}</p><p>清理后无法从版本列表恢复；数据库文件大小可能不会立即缩小。</p></ConfirmDialog>}
    <details className="history-versions"><summary>历史候选与旧版本（{data.versions.filter(v=>!currentIds.includes(v.id)).length}）</summary>
    {data.versions.filter(v=>!currentIds.includes(v.id)).map(renderVersion)}
    {data.groups.map(g=><details key={g.id} id={'generation-'+g.id} tabIndex={-1}><summary>生成原文与失败留存 · {g.id.slice(0,8)}</summary><pre>{g.output?.raw_text??'作业尚未返回；可查看处理记录。'}</pre></details>)}
    </details>
    <small>抖音发布规则：{platformStatus[data.platform_rules?.status||'unverified']}。素材确认不代表平台审核；文案中的“抖音发布检查”可查看数量、来源和日期。确认当前口播后，可到“配音与素材导出”生成配音并导出素材。</small>
  </section>;
}
