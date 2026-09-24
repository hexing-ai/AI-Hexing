'use client';
import Link from 'next/link';
import {pollRead} from '../lib/pollRead';
import { useCallback, useEffect, useState } from 'react';
import { api, type Job, type List, type Run } from '../lib/api';

export type ResultAction={kind:'task'|'snapshot'|'analysis'|'voice'|'discover'|'audio'|'export';label:string;id?:string;task_id?:string;generation_id?:string};
type ActivityJob=Job & {title:string;kind_label:string;status_label:string;needs_attention:boolean;created_at:string;updated_at:string;task_id:string|null};
type Detail=ActivityJob & {cost_record_path?:string;guidance:string;billing_note:string;actions:ResultAction[];attempts:{number:number;request_id:string|null;usage:Record<string,number>|null;model:string|null;elapsed_ms:number|null}[];result?:NonNullable<Run['output']>&{raw_note?:string;raw_output?:unknown};discovery?:{query:string;candidates:{name:string;url:string;reason?:string}[];rejected:{message:string}[];raw_text?:string}};
const sectionNames:Record<string,string>={pain:'痛点',intro:'栏目句',feature:'亮点',example:'示例'};

function ActivityItem({job,onOpenResult}:{job:ActivityJob;onOpenResult:(action:ResultAction)=>Promise<void>}){
  const [open,setOpen]=useState(false);const [detail,setDetail]=useState<Detail|null>(null);
  const [error,setError]=useState('');const [busy,setBusy]=useState(false);
  const read=useCallback(async()=>{const result=await api<Detail>(`/jobs/${job.id}`);setDetail(result);setError('');},[job.id]);
  useEffect(()=>{
    if(!open)return;
    return pollRead(()=>api<Detail>(`/jobs/${job.id}`),value=>{setDetail(value);setError('');},e=>setError(e.message));
  },[open,job.id]);
  const active=detail||job;
  return <details className="activity-record" onToggle={e=>setOpen(e.currentTarget.open)}><summary><span><strong>{job.title} · {job.kind_label}</strong><small>{new Date(job.created_at).toLocaleString('zh-CN')} · 查看详情</small></span><span className={`badge ${active.needs_attention?'warning':''}`}>{active.status_label}</span></summary>
    {error&&<p className="error" role="alert">{error}<button onClick={()=>void read().catch(e=>setError(e.message))}>重新读取详情</button></p>}
    {!detail&&!error&&<p role="status">正在读取处理详情…</p>}
    {detail&&<div className="activity-detail"><p>{detail.guidance}</p>{detail.error&&<p className="error">{detail.error.message}</p>}
      <div className="actions">{detail.actions.map((action,i)=><button key={i} disabled={busy} onClick={()=>{setBusy(true);void onOpenResult(action.kind==='discover'&&!action.id?{...action,id:detail.result_ref?.discovery_id}:action).catch(e=>setError(e.message)).finally(()=>setBusy(false));}}>{action.label}</button>)}
      {['queued','running'].includes(detail.status)&&<button disabled={busy} onClick={()=>{
        if(!window.confirm('停止采用本次操作的后续结果？供应商已受理的请求仍可能计费。'))return;
        setBusy(true);void api(`/jobs/${job.id}/cancel`,{}).then(()=>read()).catch(e=>setError(e.message)).finally(()=>setBusy(false));
      }}>停止采用后续结果</button>}</div>
      {detail.result?.content?.script&&<div className="script-block">{Object.entries(detail.result.content.script).map(([key,value])=><p key={key}><span>{sectionNames[key]||key}</span>{value}</p>)}</div>}
      {detail.result?.content?.copy&&<div className="muted-box"><strong>发布文案</strong><p>{detail.result.content.copy.body}</p><small>{detail.result.content.copy.tags.map(t=>'#'+t).join(' ')}</small></div>}
      {detail.result?.issues&&<ul>{detail.result.issues.map((issue,i)=><li key={i}>{issue.message}</li>)}</ul>}
      {detail.result?.asset_id&&<div className="audio-result">{detail.result.is_test_tone&&<p>模拟测试音，不是真实配音。</p>}<audio controls preload="none" src={`/api/v1/assets/${detail.result.asset_id}/content`}/><a href={`/api/v1/assets/${detail.result.asset_id}/content`} download>下载本次音频</a><small>时长 {((detail.result.duration_ms||0)/1000).toFixed(2)} 秒</small></div>}
      {detail.result&&(detail.result.raw_text||detail.result.raw_output||detail.result.raw_note)&&<details><summary>查看本次原始返回与失败留存</summary><pre>{detail.result.raw_text||detail.result.raw_note||JSON.stringify(detail.result.raw_output,null,2)}</pre></details>}
      {detail.discovery&&<div><h3>{detail.discovery.query} · 本次结果</h3>{detail.discovery.candidates.map(c=><p key={c.url}><a href={c.url} target="_blank" rel="noreferrer">{c.name} ↗</a>{c.reason&&<small>{c.reason}</small>}</p>)}{detail.discovery.rejected.map((r,i)=><p key={i}>{r.message}</p>)}{detail.discovery.raw_text&&<details><summary>查看评分原文</summary><pre>{detail.discovery.raw_text}</pre></details>}</div>}
      <details><summary>用量与核对信息</summary><p>{detail.billing_note}</p>{detail.cost_record_path&&<Link href={detail.cost_record_path}>核对本次账单 →</Link>}<p>处理编号：<code>{detail.id}</code> <button onClick={()=>void navigator.clipboard.writeText(detail.id).catch(()=>setError('复制失败，请手动复制处理编号。'))}>复制编号</button></p>
        {detail.attempts.length===0&&<p>尚无调用记录。</p>}{detail.attempts.map(a=><div key={a.number}><p>{a.model||'资料读取'}{a.elapsed_ms!=null?` · ${(a.elapsed_ms/1000).toFixed(1)} 秒`:''}</p><small>供应商请求编号：{a.request_id||'未取得'}</small>{a.usage?<pre>{JSON.stringify(a.usage,null,2)}</pre>:<small>用量未取得</small>}</div>)}
      </details>
    </div>}
  </details>;
}

async function readPages(params:string,bucket:'attention'|'history',pages:number){
  let cursor:string|null=null;const items:ActivityJob[]=[];
  for(let i=0;i<pages;i++){
    const page:List<ActivityJob>=await api<List<ActivityJob>>(`/jobs?${params}&bucket=${bucket}${cursor?'&cursor='+cursor:''}`);
    items.push(...page.items);cursor=page.next_cursor;if(!cursor)break;
  }
  return {items,next_cursor:cursor};
}

export default function ProcessingRecords({taskId,area,full=false,onOpenResult}:{taskId?:string;area?:'discover'|'validation';full?:boolean;onOpenResult:(action:ResultAction)=>Promise<void>}){
  const [attention,setAttention]=useState<List<ActivityJob>>({items:[],next_cursor:null});
  const [history,setHistory]=useState<List<ActivityJob>>({items:[],next_cursor:null});
  const [attentionPages,setAttentionPages]=useState(1);
  const [historyPages,setHistoryPages]=useState(1);
  const [historyOpen,setHistoryOpen]=useState(false);const [error,setError]=useState('');
  const params=new URLSearchParams({limit:'20',...(taskId?{task_id:taskId}:{}),...(area?{area}:{})}).toString();
  useEffect(()=>{
    return pollRead(()=>readPages(params,'attention',attentionPages),value=>{setAttention(value);setError('');},e=>setError(e.message));
  },[params,attentionPages]);
  useEffect(()=>{
    if(!historyOpen)return;
    return pollRead(()=>readPages(params,'history',historyPages),value=>{setHistory(value);setError('');},e=>setError(e.message));
  },[historyOpen,params,historyPages]);
  return <section className="jobs processing-records"><div className="panel-heading"><h2>{full?'全部处理记录':'处理记录'}</h2>{!full&&<Link href="/activity">查看全部记录 →</Link>}</div><p className="muted">{taskId?'当前任务相关处理':area==='discover'?'工具发现与资料处理':area==='validation'?'文本实验与声音处理':'所有任务与声音处理'} · 点击记录查看结果和下一步</p>
    {error&&<p className="error" role="alert">{error}</p>}
    <h3>正在处理与需要关注</h3>{attention.items.length===0?<p className="muted">暂无需要处理的问题。</p>:attention.items.map(j=><ActivityItem key={j.id} job={j} onOpenResult={onOpenResult}/>)}
    {attention.next_cursor&&<button onClick={()=>setAttentionPages(count=>count+1)}>查看更多待处理记录</button>}
    <details className="completed-records" onToggle={e=>setHistoryOpen(e.currentTarget.open)}><summary>已完成与已停止的记录</summary>{historyOpen&&<>{history.items.length===0?<p className="muted">暂无记录。</p>:history.items.map(j=><ActivityItem key={j.id} job={j} onOpenResult={onOpenResult}/>)}{history.next_cursor&&<button onClick={()=>setHistoryPages(count=>count+1)}>加载更早记录</button>}</>}</details>
  </section>;
}
