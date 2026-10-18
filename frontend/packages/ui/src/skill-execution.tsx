export function SkillExecution({snapshot}:{snapshot?:Record<string,unknown>|null}) {
  if (!snapshot) return null;
  return <details className="soft-box section-gap"><summary>本任务冻结的执行策略</summary><p className="card-note">版本：{String(snapshot.strategy_version_id)}<br/>策略指纹：<code style={{overflowWrap:'anywhere'}}>{String(snapshot.policy_hash)}</code></p><p>本任务沿用开始时的配置；后续回滚影响新任务。</p><pre className="strategy-json">{JSON.stringify(snapshot,null,2)}</pre></details>;
}
