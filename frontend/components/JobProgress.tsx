'use client';
import Link from 'next/link';
import {type Job} from '../lib/api';
import {usePolling} from '../lib/usePolling';
export type JobDetail=Job & {title:string;status_label:string;guidance:string;actions:{kind:string;id?:string;task_id?:string;generation_id?:string;label:string}[];result?:{raw_text?:string}};
export const jobPending=(status?:string)=>!status||['queued','running','needs_reconciliation'].includes(status);
export function resultHref(action:JobDetail['actions'][number]) {
  if(action.kind==='snapshot')return '/discover/snapshots/'+action.id;
  if(action.kind==='analysis')return '/discover/analyses/'+action.id;
  if(action.kind==='discover')return action.id?'/discover/search/'+action.id:'/discover';
  if(action.kind==='task')return `/tasks/${action.task_id}${action.generation_id?'/generations/'+action.generation_id:''}`;
  if(action.kind==='voice')return action.id?'/voices/'+action.id:'/voices/new';
  if(action.kind==='audio'||action.kind==='export')return action.task_id&&action.id?`/tasks/${action.task_id}/${action.kind==='audio'?'audio':'exports'}/${action.id}`:'/activity';
  return '/activity';
}
export default function JobProgress({jobId}:{jobId:string}) {
  const {data:job,error,refresh}=usePolling<JobDetail>('/jobs/'+jobId);
  return <section className="operation-progress" aria-label="本次处理状态">
    <div className="panel-heading"><h3>{job?.title||'本次处理'}</h3><span className="badge">{job?.status_label||'正在读取进度'}</span></div>
    {error&&<p role="alert" className="error">进度暂时无法读取：{error}<button onClick={()=>void refresh().catch(()=>{})}>重新读取进度</button></p>}
    <p role="status">{job?.guidance||'可以离开页面，返回此地址后继续查看。'}</p>
    {job?.error&&<p className="error">{job.error.message}</p>}
    <div className="actions">{job?.actions.map((a,i)=><Link className="button" key={i} href={resultHref(a.kind==='discover'&&!a.id?{...a,id:job.result_ref?.discovery_id}:a)}>{a.label} →</Link>)}<Link href="/activity">查看处理记录</Link></div>
    {job?.result?.raw_text&&<details><summary>本次生成原文与失败留存</summary><pre>{job.result.raw_text}</pre></details>}
  </section>;
}
