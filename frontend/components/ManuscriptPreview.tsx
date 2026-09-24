type Props = {
  kind: 'script' | 'copy';
  content: Record<string, unknown> | null;
  onCopy: (text: string, label: string) => void;
};
const sections = [
  ['pain', '痛点'], ['intro', '栏目句'], ['feature', '核心亮点'], ['example', '使用示例'],
] as const;
const textValue = (value: unknown) => typeof value === 'string' ? value : '';

/** Display only known text fields; preserve the original payload in the version's details. */
export default function ManuscriptPreview({ kind, content, onCopy }: Props) {
  if (!content) return <p className="manuscript-missing">这份结果尚无可读正文，请查看问题提示与生成原文。</p>;
  if (kind === 'script') return <div className="manuscript-preview" aria-label="四段口播正文">
    {sections.map(([key, label]) => <section className="manuscript-section" key={key}>
      <h5>{label}</h5>
      <p className={textValue(content[key]) ? '' : 'manuscript-missing'}>{textValue(content[key]) || '此段内容缺失或格式异常，请编辑补齐。'}</p>
    </section>)}
  </div>;
  const title = textValue(content.title);
  const body = textValue(content.body);
  const tags = Array.isArray(content.tags) ? content.tags.filter((tag): tag is string => typeof tag === 'string') : [];
  return <div className="manuscript-preview" aria-label="发布文案正文">
    <section className="manuscript-section">
      <div className="manuscript-field-heading"><h5>标题</h5><button disabled={!title} onClick={() => onCopy(title, '标题')}>复制标题</button></div>
      <p>{title || '未填写标题'}</p>
    </section>
    <section className="manuscript-section">
      <h5>发布正文</h5><p className={body ? '' : 'manuscript-missing'}>{body || '正文缺失或格式异常，请编辑补齐。'}</p>
    </section>
    <section className="manuscript-section">
      <div className="manuscript-field-heading"><h5>话题</h5><button disabled={!tags.length} onClick={() => onCopy(tags.map(tag => '#' + tag).join(' '), '话题')}>复制话题</button></div>
      {tags.length ? <ul className="manuscript-tags" aria-label="发布话题">{tags.map((tag, index) => <li key={index}>#{tag}</li>)}</ul> : <p className="manuscript-missing">暂无可用话题</p>}
    </section>
  </div>;
}
