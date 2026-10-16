import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '@wenjin/api-client';
import { ErrorNotice, Heading, Loading } from '@wenjin/ui';

export function Units({ search }: { search: string }) {
  const [grade, setGrade] = useState('全部'), [domain, setDomain] = useState('全部');
  const query = useQuery({ queryKey: ['student-catalog'], queryFn: ({ signal }) => api.contentCatalog(false, signal) });
  return <><Heading kicker="A MAP OF MATHEMATICS" title="数学单元" description="从代表性开发单元开始，把学习过程留在当前空间。"/>
    {query.isPending ? <Loading/> : query.isError ? <ErrorNotice error={query.error} retry={() => { void query.refetch(); }}/> : <>
      <div className="notice">{query.data.coverage_note}</div>
      <div className="filters" aria-label="年级筛选">{['全部', '七年级', '八年级', '九年级'].map(label => <button key={label} className={grade === label ? 'active' : ''} onClick={() => setGrade(label)}>{label}</button>)}</div>
      <label className="field">领域筛选<select value={domain} onChange={e => setDomain(e.target.value)}>{['全部', '数与式', '函数', '几何', '统计概率'].map(value => <option key={value}>{value}</option>)}</select></label>
      <div className="grid three section-gap">{query.data.items.filter(unit => (grade === '全部' || unit.grade === grade) && (domain === '全部' || unit.domain === domain)).map(unit => {
        const params = new URLSearchParams(search); params.set('unit', unit.unit_id);
        return <section className="card align-start" key={unit.unit_id}><div className="card-head"><span className="badge neutral">{unit.grade} · {unit.domain}</span><span className="badge">{unit.enabled ? '开发可体验' : '待管理员启用'}</span></div><h2>{unit.title}</h2><p className="body-copy">{unit.knowledge_points.join(' · ')}</p><p className="card-note">{unit.task_count} 题 · 诊断 → 练习与反思 → 独立检测</p><p className="card-note">先修参考：{unit.prerequisites.map(id => query.data.items.find(item => item.unit_id === id)?.title ?? id).join('、') || '基础数值表达'}。仅供参考，不自动判定掌握。</p><p className="card-note">{unit.boundary}</p><Link className="button section-gap" to={{ pathname: '/study', search: '?' + params }}>查看与学习</Link></section>;
      })}</div>
    </>}
  </>;
}
