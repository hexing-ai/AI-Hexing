'use client';
import {useRef,useState} from 'react';
import {downloadFile} from '../lib/download';
export default function DownloadButton({url,label,filename='AI-Hexing-素材',className='text-button'}:{url:string;label:string;filename?:string;className?:string}){
  const [busy,setBusy]=useState(false);const [message,setMessage]=useState('');const [error,setError]=useState('');const lock=useRef(false);
  return <div className="download-control"><button className={className} data-download-url={url} disabled={busy} onClick={()=>{if(lock.current)return;lock.current=true;setBusy(true);setMessage('正在读取文件…');setError('');void downloadFile(url,filename).then(()=>setMessage('文件已交给浏览器，请在下载列表查看。')).catch(e=>{setMessage('');setError(e.message);}).finally(()=>{lock.current=false;setBusy(false);});}}>{busy?'正在读取…':label}</button>{message&&<small role="status">{message}</small>}{error&&<p className="error" role="alert">{error}<br/>可再次点击“{label}”重试。</p>}</div>;
}
