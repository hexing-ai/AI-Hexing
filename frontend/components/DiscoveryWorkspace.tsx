'use client';
import Link from 'next/link';
import {useRouter} from 'next/navigation';
import {useEffect, useRef, useState} from 'react';
import {api, ApiError, type Analysis, type Receipt, type Snapshot, type Task} from '../lib/api';
import {useSessionValue} from '../lib/useSessionValue';
import {usePolling} from '../lib/usePolling';
import DiscoverySearch from './DiscoverySearch';
import JobProgress, {type JobDetail} from './JobProgress';
import {type RankingEntry} from './RankingManager';

type Authorize=(action:string,input:Record<string,unknown>)=>Promise<string|undefined>;
const audience='希望了解实用 AI 工具的抖音观众';
function SourceReview({kind,id,authorize}:{kind:string;id:string;authorize:Authorize}) {
  const router=useRouter();
  const {data:analysis,error:analysisError,refresh:readAnalysis}=usePolling<Analysis>(kind==='analyses'?'/analyses/'+id:null,15000);
  const {data:snapshot,error:snapshotError,refresh:readSnapshot}=usePolling<Snapshot>(kind==='snapshots'?'/snapshots/'+id:analysis?'/snapshots/'+analysis.snapshot_id:null,15000);
  const [analysisSubmission,setAnalysisSubmission]=useSessionValue('hexing-analysis-submission-'+id);
  const {data:analysisJob}=usePolling<JobDetail>(analysisSubmission&&analysisSubmission!=='unknown'?'/jobs/'+analysisSubmission:null);
  const [primary,setPrimary]=useState('');const [secondary,setSecondary]=useState('');const [checked,setChecked]=useState(false);
  const [busy,setBusy]=useState(false);const lock=useRef(false);const [error,setError]=useState('');
  async function act(fn:()=>Promise<void>){if(lock.current)return;lock.current=true;setBusy(true);setError('');try{await fn();}catch(e){setError((e as Error).message);}finally{lock.current=false;setBusy(false);}}
  const readError=analysisError||snapshotError;
  return <>
    <Link className="back-to-tasks" href="/discover">← 返回发现工具</Link>
    <div className="page-title"><div><span className="eyebrow">核对事实，再开始表达</span><h1>资料复核</h1><p>选择一个主推亮点，对照原文，保留必要的使用条件。</p></div><span className="phase-label">读取资料 → 分析亮点 → 复核创作</span></div>
    {readError&&<p role="alert" className="error">资料读取失败，已有结果仍保留。{readError}<button onClick={()=>void (async()=>{if(kind==='analyses')await readAnalysis();if(snapshot||analysis)await readSnapshot();})().catch(()=>{})}>重新读取资料</button></p>}
    {error&&<p className="error" role="alert">{error} <Link href="/activity">查看处理记录</Link></p>}
    {!snapshot&&!readError&&<p role="status">正在读取来源资料…</p>}
    {snapshot&&<div className="review-grid f02-review"><section className="panel source-panel"><span className="eyebrow">GitHub 来源快照</span><h2>{snapshot.metadata.full_name}</h2><p>{snapshot.metadata.description}</p><a href={snapshot.canonical_url} target="_blank" rel="noreferrer">查看 GitHub 仓库 ↗</a><small className="timestamp">读取于 {new Date(snapshot.fetched_at).toLocaleString('zh-CN')}</small>
      {snapshot.missing_fields.length>0&&<div className="mode-note">资料提示：{snapshot.missing_fields.map(v=>v==='license unknown'?'许可证未知':v==='README missing'?'缺少 README':v.startsWith('README truncated')?'资料较长，请注意分析范围':v).join('；')}</div>}
      <div className="actions"><button disabled={busy} onClick={()=>void act(async()=>{const r=await api<Receipt>(`/tools/${snapshot.tool_id}/snapshots`,{});router.push('/discover/jobs/'+r.job_id);})}>刷新资料</button>{analysis&&<button disabled={busy} onClick={()=>void act(async()=>{const r=await api<{entry:RankingEntry}>('/ranking/entries',{tool_id:snapshot.tool_id,source_snapshot_id:snapshot.id,analysis_id:analysis.id});router.push('/ranking/'+r.entry.id);})}>加入封神榜草稿</button>}</div>
      <h3>来源片段 · {snapshot.fragments.length}</h3>{snapshot.fragments.map(f=><details key={f.id} id={'evidence-'+f.id} tabIndex={-1}><summary>{f.id} · 查看原文依据</summary><pre>{f.text}</pre>{f.source_url&&<a href={f.source_url} target="_blank" rel="noreferrer">打开来源 ↗</a>}</details>)}
      {analysisSubmission&&<><p className="mode-note">这份资料已有一次分析提交，请先查看结果。</p>{analysisSubmission==='unknown'?<><p>提交结果待核对，请先查看处理记录。</p><Link href="/activity">核对处理记录</Link></>:<JobProgress jobId={analysisSubmission}/>} {(analysisSubmission==='unknown'||['failed','cancelled'].includes(analysisJob?.status||''))&&<button disabled={busy} onClick={()=>setAnalysisSubmission(null)}>已核对原记录，允许重新发起分析</button>}</>}
      {!analysis&&<button className="primary" disabled={busy||!!analysisSubmission} onClick={()=>void act(async()=>{const body={topic:'',audience,snapshot_id:snapshot.id};const permission=await authorize('analysis',body);setAnalysisSubmission('unknown');try{const r=await api<Receipt|Analysis>(`/snapshots/${snapshot.id}/analyses`,{topic:'',audience,cost_authorization_id:permission});if('job_id' in r)setAnalysisSubmission(r.job_id);else setAnalysisSubmission(null);router.push('job_id' in r?'/discover/jobs/'+r.job_id:'/discover/analyses/'+r.id);}catch(e){if(e instanceof ApiError&&e.status>=400&&e.status<500)setAnalysisSubmission(null);throw e;}})}>{busy?'正在提交…':'分析功能与亮点'}</button>}
    </section><section className="panel review-highlights"><span className="eyebrow">人工复核</span><h2>选择要讲的亮点</h2>
      {analysis&&analysis.snapshot_id===snapshot.id?<><p>{analysis.summary}</p>{analysis.highlights.map(h=><article className={`highlight ${primary===h.id?'selected':''}`} key={h.id}><label className="radio-label"><input type="radio" name="primary" checked={primary===h.id} onChange={()=>{setPrimary(h.id);setChecked(false);if(secondary===h.id)setSecondary('');}}/><strong>{h.capability}</strong></label><p>{h.benefit}</p><dl className="review-facts"><dt>使用示例</dt><dd>{h.demo}</dd><dt>使用条件</dt><dd>{h.conditions||'分析未列明，需结合来源核对。'}</dd><dt>原文依据</dt><dd>{h.evidence_refs.length?h.evidence_refs.map(ref=>{const fragment=snapshot.fragments.find(f=>f.id===ref);return <details key={ref}><summary>{ref} · 展开核对</summary>{fragment?<><pre>{fragment.text}</pre><a href={'#evidence-'+ref} onClick={()=>{const el=document.getElementById('evidence-'+ref);if(el instanceof HTMLDetailsElement){el.open=true;el.focus();}}}>定位来源片段</a></>:<p className="error">未找到这条引用，请勿确认未经核对的亮点。</p>}</details>;}):<span className="error">没有来源引用，请先核对资料。</span>}</dd></dl></article>)}
      {analysis.highlights.length>1&&<label>辅助亮点（可选）<select value={secondary} onChange={e=>{setSecondary(e.target.value);setChecked(false);}}><option value="">不添加</option>{analysis.highlights.filter(h=>h.id!==primary).map(h=><option key={h.id} value={h.id}>{h.capability}</option>)}</select></label>}
      {(analysis.unknowns.length>0||analysis.warnings.length>0)&&<div className="mode-note"><h3>仍需注意</h3>{analysis.unknowns.map((v,i)=><p key={'u'+i}>{v}</p>)}{analysis.warnings.map((v,i)=><p key={'w'+i}>{v}</p>)}</div>}
      <div className="review-confirm"><label className="check-label"><input type="checkbox" checked={checked} disabled={!primary||busy} onChange={e=>setChecked(e.target.checked)}/><span>我已对照原文核对所选亮点、示例与使用条件。</span></label><button className="primary" disabled={busy||!primary||!checked} onClick={()=>void act(async()=>{const review=await api<{id:string}>(`/analyses/${analysis.id}/reviews`,{primary_highlight_id:primary,secondary_highlight_id:secondary||null,confirmed:true},'POST',`review-${analysis.id}-${primary}-${secondary}`);const task=await api<Task>('/tasks',{review_id:review.id,audience:analysis.audience},'POST',`create-task-${review.id}`);sessionStorage.setItem('hexing-selected-task',task.id);router.push('/tasks/'+task.id);})}>{busy?'正在创建任务…':'确认资料并以此工具创作'}</button><small>来源与所选亮点会带入任务，生成后仍需采用并确认稿件。</small></div>
      </>:<div className="empty"><h3>先让事实清晰</h3><p>点击“分析功能与亮点”，完成后打开分析结果，在此核对依据。</p></div>}
    </section></div>}
  </>;
}
export default function DiscoveryWorkspace({path,authorize}:{path:string;authorize:Authorize}) {
  const router=useRouter();const [,kind,id]=path.split('/').slice(1);
  const [busy,setBusy]=useState(false);const lock=useRef(false);const [error,setError]=useState('');
  const [savedRoute,setRecovery]=useSessionValue('hexing-discovery-route');
  const recovery=savedRoute&&/^\/discover\/(jobs|analyses|snapshots|search)\/[\w-]+$/.test(savedRoute)?savedRoute:'';
  useEffect(()=>{if(id)setRecovery(path);},[path,id,setRecovery]);
  async function importRepository(url:string){if(lock.current)return;lock.current=true;setBusy(true);setError('');try{const r=await api<Receipt>('/imports',{repository_url:url});router.push('/discover/jobs/'+r.job_id);}catch(e){setError((e as Error).message);throw e;}finally{lock.current=false;setBusy(false);}}
  if((kind==='snapshots'||kind==='analyses')&&id)return <SourceReview key={path} kind={kind} id={id} authorize={authorize}/>;
  if(kind==='jobs'&&id)return <><Link className="back-to-tasks" href="/discover">← 返回发现工具</Link><div className="page-title"><div><h1>本次处理</h1><p>这次操作与结果单独留存，完成后可继续复核。</p></div></div><JobProgress jobId={id}/></>;
  return <>
    <div className="page-title"><div><span className="eyebrow">发现 → 复核 → 创作</span><h1>发现工具</h1><p>找到工具，核对它的真实能力，再整理成值得讲的一个亮点。</p></div>{recovery&&recovery!==path&&<Link className="button" href={recovery}>继续上次资料处理 →</Link>}</div>
    <nav className="discovery-entrypoints" aria-label="发现方式"><a href="#discovery-search"><strong>按主题寻找</strong><span>还没有选题，从关键词找到候选 ↓</span></a></nav>
    <DiscoverySearch key={id||'search-home'} batchId={kind==='search'?id:undefined} authorize={authorize} importRepository={importRepository} importing={busy}/>
    {error&&<p className="error" role="alert">{error} <Link href="/activity">查看处理记录</Link></p>}
  </>;
}
