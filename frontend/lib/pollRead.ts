/** Lifecycle-scoped GET scheduler. Mutation callbacks must never be supplied. */
export function pollRead<T>(read:()=>Promise<T>,receive:(value:T)=>void,fail:(error:Error)=>void,interval=3000) {
  let alive=true,inFlight=false,failures=0;
  let timer:ReturnType<typeof setTimeout>;
  const tick=async()=>{
    clearTimeout(timer);
    if(!alive||document.hidden||inFlight)return;
    inFlight=true;
    try{const value=await read();if(alive){failures=0;receive(value);}}
    catch(e){if(alive){failures++;fail(e as Error);}}
    finally{inFlight=false;if(alive&&!document.hidden)timer=setTimeout(tick,Math.min(30000,interval*2**Math.min(4,failures)));}
  };
  const visible=()=>{clearTimeout(timer);if(!document.hidden)void tick();};
  void tick();document.addEventListener('visibilitychange',visible);window.addEventListener('online',visible);
  return()=>{alive=false;clearTimeout(timer);document.removeEventListener('visibilitychange',visible);window.removeEventListener('online',visible);};
}
