// One 24 fps clock for visible avatars. No animation loop remains after unmount.
type Tick=(time:number)=>void;
const listeners=new Set<Tick>();let frame=0,last=0;
function tick(now:number){
  frame=0;
  if(document.hidden||!listeners.size)return;
  if(now-last>=1000/24){last=now;for(const draw of listeners)draw(now/1000);}
  frame=requestAnimationFrame(tick);
}
function visibility(){cancelAnimationFrame(frame);frame=0;last=0;if(!document.hidden&&listeners.size)frame=requestAnimationFrame(tick);}
export function animateBot(draw:Tick){
  if(!listeners.size)document.addEventListener('visibilitychange',visibility);
  listeners.add(draw);if(!frame&&!document.hidden)frame=requestAnimationFrame(tick);
  return()=>{listeners.delete(draw);if(!listeners.size){cancelAnimationFrame(frame);frame=0;last=0;document.removeEventListener('visibilitychange',visibility);}};
}
