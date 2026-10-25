export function SkillExecution({snapshot}:{snapshot?:Record<string,unknown>|null}) {
  if (!snapshot) return null;
  const plan=snapshot.plan_execution as Record<string,unknown>|undefined;
  const execution = snapshot.execution as Record<string, unknown> | undefined;
  if (!execution) return null;
  return <div className="soft-box section-gap"><p>练习安排：{execution.practice_order==='errors_first'?'优先复练本项目错题':'按课程顺序练习'}。</p><p>学习支持：{execution.support_mode==='adaptive'?'先独立尝试，需要时恢复帮助':'逐步提供提示'}。</p><p>{plan?.status==='applied_priority'?'已优先安排本次确认计划中的练习，其余练习与检测保留。':'没有同版本的已确认任务排序，沿用当前策略安排。'}</p><p className="card-note">本轮沿用开始时的学习安排，后续调整从新任务生效。</p></div>;
}
