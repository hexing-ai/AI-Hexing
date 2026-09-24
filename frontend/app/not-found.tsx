import Link from 'next/link';
export default function NotFound(){
  return <main className="recovery-screen"><section><span className="brand">AI-Hexing</span><small>404</small><h1>这个页面不存在</h1><p>请检查网址，或返回工作台继续创作。</p><div className="actions"><Link className="button" href="/tasks">返回创作任务</Link><Link className="button" href="/display">浏览工具封神榜</Link></div></section></main>;
}
