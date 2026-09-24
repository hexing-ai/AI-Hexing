'use client';
import {useEffect, useState} from 'react';
import Link from 'next/link';
import {api} from '../lib/api';

export type Budget = {month:string;single_limit_micros:number;monthly_limit_micros:number;priced_micros:number;actual_micros:number;awaiting_bill_count:number;reconciled_count:number;held_micros:number;remaining_micros:number;pending_count:number;historical_unknown_count:number;blocked_by_overrun:boolean;scope:string};
export const yuan = (micros:number) => (micros/1_000_000).toFixed(6).replace(/0+$/,'').replace(/\.$/,'');
export default function CostBudget(){
  const [budget,setBudget]=useState<Budget|null>(null);
  const [error,setError]=useState('');
  useEffect(()=>{
    let active=true;
    const refresh=()=>api<Budget>('/cost-budget').then(b=>{if(active){setBudget(b);setError('');}}).catch(()=>{if(active)setError('预算暂时无法读取，调用前仍由服务端校验。');});
    void refresh();const timer=setInterval(()=>void refresh(),5000);
    const changed=()=>void refresh();window.addEventListener('hexing-cost-changed',changed);
    return ()=>{active=false;clearInterval(timer);window.removeEventListener('hexing-cost-changed',changed);};
  },[]);
  return <details className="muted-box"><summary>费用预算{budget?` · 单次 ¥${yuan(budget.single_limit_micros)} · 每月 ¥${yuan(budget.monthly_limit_micros)}`:''}</summary>{error&&<p className="error">{error}</p>}{budget&&<><p>{budget.month}（北京时间）· 已核实实际费用 ¥{yuan(budget.actual_micros||0)} · 尚未对账目录价 ¥{yuan(budget.priced_micros)} · 预占/待对账 ¥{yuan(budget.held_micros)} · 剩余 ¥{yuan(budget.remaining_micros)}</p><p>待对账 {budget.pending_count} 条；预算启用前的历史未知费用 {budget.historical_unknown_count} 条，未当作零费用计入。</p>{budget.blocked_by_overrun&&<p className="error">发现供应商用量超出预占，新增调用已暂停，请核对原记录。</p>}<small>{budget.scope} 上限由本地服务配置管理。</small><p><Link href="/costs">查看费用并核对账单 →</Link></p></>}</details>;
}
