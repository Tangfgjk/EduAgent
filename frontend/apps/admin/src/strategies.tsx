import { useRef, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type MaterialInput, type PlanningMaterial } from '@wenjin/api-client';
import { Empty, ErrorNotice, Heading, Loading, Modal, StrategyDetails, strategyPhases } from '@wenjin/ui';

export function StrategiesPage() {
  const [tab, setTab] = useState<'queue' | 'materials'>('queue');
  return <><Heading kicker="PROPOSE, REVIEW, THEN ACTIVATE" title="策略评审" description="核对学习证据与课程资料，审核受限候选，再交由学生决定。"/><div className="notice">开发提案只调整建议时长与反思重点。审核通过不代表教学效果已验证，也不会替学生确认；资料不具备修改教学规则或执行代码的权限。</div><div className="tabs" role="tablist" aria-label="策略管理"><button role="tab" aria-selected={tab === 'queue'} onClick={() => setTab('queue')}>提案评审</button><button role="tab" aria-selected={tab === 'materials'} onClick={() => setTab('materials')}>课程资料</button></div>{tab === 'queue' ? <Queue/> : <Materials/>}</>;
}

function Queue() {
  const client = useQueryClient(), [offset, setOffset] = useState(0), [selected, setSelected] = useState<string>();
  const query = useQuery({ queryKey: ['admin-strategies', offset], queryFn: ({ signal }) => api.strategies(undefined, offset, signal), staleTime: 0, refetchInterval: 2000 });
  const detail = useQuery({ queryKey: ['admin-strategy', selected], queryFn: ({ signal }) => api.strategy(selected!, undefined, signal), enabled: !!selected, staleTime: 0, refetchInterval: 2000 });
  function refresh() { void client.invalidateQueries({ queryKey: ['admin-strategies'] }); void client.invalidateQueries({ queryKey: ['admin-strategy'] }); }
  return <>{query.isPending ? <Loading/> : query.isError ? <ErrorNotice error={query.error}/> : query.data.items.length ? <div className="grid two">{query.data.items.map(run => <section className="card" key={run.run_id}><h2>{run.workspace_title}</h2><p className="card-note">{run.run_id}<br/>{run.status === 'queued' ? '正在处理' : run.status === 'failed' ? '已失效' : run.status === 'cancelled' ? '已取消' : strategyPhases[run.view?.phase ?? '']}</p><button className="button" onClick={() => setSelected(run.run_id)}>查看提案与依据</button></section>)}</div> : <section className="card"><Empty title="还没有策略提案">学生选择正式项目与已发布资料后，可以提交学习体验并生成开发提案。</Empty></section>}
    <div className="pagination"><button className="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</button><button className="button" disabled={(query.data?.items.length ?? 0) < 20} onClick={() => setOffset(offset + 20)}>下一页</button></div>{selected && (detail.isPending ? <Loading/> : detail.isError ? <ErrorNotice error={detail.error}/> : <StrategyDetails key={detail.data.run_id} run={detail.data} admin refresh={refresh}/>)}</>;
}

function Materials() {
  const client = useQueryClient(), [offset, setOffset] = useState(0), [open, setOpen] = useState(false);
  const query = useQuery({ queryKey: ['admin-planning-materials', offset], queryFn: ({ signal }) => api.planningMaterials(true, offset, signal), staleTime: 0 });
  const refresh = () => { void client.invalidateQueries({ queryKey: ['admin-planning-materials'] }); };
  return <><section className="card"><h2>规划参考资料库</h2><p className="body-copy">录入自己有权使用的教材、课标、考纲摘录或自编开发参考。保留来源、版本与正文，创建后单独确认发布；修改内容请创建新版本，旧版不能覆盖。当前均未经过专业审核，不以发布操作冒充教师金标。</p><button className="button primary" onClick={() => setOpen(true)}>录入课程资料</button></section>{query.isPending ? <Loading/> : query.isError ? <ErrorNotice error={query.error}/> : <div className="grid two section-gap">{query.data.items.map(material => <MaterialCard key={material.material_id + material.revision} material={material} refresh={refresh}/>)}</div>}<div className="pagination"><button className="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页资料</button><button className="button" disabled={(query.data?.items.length ?? 0) < 20} onClick={() => setOffset(offset + 20)}>下一页资料</button></div>
    {open && <MaterialForm close={() => setOpen(false)} saved={() => { setOpen(false); setOffset(0); refresh(); }}/>}</>;
}

function MaterialCard({ material, refresh }: { material: PlanningMaterial; refresh: () => void }) {
  const [ack, setAck] = useState(false);
  const release = useMutation({ mutationFn: () => api.releasePlanningMaterial(material, !material.enabled), onSuccess: refresh, onError: refresh });
  return <section className="card strategy-material-card"><div className="card-head"><h2>{material.title}</h2><span className="badge neutral">{material.enabled ? '资料已发布' : '资料未发布'}</span></div><p className="card-note">{material.edition} · {material.kind}<br/>{material.source_ref}</p><details><summary>查看资料正文与版本</summary><pre className="strategy-json">{material.body}</pre><code className="strategy-json">{material.content_hash}</code></details><label className="field section-gap"><span><input type="checkbox" style={{ width: 'auto', marginRight: 8 }} checked={ack} onChange={e => setAck(e.target.checked)} disabled={release.isPending}/>我已核对来源与正文，理解这仅是开发参考</span></label><button className="button" disabled={!ack || release.isPending} onClick={() => release.mutate()}>{material.enabled ? '停用资料' : '发布资料'}</button><ErrorNotice error={release.error}/></section>;
}

function MaterialForm({ close, saved }: { close: () => void; saved: () => void }) {
  const [body, setBody] = useState<MaterialInput>({ title: '', kind: 'development_reference', edition: '', source_ref: '', body: '' });
  const submitted = useRef(false);
  const save = useMutation({ mutationFn: () => api.addPlanningMaterial(body), onSuccess: saved, onSettled: () => { submitted.current = false; } });
  function submit(event: FormEvent) { event.preventDefault(); if (!submitted.current) { submitted.current = true; save.mutate(); } }
  return <Modal title="录入课程资料" onClose={close} busy={save.isPending}><form onSubmit={submit}><label className="field">资料标题<input required minLength={2} maxLength={120} value={body.title} onChange={e => setBody({ ...body, title: e.target.value })}/></label><label className="field">资料类别<select aria-label="资料类别" value={body.kind} onChange={e => setBody({ ...body, kind: e.target.value as MaterialInput['kind'] })}><option value="development_reference">自编开发参考</option><option value="textbook">教材摘录</option><option value="curriculum">课标摘录</option><option value="exam">考纲摘录</option></select></label><label className="field">资料版本<input required maxLength={80} value={body.edition} onChange={e => setBody({ ...body, edition: e.target.value })}/></label><label className="field">资料来源<input required minLength={2} maxLength={300} value={body.source_ref} onChange={e => setBody({ ...body, source_ref: e.target.value })} placeholder="书目与页码、官方资料标识，或自编来源说明"/></label><label className="field">参考正文<textarea required minLength={2} maxLength={6000} rows={7} value={body.body} onChange={e => setBody({ ...body, body: e.target.value })}/></label><ErrorNotice error={save.error}/><div className="dialog-footer"><button className="button" type="button" disabled={save.isPending} onClick={close}>取消</button><button className="button primary" disabled={save.isPending}>保存资料版本</button></div></form></Modal>;
}
