'use client';
import Link from 'next/link';
export default function ErrorPage({retry}:{retry:()=>void}){
  return <main className="recovery-screen"><section><span className="brand">AI-Hexing</span><h1>这个页面暂时无法显示</h1><p role="alert">请重新打开页面。已保存的稿件、音频和处理记录仍可回查。</p><p>如果刚提交过生成，请先查看处理记录，核对是否已受理。</p><div className="actions"><button onClick={retry}>重新打开页面</button><Link className="button" href="/activity">查看处理记录</Link></div></section></main>;
}
