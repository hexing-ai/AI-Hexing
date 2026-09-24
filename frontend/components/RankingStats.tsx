'use client';
import {useEffect,useState} from 'react';
import {api} from '../lib/api';
type Counts={intro_open:number;github_click:number;retention_days:number};

export default function RankingStats({entryId}:{entryId:string}){
  const [counts,setCounts]=useState<Counts|null>(null);
  const [error,setError]=useState('');
  const [refresh,setRefresh]=useState(0);
  useEffect(()=>{
    let active=true;
    api<Counts>('/ranking/entries/'+entryId+'/stats').then(value=>{if(active){setCounts(value);setError('');}}).catch(()=>{if(active)setError('交互记录暂时无法读取。');});
    return()=>{active=false;};
  },[entryId,refresh]);
  return <section className="panel"><h2>公开交互记录</h2>{error&&<p role="status">{error}</p>}{counts&&<><p>近 {counts.retention_days} 天：查看介绍 {counts.intro_open} 次 · GitHub 链接点击 {counts.github_click} 次</p><small>这是接收成功的交互次数，不表示独立访客人数或 GitHub 成功加载；记录失败不影响浏览。</small></>}<button onClick={()=>setRefresh(n=>n+1)}>刷新交互记录</button></section>;
}
