'use client';
import Link from 'next/link';
import {voiceLabel} from '../lib/voiceName';
import { useEffect, useRef, useState } from 'react';
import {usePolling} from '../lib/usePolling';
import {useSessionValue} from '../lib/useSessionValue';
import MediaAudio from './MediaAudio';
import DownloadButton from './DownloadButton';
import JobProgress,{jobPending,type JobDetail} from './JobProgress';
import { api, ApiError, type Receipt, type Task, type Voice } from '../lib/api';

type Text={id:string;confirmed_at:string|null;content:Record<string,unknown>};
type AudioVersion={can_confirm:boolean;context_stale:boolean;id:string;job_id:string;script_id:string;voice_id:string;voice_revision:number;asset_id:string|null;created_at:string;confirmed_at:string|null;status:string;job_status:string;stale:boolean;duration_valid:boolean;available:boolean;duration_ms:number|null;original_text:string;synthesis_text:string;pronunciation:string;is_test_tone:boolean};
type Package={id:string;job_id:string;status:string;complete:boolean;available:boolean;task_revision:number;mode:string;download_path:string|null;missing:string[]};
type Data={task:Task&{current_script:Text|null;current_copy:Text|null;data_mode:string};materials:{ready:boolean;missing:string[];audio:AudioVersion|null};versions:AudioVersion[];voices:Voice[];exports:Package[]};
type Preview={original_text:string;synthesis_text:string;tool_name:string;voice_revision:number};

export default function AudioStudio({taskId,authorize,focusId,focusType,focusRequest=0}:{taskId:string;authorize:(action:string,input:Record<string,unknown>)=>Promise<string|undefined>;focusId?:string;focusType?:string;focusRequest?:number}){
  const {data,error:readError,refresh}=usePolling<Data>(`/tasks/${taskId}/audio`,3000);
  const [operation,setOperation]=useSessionValue('hexing-audio-operation-'+taskId);
  const [baseline,setBaseline]=useSessionValue('hexing-audio-baseline-'+taskId);
  const {data:operationJob}=usePolling<JobDetail>(operation&&operation!=='unknown'?'/jobs/'+operation:null);
  const [exportJob,setExportJob]=useSessionValue('hexing-export-operation-'+taskId);const [error,setError]=useState('');const [notice,setNotice]=useState('');
  const [voiceId,setVoiceId]=useSessionValue('hexing-task-voice-'+taskId);const [pronunciation,setPronunciation]=useState('');const [pronunciationReviewed,setPronunciationReviewed]=useState(false);
  const [preview,setPreview]=useState<{signature:string;value:Preview}|null>(null);const [textReviewed,setTextReviewed]=useState(false);
  const [reviewedAudio,setReviewedAudio]=useState<string|null>(null);const reviewedRevision=useRef<number|null>(null);const [busy,setBusy]=useState(false);const lock=useRef(false);const focused=useRef('');const pendingSubmission=useRef<{signature:string;body:Record<string,unknown>;key:string}|null>(null);
  useEffect(()=>{
    if(operation!=='unknown'||!data||!baseline)return;
    try{const ids=JSON.parse(baseline) as string[];const recovered=data.versions.find(v=>!ids.includes(v.id));if(recovered){pendingSubmission.current=null;setOperation(recovered.job_id);setBaseline(null);}}catch{/* original record remains available */}
  },[data,operation,baseline,setOperation,setBaseline]);
  useEffect(()=>{
    if(!data||!focusId)return;const key=focusId+':'+focusRequest;if(focused.current===key)return;
    const element=document.getElementById((focusType==='exports'?'export-':'audio-')+focusId);if(!element)return;
    let parent=element.parentElement;while(parent){if(parent instanceof HTMLDetailsElement)parent.open=true;parent=parent.parentElement;}
    element.focus({preventScroll:true});element.scrollIntoView({block:'start'});focused.current=key;
  },[data,focusId,focusType,focusRequest]);
  useEffect(()=>{const changed=()=>{setReviewedAudio(null);setTextReviewed(false);void refresh().catch(e=>setError(e.message));};window.addEventListener('hexing-context-changed',changed);window.addEventListener('hexing-text-changed',changed);return()=>{window.removeEventListener('hexing-context-changed',changed);window.removeEventListener('hexing-text-changed',changed);};},[refresh]);
  async function act(fn:()=>Promise<void>){if(lock.current)return;lock.current=true;setBusy(true);setError('');setNotice('');try{await fn();await refresh();}catch(e){setError((e as Error).message);}finally{lock.current=false;setBusy(false);}}
  if(!data)return <section className="panel audio-studio"><h2>配音与素材导出</h2>{readError?<p role="alert" className="error">配音读取失败：{readError}<button onClick={()=>void refresh().catch(()=>{})}>重新读取配音</button></p>:<p role="status">正在读取配音…</p>}</section>;
  const script=data.task.current_script;
  const preferredId=voiceId||data.materials.audio?.voice_id||data.versions[0]?.voice_id||data.voices.find(v=>v.is_default&&v.eligible)?.id||data.voices.find(v=>v.eligible)?.id;
  const voice=data.voices.find(v=>v.id===preferredId);
  const audioPending=data.versions.some(v=>jobPending(v.job_status))||!!operation&&(operation==='unknown'||jobPending(operationJob?.status));
  const packing=data.exports.some(e=>['queued','running'].includes(e.status));
  const body={task_id:taskId,script_id:script?.id||'',probe_voice_id:voice?.id||'',voice_revision:voice?.revision||1,expected_revision:data.task.revision,pronunciation,pronunciation_reviewed:pronunciationReviewed};
  const signature=JSON.stringify(body);const validPreview=preview?.signature===signature;
  const canPreview=!!script?.confirmed_at&&voice?.eligible===true&&(!pronunciation||pronunciationReviewed);
  async function generate(){
    if(!validPreview||!textReviewed||audioPending)return;
    if(pendingSubmission.current?.signature!==signature){
      const permission=await authorize('audio_generation',body);
      pendingSubmission.current={signature,body:{...body,cost_authorization_id:permission},key:crypto.randomUUID()};
    }
    const pending=pendingSubmission.current;
    setBaseline(JSON.stringify(data!.versions.map(v=>v.id)));setOperation('unknown');
    try{
      const r=await api<Receipt>(`/tasks/${taskId}/audio-generations`,pending.body,'POST',pending.key);
      pendingSubmission.current=null;setBaseline(null);setOperation(r.job_id);setNotice('完整配音已受理。新结果单独留存，请在本次进度中查看。');setTextReviewed(false);
    }catch(e){if(e instanceof ApiError&&e.status>=400&&e.status<500){setOperation(null);pendingSubmission.current=null;}throw e;}
  }
  function confirmVersion(id:string){void act(async()=>{await api(`/tasks/${taskId}/audio/${id}/confirm`,{expected_revision:reviewedRevision.current,accepted:true});setReviewedAudio(null);setNotice('试听确认已保存，已采用这份配音。');});}
  const featured=data.materials.audio||data.versions[0];
  const history=data.versions.filter(v=>v.id!==featured?.id);
  const currentPackage=data.exports.find(e=>e.complete&&e.available&&e.task_revision===data.task.revision&&e.download_path);
  return <section id="voice-creation" className="panel audio-studio"><div className="panel-heading"><div><h2>配音与素材导出</h2></div><Link href="/voices">管理音色 ↗</Link></div>
    {readError&&<p className="error" role="alert">配音暂时无法更新，已有结果保留。{readError}<button onClick={()=>void refresh().catch(()=>{})}>重新读取配音</button></p>}
    {operation==='unknown'?<div className="mode-note"><p>合成提交结果待核对，请先查看原处理记录。不会自动重新合成。</p><Link href="/activity">核对原记录 →</Link><button disabled={busy||data.versions.some(v=>jobPending(v.job_status))} onClick={()=>setOperation(null)}>已核对原记录，解除提交锁定</button></div>:operation&&<details className="audio-operation" open={jobPending(operationJob?.status)}><summary>本次配音 · {operationJob?.status_label||'正在读取'}</summary><JobProgress jobId={operation}/></details>}
    {notice&&<p role="status" className="notice">{notice}</p>}{error&&<p role="alert" className="error">{error}</p>}
    <div className="selected-voice"><span className="voice-orb" aria-hidden="true">♫</span><div><small>当前音色</small><strong>{voice?voiceLabel(voice):(preferredId?'原音色暂不可用':'尚未选择音色')}</strong></div><button className="text-button" onClick={()=>{const section=document.getElementById('audio-generation-controls');if(section instanceof HTMLDetailsElement){section.open=true;section.querySelector('select')?.focus();}}}>更换</button></div>
    {preferredId&&!voice?.eligible&&<p className="mode-note">原来选中的声音当前不可用于配音，请点“更换”选择可用声音。已有音频保留。</p>}
    <h3>已保存配音</h3>{featured?<AudioResult v={featured} busy={busy} reviewedAudio={reviewedAudio} onReviewed={id=>{setReviewedAudio(id);reviewedRevision.current=data.task.revision;}} onConfirm={confirmVersion}/>:<p className="muted">还没有正式配音。确认口播后，展开下方入口预览文字并生成。</p>}
    <div className="export-panel"><h3>素材准备情况</h3><ul className="material-checklist"><li>{data.task.current_script?.confirmed_at?'✓ 口播已确认':'○ 口播待确认'}</li><li>{data.task.current_copy?.confirmed_at&&!data.task.copy_stale?'✓ 发布文案已确认':'○ 发布文案待确认'}</li><li>{data.materials.ready?'✓ 配音与当前口播一致':'○ 配音与素材状态待核对'}</li></ul>{data.materials.ready?<p className="notice">{data.task.data_mode==='mock'?'模拟素材流程已就绪。':'当前口播、音频和文案版本一致，已完成确认。'}</p>:<p>完整包还缺：{data.materials.missing.join('；')}。</p>}
    {!data.materials.ready&&<div className="actions material-next-actions">{!script?.confirmed_at&&<button onClick={()=>{document.getElementById('tab-script')?.click();document.getElementById('text-creation')?.scrollIntoView({block:'start'});document.getElementById('tab-script')?.focus();}}>去确认口播</button>}{(!data.task.current_copy?.confirmed_at||data.task.copy_stale)&&<button onClick={()=>{document.getElementById('tab-copy')?.click();document.getElementById('tab-copy')?.focus();}}>去核对发布文案</button>}{!voice?.eligible?<Link href="/voices">选择可用声音 →</Link>:(!data.materials.audio?.confirmed_at||data.materials.audio.stale||data.materials.audio.context_stale||!data.materials.audio.available||!data.materials.audio.duration_valid)&&<button onClick={()=>{const panel=document.getElementById('audio-generation-controls');if(panel instanceof HTMLDetailsElement){panel.open=true;panel.scrollIntoView({block:'start'});panel.querySelector('select')?.focus();}}}>准备配音</button>}</div>}
    {exportJob&&<details className="export-operation" open={packing}><summary>本次打包进度与结果</summary><JobProgress jobId={exportJob}/></details>}
    {currentPackage&&data.materials.ready&&<DownloadButton className="button primary package-download" url={currentPackage.download_path!} label={data.task.data_mode==='mock'?'下载模拟素材包':'下载完整素材包'} filename="AI-Hexing-素材包.zip"/>}
    <details className="individual-exports" open={!currentPackage}><summary>单项下载与其他打包方式</summary>
    <div className="actions">{[data.task.current_script,data.task.current_copy].map((v,i)=>v&&<div key={v.id}><DownloadButton url={`/api/v1/tasks/${taskId}/texts/${v.id}/download?format=txt`} label={(i===0?'口播':'文案')+' TXT'} filename="AI-Hexing-文稿.txt"/><DownloadButton url={`/api/v1/tasks/${taskId}/texts/${v.id}/download?format=md`} label="Markdown" filename="AI-Hexing-文稿.md"/></div>)}</div>

    <div className="actions package-actions"><button className={currentPackage?'':'primary'} disabled={busy||packing||!data.materials.ready} onClick={()=>void act(async()=>{const r=await api<Receipt>(`/tasks/${taskId}/exports`,{expected_revision:data.task.revision,complete:true},'POST',`export-${taskId}-${data.task.revision}-complete`);setExportJob(r.job_id);setNotice('已固定当前版本，正在打包；此操作不调用模型。');})}>{data.task.data_mode==='mock'?'打包模拟验收素材':'打包完整素材'}</button><button disabled={busy||packing||(!script&&!data.task.current_copy&&!data.materials.audio)} onClick={()=>void act(async()=>{const r=await api<Receipt>(`/tasks/${taskId}/exports`,{expected_revision:data.task.revision,complete:false},'POST',`export-${taskId}-${data.task.revision}-partial`);setExportJob(r.job_id);setNotice('正在打包部分素材，包内将列明缺项与版本。');})}>打包当前部分素材</button></div>
    </details>
    <details className="package-history"><summary>素材包版本与下载（{data.exports.length}）</summary>{data.exports.map(e=><article id={'export-'+e.id} tabIndex={-1} key={e.id} className="muted-box"><strong>{e.mode==='mock'?'模拟验收包':e.complete?'完整素材包':'部分素材包'} · 任务版本 {e.task_revision}</strong><p>{e.available?'素材包已保存；内容固定于提交时版本。':e.status==='queued'||e.status==='running'?'正在打包…':'素材包不可下载，可从固定素材重新打包。'}</p>{e.download_path&&<DownloadButton url={e.download_path} label="下载 ZIP 素材包" filename="AI-Hexing-素材包.zip"/>}<button disabled={busy||packing||['queued','running'].includes(e.status)} onClick={()=>void act(async()=>{const r=await api<Receipt>(`/exports/${e.id}/rebuild`,{confirm:true});setExportJob(r.job_id);setNotice('正在按原版本重新打包，不调用模型。');})}>重新打包原版本</button></article>)}</details>
    <small>临时包保留 24 小时，过期可重新打包。素材完整不代表平台审核通过，剪辑后的成片时长仍需复核。</small></div>
    {history.length>0&&<details><summary>历史配音（{history.length}）{history.some(v=>['queued','running','failed','needs_reconciliation'].includes(v.job_status))?' · 有待处理记录':''}</summary>{history.map(v=><AudioResult key={v.id} v={v} busy={busy} reviewedAudio={reviewedAudio} onReviewed={id=>{setReviewedAudio(id);reviewedRevision.current=data.task.revision;}} onConfirm={confirmVersion}/>)}</details>}
    <details id="audio-generation-controls" className="audio-generation-controls" open={!data.versions.length}><summary>选择音色 · 预览文字 · 生成配音</summary>
    {!script?.confirmed_at?<p className="notice">请先在正文区采用并确认当前口播，随后即可生成正式配音。</p>:<p>当前已确认口播 · {script.id.slice(0,8)}。合成使用四段连续正文。</p>}
    <div className="voice-form"><label>用于本次配音的声音<select value={preferredId||''} onChange={e=>{setVoiceId(e.target.value);setTextReviewed(false);}} disabled={busy}><option value="">请选择已确认音色</option>{preferredId&&!voice?.eligible&&<option value={preferredId} disabled>{voice?voiceLabel(voice):'原声音'}（不可用，请重新选择）</option>}{data.voices.filter(v=>v.eligible).map(v=><option key={v.id} value={v.id}>{voiceLabel(v)}{v.is_default?'（默认）':''}</option>)}</select></label>
    <label>工具名读法（可选）<input value={pronunciation} maxLength={60} placeholder="例如：赫星，仅替换工具名" disabled={busy} onChange={e=>{setPronunciation(e.target.value);setPronunciationReviewed(false);setTextReviewed(false);}}/></label></div>
    {pronunciation&&<label className="check-label"><input type="checkbox" checked={pronunciationReviewed} onChange={e=>setPronunciationReviewed(e.target.checked)}/><span>替换只用于工具名发音，不增加或改变正文含义。</span></label>}
    <button disabled={busy||!canPreview} onClick={()=>void act(async()=>{const latest=await refresh();const currentVoice=latest.voices.find(v=>v.id===voice?.id);const previewBody={...body,script_id:latest.task.current_script?.id||'',expected_revision:latest.task.revision,voice_revision:currentVoice?.revision||1};const value=await api<Preview>(`/tasks/${taskId}/audio-preview`,previewBody);setPreview({signature:JSON.stringify(previewBody),value});setTextReviewed(false);})}>预览合成文字</button><small>预览不调用模型。生成配音将单独提示本次费用。</small>
    {validPreview&&<div className="muted-box"><h3>即将合成的完整文字</h3><pre>{preview.value.synthesis_text}</pre><label className="check-label"><input type="checkbox" checked={textReviewed} onChange={e=>setTextReviewed(e.target.checked)}/><span>我已核对合成文字和工具名读法。</span></label><button className="primary" disabled={busy||audioPending||!textReviewed||!canPreview} onClick={()=>void act(generate)}>生成完整配音</button></div>}
    </details>
  </section>;
}

function AudioResult({v,busy,reviewedAudio,onReviewed,onConfirm}:{v:AudioVersion;busy:boolean;reviewedAudio:string|null;onReviewed:(id:string|null)=>void;onConfirm:(id:string)=>void}){return <article key={v.id} id={'audio-'+v.id} tabIndex={-1} className="audio-version muted-box">
    <div className="panel-heading"><strong>配音 · {v.id.slice(0,8)}</strong><span className={`badge ${v.stale||!v.duration_valid?'warning':''}`}>{v.status}</span></div>

    {v.duration_ms!=null&&<div className="duration-check"><span className="duration-number">{(v.duration_ms/1000).toFixed(v.duration_valid?2:6)}<small> 秒</small></span><div><strong>{v.duration_valid?(v.confirmed_at?(v.stale||v.context_stale?'时长达标 · 旧版试听记录':'时长达标，已试听确认'):'时长达标，仍需完整试听'):'时长未达标'}</strong><p>目标 30–35 秒{!v.duration_valid?(v.duration_ms<30000?' · 请回到口播扩写后重新确认':' · 请回到口播压缩后重新确认'):''}</p></div></div>}
    {v.context_stale&&<p className="notice">资料已调整。先重新确认同一口播，再试听确认这段音频即可复用，不会重新合成。</p>}
    {v.is_test_tone&&<p className="notice">模拟测试音，仅验证操作流程，不代表真实配音效果。</p>}
    {v.asset_id&&<div className="audio-result"><MediaAudio key={v.asset_id} src={`/api/v1/assets/${v.asset_id}/content`} label="配音试听"/><DownloadButton url={`/api/v1/assets/${v.asset_id}/content`} label={v.confirmed_at&&!v.stale?'下载此版本音频':'下载草稿／旧版音频'} filename="AI-Hexing-配音.wav"/></div>}
    <details><summary>查看原口播与实际合成文字</summary><small>口播 {v.script_id.slice(0,8)} · 音色版本 {v.voice_revision} · {new Date(v.created_at).toLocaleString('zh-CN')}</small><h4>原口播</h4><pre>{v.original_text}</pre><h4>实际合成文字{v.pronunciation?'（已替换工具名读法）':''}</h4><pre>{v.synthesis_text}</pre></details>
    {v.can_confirm&&<><label className="check-label"><input type="checkbox" checked={reviewedAudio===v.id} onChange={e=>onReviewed(e.target.checked?v.id:null)}/><span>{v.is_test_tone?'我已检查模拟流程，确认这份测试音频':'我已完整试听，确认声音自然、工具名读法正确，没有漏读、重复或截断'}</span></label><button disabled={busy||reviewedAudio!==v.id} onClick={()=>onConfirm(v.id)}>确认并采用这份配音</button></>}
    {['failed','needs_reconciliation','queued','running','cancelled'].includes(v.job_status)&&<details><summary>查看这次配音的进度与问题</summary><JobProgress jobId={v.job_id}/></details>}
  </article>;}
