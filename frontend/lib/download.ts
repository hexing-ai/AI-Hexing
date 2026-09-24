import {ApiError} from './api';
export async function downloadFile(url:string,fallback:string){
  const response=await fetch(url,{credentials:'same-origin',signal:AbortSignal.timeout(30000)}).catch(()=>{throw new Error('文件读取中断或超时，请检查连接后重试；原文件记录仍保留。');});
  if(response.status===401)window.dispatchEvent(new Event('hexing-session-expired'));
  if(!response.ok){const body=await response.json().catch(()=>null);throw new ApiError(body?.error?.message||'文件暂时无法下载，请重新读取；过期素材包可重新打包。',response.status);}
  const blob=await response.blob().catch(()=>{throw new Error('文件读取中断或超时，请重新读取；原文件记录仍保留。');});if(!blob.size)throw new Error('返回的文件为空，请重新读取。');
  const name=response.headers.get('Content-Disposition')?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  let filename=fallback;try{if(name)filename=decodeURIComponent(name);}catch{/* fallback */}
  const objectUrl=URL.createObjectURL(blob);const link=document.createElement('a');link.href=objectUrl;link.download=filename;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(objectUrl),1000);
}
