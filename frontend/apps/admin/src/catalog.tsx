import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type CatalogUnit } from '@wenjin/api-client';
import { ErrorNotice, Loading, Modal } from '@wenjin/ui';

export function CatalogPanel() {
  const [selected, setSelected] = useState<CatalogUnit>();
  const query = useQuery({ queryKey: ['admin-catalog'], queryFn: ({ signal }) => api.contentCatalog(true, signal) });
  return <section className="section-gap"><h2>跨领域开发内容包</h2>
    {query.isPending ? <Loading/> : query.isError ? <ErrorNotice error={query.error} retry={() => { void query.refetch(); }}/> : <>
      <div className="notice section-gap">{query.data.coverage_note}</div>
      <div className="grid two section-gap">{query.data.items.filter(item => item.unit_id !== 'math.g7.equations.dev1').map(item => <section className="card align-start" key={item.unit_id}><div className="card-head"><h3>{item.title}</h3><span className="badge">{item.enabled ? '开发已启用' : '未启用'}</span></div><p className="card-note">{item.grade} · {item.domain} · {item.task_count} 题</p><p className="body-copy">{item.knowledge_points.join(' · ')}</p><button className="button" onClick={() => setSelected(item)}>核对{item.title}</button></section>)}</div>
    </>}
    {selected && <CatalogReview key={selected.unit_id + ':' + selected.revision} item={selected} close={() => setSelected(undefined)}/>}
  </section>;
}

function CatalogReview({ item, close }: { item: CatalogUnit; close: () => void }) {
  const [ack, setAck] = useState(false), client = useQueryClient();
  const mutation = useMutation({ mutationFn: () => api.releaseCatalogUnit(item, !item.enabled), onSuccess: () => { void client.invalidateQueries({ queryKey: ['admin-catalog'] }); close(); }, onError: () => { void client.invalidateQueries({ queryKey: ['admin-catalog'] }); } });
  return <Modal title={'核对：' + item.title} onClose={close} busy={mutation.isPending}><p className="dialog-copy">仅供模拟与成人测试。启用不代表专业教师审核或课程覆盖完成。</p><p className="card-note" style={{ overflowWrap: 'anywhere' }}>版本：{item.content_version}</p><p className="card-note">{item.boundary}</p>
    {(item.tasks ?? []).map(task => <article className="section-gap" key={String(task.id)}><h3>{String(task.id)} · {String(task.stage)}</h3><p>{String(task.stem)}</p><p>参考数值：{String(task.answer)}</p><p>常见误区：{String(task.misconception || '待专业审核补充')}</p>{Array.isArray(task.hints) && <ol>{task.hints.map((hint, i) => <li key={i}>L{i}：{String(hint)}</li>)}</ol>}</article>)}
    <label className="field section-gap"><span><input type="checkbox" checked={ack} disabled={mutation.isPending} onChange={e => setAck(e.target.checked)} style={{ width: 'auto', marginRight: 8 }}/>我已核对该版本，理解它仅供开发测试</span></label><ErrorNotice error={mutation.error}/><div className="dialog-footer"><button className="button" disabled={mutation.isPending} onClick={close}>关闭</button><button className="button primary" disabled={!ack || mutation.isPending} onClick={() => mutation.mutate()}>{item.enabled ? '停用此内容包' : '启用此内容包'}</button></div>
  </Modal>;
}
