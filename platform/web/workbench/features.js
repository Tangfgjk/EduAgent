/* Project/temporary context, scoped growth, competency radar and planner UI. */
'use strict';

let activeProjectId = null;
let activeContextId = null;
let selectedHarnessId = null;

function syncStudyMode() {
  const temporary = !!activeContextId;
  document.body.dataset.temporary = String(temporary);
  $('send').textContent = temporary ? '发送消息 →' : '提交答案 →';
  $('message').placeholder = temporary ? '说说你想讨论的问题，不计入学习成绩…' : '写下这道题的答案或解题步骤…';
  $('help').hidden = temporary;
  $('assessment-prompt').hidden = temporary;
  $('study-task-title').textContent = temporary ? '临时讨论' : $('target').selectedOptions[0]?.textContent || '开始学习';
  $('study-task-meta').textContent = temporary ? '独立于学习项目，不更新正式成长记录。' : '先独立作答，再核对结果；不会时可以请求提示。';
}

function lines(value) { return String(value || '').split(/\n|,/).map(item => item.trim()).filter(Boolean); }
function featureListBox(id, values, empty = '暂无') {
  const box = $(id); if (!box) return; box.replaceChildren();
  if (!values.length) { box.append(text('p', empty)); return; }
  for (const value of values) {
    const row = document.createElement('div'); row.className = 'feature-row'; row.textContent = value; box.append(row);
  }
}
function renderTodos(project) {
  const box = $('active-project-todos'); box.replaceChildren();
  if (!project) { box.append(text('p', '选择项目后显示任务')); return; }
  const tasks = project.content.tasks || [];
  if (!tasks.length) { box.append(text('p', '项目暂无任务')); return; }
  for (const task of tasks) {
    const row = document.createElement('div'); row.className = 'feature-row todo-row';
    const title = text('span', task.title); const select = document.createElement('select');
    for (const [value, label] of [['todo', '待办'], ['doing', '进行中'], ['done', '完成']]) { const option = text('option', label); option.value = value; select.append(option); }
    select.value = task.status; select.onchange = run(async () => {
      const saved = await api('/api/learning/todos/' + learner + '/' + encodeURIComponent(project.record_id) + '/' + encodeURIComponent(task.task_id), {status: select.value, expected_revision: project.revision});
      project = saved; window.__wenjinProjects = (window.__wenjinProjects || []).map(item => item.record_id === project.record_id ? saved : item); renderTodos(project); notice('待办状态已更新');
    });
    row.append(title, select); box.append(row);
  }
}

// Keep the global route selectable even after projects have been created.
function bindProjectPicker() {
  const picker=$('project-switch');
  if (![...picker.options].some(option=>option.value==='')) {
    const option=text('option','全局学习空间');option.value='';picker.prepend(option);
  }
  picker.value=activeProjectId || '';
  picker.onchange=run(()=>setProject(picker.value));
}

async function loadContexts() {
  if (!learner || !$('teaching-consent').checked) return;
  const result = await api('/api/learning/contexts/' + learner);
  const projectContexts = (result.contexts || []).filter(item => item.context_type === 'project' && !item.closed_at);
  const temporaryContexts = (result.contexts || []).filter(item => item.context_type === 'temporary' && !item.closed_at);
  featureListBox('sidebar-contexts', temporaryContexts.map(item => item.title), '暂无临时会话');
  const scope = $('growth-scope');
  const prior = scope.value;
  scope.replaceChildren(text('option', '全局宏观')); scope.firstChild.value = 'global';
  for (const project of (window.__wenjinProjects || [])) {
    const option = text('option', '项目局部 · ' + project.content.title); option.value = project.record_id; scope.append(option);
  }
  scope.value = [...scope.options].some(item => item.value === prior) ? prior : 'global';
  if (activeContextId) {
    const current = temporaryContexts.find(item => item.context_id === activeContextId);
    if (!current) { activeContextId = null; $('close-temporary').hidden = true; }
  }
  $('active-context-label').textContent = activeContextId ? '临时会话 · 不进入正式画像' : (activeProjectId ? '项目局部 · ' + ((window.__wenjinProjects || []).find(item => item.record_id === activeProjectId)?.content.title || '') : '全局学习空间');
}

async function setProject(projectId) {
  const changed = activeProjectId !== (projectId || null) || !!activeContextId;
  activeProjectId = projectId || null;
  activeContextId = null; $('close-temporary').hidden = true;
  $('route-project').classList.add('primary'); $('route-temporary').classList.remove('primary');
  const project = (window.__wenjinProjects || []).find(item => item.record_id === activeProjectId);
  $('active-context-label').textContent = project ? '项目局部 · ' + project.content.title : '全局学习空间';
  renderTodos(project);
  if (changed) chooseTask();
  $('project-switch').value = activeProjectId || '';
  syncStudyMode();
  if ($('growth-scope')) { $('growth-scope').value = activeProjectId || 'global'; }
  await loadCompetencies();
}

async function startTemporaryContext() {
  const title = window.prompt('给临时会话起个名字（不会写入正式画像）', '临时探究');
  if (!title) return;
  const result = await api('/api/learning/contexts/' + learner, {context_type: 'temporary', title});
  activeContextId = result.context_id; activeProjectId = null;
  $('route-project').classList.remove('primary'); $('route-temporary').classList.add('primary'); $('close-temporary').hidden = false;
  $('active-context-label').textContent = '临时会话 · 不进入正式画像';
  sessionId=null; sessionSubmission=null; submission=null;
  $('chat').replaceChildren(text('p','说说你想讨论的问题。此处不会判分，也不会更新正式成长记录。'));
  $('message').value=''; $('verdict').textContent='';
  syncStudyMode(); show('study');
  await loadContexts();
  if (!sessionId) await startSession();
  notice('临时会话已开启：讨论不会进入正式成长证据');
}

async function closeTemporaryContext() {
  if (!activeContextId) return;
  await api('/api/learning/contexts/' + learner + '/' + activeContextId + '/close', {});
  activeContextId = null; $('close-temporary').hidden = true; $('route-temporary').classList.remove('primary'); $('route-project').classList.add('primary');
  $('project-switch').value=''; $('growth-scope').value='global';
  chooseTask(); syncStudyMode();
  await loadContexts(); notice('临时会话已结束');
}

function renderRadar(result) {
  const box = $('competency-radar'); box.replaceChildren();
  const known = (result.dimensions || []).filter(item => item.value !== null);
  if (!known.length) { box.className = 'competency-radar empty'; box.append(text('p', '暂无素养评估证据')); $('competency-note').textContent = result.note || ''; return; }
  box.className = 'competency-radar';
  for (const item of result.dimensions) {
    const row = document.createElement('div'); row.className = 'radar-row';
    row.append(text('span', item.label)); const track = document.createElement('span'); track.className = 'radar-track'; const fill = document.createElement('i'); fill.style.width = item.value === null ? '0%' : (item.value * 100) + '%'; track.append(fill); row.append(track, text('b', item.value === null ? '待积累' : Math.round(item.value * 100) + '%')); box.append(row);
  }
  $('competency-note').textContent = (result.provenance || '') + ' · ' + (result.note || '');
}

async function loadCompetencies() {
  if (!learner || !$('teaching-consent').checked) return;
  const projectId = $('growth-scope')?.value && $('growth-scope').value !== 'global' ? $('growth-scope').value : null;
  const result = await api('/api/learning/competencies/' + learner + (projectId ? '?project_id=' + encodeURIComponent(projectId) : ''));
  renderRadar(result);
}

async function loadScopedGrowth() {
  if (!$('growth-scope') || !learner || !$('teaching-consent').checked) return;
  const projectId = $('growth-scope').value !== 'global' ? $('growth-scope').value : null;
  const result = projectId ? await api('/api/learning/growth/' + learner + '/project/' + encodeURIComponent(projectId)) : await api('/api/learning/growth/' + learner);
  WenjinCharts.line($('mastery-chart'), result.mastery.map(s => ({label: kcTitle(s.kc_id), points: s.points})), '当前范围掌握估计');
  WenjinCharts.line($('independent-chart'), [{label: '首次有效独立作答通过率', points: result.independent}], '独立作答累计通过率');
  WenjinCharts.line($('assistance-chart'), [{label: '累计使用提示/辅助比例', points: result.assistance}], '累计辅助作答比例');
  WenjinCharts.bars($('kc-chart'), result.current_mastery.map(m => ({label: kcTitle(m.kc_id), value: m.p_mastery})), '当前范围掌握估计');
  $('growth-source').textContent = '来源：' + (projectId ? '项目局部证据' : '全部有效证据') + ' · ' + result.effective_count + ' 条 · 不包含写死成绩。';
  await loadCompetencies();
}

async function loadHarnesses() {
  if (!learner || !$('teaching-consent').checked) return;
  const result = await api('/api/learning/planner-harness/' + learner + (activeProjectId ? '?project_id=' + encodeURIComponent(activeProjectId) : ''));
  const box = $('harness-versions'); box.replaceChildren(); selectedHarnessId = null;
  if (!result.versions.length) { box.append(text('p', '暂无候选版本')); $('harness-preview').textContent = '尚未生成'; return; }
  for (const item of result.versions) { const row = document.createElement('button'); row.className = 'harness-version'; row.textContent = item.version_id.slice(0, 19) + ' · ' + item.status; row.onclick = () => { selectedHarnessId = item.version_id; $('harness-preview').textContent = item.skill_md; }; box.append(row); }
  box.querySelector('button')?.click();
}

async function generateHarness() {
  const body = {project_id: activeProjectId, subjective_feedback: $('planner-feedback').value, objective_evidence_refs: [], materials: lines($('planner-materials').value), standards: lines($('planner-standards').value)};
  const evidence = await api('/api/learning/evidence/' + learner); const refs = new Set((window.__wenjinProjects || []).find(item => item.record_id === activeProjectId)?.content?.evidence_refs || []);
  body.objective_evidence_refs = evidence.filter(item => !activeProjectId || refs.has(item.evidence_id)).slice(-20).map(item => item.evidence_id);
  const result = await api('/api/learning/planner-harness/' + learner, body); $('harness-preview').textContent = result.skill_md; selectedHarnessId = result.version_id; await loadHarnesses(); notice('已生成候选 Skill.md，等待审核');
}

async function reviewHarness(status) { if (!selectedHarnessId) throw Error('请先选择一个候选版本'); await api('/api/learning/planner-harness/' + learner + '/' + encodeURIComponent(selectedHarnessId) + '/review', {status}); await loadHarnesses(); notice(status === 'approved' ? '候选已审核通过' : status === 'rejected' ? '候选已退回修改' : '候选已回滚'); }

if ($('new-temporary-context')) $('new-temporary-context').onclick = run(startTemporaryContext);
$('close-temporary').onclick = run(closeTemporaryContext);
$('route-project').onclick = run(() => setProject(null));
$('route-temporary').onclick = run(startTemporaryContext);
$('growth-scope').onchange = run(async () => { await loadScopedGrowth(); });
$('generate-harness').onclick = run(generateHarness); $('refresh-harness').onclick = run(loadHarnesses);
$('approve-harness').onclick = run(() => reviewHarness('approved')); $('reject-harness').onclick = run(() => reviewHarness('rejected')); $('rollback-harness').onclick = run(() => reviewHarness('rolled_back'));

const originalUpdateOverview = updateOverview;
updateOverview = async function() { await originalUpdateOverview(); if (!learner || !$('teaching-consent').checked) return; const result = await api('/api/learning/projects/' + learner); window.__wenjinProjects = (result.projects || []).filter(item=>!item.content.archived); await loadContexts(); if (activeProjectId && !window.__wenjinProjects.some(item => item.record_id === activeProjectId)) activeProjectId = null; renderTodos((window.__wenjinProjects || []).find(item => item.record_id === activeProjectId)); bindProjectPicker(); await loadCompetencies(); };
const originalLoadEvidence = loadEvidence;
loadEvidence = async function() { await originalLoadEvidence(); await loadScopedGrowth(); };
document.addEventListener('click', event => { const button = event.target.closest('#tab-planner'); if (button) loadHarnesses().catch(error => notice(error.message, true)); });
$('study-open-plan').onclick = () => show('plan');
syncStudyMode();
