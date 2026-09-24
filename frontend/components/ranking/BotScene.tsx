'use client';
import {useEffect,useRef,useState,type PointerEvent as ReactPointerEvent} from 'react';
import type {Body as MatterBody} from 'matter-js';
import type {PublicTool} from '../../lib/public-api';
import BotAvatar from './BotAvatar';

export const BOT_DRAG_THRESHOLD_PX=5;
type Runtime={move:(id:string,x:number,y:number)=>void;freeze:(id:string,on:boolean)=>void;pause:(on:boolean)=>void;reset:()=>void};
type Drag={id:string;pointer:number;x:number;y:number;offsetX:number;offsetY:number;travel:number;node:HTMLButtonElement};

export default function BotScene({tools,paused,onOpen,onFailure}:{tools:PublicTool[];paused:boolean;onOpen:(tool:PublicTool,button:HTMLButtonElement)=>void;onFailure:()=>void}){
  const board=useRef<HTMLDivElement>(null);const runtime=useRef<Runtime|null>(null);
  const flags=useRef({paused,focus:''});const drag=useRef<Drag|null>(null);const suppress=useRef(false);
  const [ready,setReady]=useState(false);const [failed,setFailed]=useState(false);const [active,setActive]=useState('');const [hovered,setHovered]=useState('');
  const ids=JSON.stringify(tools.map(t=>t.id));
  const failCallback=useRef(onFailure);
  useEffect(()=>{failCallback.current=onFailure;},[onFailure]);
  useEffect(()=>{flags.current.paused=paused;runtime.current?.pause(paused);},[paused]);

  useEffect(()=>{
    const element=board.current;if(!element)return;
    let disposed=false;let frame=0;let cleanup=()=>{};
    void import('matter-js').then(module=>{
      if(disposed)return;
      const {Engine,Bodies,Body,Composite}=module.default||module;
      const engine=Engine.create({gravity:{x:0,y:0},positionIterations:8,velocityIterations:8});
      const entries=JSON.parse(ids) as string[];const bodies=new Map<string,MatterBody>();
      const nodes=new Map(Array.from(element.querySelectorAll<HTMLButtonElement>('[data-bot-id]')).map(n=>[n.dataset.botId!,n]));
      let width=0,height=0,cardWidth=110,cardHeight=118,walls:MatterBody[]=[];let last=0;
      const velocity=(i:number)=>({x:(i%2?1:-1)*(.16+(i%5)*.02),y:(i%3?1:-1)*(.13+(i%4)*.025)});
      const paint=()=>{for(const [id,body] of bodies){const node=nodes.get(id);if(node)node.style.transform=`translate3d(${(body.position.x-cardWidth/2).toFixed(2)}px,${(body.position.y-cardHeight/2).toFixed(2)}px,0)`;}};
      const contain=(body:MatterBody,x:number,y:number)=>Body.setPosition(body,{x:Math.max(cardWidth/2+6,Math.min(width-cardWidth/2-6,x)),y:Math.max(cardHeight/2+6,Math.min(height-cardHeight/2-6,y))});
      const arrange=()=>{
        // Deterministic best-candidate packing gives the scene an uneven, open layout.
        // The full label rectangle participates, so long names do not overlap neighbours.
        let seed=entries.join('').split('').reduce((n,c)=>(n*31+c.charCodeAt(0))>>>0,741);
        const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};
        const placed:{x:number;y:number}[]=[];
        entries.forEach((id,i)=>{
          let best={x:width/2,y:height/2},score=-1;
          for(let n=0;n<100;n++){
            const candidate={x:cardWidth/2+12+random()*Math.max(0,width-cardWidth-24),y:cardHeight/2+12+random()*Math.max(0,height-cardHeight-24)};
            const distance=placed.length?Math.min(...placed.map(p=>Math.max(Math.abs(p.x-candidate.x)/(cardWidth+12),Math.abs(p.y-candidate.y)/(cardHeight+12)))):1;
            if(distance>score){score=distance;best=candidate;}
          }
          placed.push(best);const body=bodies.get(id)!;contain(body,best.x,best.y);Body.setVelocity(body,velocity(i));
        });paint();
      };
      const resize=()=>{
        const rect=element.getBoundingClientRect();width=rect.width;height=rect.height;
        const nextWidth=width<550?92:142,nextHeight=width<550?112:120;
        for(const body of bodies.values()){Body.scale(body,nextWidth/cardWidth,nextHeight/cardHeight);Body.setInertia(body,Infinity);}
        cardWidth=nextWidth;cardHeight=nextHeight;
        element.style.setProperty('--bot-width',cardWidth+'px');element.style.setProperty('--bot-height',cardHeight+'px');
        if(walls.length)Composite.remove(engine.world,walls);
        walls=[Bodies.rectangle(width/2,-24,width+100,48,{isStatic:true}),Bodies.rectangle(width/2,height+24,width+100,48,{isStatic:true}),Bodies.rectangle(-24,height/2,48,height+100,{isStatic:true}),Bodies.rectangle(width+24,height/2,48,height+100,{isStatic:true})];Composite.add(engine.world,walls);arrange();
      };
      entries.forEach((id,i)=>{const body=Bodies.rectangle(80,80,cardWidth,cardHeight,{chamfer:{radius:18},restitution:1,friction:0,frictionStatic:0,frictionAir:0,inertia:Infinity});Body.setVelocity(body,velocity(i));bodies.set(id,body);Composite.add(engine.world,body);});
      const stop=()=>{cancelAnimationFrame(frame);frame=0;last=0;};
      const tick=(now:number)=>{
        frame=0;if(disposed||flags.current.paused||document.hidden)return;
        try{
          Engine.update(engine,last?Math.min(1000/60,now-last):1000/60);last=now;
          for(const [id,body] of bodies){
            if(!body.isStatic){const speed=Math.hypot(body.velocity.x,body.velocity.y);if(speed>0.8)Body.setVelocity(body,{x:body.velocity.x*.8/speed,y:body.velocity.y*.8/speed});else if(speed<.15)Body.setVelocity(body,velocity(entries.indexOf(id)));const {x,y}=body.position,v=body.velocity;Body.setVelocity(body,{x:x<=cardWidth/2+6?Math.abs(v.x):x>=width-cardWidth/2-6?-Math.abs(v.x):v.x,y:y<=cardHeight/2+6?Math.abs(v.y):y>=height-cardHeight/2-6?-Math.abs(v.y):v.y});contain(body,x,y);}
          }
          paint();frame=requestAnimationFrame(tick);
        }catch{stop();setFailed(true);failCallback.current();}
      };
      const start=()=>{if(!frame&&!disposed&&!flags.current.paused&&!document.hidden)frame=requestAnimationFrame(tick);};
      const freeze=(id:string,on:boolean)=>{const body=bodies.get(id);if(body){Body.setStatic(body,on);if(!on)Body.setVelocity(body,velocity(entries.indexOf(id)));}};
      const visible=()=>{stop();if(document.hidden){if(drag.current){const d=drag.current;drag.current=null;suppress.current=true;if(d.node.hasPointerCapture(d.pointer))d.node.releasePointerCapture(d.pointer);freeze(d.id,false);setActive('');}}else start();};
      runtime.current={move:(id,x,y)=>{const body=bodies.get(id);if(body){contain(body,x,y);paint();}},freeze,pause:on=>{stop();if(!on)start();},reset:arrange};
      resize();if(flags.current.focus)freeze(flags.current.focus,true);setReady(true);
      const observer=new ResizeObserver(resize);observer.observe(element);document.addEventListener('visibilitychange',visible);start();
      cleanup=()=>{stop();observer.disconnect();document.removeEventListener('visibilitychange',visible);Composite.clear(engine.world,false);Engine.clear(engine);runtime.current=null;};
    }).catch(()=>{if(!disposed){setFailed(true);failCallback.current();}});
    return()=>{disposed=true;cancelAnimationFrame(frame);cleanup();drag.current=null;};
  },[ids]);

  function cancel(){const d=drag.current;if(!d)return;drag.current=null;suppress.current=true;setActive('');if(d.node.hasPointerCapture(d.pointer))d.node.releasePointerCapture(d.pointer);runtime.current?.freeze(d.id,flags.current.focus===d.id);}
  useEffect(()=>{window.addEventListener('blur',cancel);return()=>window.removeEventListener('blur',cancel);},[]);
  function down(event:ReactPointerEvent<HTMLButtonElement>,id:string){
    if(!event.isPrimary||event.button!==0){cancel();return;}if(!runtime.current||failed)return;
    const node=event.currentTarget,rect=node.getBoundingClientRect();suppress.current=false;
    drag.current={id,pointer:event.pointerId,x:event.clientX,y:event.clientY,offsetX:event.clientX-rect.left-rect.width/2,offsetY:event.clientY-rect.top-rect.height/2,travel:0,node};node.setPointerCapture(event.pointerId);runtime.current.freeze(id,true);
  }
  function move(event:ReactPointerEvent<HTMLButtonElement>){const d=drag.current;if(!d||d.pointer!==event.pointerId)return;d.travel+=Math.hypot(event.clientX-d.x,event.clientY-d.y);d.x=event.clientX;d.y=event.clientY;if(d.travel<BOT_DRAG_THRESHOLD_PX)return;setActive(d.id);suppress.current=true;const rect=board.current!.getBoundingClientRect();runtime.current?.move(d.id,event.clientX-rect.left-d.offsetX,event.clientY-rect.top-d.offsetY);}
  function up(event:ReactPointerEvent<HTMLButtonElement>){const d=drag.current;if(!d||d.pointer!==event.pointerId)return;d.travel+=Math.hypot(event.clientX-d.x,event.clientY-d.y);if(d.travel>=BOT_DRAG_THRESHOLD_PX)suppress.current=true;drag.current=null;setActive('');if(d.node.hasPointerCapture(d.pointer))d.node.releasePointerCapture(d.pointer);runtime.current?.freeze(d.id,flags.current.focus===d.id);}

  return <div className="bot-scene-shell">
    <div ref={board} className={`bot-scene ${ready&&!failed?'scene-ready':'scene-static'} ${paused?'scene-paused':''}`} data-motion={paused?'paused':failed?'fallback':'running'} aria-label="可拖动的工具 Bot，点按查看介绍">
      {tools.map((tool,i)=><button key={tool.id} type="button" className={`scene-bot ${active===tool.id?'is-dragging':''}`} data-bot-id={tool.id} aria-label={'查看 '+tool.name+' 的介绍'} aria-haspopup="dialog" style={{animationDelay:(i%6)*60+'ms'}}
        onPointerDown={e=>down(e,tool.id)} onPointerMove={move} onPointerUp={up} onPointerCancel={cancel} onLostPointerCapture={()=>{if(drag.current)cancel();}}
        onPointerEnter={()=>setHovered(tool.id)} onPointerLeave={()=>setHovered('')}
        onFocus={()=>{flags.current.focus=tool.id;runtime.current?.freeze(tool.id,true);setHovered(tool.id);}} onBlur={()=>{flags.current.focus='';runtime.current?.freeze(tool.id,false);setHovered('');}}
        onClick={e=>{if(e.detail!==0&&suppress.current){suppress.current=false;return;}onOpen(tool,e.currentTarget);}}>
        <BotAvatar avatar={tool.bot_avatar} mood={active===tool.id?'grabbed':hovered===tool.id?'curious':'idle'} still={paused||failed}/><span className="bot-name">{tool.name}</span>
      </button>)}
    </div>
    <div className="scene-instructions"><span>{failed?'动效暂不可用，仍可点名称查看介绍。':paused?'已暂停，仍可点按查看介绍。':'点一点，认识工具；拖一拖，让它换个位置。'}</span><button className="scene-reset" onClick={()=>runtime.current?.reset()} disabled={!ready||failed}>整理位置</button></div>
  </div>;
}
