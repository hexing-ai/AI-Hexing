import type {Metadata} from 'next';
import PublicCatalog from '../../components/PublicCatalog';

export const metadata:Metadata={title:'AI-Hexing · 工具封神榜',description:'查看已公开工具的介绍与 GitHub 仓库。'};
export const dynamic='force-dynamic';

export default function Page(){return <>{process.env.HEXING_QA==='1'&&<div className="qa-catalog-banner" role="note">隔离工程验收 · 目录为测试样本，不是真实推荐</div>}<PublicCatalog/></>;}
