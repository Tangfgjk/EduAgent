import { useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api, type StrategyRun } from '@wenjin/api-client';
import { ErrorNotice } from './index';

export const strategyPhases: Record<string, string> = { admin_review: '等待管理员审核', student_confirm: '等待学生确认', approved: '已确认并激活', rejected: '管理员已退回', declined: '学生未采纳' };

export function StrategyDetails({ run, admin = false, refresh }: { run: StrategyRun; admin?: boolean; refresh: () => void }) {
  const [note, setNote] = useState(''), intent = useRef({ signature: '', key: '' });
  const mutation = useMutation({ mutationFn: (action: 'approve' | 'reject' | 'confirm' | 'decline' | 'cancel') => {
    if (action === 'cancel') return api.cancelStrategy(run);
    const signature = JSON.stringify([run.revision, run.gate_id, action, note.trim()]);
    if (intent.current.signature !== signature) intent.current = { signature, key: crypto.randomUUID() };
    return api.decideStrategy(run, action, note.trim(), intent.current.key, admin);
  }, onSuccess: () => { setNote(''); refresh(); }, onError: refresh });
  const phase = run.view?.phase, canDecide = run.workspace_status === 'active' && run.status === 'awaiting_input' && phase === (admin ? 'admin_review' : 'student_confirm');
  return <section className="card section-gap strategy-detail"><div className="card-head"><h2>{run.workspace_title} · 策略提案</h2><span className="badge">{run.status === 'queued' ? '正在处理' : run.status === 'failed' ? '提案失效' : run.status === 'cancelled' ? '已取消' : strategyPhases[phase ?? ''] ?? '等待生成'}</span></div><p className="card-note">版本标识：{run.run_id}<br/>版本校验：<code style={{ overflowWrap: 'anywhere' }}>{run.version_hash ?? '待生成'}</code></p>
    {run.workspace_status === 'archived' && <p className="notice">项目已归档，只能查阅；恢复后才能审核、确认或取消。</p>}
    {run.error_code && <div className="notice">此提案未能继续（{run.error_code}）。请核对来源与当前基线后重新生成；没有激活此候选。</div>}
    {run.view && <><div className="soft-box section-gap"><strong>每日建议 {run.view.policy.daily_minutes} 分钟</strong><p>反思引导：{run.view.policy.reflection_focus === 'verification' ? '代入核验与检查' : '依据与方法回望'}</p><p>只影响后续学习；不会改写已有作答、已确认计划、提示阶梯或检测规则。</p></div><h3 className="section-gap">已执行的结构检查</h3><ul className="detail-list">{run.view.checks.map(check => <li key={check}>{check}</li>)}</ul><p className="card-note">检查不代表教学效果已验证；本轮是固定开发提案，未调用真实模型。</p></>}
    {run.bundle && <section className="section-gap"><h3>本次调整的依据</h3><span className="badge neutral">{{ synthetic: '模拟数据', adult_test: '成人测试', real_student: '真实学生' }[run.bundle.source_kind] ?? run.bundle.source_kind} · 仅本项目</span><h4>学生本次自述</h4><blockquote className="strategy-experience">{run.bundle.student_experience}</blockquote><p className="card-note">已冻结 {run.bundle.evidence.counts.facts ?? 0} 条学习事实、{run.bundle.evidence.counts.self_reports ?? 0} 条来源自述、{run.bundle.evidence.counts.judgments ?? 0} 条人工判断。原文切片不等于对学生能力的评价。</p>
      {run.bundle.materials.map(material => <details className="section-gap" key={material.material_id}><summary>{material.title} · {material.edition}</summary><p className="card-note">来源：{material.source_ref} · 未经专业审核</p><pre className="strategy-json">{material.body}</pre></details>)}
      <details className="section-gap"><summary>查看冻结的学习记录切片</summary>{run.bundle.evidence.facts.length ? <ul className="detail-list">{run.bundle.evidence.facts.map((fact, index) => <li key={index}>{String(fact.task_id)} · {String(fact.source_kind)} · {fact.status ? String(fact.status) : '过程记录'}<p>{String(fact.reasoning || fact.reflection || fact.answer || '无额外文字')}</p></li>)}</ul> : <p className="card-note">当前没有学习事实，本次仅保留学生体验与所选参考，不推断学习能力。</p>}<p className="card-note">{run.bundle.evidence.limitation} 推理与反思文本每条最多展示600字；不足以评审时应退回提案。</p><details><summary>查看完整版本与引用明细</summary><pre className="strategy-json">{JSON.stringify(run.bundle, null, 2)}</pre></details></details>
    </section>}
    {run.skill_markdown && <details className="section-gap"><summary>查看 Skill.md 与 Harness 候选</summary><pre className="strategy-json">{run.skill_markdown}</pre><pre className="strategy-json">{JSON.stringify(run.view?.policy, null, 2)}</pre>{!admin && <a className="button small" href={`/api/v1/student/workspaces/${run.workspace_id}/strategies/${run.run_id}/skill`}>下载此版本 Skill.md</a>}</details>}
    {!!run.receipts.length && <><h3 className="section-gap">确认与退回记录</h3><ul className="detail-list">{run.receipts.map((row, index) => <li key={index}>{{ approve: '管理员通过', reject: '管理员退回', confirm: '学生确认', decline: '学生未采纳' }[String(row.action)] ?? String(row.action)}：{String(row.note)}</li>)}</ul></>}
    {run.expires_at && <p className="card-note">提案确认期限：{new Date(run.expires_at * 1000).toLocaleString()}。确认前新增学习事实、自述、人工判断，资料发布状态或生效基线变化，均需重新生成。</p>}
    {canDecide && <div className="section-gap"><label className="field">{admin ? '评审依据' : '我的确认或拒绝理由'}<textarea value={note} onChange={e => setNote(e.target.value)} minLength={2} maxLength={1000} disabled={mutation.isPending}/></label><div className="actions"><button className="button" disabled={mutation.isPending || note.trim().length < 2} onClick={() => mutation.mutate(admin ? 'reject' : 'decline')}>{admin ? '退回提案' : '暂不采纳'}</button><button className="button primary" disabled={mutation.isPending || note.trim().length < 2} onClick={() => mutation.mutate(admin ? 'approve' : 'confirm')}>{admin ? '审核通过，交学生确认' : '我确认，激活此策略'}</button></div></div>}
    {!admin && run.workspace_status === 'active' && ['queued', 'awaiting_input'].includes(run.status) && <button className="button section-gap" disabled={mutation.isPending} onClick={() => mutation.mutate('cancel')}>取消此提案</button>}
    <ErrorNotice error={mutation.error}/>
  </section>;
}
