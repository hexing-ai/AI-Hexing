'use client';
import {useEffect,useId,useRef,type CSSProperties} from 'react';
import {BotEngine,type BotFrame} from '../../lib/bloub/engine';
import {SHAPE_BY_ID} from '../../lib/bloub/skins';
import {EXPRESSIONS} from '../../lib/bloub/expressions';
import {shapeOptions,palette,type Avatar} from '../../lib/botAppearance';
import {animateBot} from '../../lib/botAnimationClock';
import {useMediaPreference} from './useMediaPreference';
export type {Avatar} from '../../lib/botAppearance';
export {palette} from '../../lib/botAppearance';
const frames=new Map<string,BotFrame>();
function shapeFor(preset:string){return SHAPE_BY_ID.get(shapeOptions.find(s=>s.value===preset)?.shape||'cercle')!.radii;}
function rest(preset:string){if(!frames.has(preset))frames.set(preset,new BotEngine(100,'idle',shapeFor(preset)).sample(0));return frames.get(preset)!;}

/** React adapter for jeremy-prt/bloub's MIT-licensed geometry and eye engine. */
export default function BotAvatar({avatar,mood='idle',still=false}:{avatar?:Avatar;mood?:'idle'|'curious'|'grabbed'|'happy';still?:boolean}){
  const svg=useRef<SVGSVGElement>(null);const id=useId();const clip='bloub-'+id.replace(/:/g,'');
  const preset=avatar?.preset||'orbit',color=palette[avatar?.palette||'mint']||palette.mint;
  const reduced=useMediaPreference('(prefers-reduced-motion: reduce)',true);
  const initial=rest(preset);
  useEffect(()=>{
    const node=svg.current;if(!node)return;
    const engine=new BotEngine(100,'idle',shapeFor(preset));
    const state=mood==='curious'?'wide':mood==='grabbed'?'egg':mood==='happy'?'wink':'idle';
    const seed=Array.from(id+preset+(avatar?.palette||'mint')).reduce((n,c)=>(n*31+c.charCodeAt(0))>>>0,7);
    const offset=seed%71/7;
    const expression=['neutre','attentif','heureux','curieux','surpris'][seed%5];
    engine.setExpression(EXPRESSIONS.find(e=>e.id===expression)!,offset-1);
    engine.setState(state,offset);
    const body=node.querySelector('[data-body]')!,mask=node.querySelector('clipPath path')!;
    const eyes=Array.from(node.querySelectorAll('[data-eye]'));
    const paint=(time:number)=>{const f=engine.sample(time);body.setAttribute('d',f.bodyPath);mask.setAttribute('d',f.bodyPath);body.setAttribute('opacity',String(f.bodyAlpha));eyes.forEach((eye,i)=>{const e=f.eyes[i];eye.setAttribute('opacity',String(e?.alpha||0));if(e){eye.setAttribute('d',e.d);eye.setAttribute('transform',e.matrix);}});};
    paint(still||reduced?offset+.8:offset);
    if(still||reduced)return;
    let stop:(()=>void)|undefined;let start=0;
    const observer=new IntersectionObserver(entries=>{if(entries[0]?.isIntersecting){if(!stop){start=performance.now()/1000;stop=animateBot(t=>paint(offset+t-start));}}else{stop?.();stop=undefined;}});
    observer.observe(node);
    return()=>{observer.disconnect();stop?.();};
  },[preset,avatar?.palette,mood,still,reduced,id]);
  return <svg ref={svg} className={`hexing-bot bot-${preset} ${still||reduced?'bot-still':''}`} style={{'--bot-color':color} as CSSProperties} viewBox="-128 -128 256 256" aria-hidden="true" focusable="false">
    <defs><clipPath id={clip}><path d={initial.bodyPath}/></clipPath></defs>
    <path data-body d={initial.bodyPath} fill={color} stroke={avatar?.palette==='ink'?'#414746':'none'} strokeWidth="1.5"/>
    <g clipPath={`url(#${clip})`} fill={avatar?.palette==='cream'?'#353c3b':'#fff'}>{[0,1].map(i=><path key={i} data-eye d={initial.eyes[i]?.d||''} transform={initial.eyes[i]?.matrix} opacity={initial.eyes[i]?.alpha||0}/>)}</g>
  </svg>;
}
