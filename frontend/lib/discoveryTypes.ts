export type FactKey='feature'|'usage'|'requirements'|'angle'|'limitations';
export type Fact={text:string;refs:string[];source_url?:string;kind:'source_quote'};
export type Issue={repository?:string;code?:string;field?:string;message:string};
export type Candidate={
  metadata:{full_name:string;html_url:string;description:string;stargazers_count:number};
  read_at:string;fragments:{id:string;text:string;source_url?:string}[];missing_fields?:string[];
  brief?:Brief;facts?:Partial<Record<FactKey,Fact>>;disposition?:'recommended'|'pending'|'not_recommended';
  evaluation_issues?:Issue[];decision_reasons?:string[];
  score?:{reason:string|null;barrier:string|null;relevance:number|null;completeness:number|null;demonstration:number|null;accessibility:number|null;maintenance:number|null;total:number;partial?:boolean;evidence:Record<string,string[]>};
  maintenance_reason?:string;
};
export type ScoreRun={job_id:string;recommendations:Candidate[];evaluated_candidates?:Candidate[];summary?:{recommended:number;pending:number;not_recommended:number};rejected:Issue[];raw_text:string;revalidated_from?:string};
export type Batch={id:string;query:string;created_at:string;status:string;error?:{message:string};candidates:Candidate[];missing?:{repository:string;message:string}[];brief_status?:'ready'|'missing'|'incomplete';brief_job?:{id:string;status:string;error?:{message:string}};brief_runs?:{job_id:string;raw_text:string}[];score_runs?:ScoreRun[]};
export const factLabels:Record<FactKey,string>={feature:'核心特点',usage:'使用方式',requirements:'准备条件',angle:'可展示场景',limitations:'主要限制'};
export const factKeys=Object.keys(factLabels) as FactKey[];
export function candidateStatus(candidate:Candidate){return candidate.disposition==='recommended'?'推荐':candidate.disposition==='not_recommended'?'暂不推荐':candidate.disposition==='pending'?'评估待核对':'尚未评分';}
export function batchStatus(batch:Batch){
  const summary=batch.score_runs?.at(-1)?.summary;
  if(summary)return summary.pending?(summary.pending===batch.candidates.length?'评估待核对':'部分待核对'):'评估完成';
  return batch.status==='queued'?'搜索中':batch.status==='failed'?'失败':batch.score_runs?.length?'评估待核对':`${batch.candidates.length} 项`;
}
export function allCandidates(batch:Batch|null|undefined):Candidate[]{
  if(!batch)return [];
  const latest=batch.score_runs?.at(-1);
  const evaluated=latest?.evaluated_candidates||latest?.recommendations||[];
  const byName=new Map(evaluated.map(c=>[c.metadata.full_name,c]));
  return batch.candidates.map(c=>({...c,...(byName.get(c.metadata.full_name)||(latest?{disposition:'pending' as const}:{})),brief:c.brief}));
}
/** Strip formatting only for compact reading; preserve the original quote in details. */
export function factText(fact:Fact|undefined){return fact?.text.replace(/\[([^\]]+)\]\([^)]+\)/g,'$1').replace(/[*`]/g,'').replace(/^\s*\||\|\s*$/g,'').replace(/\s*\|\s*/g,' · ').replace(/\s+/g,' ').trim();}

export const briefLabels={purpose:'用途',feature:'核心特点',usage:'使用方式',requirements:'准备条件'} as const;
export const briefKeys=Object.keys(briefLabels) as (keyof typeof briefLabels)[];
export type BriefField={text:string;evidence:{ref:string;quote:string}[]};
export type Brief={version:string;source_hash:string;fields:Record<keyof typeof briefLabels,BriefField>};
export function briefField(candidate:Candidate,key:keyof typeof briefLabels):BriefField{return candidate.brief?.fields[key]||{text:'资料不足',evidence:[]};}
