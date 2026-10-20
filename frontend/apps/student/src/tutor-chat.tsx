import { useInfiniteQuery,useMutation,useQueryClient } from '@tanstack/react-query';
import { useEffect, useState,useRef, type FormEvent } from 'react';
import { Lightbulb, Send } from 'lucide-react';
import { api,watchTutor, type TutorCommand, type TutorRun } from '@wenjin/api-client';
import { ErrorNotice, Loading } from '@wenjin/ui';

const checks: Record<string, string> = { passed: '最终数值通过独立核验；解释仍需评阅。', failed: '最终数值未通过；请检查条件与运算。', unverifiable: '尚不能确认最终答案，这不代表答错。请澄清你的表达。' };

/** Persisted server history is the transcript; local storage never holds answers. */
export function TutorChat({ run, locked, send }: { run: TutorRun; locked: boolean; send: (command: TutorCommand) => void }) {
  const [text, setText] = useState('');
  const [mode, setMode] = useState<'answer' | 'discuss'>('answer');
  const [live,setLive]=useState(false),queryClient=useQueryClient(),sent=useRef<string|undefined>(undefined);
  const view = run.view!;
  useEffect(() => { setText(''); }, [view.phase, view.task?.task_id]);
  useEffect(()=>{if(run.status==='queued'&&sent.current===text){setText('');sent.current=undefined;}},[run.revision]);
  useEffect(()=>{
    const controller=new AbortController();
    void(async()=>{while(!controller.signal.aborted){try{await watchTutor(run,controller.signal,()=>{setLive(true);void queryClient.invalidateQueries({queryKey:['tutor-chat',run.workspace_id,run.run_id]});void queryClient.invalidateQueries({queryKey:['tutoring',run.workspace_id]});});}catch{setLive(false);}if(!controller.signal.aborted)await new Promise(resolve=>window.setTimeout(resolve,1000));}})();
    return()=>controller.abort();
  },[run.workspace_id,run.run_id]);
  const cancel=useMutation({mutationFn:()=>api.cancelModelTask(run),onSuccess:()=>{void queryClient.invalidateQueries({queryKey:['tutoring',run.workspace_id]});}});
  const history = useInfiniteQuery({ queryKey: ['tutor-chat', run.workspace_id, run.run_id], initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => api.tutorHistory(run, pageParam, signal),
    getNextPageParam: last => last.has_more ? last.cursor : undefined,
    refetchInterval: live?10000:1500 });
  // Ascending pagination preserves order; later pages are explicitly reachable.
  const rows = history.data?.pages.flatMap(page => page.items) ?? [];
  const independent = view.phase === 'diagnostic' || view.phase === 'checkpoint';
  const discussing = mode === 'discuss' && !independent && !view.can_reflect;
  const enabled = !locked && (view.can_reflect || (discussing ? view.can_hint && run.coaching_available : view.can_answer));
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!enabled || !text.trim() || ((discussing || view.can_reflect) && text.trim().length < 2)) return;
    sent.current=text;
    send(view.can_reflect ? { action: 'reflect', reflection: text.trim() } : discussing ? { action: 'hint', coaching_text: text.trim() } : { action: 'answer', answer: text.trim(), reasoning: text.trim() });
  }
  return <section className="card tutor-chat" aria-label="持续陪学对话">
    <div className="card-head"><h2>与津津一起思考</h2><span className="badge neutral">{independent ? '独立尝试 · 不提供提示' : '对话保存在当前空间'}</span></div>
    <p className="card-note" role="status">{live?'实时消息流已连接；只显示已保存且通过检查的结果。':'正在恢复实时连接；已有记录仍可刷新查看。'}</p>{run.coaching&&['waiting','pending','running'].includes(run.coaching.status)&&<div className="notice" role="status">AI 正在处理本轮表达，可继续查看已保存的消息。<button className="button small" disabled={cancel.isPending} onClick={()=>cancel.mutate()}>停止本次生成</button></div>}<ErrorNotice error={cancel.error}/>
    <div className="chat-transcript" role="log" aria-label="学习对话记录" aria-live="polite">
      {history.isPending ? <Loading text="恢复对话…"/> : <ErrorNotice error={history.error} retry={() => { void history.refetch(); }}/>}
      {!rows.some(row => row.answer || row.thinking || row.reflection) && <p className="chat-message assistant">先说说你的答案或思路；不确定也可以如实表达。</p>}
      {rows.map(row => <div key={row.sequence} className="chat-turn">
        {row.task_stem && <details className="chat-task"><summary>当时题目 · {row.task_id}</summary>{row.task_stem}</details>}
        {(row.answer || row.thinking || row.reflection) && <p className="chat-message student"><strong>我</strong>{row.answer || row.thinking || row.reflection}</p>}
        {row.numeric_status && <p className="chat-message assistant"><strong>数学核验</strong>{checks[row.numeric_status]}</p>}
        {row.preset_hint && <p className="chat-message assistant"><strong>学习支持</strong>{row.preset_hint}</p>}
        {row.model?.result && <div className="chat-message assistant"><strong>AI 追问</strong><p>{row.model.result.question}</p><small>模型建议，未作为数学真值。</small>{row.model.result.answer_understanding && <p>AI 对这段表达的理解：最终数值可能是 {String(row.model.result.answer_understanding.candidate)}；请先确认后在“回答这道题”提交，当前未据此判分。</p>}</div>}
        {row.model && ['failed', 'unknown', 'cancelled', 'discarded'].includes(row.model.status) && <p className="chat-message assistant">本次 AI 未产生可用结果（{row.model.status}），不会自动重试；可以继续回答或使用现有提示。</p>}
        {!row.processed && row.action !== 'start' && <p className="card-note">这条表达已保存，正在处理。</p>}
      </div>)}
      {history.hasNextPage && <button className="button small" disabled={history.isFetchingNextPage} onClick={() => { void history.fetchNextPage(); }}>加载后续对话</button>}
    </div>
    {view.task && <div className="chat-current" data-task-id={view.task.task_id}><span className="badge">当前 · 第 {view.task_index}/{view.task_count} 题</span><p className="study-stem">{view.task.stem}</p></div>}
    {!!view.feedback?.interpretation?.candidate && <p className="card-note">本轮识别的最终数值：{String(view.feedback.interpretation.candidate)}。识别有误时请明确更正；已提交的独立结果不可覆盖。</p>}
    {view.feedback && <p className="notice" role="status">{view.feedback.message}</p>}
    {!independent && view.can_answer && run.coaching && run.coaching.task_id===view.task?.task_id && run.coaching.result?.answer_understanding && <div className="soft-box"><p>请确认：你这段话表达的最终数值是 {String(run.coaching.result.answer_understanding.candidate)} 吗？AI 没有据此判分。</p><button className="button" disabled={locked} onClick={()=>send({action:'answer',answer:String(run.coaching!.result!.answer_understanding!.candidate),reasoning:text||'我确认了上轮 AI 对我原始表达的理解'})}>确认这个数值并独立核验</button><button className="button text" onClick={()=>{setMode('answer');setText('');}}>不是，我重新说明</button></div>}
    {view.can_reflect && <p className="chat-message assistant">{view.reflection_prompt}</p>}
    {!independent && !view.can_reflect && <div className="tabs" role="group" aria-label="表达方式"><button aria-pressed={mode === 'answer'} onClick={() => setMode('answer')}>回答这道题</button><button aria-pressed={mode === 'discuss'} disabled={!run.coaching_available} onClick={() => setMode('discuss')}>回应 AI 的追问</button></div>}
    <form className="form-stack" onSubmit={submit}><label htmlFor="chat-answer">{view.can_reflect ? '我的反思' : discussing ? '我的思路与问题' : '我的回答'}</label><textarea id="chat-answer" rows={3} maxLength={view.can_reflect ? 1000 : 1500} value={text} disabled={!enabled} onChange={event => setText(event.target.value)} placeholder={view.can_reflect ? '回望你的方法，再继续下一题' : '例如：这个答案是3，因为……'}/><div className="actions"><button className="button primary" disabled={!enabled || !text.trim() || ((discussing || view.can_reflect) && text.trim().length < 2)}><Send size={16}/>发送</button><button type="button" className="button" disabled={locked || !view.can_hint} onClick={() => send({ action: 'hint', coaching_text: '' })}><Lightbulb size={16}/>给我一点提示</button>{view.can_continue && <button type="button" className="button" disabled={locked} onClick={() => send({ action: 'next' })}>{view.feedback?.status === 'unverifiable' ? '保留为未确定，继续' : '继续下一步'}</button>}{view.can_skip && <button type="button" className="button text" disabled={locked} onClick={() => send({ action: 'skip' })}>暂时不会，先反思</button>}</div></form>
    <p className="card-note">普通中文答案先做保守提取，再由程序核验数值；复杂语言、单位和证明尚不能全面判断。AI 讨论仅使用已配置的开发模型及额度，不发送其他项目记录。</p>
  </section>;
}
