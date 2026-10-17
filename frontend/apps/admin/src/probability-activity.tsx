import { ContentDevelopmentReview } from './development-review';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type ActivityContentInfo } from '@wenjin/api-client';
import { ErrorNotice, Loading, Modal } from '@wenjin/ui';

export function ProbabilityActivityPanel() {
  const [selected, setSelected] = useState<ActivityContentInfo>();
  const query = useQuery({ queryKey: ['admin-activity-catalog'], queryFn: ({ signal }) => api.activityCatalog(true, signal) });
  return <section className="section-gap"><h2>新增单元互动审核</h2>{query.isPending ? <Loading/> : query.isError ? <ErrorNotice error={query.error} retry={() => { void query.refetch(); }}/> : query.data.items.filter(item => item.unit_id !== 'math.g7.equations.activities.dev1').map(item => <section className="card section-gap" key={item.unit_id}><div className="card-head"><h3>{item.title}</h3><span className="badge">{item.enabled ? '互动已启用' : '互动未启用'}</span></div><p className="card-note">陪学与互动分别启停。核对题目、预设观点及误区；仅供模拟和成人测试，不代表专业教学审核。</p><button className="button" onClick={() => setSelected(item)}>审核{item.title}</button></section>)}
    {selected && <Review key={selected.unit_id + ':' + selected.revision} item={selected} close={() => setSelected(undefined)}/>}
  </section>;
}

function Review({ item, close }: { item: ActivityContentInfo; close: () => void }) {
  const [ack, setAck] = useState(false), client = useQueryClient();
  const save = useMutation({ mutationFn: () => api.releaseActivityPack(item, !item.enabled), onSuccess: () => { void client.invalidateQueries({ queryKey: ['admin-activity-catalog'] }); close(); }, onError: () => { void client.invalidateQueries({ queryKey: ['admin-activity-catalog'] }); } });
  return <Modal title={'审核：' + item.title} onClose={close} busy={save.isPending}><p className="dialog-copy">预设角色仍为固定模板；概率新流程另可请求已开放模型根据本次讲解追问。模型生成内容须核验，讲解质量和素养尚未评定。</p><p className="card-note" style={{ overflowWrap: 'anywhere' }}>互动版本：{item.content_version}</p>{(item.tasks ?? []).map(task => <article key={String(task.id)}><h3>{String(task.id)}</h3><p>{String(task.stem)}</p><p>参考数值：{String(task.answer)}</p><p className="card-note" style={{ overflowWrap: 'anywhere' }}>基础内容版本：{String(task.base_content_version)}</p>{task.verification_task != null && <p>另一题无辅助验证（管理员可见）：{JSON.stringify(task.verification_task)}</p>}<pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(task.activity_templates, null, 2)}</pre></article>)}
    <ContentDevelopmentReview target={item.unit_id} version={item.content_version}/>
    <label className="field section-gap"><span><input type="checkbox" checked={ack} disabled={save.isPending} onChange={e => setAck(e.target.checked)} style={{ width: 'auto', marginRight: 8 }}/>我已核对该版本的题目与模板，理解它仅供开发测试</span></label><ErrorNotice error={save.error}/><div className="dialog-footer"><button className="button" disabled={save.isPending} onClick={close}>关闭</button><button className="button primary" disabled={!ack || save.isPending} onClick={() => save.mutate()}>{item.enabled ? '停用此互动包' : '启用此互动包'}</button></div>
  </Modal>;
}
