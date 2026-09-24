export type PlatformReport={status:string;version?:string|null;policy_hash?:string|null;reviewed_at?:string|null;verified_at?:string|null;review_note?:string;notice?:string;result?:string;count_note?:string;checks?:{field:string;label:string;count:number;unit:string;maximum:number|null;status:string;result:string;verified_at?:string|null;valid_until?:string|null}[];sources?:{id:string;url:string;title:string;surface:string;available:boolean;excerpt:string}[]};
export const platformStatus:Record<string,string>={unverified:'未核实',partial:'部分规则已核实',verified:'规则已核实',expired:'规则已过期',config_error:'配置暂不可用',not_applicable:'不适用'};
const units:Record<string,string>={unicode_codepoints:'Unicode 码点',utf16_units:'UTF-16 单元',items:'个'};
export default function PlatformCheck({report,savedHash}:{report:PlatformReport;savedHash?:string|null}){
  return <details className="platform-check"><summary>抖音发布检查 · {platformStatus[report.status]||'待核对'}</summary><section aria-label="抖音发布检查">
    <p>适用范围：手动复制发布。最近核查：{report.reviewed_at?new Date(report.reviewed_at).toLocaleDateString('zh-CN'):'未取得'}；最近有效规则核验：{report.verified_at?new Date(report.verified_at).toLocaleDateString('zh-CN'):'未核实'}。</p>
    <p>{report.review_note}</p>{report.result==='exceeded'&&<p className="notice">已超出部分已核实限制。原稿已保留，请修改或在发布端再次核对。</p>}
    {report.checks?.map(c=><p key={c.field}>{c.label}：{c.count} {units[c.unit]||c.unit}；上限 {c.maximum===null?'未核实':c.maximum} · {c.result==='exceeded'?'超出限制':c.result==='within_limit'?'未超出此项已知限制':platformStatus[c.status]||'未检查'}{c.valid_until?`（规则有效至 ${new Date(c.valid_until).toLocaleDateString('zh-CN')}）`:''}</p>)}
    <small>{report.count_note}</small><p>{report.notice}</p>
    {report.policy_hash&&savedHash!==report.policy_hash&&<p>这里按当前配置重新检查；{savedHash?'保存时使用的是其他配置':'此历史稿件没有完整规则快照'}，原稿与确认记录保留。</p>}
    <p>配置版本：{report.version||'未取得'}</p>
    {report.sources?.map(s=><p key={s.id}><a href={s.url} target="_blank" rel="noreferrer">{s.title} ↗</a>（{s.surface==='manual_copy'?'手动发布入口':'开放 API 参考'}；{s.available?'规则来源可查':'本次未取得适用规则'}）<br/>{s.excerpt}</p>)}
  </section></details>;
}
