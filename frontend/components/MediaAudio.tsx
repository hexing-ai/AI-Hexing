'use client';
import {useRef,useState} from 'react';
export default function MediaAudio({src,label='音频试听'}:{src:string;label?:string}){
  const player=useRef<HTMLAudioElement>(null);const [state,setState]=useState<'idle'|'loading'|'ready'|'playing'|'error'>('idle');
  return <div className="media-audio"><audio ref={player} controls preload="none" aria-label={label} src={src} onLoadStart={()=>setState('loading')} onSuspend={()=>{if(player.current?.readyState===0)setState('idle');}} onLoadedData={()=>setState('ready')} onCanPlay={()=>setState(player.current?.paused?'ready':'playing')} onPlay={()=>setState('playing')} onPause={()=>setState(old=>old==='error'?'error':'ready')} onWaiting={()=>setState('loading')} onError={()=>setState('error')} onEnded={()=>setState('ready')}/>{state==='loading'&&<small role="status">正在读取或缓冲音频…</small>}{state==='playing'&&<small role="status">正在播放</small>}{state==='error'&&<div className="error" role="alert"><p>音频暂时无法播放。可以重新读取，或下载后检查；这不会重新合成。</p><button onClick={()=>{setState('loading');player.current?.load();}}>重新读取音频</button></div>}</div>;
}
