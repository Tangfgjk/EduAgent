import { Link } from 'react-router-dom';
import type { TutorRun, TutorView } from '@wenjin/api-client';

type Trace = NonNullable<TutorView['teaching_history']>[number];
const outcomes: Record<string, string> = { passed: '数值通过', failed: '数值未通过', unverifiable: '尚不能验证', skipped: '未完成' };

function executionText(item: Trace) {
  const execution = item.execution;
  const route = execution.next_stage === 'checkpoint' ? '已进入无辅助检测，保持独立作答。'
    : execution.next_stage === 'summary' ? '已进入学习小结。'
    : execution.status === 'variant_added' ? '已加入经过数值核验的变式，原有练习和检测保留。'
    : '已安排下一道未完成练习。';
  const support = !execution.support_applied ? '' : item.support === 'restore' ? ' 保留具体帮助，可按需求助。'
    : item.support === 'fade' ? ' 先独立尝试，仍可请求帮助。' : ' 继续提供现有支持。';
  return route + support;
}

function Result({ item, status }: { item: Trace; status: TutorRun['status'] }) {
  return <p className="card-note">{item.outcome
    ? `后续记录：${outcomes[item.outcome.status]}，尝试 ${item.outcome.attempts} 次，使用提示 ${item.outcome.hints_used} 次。${item.outcome.reasoning_status === 'contradiction_found' ? '理由仍有算术矛盾。' : '完整推理尚未评阅。'}`
    : status === 'cancelled' || status === 'failed' ? '这次流程已停止，尚未获得后续完成记录。' : '后续表现：尚未获得完成记录。'}</p>;
}

export function TeachingTrace({ run }: { run: TutorRun }) {
  const history = run.view?.teaching_history ?? [];
  const latest = history.at(-1);
  if (!latest) return null;
  return <section className="soft-box section-gap" aria-label="教学调整与反馈">
    <h3>为什么安排这一步</h3>
    <p>{latest.support_reason ?? latest.reason}</p>
    <p>{executionText(latest)}</p>
    {latest.execution.status === 'awaiting_student_open' && <p>另有阅读建议，尚未记录打开或阅读结果。{run.view?.phase === 'practice' && <Link to={{ pathname: '/courses', search: '?workspace=' + run.workspace_id }}>查看课程资料</Link>}</p>}
    <Result item={latest} status={run.status}/>
    <details><summary>查看本轮调整记录（{history.length}）</summary>
      <ol>{history.map(item => <li key={item.sequence}>
        <p><strong>{item.source === 'model' ? '模型建议，经执行规则检查' : '依据学习记录的本地调整'}</strong></p>
        <p>{item.reason}</p>{item.support_reason && item.support_reason !== item.reason && <p>{item.support_reason}</p>}
        <p>{executionText(item)}</p><Result item={item} status={run.status}/>
      </li>)}</ol>
      <p className="card-note">记录说明安排是否执行、后续如何作答，不据此认定已掌握或教学效果提升。</p>
    </details>
  </section>;
}
