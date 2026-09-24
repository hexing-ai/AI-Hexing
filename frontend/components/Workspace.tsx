'use client';
import Link from 'next/link';
import {pollRead} from '../lib/pollRead';
import WorkspaceFrame from './WorkspaceFrame';
import TaskWorkspace from './TaskWorkspace';
import WindowLogin from './WindowLogin';
import VoiceLibrary from './VoiceLibrary';
import VoiceSetup from './VoiceSetup';
import RankingManager from './RankingManager';
import CostRecords from './CostRecords';
import CostBudget, {type Budget, yuan} from './CostBudget';
type CostPreview = {preview_id:string;input_hash:string;model:string;data_categories:string[];maximum_cost:string;budget:Budget;quote:{source:string;price_version:string;notice?:string}};
import DiscoveryWorkspace from './DiscoveryWorkspace';
import ProcessingRecords, { type ResultAction } from './ProcessingRecords';
import { usePathname, useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, setCsrf, type Asset, type Capabilities, type Job, type List, type Receipt, type Run, type Task, type Voice } from '../lib/api';

const labels: Record<string,string> = {generation:'正式文本生成',discovery_search:'GitHub 主题搜索',discovery_score:'候选评分',discovery_brief:'工具中文介绍',queued:'排队中',running:'处理中',succeeded:'已完成',failed:'失败',needs_reconciliation:'结果待核对',cancelled:'已取消',cloning:'克隆中',awaiting_preview:'待试听',ready:'可用',disabled:'已停用',deletion_pending:'删除中',remote_deletion_pending:'远端删除待确认',deleted:'已删除',passed:'结构检查通过',blocked:'待完成',not_run:'尚未运行',analysis:'资料分析',import:'仓库导入',writing:'口播与文案',voice_enrollment:'声音复刻',voice_preview:'短句试听',voice_reuse:'音色复用',voice_delete:'删除音色'};

function Badge({value}:{value:string}) { return <span className={`badge ${['failed','needs_reconciliation','remote_deletion_pending'].includes(value)?'warning':''}`}>{labels[value] || value}</span>; }

export default function Workspace() {
  const path = usePathname(); const router = useRouter();
  const routeTaskId=path.startsWith('/tasks/')?path.split('/')[2]:undefined;
  const page=path.split('/')[1]||'tasks';
  const [auth, setAuth] = useState<'loading'|'in'|'out'|'error'>('loading');
  const [authAttempt,setAuthAttempt]=useState(0);
  const [caps, setCaps] = useState<Capabilities|null>(null);
  const [error, setError] = useState(''); const [notice,setNotice] = useState('');
  const [busy,setBusy] = useState(false); const busyRef = useRef(false);
  const [name,setName] = useState(''); const [password,setPassword] = useState('');
  const [tasks,setTasks] = useState<Task[]>([]);
  const [tasksLoaded,setTasksLoaded]=useState(false);
  const [tasksError,setTasksError]=useState('');
  const [voices,setVoices] = useState<Voice[]>([]); const [runs,setRuns] = useState<Run[]>([]);
  const [selectedTask,setSelectedTask] = useState('');
  const [selectedVoice,setSelectedVoice] = useState('');
  const [sample,setSample] = useState<Asset|null>(null); const [file,setFile] = useState<File|null>(null);
  const [consent,setConsent] = useState(false); const [sampleUrl,setSampleUrl] = useState('');
  const [text,setText] = useState('每天分享好用的工具，今天讲 AI-Hexing。');
  const [deadline,setDeadline] = useState('');
  const [sampleFeedback,setSampleFeedback] = useState<{kind:'error'|'success';text:string}|null>(null);
  const [savingSample,setSavingSample] = useState(false);
  const seen = useRef(new Set<string>());
  const pendingCost = useRef<((accepted:boolean)=>void)|null>(null);
  const [costPrompt,setCostPrompt] = useState<CostPreview|null>(null);
  const costDialog=useRef<HTMLDialogElement>(null);
  const costTrigger=useRef<HTMLElement|null>(null);
  useEffect(()=>{const remember=(event:FocusEvent)=>{const target=event.target;if(target instanceof HTMLElement&&target!==document.body&&!costDialog.current?.contains(target))costTrigger.current=target;};document.addEventListener('focusin',remember);return()=>document.removeEventListener('focusin',remember);},[]);
  useEffect(()=>{if(costPrompt&&!costDialog.current?.open)costDialog.current?.showModal();},[costPrompt]);
  function resolveCost(accepted:boolean){
    const trigger=costTrigger.current;costDialog.current?.close();pendingCost.current?.(accepted);
    if(!trigger)return;
    const observer=new MutationObserver(()=>{if(trigger.isConnected&&!trigger.hasAttribute('disabled')){observer.disconnect();trigger.focus();}});
    observer.observe(trigger,{attributes:true,attributeFilter:['disabled']});
    requestAnimationFrame(()=>{if(trigger.isConnected&&!trigger.hasAttribute('disabled')){observer.disconnect();trigger.focus();}});
    setTimeout(()=>observer.disconnect(),5000);
  }


  useEffect(()=>()=>{pendingCost.current?.(false);pendingCost.current=null;},[path]);
  const reading=useRef<{scope:string;promise:Promise<void>|null}|null>(null);
  useEffect(()=>()=>{reading.current=null;},[page,routeTaskId,auth]);

  const refresh = useCallback(async () => {
    if(page!=='tasks'&&page!=='validation')return;
    const scope=page+':'+(routeTaskId||'');
    if(reading.current?.scope===scope&&reading.current.promise)return reading.current.promise;
    const request={scope,promise:null as Promise<void>|null};reading.current=request;
    request.promise=(async()=>{
      const t=await api<List<Task>>('/tasks');
      const targetId=routeTaskId;
      if(targetId&&!t.items.some(task=>task.id===targetId)){
        try{const target=await api<Task>(`/tasks/${targetId}`);if(!target.lifecycle||target.lifecycle.state==='active')t.items.unshift(target);}
        catch(e){if(!(e instanceof ApiError&&e.status===404))throw e;}
      }
      if(reading.current!==request)return;
      setTasks(t.items);setTasksLoaded(true);setTasksError('');
      if(page==='validation'){
        const [j,v,r]=await Promise.all([api<List<Job>>('/jobs'),api<List<Voice>>('/probe-voices'),api<List<Run>>('/validation-runs')]);
        if(reading.current!==request)return;
        setVoices(v.items);setRuns(r.items);
        for(const item of j.items){
          if(item.status!=='succeeded'||seen.current.has(item.id))continue;
          seen.current.add(item.id);
          if(sessionStorage.getItem('hexing-active-job')===item.id&&item.result_ref?.probe_voice_id){setSelectedVoice(item.result_ref.probe_voice_id);sessionStorage.setItem('hexing-selected-voice',item.result_ref.probe_voice_id);}
        }
      }
    })().finally(()=>{if(reading.current===request)reading.current=null;});
    return request.promise;
  },[page,routeTaskId]);

  useEffect(()=>{
    const expired=()=>{setCsrf('');setAuth('out');setError('登录已过期，请重新登录后继续当前页面。');pendingCost.current?.(false);};
    window.addEventListener('hexing-session-expired',expired);
    return()=>window.removeEventListener('hexing-session-expired',expired);
  },[]);
  useEffect(()=>{
    let disposed=false;
    void (async()=>{
      const me=await api<{csrf_token:string}>('/auth/me');
      const c=await api<Capabilities>('/capabilities');
      if(disposed)return;
      setCsrf(me.csrf_token);setCaps(c);setAuth('in');setError('');
      setSelectedTask(sessionStorage.getItem('hexing-selected-task')||'');setSelectedVoice(sessionStorage.getItem('hexing-selected-voice')||'');
    })().catch(e=>{if(!disposed){if(e instanceof ApiError&&e.status===401){setCsrf('');setAuth('out');}else{setAuth('error');setError((e as Error).message);}}});
    return()=>{disposed=true;};
  },[authAttempt]);
  useEffect(()=>{
    if(auth!=='in'||page!=='validation')return;
    let active=true;const id=sessionStorage.getItem('hexing-sample');
    if(id)void api<Asset>('/assets/'+id).then(value=>{if(active&&value.state==='ready')setSample(value);}).catch(()=>{});
    return()=>{active=false;};
  },[auth,page]);

  useEffect(() => {
    if (auth!=='in'||(page!=='tasks'&&page!=='validation')) return;
    return pollRead(refresh,()=>{},e=>{setTasksError(e.message);if(e instanceof ApiError&&e.status===401)setAuth('out');},3000);
  },[auth,refresh,page]);

  useEffect(()=>{if(auth!=='in')return;const changed=()=>void refresh().catch(e=>setTasksError(e.message));window.addEventListener('hexing-text-changed',changed);return()=>window.removeEventListener('hexing-text-changed',changed);},[auth,refresh]);

  function openTextStudio(taskId:string) {
    router.push(`/tasks/${taskId}`);
    setSelectedTask(taskId);
    try {sessionStorage.setItem('hexing-selected-task',taskId);} catch {
      setNotice('已打开文本创作，但浏览器未能保存任务恢复标记。');
    }
  }

  async function act(fn:()=>Promise<void>) {
    if (busyRef.current) return;
    busyRef.current=true;setBusy(true);setError('');setNotice('');
    try {await fn();} catch(e) {
      const err=e as ApiError;
      setError(err.message+(err.requestId?` · 请求编号 ${err.requestId}`:''));
      if(err.status===401)setAuth('out');
    } finally {busyRef.current=false;setBusy(false);}
  }
  async function jobSubmit(url:string,input:Record<string,unknown>) {
    const r=await api<Receipt>(url,input);
    sessionStorage.setItem('hexing-active-job',r.job_id);
    setNotice('作业已受理。关闭页面后仍会继续，可在处理记录中恢复。');
    await refresh();
  }
  async function authorize(action:string,input:Record<string,unknown>) {
    if(caps?.mode!=='live')return undefined;
    const preview=await api<CostPreview>('/cost-authorizations/preview',{action,input});
    setCostPrompt(preview);
    const accepted=await new Promise<boolean>(resolve=>{pendingCost.current=resolve;});
    pendingCost.current=null;setCostPrompt(null);
    if(!accepted)throw new Error('本次调用已取消。');
    const permission=await api<{id:string}>('/cost-authorizations',{preview_id:preview.preview_id,action,input_hash:preview.input_hash,max_calls:1,max_cost:Number(preview.maximum_cost),accept_unknown_cost:false});
    return permission.id;
  }
  async function validate(kind:string,inputs:Record<string,unknown>) {
    const body={kind,contract_version:'S1-2026-09-08',...inputs};
    const permission=await authorize(kind,body);
    await jobSubmit('/validation-runs',{...body,cost_authorization_id:permission});
  }
  async function openProcessingResult(action:ResultAction){
    if((action.kind==='audio'||action.kind==='export')&&action.task_id){
      sessionStorage.setItem('hexing-selected-task',action.task_id);setSelectedTask(action.task_id);
      router.push(`/tasks/${action.task_id}/${action.kind==='audio'?'audio':'exports'}/${action.id}`);
    }else if(action.kind==='task'&&action.task_id){
      sessionStorage.setItem('hexing-selected-task',action.task_id);setSelectedTask(action.task_id);
        router.push(`/tasks/${action.task_id}${action.generation_id?'/generations/'+action.generation_id:''}`);
    }else if(action.kind==='snapshot'&&action.id){
      router.push('/discover/snapshots/'+action.id);
    }else if(action.kind==='analysis'&&action.id){
      router.push('/discover/analyses/'+action.id);
    }else if(action.kind==='discover'){
      router.push(action.id?'/discover/search/'+action.id:'/discover');
    }else if(action.kind==='voice'){
      if(action.id){setSelectedVoice(action.id);sessionStorage.setItem('hexing-selected-voice',action.id);}router.push(action.id?'/voices/'+action.id:'/voices/new');
    }
  }
  function sampleProblems() {
    const problems: string[] = [];
    if (!file) problems.push('请选择一份 WAV 录音文件。');
    else {
      if (!/\.wav$/i.test(file.name)) problems.push('当前仅支持 WAV，MP3 暂不支持；请转换为 16-bit PCM WAV，不能只修改扩展名。');
      if (file.size === 0) problems.push('录音文件为空，请重新选择。');
      if (file.size > 10 * 1024 * 1024) problems.push('录音文件不得超过 10 MB。');
    }
    const cleanupTime = new Date(deadline).getTime();
    if (!deadline) problems.push('请设置本次样本清理时间。');
    else if (!Number.isFinite(cleanupTime) || cleanupTime <= Date.now() || cleanupTime > Date.now() + 7 * 86400000) problems.push('清理时间需在当前时间之后、未来七天以内。');
    if (!consent) problems.push('请勾选本人录音及用途、清理确认。');
    return problems;
  }
  async function saveSample() {
    if (busyRef.current) return;
    setSampleFeedback(null);
    const problems = sampleProblems();
    if (problems.length || !file) {
      setSampleFeedback({kind:'error',text:problems.join(' ')});
      return;
    }
    const selectedFile = file;
    setSavingSample(true);
    try {
      await act(async()=>{
        try {
          const form = new FormData();
          form.set('file',selectedFile);
          form.set('kind','voice_sample');
          form.set('purpose','validation');
          form.set('cleanup_after',new Date(deadline).toISOString());
          form.set('consent',JSON.stringify({is_own_voice:true,purpose:'voice_clone_validation',notice_version:'S1-2026-09-08',cleanup_acknowledged:true}));
          const saved = await api<Asset>('/assets',form);
          setSample(saved);
          setSampleFeedback({kind:'success',text:'样本保存成功，已存入本地私有空间。下一步可创建实验音色。'});
          try {sessionStorage.setItem('hexing-sample',saved.id);} catch {
            setSampleFeedback({kind:'success',text:'样本保存成功，但浏览器未能保存刷新恢复标记。请在当前页面继续创建实验音色。'});
          }
        } catch (e) {
          const err = e as ApiError;
          setSampleFeedback({kind:'error',text:err.message+(err.requestId?` · 请求编号 ${err.requestId}`:'')});
          throw e;
        }
      });
    } finally {setSavingSample(false);}
  }
  const voice=voices.find(v=>v.id===selectedVoice);
  const voiceRuns=runs.filter(r=>r.output?.probe_voice_id===selectedVoice && r.output?.asset_id);
  const previous=voiceRuns.find(r=>r.kind==='voice_preview');


  if(auth==='error')return <main className="recovery-screen"><section><span className="brand">AI-Hexing</span><h1>暂时无法恢复工作台</h1><p role="alert">{error}</p><p>连接恢复后，可继续查看原来的页面。</p><button onClick={()=>{setAuth('loading');setAuthAttempt(n=>n+1);}}>重新连接工作台</button></section></main>;
  if(auth==='loading')return <main className="center"><div className="brand">AI-Hexing</div><p role="status">正在恢复工作台…</p></main>;
  if(auth==='out')return <main className="login-screen"><section className="login-story"><div className="brand">AI-Hexing<span>创作工作台</span></div><div><span className="eyebrow">从资料到表达</span><h1>让值得分享的工具，<br/>成为下一条内容。</h1><p>先核对事实，再整理口播。<br/>每一次确认，都留下可靠的依据。</p></div><small>资料 · 创作 · 声音 · 素材</small></section><section className="login-form"><form onSubmit={e=>{e.preventDefault();void act(async()=>{const me=await api<{csrf_token:string}>('/auth/login',{login_name:name,password});setCsrf(me.csrf_token);setPassword('');setAuth('loading');setAuthAttempt(n=>n+1);});}}><span className="eyebrow">私有空间</span><h2>登录工作台</h2><p>使用本机创建的创作者账户。</p><label>账户<input autoComplete="username" required value={name} onChange={e=>setName(e.target.value)}/></label><label>密码<input type="password" autoComplete="current-password" required value={password} onChange={e=>setPassword(e.target.value)}/></label><button disabled={busy} className="primary">{busy?'登录中…':'进入工作台 →'}</button>{error&&<p className="error" role="alert">{error}</p>}<details><summary>首次使用</summary><p>在项目终端运行账户初始化命令，再返回此处登录。</p><code>.venv/bin/python -m backend.app.cli create-owner</code></details><WindowLogin loggedIn={false}/></form></section></main>;

  return <WorkspaceFrame page={page} mode={caps?.mode} onLogout={()=>void act(async()=>{await api('/auth/logout',{});setAuth('out');sessionStorage.clear();})}>{caps?.mode==='mock'&&<div className="mode-note">模拟数据，仅用于工程验证。声音实验播放测试音，不代表本人克隆音效果。</div>}{error&&<div className="error" role="alert">{error}</div>}{notice&&<div className="notice" role="status">{notice}</div>}{tasksError&&page!=='tasks'&&<div className="error" role="alert">{tasksError}</div>}

  {page==='costs'&&<CostRecords key={path} jobId={path.split('/')[2]}/>}
  {page==='voices'&&(path.split('/')[2]?<VoiceSetup key={path} voiceId={path.split('/')[2]==='new'?undefined:path.split('/')[2]} caps={caps} authorize={authorize}/>:<VoiceLibrary/>)}
  {page==='ranking'&&<RankingManager key={path} entryId={path.split('/')[2]}/>}

  {page==='discover'&&<DiscoveryWorkspace key={path} path={path} authorize={authorize}/>}

  {page==='tasks'&&<TaskWorkspace tasks={tasks} loaded={tasksLoaded} error={tasksError} taskId={routeTaskId} path={path} onOpen={openTextStudio} onRefresh={async()=>{try{await refresh();}catch(e){setTasksError((e as Error).message);throw e;}}} onChanged={async taskId=>{if(selectedTask===taskId){setSelectedTask('');sessionStorage.removeItem('hexing-selected-task');}await refresh();}} authorize={authorize}/>}

  {page==='validation'&&<><div className="page-title"><div><span className="eyebrow">第一阶段 · 接入验证</span><h1>让能力经得起验证</h1><p>查看真实输出、试听声音，并记录每次调用的结果。</p></div></div><details className="capability-panel"><summary>服务接入状态 · {caps?.mode==='mock'?'当前为模拟验证':'配置完成不等于实际验证通过'}</summary>{caps?.contracts.map(c=><div className="capability-row" key={c.kind}><strong>{labels[c.kind]||c.kind}</strong><span>{c.state==='mock'?'模拟替身':c.state==='blocked'?'待配置':'已配置，待实测'}</span>{c.missing_config_names.length>0&&<code>{c.missing_config_names.join('、')}</code>}</div>)}</details><div className="experiment-grid"><section className="panel"><span className="eyebrow">文字契约</span><h2>口播与文案</h2><p>使用已复核资料，验证四段口播和对应发布文案。</p><label>选择创作任务<select value={selectedTask} onChange={e=>{setSelectedTask(e.target.value);sessionStorage.setItem('hexing-selected-task',e.target.value);}}><option value="">请选择</option>{tasks.map(t=><option value={t.id} key={t.id}>任务 {t.id.slice(0,8)}</option>)}</select></label><button disabled={busy||!selectedTask} className="primary" onClick={()=>void act(()=>validate('writing',{task_id:selectedTask}))}>生成实验稿件</button><small>实验结果可查看；正式编辑与版本采用请进入创作任务。</small></section><section className="panel"><span className="eyebrow">声音契约</span><h2>创建实验音色</h2><p>上传本人的清晰录音，验证克隆与音色复用。</p><label>样本录音<input type="file" accept=".wav,audio/wav" disabled={savingSample} onChange={e=>{setFile(e.target.files?.[0]||null);setSampleFeedback(null);}}/></label><small>16-bit PCM WAV · 16kHz 以上 · 5–60 秒 · ≤10 MB。建议录制 10–20 秒无背景音乐的单人讲话。</small><label>本次样本清理时间<input type="datetime-local" disabled={savingSample} value={deadline} onChange={e=>{setDeadline(e.target.value);setSampleFeedback(null);}}/></label><small>必填：请选择未来七天内的时间，到期后清理本次录音样本。</small><label className="check-label"><input type="checkbox" disabled={savingSample} checked={consent} onChange={e=>{setConsent(e.target.checked);setSampleFeedback(null);}}/><span>录音属于本人，同意用于本次声音复刻实验及按所选时间清理。真实模式下录音将交给百炼北京声音服务；本地清理不等于服务方已删除。</span></label><button className="primary" disabled={busy} aria-busy={savingSample} aria-describedby="sample-save-feedback" onClick={()=>void saveSample()}>{savingSample?'正在上传并检查录音…':'保存样本'}</button><div id="sample-save-feedback" className="sample-save-feedback">{savingSample?<p role="status">正在上传并检查格式与时长，请稍候。</p>:sampleFeedback?<p className={sampleFeedback.kind==='error'?'error':'notice'} role={sampleFeedback.kind==='error'?'alert':'status'}>{sampleFeedback.text}</p>:<small>{busy?'其他操作正在处理，请稍候再保存样本。':sampleProblems().length?sampleProblems().join(' '):'信息已齐全，点击保存样本后将检查实际音频格式与时长。'}</small>}</div>{sample&&<div className="muted-box"><p>样本已保存 · {(sample.duration_ms/1000).toFixed(2)} 秒</p>{caps?.mode==='live'&&<><label>私有存储短时签名 URL<input type="password" autoComplete="off" value={sampleUrl} onChange={e=>setSampleUrl(e.target.value)}/></label><button disabled={busy||!sampleUrl} onClick={()=>void act(async()=>{await api(`/assets/${sample.id}/source-url`,{url:sampleUrl});setSampleUrl('');setNotice('已绑定受控 URL，复刻前会核对文件一致性。');})}>绑定样本地址</button></>}{caps?.mode==='live'&&<p>北京 CosyVoice 创建音色免费；后续试听和配音单独计费，提交前会分别确认。</p>}<div className="actions"><button className="primary" disabled={busy} onClick={()=>void act(()=>validate('voice_enrollment',{sample_asset_id:sample.id,consent_record_id:sample.consent_record_id}))}>创建一次实验音色</button><button disabled={busy} onClick={()=>void act(async()=>{if(!window.confirm('确认删除本地实验样本？'))return;await api(`/assets/${sample.id}`,{confirm:true},'DELETE');setSample(null);sessionStorage.removeItem('hexing-sample');})}>删除样本</button></div></div>}</section></div>
  <section className="panel voice-panel"><div><span className="eyebrow">克隆一次 · 持续复用</span><h2>试听与复用</h2></div><div className="voice-controls"><label>实验音色<select value={selectedVoice} onChange={e=>{setSelectedVoice(e.target.value);sessionStorage.setItem('hexing-selected-voice',e.target.value);}}><option value="">请选择</option>{voices.map(v=><option key={v.id} value={v.id}>{v.id.slice(0,8)} · {labels[v.status]||v.status}</option>)}</select></label><label>测试文字<textarea rows={3} value={text} onChange={e=>setText(e.target.value)} maxLength={2000}/></label></div>{voice&&<><div className="actions"><Badge value={voice.status}/><small>{voice.region} · {voice.target_model}</small></div><div className="actions"><button disabled={busy||!['awaiting_preview','ready'].includes(voice.status)} onClick={()=>void act(()=>validate('voice_preview',{probe_voice_id:voice.id,text}))}>合成短句试听</button><button disabled={busy||!previous||voice.status!=='awaiting_preview'} onClick={()=>void act(async()=>{await api(`/probe-voices/${voice.id}/confirm`,{preview_run_id:previous?.id,accepted:true,expected_revision:voice.revision});await refresh();})}>{caps?.mode==='mock'?'确认模拟流程':'试听后确认音色可用'}</button><button className="primary" disabled={busy||voice.status!=='ready'||!previous} onClick={()=>void act(()=>validate('voice_reuse',{probe_voice_id:voice.id,previous_run_id:previous?.id,text}))}>用另一段文字验证复用</button></div><div className="actions subtle"><button disabled={busy||['deleted','deletion_pending','remote_deletion_pending'].includes(voice.status)} onClick={()=>void act(async()=>{await api(`/probe-voices/${voice.id}/disable`,{expected_revision:voice.revision});await refresh();})}>停用音色</button><button disabled={busy||['deleted','deletion_pending'].includes(voice.status)} onClick={()=>void act(async()=>{if(!window.confirm('确认删除实验音色及本地样本？远端删除结果将单独核对。'))return;const r=await api<Receipt>(`/probe-voices/${voice.id}`,{confirm:true,expected_revision:voice.revision},'DELETE');sessionStorage.setItem('hexing-active-job',r.job_id);await refresh();})}>删除音色与样本</button></div>{voice.status==='remote_deletion_pending'&&<div className="error">远端删除待确认。请打开阿里云百炼控制台的音色管理核对；如控制台未提供入口，请联系供应商支持并提供原作业请求编号。此处不会将远端未知状态标为已删除。</div>}</>}</section>
  <section className="results"><h2>实验结果</h2>{runs.length===0?<p className="muted-box">完成一次实验后，结果会保存在这里。</p>:runs.map(r=><article className="panel" key={r.id}><div className="panel-heading"><h3>{labels[r.kind]||r.kind}</h3><Badge value={r.status}/></div><small>{r.mode==='mock'?'模拟验证':'真实调用'} · {r.id.slice(0,8)}</small>{r.output?.content?.script&&<div className="script-block">{Object.entries(r.output.content.script).map(([k,v])=><p key={k}><span>{({pain:'痛点',intro:'栏目句',feature:'亮点',example:'示例'} as Record<string,string>)[k]}</span>{v}</p>)}</div>}{r.output?.content?.copy&&<div className="muted-box"><strong>发布文案</strong><p>{r.output.content.copy.body}</p><small>{r.output.content.copy.tags.map(t=>'#'+t).join(' ')}</small></div>}{r.output?.checks&&<p className="muted">结构检查：{Object.entries(r.output.checks).map(([k,v])=>`${({script:'口播',copy:'文案',evidence:'证据'} as Record<string,string>)[k]||k} ${v?'通过':'未通过'}`).join(' · ')}。事实与自然度仍需人工检查。</p>}{r.output?.issues&&<ul>{r.output.issues.map((issue,i)=><li key={i}>{issue.severity==='warning'?'待复核':'需修正'} · {issue.path}：{issue.message}</li>)}</ul>}{r.kind==='writing'&&<details><summary>查看原始返回（含未通过字段）</summary><pre>{r.output?.raw_text ?? '此历史结果未保存完整原文，无法恢复已丢弃字段。'}</pre></details>}{r.output?.asset_id&&<div className="audio-result">{r.output.is_test_tone&&<p>这是一段模拟测试音，不是真实克隆配音。</p>}<audio controls preload="none" src={`/api/v1/assets/${r.output.asset_id}/content`}/><a href={`/api/v1/assets/${r.output.asset_id}/content`} download>下载音频 ↗</a><small>实际时长 {((r.output.duration_ms||0)/1000).toFixed(3)} 秒</small></div>}</article>)}</section></>}

  {page==='activity'&&<div className="page-title"><div><span className="eyebrow">结果与问题都可追溯</span><h1>处理记录</h1><p>查看各次处理的结果、失败原因与用量。打开记录不会重新调用模型。</p></div><Link href="/tasks">返回创作任务 →</Link></div>}
  {page==='tasks'?routeTaskId&&<details id="task-processing" className="panel task-processing"><summary>处理记录 · 查看结果与问题</summary><ProcessingRecords key={'task:'+routeTaskId} taskId={routeTaskId} onOpenResult={openProcessingResult}/></details>:page!=='voices'&&page!=='costs'&&page!=='ranking'&&<ProcessingRecords key={page} area={page==='discover'?'discover':page==='validation'?'validation':undefined} full={page==='activity'} onOpenResult={openProcessingResult}/>}
  {caps?.mode==='live'&&page!=='ranking'&&<CostBudget/>}
  {costPrompt&&<dialog ref={costDialog} className="cost-dialog" aria-labelledby="cost-title" onCancel={e=>{e.preventDefault();resolveCost(false);}} onKeyDown={e=>{if(e.key!=='Tab')return;const items=Array.from(e.currentTarget.querySelectorAll<HTMLElement>('a[href],button:not(:disabled),input:not(:disabled)'));if(!items.length)return;e.preventDefault();const index=items.indexOf(document.activeElement as HTMLElement);items[(index+(e.shiftKey?-1:1)+items.length)%items.length].focus();}}><h2 id="cost-title">确认本次真实调用</h2><p>将向百炼 {costPrompt.model} 发送{costPrompt.data_categories.join('、')}。</p><p>本次保守预占最多 ¥{costPrompt.maximum_cost}，仅允许 1 次调用，不自动重试。</p><p>单次上限 ¥{yuan(costPrompt.budget.single_limit_micros)}；每月上限 ¥{yuan(costPrompt.budget.monthly_limit_micros)}，当前剩余 ¥{yuan(costPrompt.budget.remaining_micros)}。</p><p>{costPrompt.quote.notice||'按目录价预占；免费额度和折扣未抵扣。结果未知时保留占用，实际云账单需另行核对。'}</p><a href={costPrompt.quote.source} target="_blank" rel="noreferrer">官方价格依据（{costPrompt.quote.price_version}）↗</a><div className="actions"><button onClick={()=>resolveCost(false)}>取消</button><button className="primary" onClick={()=>resolveCost(true)}>确认金额并继续一次</button></div></dialog>}</WorkspaceFrame>;
}
