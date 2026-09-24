import Workspace from '../../components/Workspace';
import {notFound,redirect} from 'next/navigation';
import type {Metadata} from 'next';

type Props={params:Promise<{path?:string[]}>};
const titles:Record<string,string>={discover:'发现工具',tasks:'创作任务',voices:'我的声音',ranking:'工具封神榜管理',costs:'费用核对',activity:'处理记录',validation:'接入设置'};
function valid(parts:string[]){
  const [page,kind,id]=parts;
  if(!page)return true;
  if(!Object.hasOwn(titles,page))return false;
  if(parts.length===1)return true;
  if(page==='discover')return parts.length===3&&['jobs','analyses','snapshots','search'].includes(kind)&&!!id;
  if(page==='tasks')return parts.length===2||(parts.length===4&&['generations','audio','exports'].includes(id));
  return ['voices','ranking','costs'].includes(page)&&parts.length===2;
}
export async function generateMetadata({params}:Props):Promise<Metadata>{
  const {path=[]}=await params;
  let title=titles[path[0]||'tasks']||'页面未找到';
  if(path.length>1){
    if(path[0]==='tasks')title=path[2]==='audio'?'配音结果':path[2]==='exports'?'素材包结果':path[2]==='generations'?'生成候选':'稿件与素材';
    if(path[0]==='voices')title=path[1]==='new'?'设置本人声音':'声音详情与试听';
    if(path[0]==='discover')title=path[1]==='jobs'?'资料处理进度':path[1]==='search'?'工具搜索结果':'资料复核';
    if(path[0]==='costs')title='费用明细核对';
    if(path[0]==='ranking')title='编辑榜单工具';
  }
  return {title:`${title} · AI-Hexing`,robots:{index:false,follow:false}};
}
export default async function Page({params}:Props) {
  const {path=[]}=await params;
  if(path.length===1&&path[0]==='login')redirect('/tasks');
  if(!valid(path))notFound();
  return <Workspace />;
}
