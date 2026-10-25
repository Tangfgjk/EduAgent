import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { LearningInsights, ObservationRef } from '@wenjin/api-client';
import type { Trace } from './review';

const sourceNames: Record<string,string> = { synthetic:'模拟数据',adult_test:'成人测试',real_student:'真实学生' };
const supportNames: Record<string,string> = { step_support:'逐级支持',socratic_first:'先自我提问',restored_step_support:'已恢复具体支持',unrecorded:'支架决定未记录' };
type Selection = { label: string; refs: ObservationRef[]; total: number };

export function LearningAnalysis({ report, trace, workspaceId, view, panel }: { report: LearningInsights; trace: (value: Trace) => void; workspaceId?: string; view: string; panel: string }) {
  const [selection,setSelection]=useState<Selection>(), [dimension,setDimension]=useState('数学抽象');
  const maxSubmissions = Math.max(1,...report.measures.map(d=>d.submissions));
  const max = Math.max(10,...report.competencies.map(d=>d.candidate_count));
  const selected=report.competencies.find(d=>d.name===dimension)!;
  const title=(id:string)=>report.knowledge_nodes.find(n=>n.kc_id===id)?.knowledge_points[0] ?? id;
  function choose(label:string,refs:ObservationRef[],total=refs.length){setSelection({label,refs,total});}
  return <>
    {view==='knowledge' && panel==='map' && <section className="card section-gap"><div className="card-head"><h2>知识点地图 · 记录覆盖</h2><span className="badge neutral">不表示掌握度</span></div>
      <p className="card-note">开发知识点按内容版本、来源分别展示；单元标签不证明每道题覆盖了全部知识点。空白表示没有此范围的记录。</p>
      <div className="knowledge-map">{report.knowledge_nodes.map(node=><button className={`knowledge-node ${node.facts?'has-evidence':''}`} key={node.node_id} onClick={()=>choose(node.unit_title,node.refs,node.reference_count)} aria-label={`${node.unit_title}，${node.facts}条事实，${node.task_count}道题`}>
        <span className="badge neutral">{node.mapping_status==='unmapped'?'未映射':node.domain}</span><strong>{node.unit_title}</strong><span>{node.facts?`${node.facts} 条记录 · ${node.task_count} 道不同题`:'暂无记录'}</span>
        <small>{node.source_kind?sourceNames[node.source_kind]??node.source_kind:'开发知识点'}{node.content_version?` · 版本 ${node.content_version.slice(0,8)}`:''}</small>
        <small>{Object.entries(node.measures).filter(([key])=>key.startsWith('assessment')).map(([key,value])=>`${key==='assessment_first'?'首次曝光':key==='assessment_repeat'?'已见题':'曝光未知'} ${value}`).join(' · ')}</small>
      </button>)}</div>
      <details className="section-gap"><summary>查看开发先修关系</summary><ul>{report.prerequisite_edges.map(edge=><li key={edge.from_id+edge.to_id}>{title(edge.from_id)} → {title(edge.to_id)}</li>)}</ul><p>这是开发映射，不是正式课标达标图；完成一个节点不自动解锁或判定另一个节点。</p></details>
    </section>}
    {view==='knowledge' && panel==='performance' && <section className="card section-gap"><h2>学习表现 · 按辅助和曝光分开</h2><p className="card-note">浅色条长度显示提交次数，深色部分显示其中的数值通过数，不比较不同题目/版本的学习增益。到期与提前均是原题复做，不能当作未见题迁移。</p>
      {report.measures.length?<div className="observation-bars">{report.measures.map(row=><button key={row.source_kind+row.metric} className="observation-bar" onClick={()=>choose(`${row.label} · ${sourceNames[row.source_kind]??row.source_kind}`,row.refs,row.submissions)}>
        <span>{row.label}<small>{sourceNames[row.source_kind]??row.source_kind}</small></span><span className="observation-track" style={{width:`${100*row.submissions/maxSubmissions}%`}}><span style={{width:`${100*row.passed/Math.max(1,row.submissions)}%`}}/></span><strong>{row.passed} / {row.submissions}</strong><small>通过 / 提交；未通过 {row.failed}，不可验证 {row.unverifiable}</small>
      </button>)}</div>:<p>暂无可分类数值提交；不会显示为能力零分。</p>}
    </section>}
    {view==='support' && panel==='review' && <section className="card section-gap"><h2>复习矩阵 · 触发原因与到期状态</h2><p className="card-note">截至当前的队列卡片，点击格子可看原始触发事实；复做次数受所选时间窗口限制。同一卡片可有多个原因，各行不能相加成总题数。这里没有推测学生的具体语义错因。</p>
      {(report.review_matrix??[]).length?<div className="evidence-table-wrap"><table className="evidence-table"><caption>卡片计数与观察来源，非能力评分</caption><thead><tr><th>来源与原因</th><th>已到期</th><th>待到期</th><th>窗口内原题复做</th></tr></thead><tbody>{(report.review_matrix??[]).map(row=><tr key={row.source_kind+row.reason}><th>{sourceNames[row.source_kind]} · {{numeric_error:'数值未通过',assisted_success:'辅助后通过',human_review:'人工复核触发',unclassified:'未分类'}[row.reason]??row.reason}</th><td><button className="button small" onClick={()=>choose('到期复习来源',row.refs,row.cards)}>{row.due} 张</button></td><td>{row.scheduled} 张</td><td>{row.window_recalls} 次</td></tr>)}</tbody></table></div>:<p>当前范围没有复习卡片；这表示暂无此类队列依据，不代表没有学习困难。</p>}{workspaceId&&<Link className="button section-gap" to={`/review?workspace=${workspaceId}`}>去当前项目复习</Link>}
    </section>}
    {view==='support' && panel==='help' && <section className="card section-gap"><h2>支架时间线</h2><p className="card-note">仅陈列实际记录的支持决定和求助次数；减少帮助不等于进步。显示最近50条 / 共{report.support_event_count}条，可逐条回到原始尝试。</p>
      {report.support_events.length?<ol className="support-timeline">{report.support_events.map(event=><li key={event.fact_id}><button className="button small" onClick={()=>trace({wid:event.workspace_id,fid:event.fact_id,archived:event.archived})}>{supportNames[event.mode]??event.mode} · {event.event==='hint'?'请求帮助':event.event==='answer'?'提交作答':'保存反思'}</button><p>{event.task_id} · 提示 {event.hints_used} 次 · {sourceNames[event.source_kind]??event.source_kind} · {new Date(event.created_at*1000).toLocaleString()}</p><small>{event.basis}{event.restorable?'；仍可请求恢复支持':''}</small></li>)}</ol>:<p>暂无支架过程；旧记录未保存决定时保留“未记录”。</p>}
    </section>}
    {view==='competency' && <section className="card section-gap"><div className="card-head"><h2>数学素养 · 六个观察方向</h2><span className="badge neutral">尚无评分</span></div><p className="card-note">图形是待评阅候选材料数量，维度间可重叠；归档依据是单元标签与提交字段，不是模型理解或正式量规评分。0条材料不代表能力为0。</p>
      <div className="competency-bars" role="group" aria-label="六个观察方向候选材料条数">{report.competencies.map(item=><button key={item.name} aria-pressed={dimension===item.name} onClick={()=>{setDimension(item.name);choose(`${item.name} · 候选材料`,item.refs,item.candidate_count);}}><strong>{item.name}</strong><span className="candidate-track"><span style={{width:`${item.candidate_count/max*100}%`}}/></span><span>{item.candidate_count} 条候选</span></button>)}</div>
      <div className="soft-box section-gap" role="status"><strong>{dimension} · 未评定</strong><p>{selected.routing_rule}</p><p>专业评阅：尚未进行；能力分数：未知。</p></div>
    </section>}
    {selection&&<section className="card section-gap" aria-label="图形来源钻取"><div className="card-head"><h2>{selection.label} · 来源</h2><button className="button small" onClick={()=>setSelection(undefined)}>收起图形来源</button></div><p>共{selection.total}条；展示最近最多8条来源索引，完整过程可到“学习记录”分类按日期查询。</p>{selection.refs.length?<ul className="insight-references">{selection.refs.map((ref,index)=><li key={ref.fact_id+index}><span>{ref.workspace_title}{ref.archived?' · 已归档':''} · {ref.task_id} · {sourceNames[ref.source_kind]??ref.source_kind}</span><button className="button small" onClick={()=>trace({wid:ref.workspace_id,fid:ref.fact_id,archived:ref.archived})}>查看这条原始材料</button></li>)}</ul>:<p>尚无候选材料或记录，不作能力结论。</p>}</section>}

  </>;
}
