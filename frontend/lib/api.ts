export class ApiError extends Error {
  constructor(message: string, public status: number, public requestId?: string) { super(message); }
}
let csrf = '';
export function setCsrf(value: string) { csrf = value; }
export async function api<T>(path: string, body?: unknown, method = body === undefined ? 'GET' : 'POST', key?: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== 'GET') {
    headers['X-CSRF-Token'] = csrf;
    headers['Idempotency-Key'] = key || crypto.randomUUID();
  }
  const isForm = body instanceof FormData;
  if (body !== undefined && !isForm) headers['Content-Type'] = 'application/json';
  const controller=method==='GET'?new AbortController():null;
  const timeout=controller?setTimeout(()=>controller.abort(),20000):null;
  try {
    const response=await fetch('/api/v1'+path,{method,signal:controller?.signal,credentials:'same-origin',headers,body:body===undefined?undefined:isForm?body:JSON.stringify(body)});
    if(response.status===401&&!path.startsWith('/auth/'))window.dispatchEvent(new Event('hexing-session-expired'));
    if(response.status===204)return undefined as T;
    const data=await response.json().catch(()=>null);
    if(!response.ok)throw new ApiError(data?.error?.message||'服务暂时不可用，请稍后查看。',response.status,data?.error?.request_id);
    if(!data||typeof data!=='object'||!Object.hasOwn(data,'data'))throw new ApiError('服务返回内容不完整，请重新读取；已提交操作请先查看处理记录。',502);
    return data.data as T;
  }catch(error){
    if(error instanceof ApiError)throw error;
    throw new ApiError(method==='GET'?'读取连接中断或超时，请重新读取。':'连接中断，请查看原作业；不要重复提交收费操作。',0);
  }finally{if(timeout)clearTimeout(timeout);}
}
export type Mode = 'mock' | 'live';
export type Job = { id: string; kind: string; status: string; phase: string; result_ref: Record<string,string> | null; error: {message:string;request_id?:string} | null; possible_charge:boolean; mode: Mode };
export type Receipt = { job_id: string };
export type Fragment = {id:string;text:string;source_url:string};
export type Snapshot = {id:string;tool_id:string;metadata:{full_name:string;description:string};fragments:Fragment[];missing_fields:string[];fetched_at:string;canonical_url:string};
export type Analysis = {id:string;snapshot_id:string;summary:string;audience:string;highlights:{id:string;capability:string;benefit:string;demo:string;conditions:string;evidence_refs:string[]}[];unknowns:string[];warnings:string[]};
export type Task = {materials_ready?:boolean;context_id?:string|null;lifecycle?:{state:string;purge_after:string|null;can_restore:boolean;cleanup_status:string|null};id:string;tool_id:string;snapshot_id:string;review_id:string;audience:string;revision:number;status:string;current_script?:unknown;current_copy?:unknown;current_audio?:unknown;audio_stale?:boolean;copy_stale?:boolean};
export type Voice = {id:string;display_name?:string;is_default?:boolean;eligible?:boolean;data_mode?:Mode;status:string;revision:number;target_model:string;region:string;preview_confirmed_at:string|null;cleanup_state:string};
export type Asset = {id:string;consent_record_id:string;duration_ms:number;download_path:string;state:string};
export type Run = {id:string;kind:string;job_id:string;status:string;mode:Mode;output:{raw_text?:string;issues?:{path:string;code:string;message:string;severity:string}[];content?:{script?:Record<string,string>;copy?:{body:string;tags:string[];title?:string}};checks?:Record<string,boolean>;asset_id?:string;duration_ms?:number;probe_voice_id?:string;is_test_tone?:boolean}|null;human_verdict:string|null};
export type Capabilities = {mode:Mode;contracts:{kind:string;state:string;missing_config_names:string[]}[];contract_version:string;validation_tools_enabled:boolean};
export type List<T> = {items:T[];next_cursor:string|null};
