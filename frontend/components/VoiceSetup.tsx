'use client';
import Link from 'next/link';
import {voiceLabel} from '../lib/voiceName';
import {useRouter} from 'next/navigation';
import {useEffect,useRef,useState} from 'react';
import {api,ApiError,type Asset,type Capabilities,type List,type Receipt,type Run} from '../lib/api';
import {usePolling} from '../lib/usePolling';
import {useSessionValue} from '../lib/useSessionValue';
import JobProgress,{jobPending,type JobDetail} from './JobProgress';
import MediaAudio from './MediaAudio';
import DownloadButton from './DownloadButton';
import {voiceStatus,type VoiceCollection} from './VoiceLibrary';

async function readVoiceJobs(path:string):Promise<List<JobDetail>>{
  const items:JobDetail[]=[];const seen=new Set<string>();let cursor:string|null=null;
  do{const page:List<JobDetail>=await api(path+(cursor?'&cursor='+encodeURIComponent(cursor):''));items.push(...page.items);cursor=page.next_cursor;if(cursor&&seen.has(cursor))throw new Error('处理记录翻页异常，请重新读取。');if(cursor)seen.add(cursor);}while(cursor);
  return {items,next_cursor:null};
}
type SavedSample=Asset&{state:string;cleanup_after:string;byte_size:number};
export default function VoiceSetup({voiceId,caps,authorize}:{voiceId?:string;caps:Capabilities|null;authorize:(action:string,input:Record<string,unknown>)=>Promise<string|undefined>}){
  const router=useRouter();const [sampleId,setSampleId]=useSessionValue('hexing-voice-sample');const [savedName,setSavedName]=useSessionValue('hexing-new-voice-name');
  const [jobId,setJobId]=useSessionValue('hexing-voice-operation-'+(voiceId||'new'));
  const {data:sample,error:sampleError,refresh:readSample}=usePolling<SavedSample>(!voiceId&&sampleId?'/assets/'+sampleId:null,10000);
  const {data:library,error:voiceError,refresh:readVoices}=usePolling<VoiceCollection>(voiceId?'/voices':null,4000);
  const {data:job}=usePolling<JobDetail>(jobId&&jobId!=='unknown'?'/jobs/'+jobId:null);
  const {data:runs,error:runsError}=usePolling<List<Run>>(voiceId&&caps?.validation_tools_enabled?'/validation-runs':null,5000);
  const {data:activeJobs,error:jobsError,refresh:readJobs}=usePolling<List<JobDetail>>(voiceId?'/jobs?area=validation&bucket=attention&limit=50':null,5000,readVoiceJobs);
  const voice=library?.items.find(v=>v.id===voiceId);
  const createdId=job?.result_ref?.probe_voice_id;
  const {data:focusedRun}=usePolling<Run>(job?.result_ref?.validation_run_id?'/validation-runs/'+job.result_ref.validation_run_id:null,5000);
  const preview=(focusedRun?.kind==='voice_preview'&&focusedRun.output?.probe_voice_id===voiceId?focusedRun:undefined)||runs?.items.find(r=>r.kind==='voice_preview'&&r.output?.probe_voice_id===voiceId&&r.output?.asset_id);
  const [file,setFile]=useState<File|null>(null);const [deadline,setDeadline]=useState('');const [consent,setConsent]=useState(false);const [sourceUrl,setSourceUrl]=useState('');const [bound,setBound]=useState('');
  const [text,setText]=useState('每天分享好用的工具，今天讲 AI-Hexing。');const [reviewed,setReviewed]=useState('');
  const [busy,setBusy]=useState(false);const lock=useRef(false);const [error,setError]=useState('');const [notice,setNotice]=useState('');
  const working=!!jobId&&(jobId==='unknown'||jobPending(job?.status));
  const voiceWorking=!!voiceId&&(!!jobsError||!activeJobs||activeJobs.items.some(j=>jobPending(j.status)&&j.actions.some(a=>a.kind==='voice'&&a.id===voiceId)));
  async function act(fn:()=>Promise<void>){if(lock.current)return;lock.current=true;setBusy(true);setError('');setNotice('');try{await fn();}catch(e){setError((e as Error).message);}finally{lock.current=false;setBusy(false);}}
  async function submit(kind:string,input:Record<string,unknown>){
    const body={kind,contract_version:'S1-2026-09-08',...input};
    const permission=await authorize(kind,body);setJobId('unknown');
    try{const result=await api<Receipt>('/validation-runs',{...body,cost_authorization_id:permission});setJobId(result.job_id);setNotice('已受理，请查看本次进度。离开或刷新页面后可继续。');}
    catch(e){if(e instanceof ApiError&&e.status>=400&&e.status<500)setJobId(null);throw e;}
  }
  const [now,setNow]=useState(()=>Date.now());
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[]);
  const deadlineMs=new Date(deadline).getTime();const validDeadline=Number.isFinite(deadlineMs)&&deadlineMs>now&&deadlineMs<=now+7*86400000;
  const fileError=file?(!/\.wav$/i.test(file.name)?'目前仅支持 WAV，MP3 请先转换为 16-bit PCM WAV，不能只改扩展名。':!file.size?'文件为空，请重新选择。':file.size>10*1024*1024?'文件不能超过 10 MB。':''):'';
  const sampleReady=sample?.state==='ready'&&new Date(sample.cleanup_after).getTime()>now;
  return <section className="voice-setup"><Link className="back-to-tasks" href="/voices">← 返回我的声音</Link><div className="page-title"><div><span className="eyebrow">本人录音 → 创建声音 → 试听确认</span><h1>{voiceId?(voice?voiceLabel(voice):'声音详情'):'设置本人声音'}</h1><p>{voiceId?'核对状态，试听后决定是否用于配音。':'准备一段清晰的本人录音，创建后可在多个任务中复用。'}</p></div>{voice&&<span className="badge">{voiceStatus[voice.status]||'状态待核对'}</span>}</div>
    {!caps?.validation_tools_enabled&&<p className="notice">当前环境未开放声音创建与短句试听入口，已有可用音色仍可用于任务配音。</p>}
    {error&&<p role="alert" className="error">{error}</p>}{notice&&<p role="status" className="notice">{notice}</p>}
    {!voiceId?<div className="voice-setup-grid"><section className="panel"><h2>1. 准备录音与用途</h2><p>单人、清晰、无背景音乐。建议 10–20 秒，实际接收 5–60 秒、16-bit PCM WAV、16 kHz 以上，大小不超过 10 MB。</p>
      <label>声音名称<input value={savedName||''} onChange={e=>setSavedName(e.target.value)} maxLength={60} placeholder="例如：我的口播声音" disabled={busy}/></label>
      <label>本人录音<input type="file" accept=".wav,audio/wav" disabled={busy||working} onChange={e=>{setFile(e.target.files?.[0]||null);setError('');}}/></label>{file&&<small>{file.name} · {(file.size/1024/1024).toFixed(2)} MB</small>}{fileError&&<p className="error" role="alert">{fileError}</p>}
      <label>本次样本清理时间<input type="datetime-local" value={deadline} onChange={e=>setDeadline(e.target.value)} disabled={busy}/></label><small>选择未来七天内的时间。到期清理本产品样本；不代表服务方已删除音色。</small>
      <label className="check-label"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)} disabled={busy}/><span>录音属于本人，同意用于声音复刻与配音，并按所选时间清理。真实模式下，录音会交给百炼北京声音服务。</span></label>
      <button className="primary" disabled={busy||working||!file||!!fileError||!validDeadline||!consent||!savedName?.trim()} onClick={()=>void act(async()=>{if(!file)return;const form=new FormData();form.set('file',file);form.set('kind','voice_sample');form.set('purpose','validation');form.set('cleanup_after',new Date(deadline).toISOString());form.set('consent',JSON.stringify({is_own_voice:true,purpose:'voice_clone_validation',notice_version:'S1-2026-09-08',cleanup_acknowledged:true}));const value=await api<SavedSample>('/assets',form);setSampleId(value.id);setJobId(null);setBound('');setNotice('录音已保存并通过格式检查。下一步创建声音。');})}>{busy?'正在上传并检查…':'保存录音样本'}</button>
      <small>未保存的文件选择和用途勾选不会跨刷新保留；已保存样本会从服务端恢复。</small>
    </section><section className="panel"><h2>2. 创建可复用声音</h2>{sampleError&&<p className="error" role="alert">样本读取失败：{sampleError}<button onClick={()=>void readSample().catch(()=>{})}>重新读取样本</button></p>}{sampleId&&!sample&&!sampleError&&<p role="status">正在恢复样本…</p>}{!sampleId&&<p>先保存左侧录音，再创建声音。</p>}{sample&&<><p>{sampleReady?'录音已保存':'样本已清理或到期，请重新上传'} · {(sample.duration_ms/1000).toFixed(2)} 秒</p><small>清理时间：{new Date(sample.cleanup_after).toLocaleString('zh-CN')}</small>{sampleReady&&<MediaAudio key={sample.id} src={sample.download_path} label="已保存本人录音"/>}
      {caps?.mode==='live'&&sampleReady&&<details open={bound!==sample.id}><summary>绑定本次录音的短时地址</summary><p>使用北京私有 OSS 桶中同一文件的短时签名 URL。后端会核对文件一致性；地址不会保存在本页草稿中。</p><label>录音短时签名 URL<input type="password" autoComplete="off" value={sourceUrl} onChange={e=>setSourceUrl(e.target.value)} disabled={busy||working}/></label><button disabled={busy||working||!sourceUrl} onClick={()=>void act(async()=>{await api(`/assets/${sample.id}/source-url`,{url:sourceUrl});setSourceUrl('');setBound(sample.id);setNotice('短时录音地址已绑定。');})}>绑定录音地址</button><small>刷新后可直接尝试创建，后端会检查原绑定是否仍有效；到期时重新绑定。</small></details>}
      <button className="primary" disabled={busy||working||!!createdId||!sampleReady||!caps?.validation_tools_enabled} onClick={()=>void act(()=>submit('voice_enrollment',{sample_asset_id:sample.id,consent_record_id:sample.consent_record_id}))}>创建本人声音</button><small>北京声音创建免费，后续短句试听和完整配音会分别提示费用。</small></>}
      {createdId&&job?.status==='succeeded'&&<button className="primary" disabled={busy} onClick={()=>void act(async()=>{if(savedName?.trim()){const collection=await api<VoiceCollection>('/voices');const entry=collection.items.find(v=>v.id===createdId);if(entry)await api(`/voices/${createdId}/metadata`,{display_name:savedName.trim(),expected_revision:entry.metadata_revision});}router.push('/voices/'+createdId);})}>保存名称并进入试听 →</button>}
    </section></div>:<section className="panel voice-audition"><h2>3. 试听并确认声音</h2>{voiceError&&<p className="error" role="alert">{voiceError}<button onClick={()=>void readVoices().catch(()=>{})}>重新读取声音</button></p>}{!library&&!voiceError&&<p role="status">正在读取声音…</p>}{library&&!voice&&<p>没有找到这份声音，请返回声音库核对。</p>}{voice&&<><p>{voiceStatus[voice.status]||'状态待核对'}。{voice.eligible?'可以直接在创作任务中复用，无需再次创建。':'试听确认通过后才可用于配音。'}</p><small>{voice.region} · {voice.target_model}</small>{voice.eligible&&<Link className="button primary" href="/tasks">选择任务并配音 →</Link>}
      <label>短句试听文字<textarea value={text} onChange={e=>{setText(e.target.value);setReviewed('');}} maxLength={2000} disabled={busy}/></label><button disabled={busy||working||voiceWorking||!text.trim()||!['awaiting_preview','ready'].includes(voice.status)||!caps?.validation_tools_enabled} onClick={()=>void act(()=>submit('voice_preview',{probe_voice_id:voice.id,text}))}>生成一次短句试听</button><small>已有音频可直接试听。只有点击新生成并确认费用后，才会调用声音服务。</small>
      {jobsError&&<p className="error" role="alert">处理状态读取失败，暂不能提交新试听。{jobsError}<button onClick={()=>void readJobs().catch(()=>{})}>重新读取处理状态</button></p>}{runsError&&<p className="error" role="alert">试听记录暂时读取失败：{runsError}</p>}
      {preview?.output?.asset_id&&<div className="audition-result"><h3>最近保存的短句</h3><p>{preview.mode==='mock'?'模拟测试音，仅验证流程。':'请完整试听：声音自然、无漏读、重复或截断。'}</p><MediaAudio key={preview.output.asset_id} src={'/api/v1/assets/'+preview.output.asset_id+'/content'}/><DownloadButton url={'/api/v1/assets/'+preview.output.asset_id+'/content'} label="下载短句音频" filename="AI-Hexing-短句.wav"/>{voice.status==='awaiting_preview'&&<><label className="check-label"><input type="checkbox" checked={reviewed===preview.id} onChange={e=>setReviewed(e.target.checked?preview.id:'')}/><span>{caps?.mode==='mock'?'已检查模拟流程':'已试听，声音与读法没有问题'}</span></label><button className="primary" disabled={busy||reviewed!==preview.id} onClick={()=>void act(async()=>{await api(`/probe-voices/${voice.id}/confirm`,{preview_run_id:preview.id,accepted:true,expected_revision:voice.revision});await readVoices();setReviewed('');setNotice('声音已确认，可在创作任务中复用。');})}>确认声音可用</button></>}</div>}
    </>}</section>}
    {jobId==='unknown'?<section className="operation-progress"><h3>提交结果待核对</h3><p>连接中断时服务端可能已受理。请先查看处理记录，不要重复生成。</p><Link href="/activity">核对本次处理 →</Link><button disabled={busy||voiceWorking} onClick={()=>setJobId(null)}>已核对未创建新记录，解除提交锁定</button></section>:jobId&&<JobProgress jobId={jobId}/>}
  </section>;
}
