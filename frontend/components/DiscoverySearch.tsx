'use client';
import Link from 'next/link';
import {useRouter} from 'next/navigation';
import {useEffect, useRef, useState} from 'react';
import {api, type List, type Receipt} from '../lib/api';
import {usePolling} from '../lib/usePolling';
import JobProgress, {jobPending} from './JobProgress';
import './discovery-cards.css';

import {allCandidates,batchStatus,candidateStatus,briefKeys,briefLabels,briefField,type Candidate,type Batch} from '../lib/discoveryTypes';

type Props={authorize:(action:string,input:Record<string,unknown>)=>Promise<string|undefined>;importRepository:(url:string)=>Promise<void>;batchId?:string;importing:boolean};
export default function DiscoverySearch({authorize,importRepository,batchId,importing}:Props){
  const router=useRouter();const [topic,setTopic]=useState('');const [category,setCategory]=useState('');const [threshold,setThreshold]=useState('');
  const {data:list,error:listError,refresh:refreshList}=usePolling<List<Batch>>('/discoveries',5000);
  const {data:batch,error:batchError,refresh:refreshBatch}=usePolling<Batch>(batchId?'/discoveries/'+batchId:null);
  const [selection,setSelection]=useState<{batchId?:string;names:string[]}>({names:[]});
  const selected=selection.batchId===batchId?selection.names:[];
  function setSelected(value:string[]|((old:string[])=>string[])){setSelection(old=>({batchId,names:typeof value==='function'?value(old.batchId===batchId?old.names:[]):value}));}
  const [busy,setBusy]=useState(false);const lock=useRef(false);const [error,setError]=useState('');
  const [opened,setOpened]=useState<{candidate:Candidate;batchId?:string}|null>(null);
  const detail=opened?.batchId===batchId?opened?.candidate:null;
  const dialog=useRef<HTMLDialogElement>(null),origin=useRef<HTMLButtonElement|null>(null);
  useEffect(()=>{const node=dialog.current;if(detail){node?.showModal();node?.querySelector<HTMLElement>('h2')?.focus({preventScroll:true});if(node)node.scrollTop=0;}else node?.close();},[detail]);
  function openDetails(candidate:Candidate,button:HTMLButtonElement){origin.current=button;setOpened({candidate,batchId});}
  function closeDetails(){dialog.current?.close();setOpened(null);origin.current?.focus({preventScroll:true});}
  async function act(fn:()=>Promise<void>){if(lock.current)return;lock.current=true;setBusy(true);setError('');try{await fn();}catch(e){setError((e as Error).message);}finally{lock.current=false;setBusy(false);}}
  const briefLock=useRef(false);
  async function generateBrief(id:string){
    if(briefLock.current)return;
    briefLock.current=true;
    try{await act(async()=>{const input={discovery_id:id};const permission=await authorize('discovery_brief',input);await api<Receipt>(`/discoveries/${id}/brief`,{...input,cost_authorization_id:permission});await refreshBatch();});}
    finally{briefLock.current=false;}
  }
  useEffect(()=>{
    if(!batch||!batchId||batch.status==='queued')return;
    const key='hexing-brief-next:'+batchId;
    if(sessionStorage.getItem(key)!=='awaiting')return;
    sessionStorage.removeItem(key);
    if(batch.candidates.length&&batch.brief_status==='missing'&&!batch.brief_job){
      // A persisted user search triggers one fee prompt, never a background paid retry.
      void generateBrief(batchId);
    }
    // generateBrief uses the current route's stable input; polling must not repeat the prompt.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[batchId,batch?.status,batch?.brief_status]);
  const candidates=allCandidates(batch);
  const latest=batch?.score_runs?.at(-1);
  const summary=latest?.summary;
  const compared=candidates.filter(c=>selected.includes(c.metadata.full_name));

  return <section className="discovery-search">
    <section className="panel" id="discovery-search"><h2>按主题发现工具</h2><p className="muted">输入想讲的方向，读取资料后整理中文介绍，再决定是否评分或导入。</p>
      <form onSubmit={e=>{e.preventDefault();void act(async()=>{const r=await api<Receipt&{discovery_id:string}>('/discoveries',{topic:topic.trim(),category,threshold});sessionStorage.setItem('hexing-brief-next:'+r.discovery_id,'awaiting');router.push('/discover/search/'+r.discovery_id);});}}>
        <div className="discovery-form"><label>主题关键词<input required value={topic} onChange={e=>setTopic(e.target.value)} minLength={2} maxLength={100} placeholder="例如：AI 视频、知识管理"/></label><label>类别<select value={category} onChange={e=>{setCategory(e.target.value);if(!topic)setTopic(e.target.value);}}><option value="">不限类别</option><option value="AI">AI 工具</option><option value="productivity">效率工具</option><option value="developer tools">开发工具</option></select></label><label>期望使用门槛<input value={threshold} onChange={e=>setThreshold(e.target.value)} maxLength={100} placeholder="例如：无需编程"/></label></div>
        <button className="primary" disabled={busy||topic.trim().length<2}>{busy?'正在提交…':'搜索 GitHub 候选'}</button>
      </form>{error&&<p role="alert" className="error">{error}<Link href="/activity">查看处理记录</Link></p>}
    </section>
    <div className="discovery-results-layout"><aside className="panel search-history"><h3>最近的搜索</h3><small>最多显示最近 30 次</small>
      {listError&&<p className="error" role="alert">搜索记录读取失败：{listError}<button onClick={()=>void refreshList().catch(()=>{})}>重新读取搜索</button></p>}
      {!list&&!listError&&<p role="status">正在读取搜索记录…</p>}
      {list?.items.length===0&&<p>还没有搜索记录。填写主题后开始。</p>}
      <nav aria-label="搜索记录">{list?.items.map(b=><Link aria-current={b.id===batchId?'page':undefined} key={b.id} href={'/discover/search/'+b.id}><strong>{b.query}</strong><small>{new Date(b.created_at).toLocaleString('zh-CN')} · {batchStatus(b)}</small></Link>)}</nav>
    </aside><div className="candidate-results">
      {!batchId&&<section className="panel empty"><h3>先找到值得讲的工具</h3><p>开始一次搜索，或打开左侧历史结果。已有仓库地址可直接导入。</p></section>}
      {batchError&&<p className="error" role="alert">本次搜索读取失败：{batchError}<button onClick={()=>void refreshBatch().catch(()=>{})}>重新读取本次结果</button></p>}
      {batchId&&!batch&&!batchError&&<p role="status">正在读取本次搜索…</p>}
      {batch&&<><div className="section-title"><div><span className="eyebrow">本次搜索</span><h2>{batch.query}</h2></div><span className="badge">{batch.score_runs?.length?batchStatus(batch):`${batch.candidates.length} 个候选 · 尚未评分`}</span></div>
        {summary&&<div className="evaluation-summary" role="status"><strong>推荐 {summary.recommended} · 待核对 {summary.pending} · 暂不推荐 {summary.not_recommended}</strong><p>保留全部 {candidates.length} 个候选。分数仅反映资料评估，缺少依据的分项不计分，不代表实际使用效果。</p>{latest?.revalidated_from&&<small>已用保存的评分结果重新核对，未再次调用模型。</small>}</div>}
        {batch.status==='queued'&&<p role="status">正在搜索与读取 GitHub 资料，刷新后可继续查看。</p>}
        {batch.error&&<p className="error">{batch.error.message} <Link href="/activity">查看失败原因与记录</Link></p>}
        {batch.status==='empty'&&<section className="panel"><h3>这次没有找到候选</h3><p>换一个更具体的主题词，再发起搜索。未生成推荐分数。</p></section>}
        {!!batch.missing?.length&&<details><summary>未读到的资料（{batch.missing.length}）</summary>{batch.missing.map((m,i)=><p key={i}>{m.repository}：{m.message}</p>)}</details>}
        {batch.candidates.length>0&&!batch.score_runs?.length&&<div className="panel score-entry"><p>可以先比较原始资料，或确认一次模型评分，最多推荐 5 项。</p><button disabled={busy} onClick={()=>void act(async()=>{const input={discovery_id:batch.id};const permission=await authorize('discovery_score',input);const r=await api<Receipt>(`/discoveries/${batch.id}/score`,{...input,cost_authorization_id:permission});router.push('/discover/jobs/'+r.job_id);})}>确认并评分本次候选</button></div>}
        {batch.candidates.length>0&&batch.brief_status!=='ready'&&!batch.brief_runs?.length&&<section className="panel brief-entry"><p>先将资料整理成四项中文介绍，再决定是否评分。整批摘要只调用一次模型，确认费用后开始。</p>{batch.brief_job&&jobPending(batch.brief_job.status)?<p role="status">{batch.brief_job.status==='needs_reconciliation'?'摘要结果待核对，请查看处理记录。':'正在整理中文介绍，原始资料仍可在详情查看。'}</p>:<button disabled={busy} onClick={()=>void generateBrief(batch.id)}>整理四项中文介绍</button>}{batch.brief_job?.error&&<p className="error">{batch.brief_job.error.message}</p>}</section>}
        {batch.brief_status==='incomplete'&&<p className="mode-note">本次有部分项目未整理出可核对的介绍。原始资料和返回结果已保留，不自动重试。</p>}
        {compared.length>0&&<section className="panel comparison" aria-label="候选比较"><div className="panel-heading"><h3>已选 {compared.length} / 3 项比较</h3><button onClick={()=>setSelected([])}>清空比较</button></div><p className="muted">与卡片使用同一份中文介绍。资料不足的项目不推测，详细条件见来源。</p><div className="comparison-scroll" tabIndex={0}><table><caption className="sr-only">候选简要比较，点击工具名查看完整资料</caption><thead><tr><th scope="col">比较项目</th>{compared.map(c=><th scope="col" key={c.metadata.full_name}><button className="comparison-tool" onClick={e=>openDetails(c,e.currentTarget)}>{c.metadata.full_name}</button></th>)}</tr></thead><tbody>{briefKeys.map(key=><tr key={key}><th scope="row">{briefLabels[key]}</th>{compared.map(c=><td key={c.metadata.full_name}><span className="comparison-preview">{briefField(c,key).text}</span>{briefField(c,key).evidence.length>0&&<button className="source-detail-link" onClick={e=>openDetails(c,e.currentTarget)}>查看依据</button>}</td>)}</tr>)}</tbody></table></div></section>}
        <div className="candidate-grid">{candidates.map(c=><article className="panel candidate-card" key={c.metadata.full_name}>
          <div className="panel-heading"><h3 title={c.metadata.full_name}>{c.metadata.full_name}</h3>{c.score&&<span className="score-number" aria-label={c.score.partial?`部分计分 ${c.score.total} 分，仍有分项待核对`:`资料评分 ${c.score.total} 分，满分 10 分`}>{c.score.total}<small>{c.score.partial?'部分计分':'/10'}</small></span>}</div>
          <span className="candidate-status" data-status={c.disposition}>{candidateStatus(c)}</span>
          <dl className="candidate-brief">{briefKeys.map(key=><div key={key}><dt>{briefLabels[key]}</dt><dd data-missing={briefField(c,key).evidence.length===0}>{briefField(c,key).text}</dd></div>)}</dl>
          <div className="candidate-meta"><span>★ {new Intl.NumberFormat('zh-CN',{notation:'compact',maximumFractionDigits:1}).format(c.metadata.stargazers_count)}</span><span>{c.fragments.length?`来源 ${c.fragments.length} 段`:'缺少原文'}</span>{!!c.missing_fields?.length&&<span className="candidate-missing">缺 {c.missing_fields.length} 项</span>}</div>
          <footer className="candidate-actions"><div className="candidate-secondary"><button aria-haspopup="dialog" onClick={e=>openDetails(c,e.currentTarget)}>查看详情</button><label className="check-label"><input type="checkbox" checked={selected.includes(c.metadata.full_name)} disabled={!selected.includes(c.metadata.full_name)&&selected.length>=3} onChange={e=>setSelected(old=>e.target.checked?[...old,c.metadata.full_name]:old.filter(v=>v!==c.metadata.full_name))}/><span>加入比较</span></label></div><button className="candidate-import" disabled={busy||importing} onClick={()=>void act(()=>importRepository(c.metadata.html_url))}>导入并复核亮点</button></footer>
        </article>)}</div>
        {batch.brief_runs?.map(run=><details className="panel evaluation-technical" key={run.job_id}><summary>中文介绍生成记录</summary><pre>{run.raw_text}</pre><JobProgress jobId={run.job_id}/></details>)}
        {batch.score_runs?.map(run=><details className="panel evaluation-technical" key={run.job_id}><summary>评估记录与技术详情</summary>{run.rejected.map((r,i)=><p key={i}>{r.repository||'返回结果'}：{r.message}</p>)}{!run.rejected.length&&<p>评分格式与引用检查通过，未验证实际使用效果。</p>}<details><summary>模型返回原文</summary><pre>{run.raw_text}</pre></details><JobProgress jobId={run.job_id}/></details>)}
      </>}
    </div></div>
    <dialog ref={dialog} className="candidate-dialog" aria-labelledby="candidate-detail-name" onCancel={e=>{e.preventDefault();closeDetails();}}>
      {detail&&<><h2 id="candidate-detail-name" tabIndex={-1}>{detail.metadata.full_name}</h2><p className="candidate-full-description">{detail.metadata.description||'仓库未提供用途说明，请核对来源。'}</p>
        <small>Star {detail.metadata.stargazers_count} · 读取于 {new Date(detail.read_at).toLocaleString('zh-CN')}</small>
        <p className="candidate-status" data-status={detail.disposition}>{candidateStatus(detail)}{detail.score?` · ${detail.score.partial?'部分计分':'资料评分'} ${detail.score.total}${detail.score.partial?' 分':'/10'}`:''}</p>
        {!!detail.decision_reasons?.length&&<p>{detail.decision_reasons.join('；')}</p>}
        {!!detail.evaluation_issues?.length&&<div className="mode-note"><strong>需要核对</strong><ul>{detail.evaluation_issues.map((issue,i)=><li key={i}>{issue.message}</li>)}</ul></div>}
        {detail.score?.reason&&<><h3>选题判断 · 模型资料评估</h3><p>{detail.score.reason}</p></>}
        {briefKeys.map(key=>{const field=briefField(detail,key);return <section key={key}><h3>{briefLabels[key]}</h3><p>{field.text}</p>{field.evidence.length>0&&<details><summary>查看摘要依据</summary>{field.evidence.map((e,i)=><section key={i}><p className="brief-source-quote">{e.quote}</p><small>{e.ref==='description'?'仓库简介':e.ref}</small></section>)}</details>}</section>;})}
        <h3>资料状态</h3><p>{detail.fragments.length?`已读取 ${detail.fragments.length} 段来源，导入后可核对亮点。`:'缺少原文，暂无法核对功能。'}</p>
        {!!detail.missing_fields?.length&&<p className="mode-note">资料缺项：{detail.missing_fields.join('；')}</p>}
        {detail.score&&<details><summary>评分依据</summary><p>主题 {detail.score.relevance??'待核对'}/3 · 说明 {detail.score.completeness??'待核对'}/2 · 演示 {detail.score.demonstration??'待核对'}/2 · 维护 {detail.score.maintenance??'待核对'}/2 · 门槛 {detail.score.accessibility??'待核对'}/1</p><p>{detail.maintenance_reason}</p><pre>{JSON.stringify(detail.score.evidence,null,2)}</pre></details>}
        <details><summary>查看来源原文（{detail.fragments.length} 段）</summary>{detail.fragments.map(f=><section key={f.id}><small>{f.id}</small><pre>{f.text}</pre>{f.source_url&&<a href={f.source_url} target="_blank" rel="noopener noreferrer">打开来源 ↗</a>}</section>)}</details>
        <footer className="candidate-dialog-actions"><a className="button" href={detail.metadata.html_url} target="_blank" rel="noopener noreferrer">查看 GitHub ↗</a><button onClick={closeDetails}>关闭</button></footer>
      </>}
    </dialog>
  </section>;
}
