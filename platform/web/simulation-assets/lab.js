'use strict';
const $ = id => document.getElementById(id);
let token = '', jobs = [], selected = null, report = null, profiles = {}, scenarios = {}, polling = false;
let trajectory = [], truncated = false, analysis = null, generation = 0;
const states = {queued:'排队',running:'运行中',stopping:'正在停止',stopped:'已停止',completed:'已完成',failed:'失败',timed_out:'预算超时',unsupported:'题型不支持',interrupted:'进程中断，检查后恢复'};
const active = job => ['queued','running','stopping'].includes(job?.state);
const percent = value => LabAnalytics.probability(value) ? (value * 100).toFixed(1) + '%' : '—';
function notice(message, error=false) { $('notice').textContent=message; $('notice').classList.toggle('error',error); }
function text(tag, value) { const element=document.createElement(tag); element.textContent=value; return element; }
async function api(path, body) {
  const response = await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','x-lab-token':token},body:body===undefined?undefined:JSON.stringify(body)});
  const value=await response.json();
  if(!response.ok) throw Error(typeof value.detail==='string'?value.detail:JSON.stringify(value.detail));
  return value;
}
function svgElement(tag, attrs={}, value) {
  const element=document.createElementNS('http://www.w3.org/2000/svg',tag);
  for(const [key, item] of Object.entries(attrs)) element.setAttribute(key,String(item));
  if(value!==undefined) element.textContent=value;
  return element;
}
function emptyChart(id, message='暂无可用观测。运行后将按实际数据绘制。') { $(id).replaceChildren(text('p',message)); $(id).firstChild.className='empty'; }
function chartFrame(id, title, xLabel='回合') {
  const svg=svgElement('svg',{viewBox:'0 0 520 220',role:'img','aria-label':title});
  svg.append(svgElement('title',{},title));
  for(const value of [0,.5,1]) {
    const y=180-value*150;
    svg.append(svgElement('line',{x1:42,y1:y,x2:500,y2:y,stroke:'#30363d'}),svgElement('text',{x:4,y:y+4,fill:'#9da7b3','font-size':12},Math.round(value*100)+'%'));
  }
  svg.append(svgElement('text',{x:480,y:214,fill:'#9da7b3','font-size':12},xLabel));
  $(id).replaceChildren(svg);
  return svg;
}
function lineChart(id, title, series) {
  const points=series.flatMap(s=>s.points).filter(p=>LabAnalytics.probability(p.value));
  if(!points.length) { emptyChart(id,'暂无有效样本；不以 0 或示例曲线代替缺失数据。'); return; }
  const min=Math.min(...points.map(p=>p.turn)), max=Math.max(...points.map(p=>p.turn));
  const x=turn=>max===min?271:42+(turn-min)/(max-min)*458, y=value=>180-value*150;
  const svg=chartFrame(id,title);
  svg.append(svgElement('text',{x:42,y:202,fill:'#9da7b3','font-size':12},min),svgElement('text',{x:455,y:202,fill:'#9da7b3','font-size':12},max));
  for(const seriesItem of series) {
    // Missing samples break the line instead of implying an observation through a gap.
    let segment=[];
    const draw=()=>{if(segment.length>1)svg.append(svgElement('polyline',{points:segment.map(p=>x(p.turn)+','+y(p.value)).join(' '),fill:'none',stroke:seriesItem.color,'stroke-width':2.5}));segment=[];};
    for(const point of seriesItem.points) {
      if(!LabAnalytics.probability(point.value)) { draw(); continue; }
      segment.push(point);
      const dot=svgElement('circle',{cx:x(point.turn),cy:y(point.value),r:3.5,fill:seriesItem.color});
      dot.append(svgElement('title',{},`${seriesItem.name} · 回合 ${point.turn} · ${percent(point.value)}`)); svg.append(dot);
    }
    draw();
  }
}
function renderKC() {
  $('kc-values').replaceChildren();
  for(const kc of analysis?.perKC || []) {
    const row=document.createElement('tr');
    row.append(text('td',kc.kc),text('td',kc.points),text('td',percent(kc.estimated)),text('td',percent(kc.latent))); $('kc-values').append(row);
  }
  const values=analysis?.perKC || [];
  if(!values.length) { emptyChart('kc-chart'); return; }
  const width=520,height=Math.max(170,values.length*45+35), svg=svgElement('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':'各知识点最后估计（绿色）与潜在终态（紫色）'});
  svg.append(svgElement('title',{},'知识点终态比较：绿色引擎估计，紫色模拟器潜在状态'));
  values.forEach((kc,index)=>{
    const y=20+index*45;
    const label=svgElement('text',{x:3,y:y+12,fill:'#9da7b3','font-size':12},kc.kc.length>18?kc.kc.slice(0,17)+'…':kc.kc);
    label.append(svgElement('title',{},kc.kc)); svg.append(label);
    for(const [value,color,offset] of [[kc.estimated,'#3fb950',0],[kc.latent,'#bc8cff',16]]) {
      if(!LabAnalytics.probability(value)) continue;
      svg.append(svgElement('rect',{x:145,y:y+offset,width:value*310,height:11,rx:2,fill:color}),svgElement('text',{x:460,y:y+offset+10,fill:color,'font-size':11},percent(value)));
    }
  });
  $('kc-chart').replaceChildren(svg);
}
function renderMastery() {
  const kc=$('kc-filter').value, points=analysis?.mastery.get(kc) || [];
  lineChart('mastery-chart','知识点 '+kc+' 的掌握度轨迹',[
    {name:'引擎估计',color:'#3fb950',points:points.map(p=>({turn:p.turn,value:p.estimated}))},
    {name:'模拟器潜在状态',color:'#bc8cff',points:points.map(p=>({turn:p.turn,value:p.latent}))}
  ]);
}
function renderAnalysis() {
  analysis=LabAnalytics.analyze(trajectory,report);
  const previous=$('kc-filter').value;
  $('kc-filter').replaceChildren();
  for(const kc of analysis.perKC) { const option=text('option',kc.kc); option.value=kc.kc; $('kc-filter').append(option); }
  if(!analysis.perKC.length) { const option=text('option','暂无知识点'); option.value=''; $('kc-filter').append(option); }
  if(analysis.perKC.some(kc=>kc.kc===previous)) $('kc-filter').value=previous;
  renderMastery();
  lineChart('independent-chart','独立首次作答累计通过率',[{name:'独立通过率',color:'#58a6ff',points:analysis.independent}]);
  lineChart('hint-chart','累计辅助作答占比',[{name:'累计辅助作答',color:'#d29922',points:analysis.hints}]);
  $('independent-summary').textContent=analysis.independentCount?`独立样本 n=${analysis.independentCount} · 通过 ${analysis.independentPassed} · ${percent(analysis.independentPassed/analysis.independentCount)}`:'暂无独立样本，不将无样本解释为未掌握。';
  $('hint-summary').textContent=`有效作答 ${analysis.answerCount} · 辅助作答 ${analysis.assistedCount} · 模拟器依赖参数终值 ${percent(report?.hint_dependency)}（仅报告终值，非观测比例）`;
  $('analysis-note').textContent=report?`source_kind: ${report.source_kind || '未标注'} · 实际事件 ${trajectory.length} · ${truncated?'轨迹已截断，累计指标只代表可见窗口，不等同于全报告。':'全轨迹采样，不补齐缺失观测。'} · 非真人教育效果`:'暂无报告。运行完成或停止后显示实际采样点；没有观测的数据不补齐。';
  renderKC();
}
function resetResults() {
  generation++; report=null; trajectory=[]; truncated=false;
  $('report').textContent='暂无报告'; $('transcript').textContent='暂无轨迹'; $('download').disabled=true;
  $('turn-count').textContent='—'; $('errors').textContent='—'; $('comparison').replaceChildren();
  $('comparison-warning').textContent='选择已产生报告的参考运行。'; renderAnalysis();
}
function choose(id) { selected=id; resetResults(); render(); loadResults().catch(error=>notice(error.message,true)); }
function render() {
  const current=jobs.find(job=>job.job_id===selected), busy=jobs.some(active);
  $('start').disabled=!token||busy; $('stop').disabled=!active(current)||current.state==='stopping';
  $('resume').disabled=busy||!current?.resumable||!current?.run_id;
  $('state').textContent=states[current?.state]||'未运行';
  $('run-id').textContent=current?.run_id?'Run '+current.run_id:current?'Job '+current.job_id:'暂无运行';
  $('jobs').replaceChildren();
  if(!jobs.length) { const tr=document.createElement('tr'),td=text('td','暂无实验作业'); td.colSpan=4; tr.append(td); $('jobs').append(tr); }
  for(const job of jobs) {
    const tr=document.createElement('tr'); if(job.job_id===selected)tr.className='selected';
    tr.append(text('td',(profiles[job.request.profile_id]||job.request.profile_id)+' / '+(scenarios[job.request.scenario_id]||job.request.scenario_id)),text('td',job.request.seed+' / '+job.request.max_turns),text('td',states[job.state]||job.state));
    const td=document.createElement('td'),button=text('button',job.job_id===selected?'当前':'查看'); button.type='button'; button.onclick=()=>choose(job.job_id); td.append(button); tr.append(td); $('jobs').append(tr);
  }
  const previous=$('compare-run').value; $('compare-run').replaceChildren(text('option','不比较')); $('compare-run').firstChild.value='';
  for(const job of jobs.filter(job=>job.job_id!==selected&&!active(job)&&job.run_id)) {
    const option=text('option',`${profiles[job.request.profile_id]||job.request.profile_id} / ${scenarios[job.request.scenario_id]||job.request.scenario_id} · seed ${job.request.seed} · ${job.job_id.slice(0,8)}`); option.value=job.job_id; $('compare-run').append(option);
  }
  if([...$('compare-run').options].some(o=>o.value===previous)) $('compare-run').value=previous;
  if(current?.state==='failed')notice('作业失败：'+current.error,true);
}
async function loadResults() {
  const current=jobs.find(job=>job.job_id===selected);
  if(!current||active(current)||!current.run_id)return;
  const jobId=current.job_id, version=generation, result=await api('/api/lab/jobs/'+jobId+'/report');
  if(selected!==jobId||version!==generation)return;
  report=result; $('report').textContent=JSON.stringify(result,null,2); $('report').classList.remove('empty'); $('download').disabled=false;
  $('turn-count').textContent=result.turns??result.turns_completed??'—'; $('errors').textContent=result.errors?.length??'—';
  try {
    const response=await api('/api/lab/jobs/'+jobId+'/transcript');
    if(selected!==jobId||version!==generation)return;
    trajectory=response.events; truncated=response.truncated;
    $('transcript').textContent=trajectory.map(item=>JSON.stringify(item)).join('\n')||'暂无轨迹'; $('transcript').classList.remove('empty');
  } catch(error) { if(selected===jobId&&version===generation)$('transcript').textContent=error.message; }
  if(selected===jobId&&version===generation) { renderAnalysis(); await compare(); }
}
async function compare() {
  const reference=$('compare-run').value, jobId=selected, version=generation;
  $('comparison').replaceChildren();
  if(!reference||!report) { $('comparison-warning').textContent='选择已产生报告的参考运行。'; return; }
  try {
    const other=await api('/api/lab/jobs/'+reference+'/report');
    if(jobId!==selected||version!==generation||reference!==$('compare-run').value)return;
    const fields=['profile_id','seed','max_turns'], different=fields.filter(field=>report.config?.[field]!==other.config?.[field]);
    for(const field of ['course_sha256','engine_sha256','simulator_version','profile_version']) if(!report[field]||report[field]!==other[field])different.push(field);
    if(report.status!=='completed'||other.status!=='completed')different.push('未完成运行');
    $('comparison-warning').textContent=different.length?'非配对/不完整比较，差异或缺失：'+different.join('、')+'。仅阅读摘要，不解释为策略增益。':'画像、种子、预算、课程与引擎相同；单次配对仍不能推断教育效果。';
    const table=document.createElement('table'),head=document.createElement('thead'),tr=document.createElement('tr');
    tr.append(text('th','指标'),text('th','当前运行'),text('th','参考运行')); head.append(tr); table.append(head);
    const body=document.createElement('tbody');
    for(const [name,field,format] of [['场景','scenario_id',v=>scenarios[v]||v||'—'],['实际回合','turns',v=>v??'—'],['独立样本','independent_attempts',v=>v??'—'],['独立通过率','observed_pass_rate',percent],['辅助作答数','assisted_attempts',v=>v??'—'],['潜在依赖终值','hint_dependency',percent]]) {
      const row=document.createElement('tr'),left=field==='scenario_id'?report.config?.[field]:report[field],right=field==='scenario_id'?other.config?.[field]:other[field];
      row.append(text('td',name),text('td',format(left)),text('td',format(right))); body.append(row);
    }
    table.append(body); $('comparison').append(table);
  } catch(error) { if(jobId===selected&&version===generation)$('comparison-warning').textContent='参考报告不可用：'+error.message; }
}
async function refresh() {
  if(polling)return; polling=true;
  try { jobs=(await api('/api/lab/jobs')).jobs; if(!selected&&jobs.length)selected=jobs[0].job_id; render(); if(!report)await loadResults(); }
  catch(error) { notice(error.message,true); } finally { polling=false; }
}
$('configuration').onsubmit=async event=>{
  event.preventDefault(); $('start').disabled=true;
  try { const job=await api('/api/lab/jobs',{profile_id:$('profile').value,scenario_id:$('scenario').value,seed:Number($('seed').value),max_turns:Number($('turns').value)}); selected=job.job_id; resetResults(); notice('实验已启动'); await refresh(); }
  catch(error) { notice(error.message,true); render(); }
};
$('stop').onclick=async()=>{if(!selected)return; $('stop').disabled=true; try{await api('/api/lab/jobs/'+selected+'/stop',{});notice('停止请求已发送');await refresh();}catch(error){notice(error.message,true);render();}};
$('resume').onclick=async()=>{if(!selected)return; $('resume').disabled=true; try{const job=await api('/api/lab/jobs/'+selected+'/resume',{});selected=job.job_id;resetResults();notice('检查点恢复已启动');await refresh();}catch(error){notice(error.message,true);render();}};
$('refresh').onclick=refresh; $('kc-filter').onchange=renderMastery; $('compare-run').onchange=compare;
$('download').onclick=()=>{if(!report)return;const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download='synthetic-report-'+selected+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
async function init() {
  resetResults();
  try { const config=await api('/api/lab/config'); token=config.token; profiles=config.profiles; scenarios=config.scenarios;
    for(const [id,name] of Object.entries(profiles)){const option=text('option',name);option.value=id;$('profile').append(option);}
    for(const [id,name] of Object.entries(scenarios)){const option=text('option',name);option.value=id;$('scenario').append(option);}
    notice('实验室已就绪'); await refresh();
  } catch(error) { notice(error.message,true); }
}
init(); setInterval(()=>{if(jobs.some(active))refresh();},1000);
