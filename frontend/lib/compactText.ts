/** A local reading preview; the original text is always kept for details. */
export function compactText(value:string|undefined,limit=80,fallback='仓库未提供用途说明，请核对来源。'){
  const text=(value||'').replace(/\s+/gu,' ').trim();
  if(!text)return fallback;
  const sentences=new Intl.Segmenter(undefined,{granularity:'sentence'}).segment(text);
  let preview='',count=0;
  for(const {segment} of sentences){
    preview+=segment;count++;
    if(count>=2||Array.from(preview).length>=Math.min(50,limit))break;
  }
  const chars=Array.from(preview.trim());
  return chars.length>limit?chars.slice(0,limit-1).join('').trimEnd()+'…':preview.trim();
}
