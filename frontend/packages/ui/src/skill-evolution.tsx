import {useRef,useState} from 'react';
import {useQuery,useMutation,useQueryClient} from '@tanstack/react-query';
import {Link} from 'react-router-dom';
import {api,type Workspace,type EvolutionRun} from '@wenjin/api-client';
import {Heading,Loading,Empty,ErrorNotice,Modal} from './index';

const phases:Record<string,string>={queued:'等待离线运行',awaiting_review:'等待开发评测复核',approved_for_proposal:'可采纳为待审策略',strategy_proposed:'已生成待审策略',blocked:'工程评测阻止送审',no_candidate:'没有适当改进假设',rejected:'已拒绝',withdrawn:'复核已撤回',cancelled:'已取消',failed:'任务失败'};
function object(value:unknown):Record<string,unknown>{return value&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{};}
function rows(value:unknown):Record<string,unknown>[]{return Array.isArray(value)?value.map(object):[];}

export function EvolutionWorkbench({workspace,admin=false}:{workspace?:Workspace;admin?:boolean}){
 const client=useQueryClient(),[offset,setOffset]=useState(0),[selected,setSelected]=useState<string>();
 const wid=admin?undefined:workspace?.workspace_id;
 const enabled=admin||workspace?.kind==='project';
 const query=useQuery({queryKey:['evolutions',admin,wid,offset],queryFn:({signal})=>api.evolutions(wid,offset,signal),enabled,refetchInterval:2000});
 const list=query.data?.items??[],rid=selected??list[0]?.run_id;
 const detail=useQuery({queryKey:['evolution',admin,wid,rid],queryFn:({signal})=>api.evolution(rid!,wid,signal),enabled:enabled&&!!rid,refetchInterval:2000});
 const [mode,setMode]=useState<'offline'|'semantic'>('offline'),[trial,setTrial]=useState(false);
 const automation=useQuery({queryKey:['learning-automation',wid],queryFn:({signal})=>api.learningAutomation(wid!,signal),enabled:!admin&&!!wid&&enabled});
 const configure=useMutation({mutationFn:(on:boolean)=>api.configureLearningAutomation(wid!,on,automation.data!.revision),onSuccess:()=>{void automation.refetch();refresh();}});
 const startKey=useRef(crypto.randomUUID());
 function refresh(){void client.invalidateQueries({queryKey:['evolutions']});void client.invalidateQueries({queryKey:['evolution']});void client.invalidateQueries({queryKey:['strategies']});}
 const start=useMutation({mutationFn:()=>api.startEvolution(wid!,startKey.current,mode,trial),onSuccess:run=>{startKey.current=crypto.randomUUID();setSelected(run.run_id);setOffset(0);refresh();}});
 return <><Heading kicker="IMPROVE, WITH A TESTABLE HYPOTHESIS" title={admin?'Skill 进化评测':'Skill 改进与试用'} description="冻结来源，提出有限假设，以实际运行行为检验，再由人决定。" actions={<button className="button" onClick={refresh}>刷新改进状态</button>}/>
 <div className="notice">可选择离线规则或模型语义候选。模型仅读取本项目最多6条脱敏记录；个人自动试用需你明确同意，通过独立工程评测后才生效，可随时撤回。全局自动发布未开放，教学效果待验证。先确认课程并激活包含四项执行参数的策略。</div>
 {!enabled?<section className="card section-gap"><Empty title="Skill改进从正式项目开始">临时会话保留局部过程，不作为自动策略改进来源。</Empty></section>:<>
 {!admin&&<section className="card section-gap">{automation.data&&<div className="soft-box"><label className="inline"><input type="checkbox" checked={automation.data.enabled} disabled={configure.isPending||workspace?.status!=='active'} onChange={event=>configure.mutate(event.target.checked)}/>启用本项目自动语义改进</label><p>仅DeepSeek开发用途；新增6条事实且任务结束后触发，每项目每天最多1个候选、每次2048输出tokens，共用平台额度。通过独立工程评测后个人试用；停用会撤回活跃试用。教学效果尚未验证。</p><ErrorNotice error={configure.error}/></div>}<h2>生成一次可核对的改进假设</h2><p>{mode==='semantic'?'模型读取本项目最多6条脱敏记录，提出可核对的改进假设；未做教学效果判断。':'离线规则读取同来源、同内容版本最多60条结构化事实及3条数值复核，不做语义理解。'}每轮只提出一个参数调整，没有足够信号则保留当前策略。</p><label className="field">改进方式<select value={mode} onChange={event=>{setMode(event.target.value as 'offline'|'semantic');startKey.current=crypto.randomUUID();}}><option value="offline">离线规则候选</option><option value="semantic">模型理解学习记录</option></select></label>{mode==='semantic'&&<label className="inline"><input type="checkbox" checked={trial} onChange={event=>{setTrial(event.target.checked);startKey.current=crypto.randomUUID();}}/>通过受限评测后，允许在本项目自动试用；教学效果未知，可以撤回</label>}<div className="actions section-gap"><button className="button primary" disabled={start.isPending||workspace?.status!=='active'||list.some(r=>['queued','awaiting_input'].includes(r.status))} onClick={()=>start.mutate()}>{mode==='semantic'?'生成语义改进候选':'生成离线改进候选'}</button><Link className="button" to={`/planner?workspace=${wid}`}>查看策略基线</Link></div><ErrorNotice error={start.error}/></section>}
 {query.isPending?<Loading/>:query.isError?<ErrorNotice error={query.error}/>:<section className="card section-gap"><h2>候选谱系与任务</h2>{list.length?<div className="plan-history">{list.map(run=><button className="button" key={run.run_id} onClick={()=>setSelected(run.run_id)}>{run.workspace_title} · {phases[run.phase]??run.phase}</button>)}</div>:<Empty title="还没有离线改进任务">先积累有来源的尝试，再生成；不会为了展示版本而虚构改进。</Empty>}<div className="pagination section-gap"><button className="button" disabled={!offset} onClick={()=>{setOffset(Math.max(0,offset-20));setSelected(undefined);}}>上一页候选</button><button className="button" disabled={list.length<20} onClick={()=>{setOffset(offset+20);setSelected(undefined);}}>下一页候选</button></div></section>}
 {rid&&(detail.isPending?<Loading/>:detail.isError?<ErrorNotice error={detail.error}/>:<EvolutionDetails key={detail.data.run_id} run={detail.data} admin={admin} refresh={refresh}/>)}
 </>}
 </>;
}

function EvolutionDetails({run,admin,refresh}:{run:EvolutionRun;admin:boolean;refresh:()=>void}){
 const [note,setNote]=useState(''),[fact,setFact]=useState<string>();
 const intent=useRef({signature:'',key:''});
 const mutate=useMutation({mutationFn:(action:'approve'|'reject'|'withdraw'|'propose'|'cancel')=>{
  if(action==='cancel')return api.cancelEvolution(run);
  const signature=JSON.stringify([run.revision,run.report_sha256,action,note.trim()]);
  if(intent.current.signature!==signature)intent.current={signature,key:crypto.randomUUID()};
  return api.decideEvolution(run,action,note.trim(),intent.current.key);
 },onSuccess:()=>{setNote('');refresh();},onError:refresh});
 const withdrawTrial=useMutation({mutationFn:()=>api.withdrawSkillTrial(run.workspace_id,run.run_id),onSuccess:refresh});
 const candidate=run.candidate,report=run.evaluation;
 const active=run.workspace_status==='active';
 const canReview=admin&&active&&['awaiting_review','approved_for_proposal','blocked'].includes(run.phase);
 const canWithdraw=admin&&['approved_for_proposal','strategy_proposed'].includes(run.phase);
 const canPropose=!admin&&active&&run.phase==='approved_for_proposal';
 return <section className="card section-gap evolution-detail"><div className="card-head"><h2>候选差异与评测</h2><span className="badge neutral">{phases[run.phase]??run.phase}</span></div><p className="card-note">{run.workspace_title} · {run.run_id}</p>{run.error_code&&<p className="notice">此任务不能继续：{run.error_code}。保留记录，可取消或重新生成。</p>}{run.generation&&<p>模型任务：{String(run.generation.status)}</p>}{run.trial&&<section className="notice"><strong>个人试用：{String(run.trial.status)} · 教学效果待验证</strong><p>已观察 {String(run.trial.observations)} 条过程；这不是教学收益。</p>{!admin&&run.trial.status==='active'&&<button className="button" disabled={withdrawTrial.isPending} onClick={()=>withdrawTrial.mutate()}>撤回个人试用，恢复原策略</button>}<ErrorNotice error={withdrawTrial.error}/></section>}{run.reason&&<p>{run.reason==='candidate_generated'?'已从结构化记录提出一个开发改进假设。':run.reason}</p>}
 {candidate&&<><h3 className="section-gap">改进假设</h3><p>{String(object(candidate.hypothesis).reason)}</p><p className="card-note">{String(object(candidate.hypothesis).limitation)}</p><ul>{rows(candidate.diff).map((row,index)=><li key={index}>{String(row.label)}：{String(row.before)} → {String(row.after)}</li>)}</ul><p className="card-note">父版本：{String(object(run.dataset?.baseline).strategy_id)}<br/>冻结数据指纹：<code style={{overflowWrap:'anywhere'}}>{String(run.dataset?.fingerprint)}</code></p><details><summary>查看候选 Skill.md 与编译配置</summary><pre className="strategy-json">{String(object(candidate.compilation).markdown)}</pre><pre className="strategy-json">{JSON.stringify(candidate.policy,null,2)}</pre></details></>}
 {run.dataset&&<details className="section-gap"><summary>查看冻结来源与原始材料</summary><p className="card-note">{String(run.dataset.limitation)}</p><ul>{rows(run.dataset.facts).map(row=><li key={String(row.id)}>{String(row.task_id)} · {String(row.status??'过程')} · 提示{String(row.hints_used)}次 <button className="button text" onClick={()=>setFact(String(row.id))}>查看原始材料</button></li>)}</ul><pre className="strategy-json">{JSON.stringify(run.dataset,null,2)}</pre></details>}
 {report&&<section className="section-gap"><h3>冻结机制评测</h3><p>{report.eligible_for_review?'工程检查通过，可申请人工复核':'工程门禁未通过，禁止生成策略提案'}</p><p className="card-note">{String(report.limitation)}</p><ul>{rows(report.cases).map(row=><li key={String(row.case_id)}><details><summary>{String(row.case_id)} · {row.split==='heldout'?'隔离保留样本':'开发样本'} · {row.passed?'工程通过':'未通过'}</summary><p>行为变化：{row.behavior_changed?'已观察到':'未观察到'}；不是学习增益。</p><pre className="strategy-json">{JSON.stringify({baseline:row.baseline,candidate:row.candidate,error:row.error_code},null,2)}</pre></details></li>)}</ul><details><summary>核对评测版本与报告哈希</summary><p style={{overflowWrap:'anywhere'}}>{run.report_sha256}</p><pre className="strategy-json">{JSON.stringify(report,null,2)}</pre></details></section>}
 {!!run.reviews.length&&<section className="section-gap"><h3>复核与采纳记录</h3><ul>{run.reviews.map((row,index)=><li key={index}>{String(row.action)}：{String(row.note)}</li>)}</ul></section>}
 {(canReview||canWithdraw||canPropose)&&<div className="section-gap"><label className="field">{admin?'离线评测复核依据':'采纳为待审策略的理由'}<textarea minLength={10} maxLength={1000} value={note} onChange={e=>setNote(e.target.value)} disabled={mutate.isPending}/></label><div className="actions section-gap">{canReview&&<><button className="button" disabled={mutate.isPending||note.trim().length<10} onClick={()=>mutate.mutate('reject')}>拒绝此改进候选</button><button className="button primary" disabled={mutate.isPending||note.trim().length<10||!report?.eligible_for_review} onClick={()=>mutate.mutate('approve')}>复核通过，允许生成提案</button></>}{canWithdraw&&<button className="button" disabled={mutate.isPending||note.trim().length<10} onClick={()=>mutate.mutate('withdraw')}>撤回评测复核</button>}{canPropose&&<button className="button primary" disabled={mutate.isPending||note.trim().length<10} onClick={()=>mutate.mutate('propose')}>采纳为待审策略</button>}</div><p className="card-note">仅开发评测复核，不代表专业教师认可或学习效果。生成提案后仍须管理员审核和学生确认。</p></div>}
 {!admin&&run.strategy_run_id&&<Link className="button primary section-gap" to={`/planner?workspace=${run.workspace_id}`}>前往策略审核与确认</Link>}
 {!admin&&active&&['queued','awaiting_input'].includes(run.status)&&<button className="button section-gap" disabled={mutate.isPending} onClick={()=>mutate.mutate('cancel')}>取消本轮离线改进</button>}
 <ErrorNotice error={mutate.error}/>{fact&&<EvolutionSource run={run} fact={fact} admin={admin} close={()=>setFact(undefined)}/>}
 </section>;
}

function EvolutionSource({run,fact,admin,close}:{run:EvolutionRun;fact:string;admin:boolean;close:()=>void}){
 const query=useQuery({queryKey:['evolution-fact',admin,run.run_id,fact],queryFn:({signal})=>api.evolutionFact(run,fact,admin,signal)});
 return <Modal title="改进假设的原始材料" onClose={close}>{query.isPending?<Loading/>:query.isError?<ErrorNotice error={query.error}/>:<><p className="dialog-copy">{query.data.task.stem}</p><p>原答案：{query.data.answer??'无数值提交'} · 自动状态：{query.data.status??'过程'}</p><p className="trace-text">步骤与依据：{query.data.reasoning??'未填写'}</p><p className="trace-text">反思：{query.data.reflection??'未填写'}</p><p className="card-note">原事实不可改写；下方注记是当前记录，冻结时结构化数据见数据集。</p>{query.data.notes.map(n=><p key={n.note_id}>{n.text}</p>)}{query.data.judgments.map(j=><p key={j.judgment_id}>{j.conclusion}：{j.note}</p>)}</>}</Modal>;
}
