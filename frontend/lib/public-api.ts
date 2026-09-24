import type {Avatar} from '../components/ranking/BotAvatar';
export type PublicTool = {id:string;version:number;first_published_at:string|null;bot_avatar:Avatar;name:string;highlights:string[];github_url:string;last_verified_at:string|null;verification_status:'verified'|'unavailable'|'unknown';category:string|null;one_line_use:string|null};
export type PublicPage = {items:PublicTool[];next_cursor:string|null};
export class PublicReadError extends Error {
  constructor(message:string,public status:number){super(message);}
}

export async function publicRead<T>(path:string):Promise<T>{
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),8000);
  try{
    const response=await fetch('/api/v1/public/tools'+path,{credentials:'omit',cache:'no-store',signal:controller.signal});
    if(!response.ok)throw new PublicReadError(response.status===404?'此工具已下架或尚未公开。':'资料暂时无法刷新，请稍后重试。',response.status);
    return (await response.json()).data as T;
  }catch(error){
    if(error instanceof PublicReadError)throw error;
    throw new PublicReadError('连接中断或超时，请稍后重试。',0);
  }finally{clearTimeout(timer);}
}

export function publicEvent(tool:PublicTool,kind:'intro_open'|'github_click'){
  // One best-effort event per interaction. Never delay navigation or queue retries.
  try{void fetch('/api/v1/public/tools/'+encodeURIComponent(tool.id)+'/events',{
    method:'POST',credentials:'omit',cache:'no-store',keepalive:true,
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({event_id:crypto.randomUUID(),version:tool.version,kind}),
  }).catch(()=>{});}catch{/* Browsing still works if the browser cannot create an event. */}
}
