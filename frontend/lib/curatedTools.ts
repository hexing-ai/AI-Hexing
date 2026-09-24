import entries from './curated-tools.json';
import {colorOptions,shapeOptions} from './botAppearance';
import type {PublicTool} from './public-api';

// User-supplied collection, in Markdown order. This does not publish API entries.
export const curatedTools:PublicTool[]=entries.map((entry,i)=>({
  id:'curated-'+entry.github_url.slice('https://github.com/'.length).toLowerCase(),
  version:1,first_published_at:null,name:entry.name,
  bot_avatar:{preset:shapeOptions[(i*5+3)%shapeOptions.length].value,palette:colorOptions[(i*7+2)%colorOptions.length].value},
  highlights:entry.highlights,github_url:entry.github_url,
  last_verified_at:null,verification_status:'unknown',category:null,one_line_use:entry.description,
}));

const repositoryKey=(url:string)=>url.trim().replace(/\/+$/,'').replace(/\.git$/i,'').toLowerCase();

// Keep the collection visible when workspace entries are added. An editable
// workspace entry for the same repository takes its place without duplication.
export function workspaceCollection(tools:PublicTool[]):PublicTool[]{
  const replacements=new Map(tools.filter(t=>t.github_url).map(t=>[repositoryKey(t.github_url),t]));
  const known=new Set(curatedTools.map(t=>repositoryKey(t.github_url)));
  return [...curatedTools.map(t=>replacements.get(repositoryKey(t.github_url))||t),...tools.filter(t=>!known.has(repositoryKey(t.github_url)))];
}
