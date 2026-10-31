export type MediaBudgetRecord={budget_id:string;resource_id:string;workspace_id:string;enabled:boolean;revision:number;body:Record<string,unknown>;operations:{operation_id:string;slot:string;kind:string;status:string;reserved_microyuan:number;receipt:Record<string,unknown>|null}[]};
export type EffectStudy = {study_id:string;title:string;status:string;revision:number;protocol:Record<string,unknown>;protocol_hash:string};
export type EffectReport = EffectStudy & {cells:{arm:string;phase:string;participants:number;passed:number;submissions:number;wilson_95:[number,number]|null}[];limitations:string[]};
import type { ClassroomManifest } from './classroom-types';
import type { components } from './schema';

export type Account = components['schemas']['AccountOut'];
export type Workspace = components['schemas']['WorkspaceOut'];
export type StudentInput = components['schemas']['StudentIn'];
export type WorkspaceInput = components['schemas']['WorkspaceIn'];
export type LoginInput = components['schemas']['LoginIn'];
export type AgentRun = components['schemas']['RunOut'];
export type PlanDecision = components['schemas']['PlanDecision'];
export type AgentEvent = components['schemas']['EventOut'];
export type TutorRun = components['schemas']['TutorRunOut'];
export type TutorHistory = components['schemas']['TutorHistoryOut'];
export type ActivityRun = components['schemas']['ActivityRunOut'];
export type ActivityKind = 'roundtable' | 'teach';
export type TutorView = components['schemas']['TutorView'];
export type CatalogUnit = components['schemas']['CatalogUnit'];
export type ActivityContentInfo = components['schemas']['ActivityContentOut'];
export type ContentInfo = components['schemas']['ContentOut'];
export type AnswerReview = components['schemas']['ReviewOut'];
export type ReviewCard = components['schemas']['CardOut'];
export type RecallResult = components['schemas']['RecallResult'];
export type LearningInsights = components['schemas']['LearningInsights'];
export type ObservationRef = components['schemas']['ObservationRef'];
export type EvidenceReport = components['schemas']['EvidenceOut'];
export type EvidenceCategory = 'all' | 'answer' | 'checkpoint' | 'hint' | 'reflection' | 'activity' | 'self_report' | 'human_judgment';
export type EvidenceSource = 'all' | 'synthetic' | 'adult_test' | 'real_student';
export type EvolutionRun = components['schemas']['EvolutionOut'];
export type StrategyRun = components['schemas']['StrategyOut'];
export type StrategyStart = components['schemas']['StartStrategy'];
export type PlanningMaterial = components['schemas']['MaterialOut'];
export type MaterialInput = components['schemas']['MaterialIn'];
export type ActiveStrategy = components['schemas']['ActiveOut'];
export type SourceRef = components['schemas']['SourceRef'];
export type DevelopmentReview = components['schemas']['ReviewRecordOut'];
export type ReviewState = components['schemas']['ReviewStateOut'];
export type EvaluationIndex = components['schemas']['EvaluationIndexOut'];
export type EvaluationDetail = components['schemas']['EvaluationDetailOut'];
type WithoutGate<T> = T extends unknown ? Omit<T, 'gate_id' | 'expected_revision'> : never;
export type TutorCommand = WithoutGate<components['schemas']['AnswerInput'] | components['schemas']['HintInput'] | components['schemas']['ReflectInput'] | components['schemas']['NextInput']>;

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string) { super(message); }
}

function browserRole(): 'student' | 'admin' | undefined {
  const segment = window.location.pathname.split('/')[1];
  return segment === 'student' || segment === 'admin' ? segment : undefined;
}

function csrfToken(role: ReturnType<typeof browserRole>) {
  const name = role ? `wenjin_${role}_csrf` : 'wenjin_csrf';
  const cookie = document.cookie.split('; ').find(item => item.startsWith(name + '='));
  return cookie ? decodeURIComponent(cookie.slice(name.length + 1)) : '';
}

async function request<T>(path: string, options: RequestInit = {}, decode?: (response: Response) => Promise<T>): Promise<T> {
  const headers = new Headers(options.headers);
  const role = browserRole();
  if (role) headers.set('X-Wenjin-Client', role);
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (options.method && options.method !== 'GET') headers.set('X-CSRF-Token', csrfToken(role));
  let response: Response;
  try {
    response = await fetch('/api/v1' + path, { ...options, headers, credentials: 'same-origin' });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(0, 'network_error', '暂时连接不上服务，请检查网络后重试');
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null) as components['schemas']['ErrorOut'] | null;
    const problem = body?.error;
    if (response.status === 401 && path !== '/auth/me' && path !== '/auth/login') window.dispatchEvent(new Event('wenjin:expired'));
    throw new ApiError(response.status, problem?.code ?? 'request_failed', problem?.message ?? '请求未完成，请稍后重试', problem?.request_id);
  }
  if (response.status === 204) return undefined as T;
  if (decode) return decode(response);
  return response.json() as Promise<T>;
}

const json = (value: unknown) => JSON.stringify(value);

/** Streams committed messages/status, never unchecked model JSON or chain of thought. */
export async function watchTutor(run:TutorRun,signal:AbortSignal,changed:()=>void){
 const response=await fetch(`/api/v1/student/workspaces/${run.workspace_id}/tutoring/${run.run_id}/live`,{
  headers:{'X-Wenjin-Client':'student','Accept':'text/event-stream'},credentials:'same-origin',signal});
 if(!response.ok||!response.body)throw new ApiError(response.status,'live_unavailable','实时连接暂不可用，已有记录可继续刷新');
 const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
 try{while(!signal.aborted){const next=await reader.read();if(next.done)break;buffer+=decoder.decode(next.value,{stream:true});
  if(buffer.length>1_000_000)throw new Error('live_response_bounds');let end:number;
  while((end=buffer.indexOf('\n\n'))>=0){const event=buffer.slice(0,end);buffer=buffer.slice(end+2);if(event.startsWith('event: closed'))throw new Error('live_scope_changed');if(event.includes('event: snapshot'))changed();}
 }}finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
}

export const api = {
  readiness:(signal?:AbortSignal)=>request<Record<string,unknown>>('/admin/readiness',{signal}),
  exportPrivacy:()=>request<{data:Record<string,unknown>;sha256:string}>('/student/privacy/export'),
  requestErasure:()=>request<{request_id:string;status:string;notice:string}>('/student/privacy/erasure-request',{method:'POST',body:json({acknowledge_export_and_disable:true})}),
  erasureRequests:(signal?:AbortSignal)=>request<{items:{request_id:string;account_id:string;username:string;created_at:number;status:string}[];notice:string}>('/admin/privacy/erasure-requests',{signal}),
  studentClassroomJobs:(wid:string,bid:string,signal?:AbortSignal)=>request<{items:{job_id:string;status:string;error_code:string|null}[]}>(`/student/workspaces/${wid}/course-builds/${bid}/openmaic`,{signal}),
  startStudentClassroom:(wid:string,bid:string)=>request<{job_id:string;status:string}>(`/student/workspaces/${wid}/course-builds/${bid}/openmaic`,{method:'POST'}),
  cancelStudentClassroom:(wid:string,bid:string,jid:string)=>request<{job_id:string;status:string}>(`/student/workspaces/${wid}/course-builds/${bid}/openmaic/${jid}/cancel`,{method:'POST'}),
  mediaBudgets:(wid?:string,signal?:AbortSignal)=>request<{items:MediaBudgetRecord[]}>(wid?`/student/workspaces/${wid}/media-budgets`:'/admin/media-budgets',{signal}),
  proposeMediaBudget:(rid:string)=>request<MediaBudgetRecord>(`/admin/resources/${rid}/media-budget`,{method:'POST'}),
  authorizeMediaBudget:(bid:string,revision:number,enabled:boolean,ack:boolean)=>request<MediaBudgetRecord>(`/admin/media-budgets/${bid}`,{method:'PUT',body:json({expected_revision:revision,enabled,acknowledge_five_yuan_and_verified_prices:ack})}),
  startMedia:(wid:string,bid:string,kind:'image'|'audio'|'video',index:number)=>request<MediaBudgetRecord>(`/student/workspaces/${wid}/media-budgets/${bid}/jobs`,{method:'POST',body:json({kind,index})}),
  learningAutomation:(wid:string,signal?:AbortSignal)=>request<{enabled:boolean;revision:number;state:Record<string,unknown>;limits:Record<string,unknown>}>(`/student/workspaces/${wid}/learning-automation`,{signal}),
  configureLearningAutomation:(wid:string,enabled:boolean,revision:number)=>request<{enabled:boolean;revision:number}>(`/student/workspaces/${wid}/learning-automation`,{method:'PUT',body:json({enabled,expected_revision:revision})}),
  effectStudies:(signal?:AbortSignal)=>request<{items:EffectStudy[]}>('/admin/effect-studies',{signal}),
  createEffectStudy:(body:components['schemas']['StudyIn'])=>request<EffectStudy>('/admin/effect-studies',{method:'POST',body:json(body)}),
  freezeEffectStudy:(id:string,revision:number)=>request<EffectStudy>(`/admin/effect-studies/${id}/freeze`,{method:'POST',body:json({expected_revision:revision})}),
  effectStudyReport:(id:string,signal?:AbortSignal)=>request<EffectReport>(`/admin/effect-studies/${id}/report`,{signal}),
  enrollEffectStudy:(id:string,body:{workspace_id:string;baseline_id:string;candidate_id:string})=>request<Record<string,unknown>>(`/admin/effect-studies/${id}/enrollments`,{method:'POST',body:json(body)}),
  recordEffectObservation:(id:string,body:components['schemas']['ObservationIn'],key:string)=>request<Record<string,unknown>>(`/admin/effect-studies/${id}/observations`,{method:'POST',body:json(body),headers:{'Idempotency-Key':key}}),
  withdrawSkillTrial: (wid:string,rid:string)=>request<{status:string}>(`/student/workspaces/${wid}/skill-evolutions/${rid}/withdraw-trial`,{method:'POST'}),
  reviewMedia: (id:string,approved:boolean,teaching_purpose:string) => request(`/admin/media-assets/${id}/visual-review`,{method:'PUT',body:json({approved,text_correct:approved,math_consistent:approved,teaching_purpose})}),
  prepareClassroom: (wid:string) => request<{resource_id:string;math_verified:boolean;external_calls:number}>(`/student/workspaces/${wid}/classrooms/prepare`,{method:'POST'}),
  libraryList: (wid:string) => request<{items:{id:string;title:string;sha256:string;characters:number}[]}>(`/student/workspaces/${wid}/library`),
  libraryAdd: (wid:string,title:string,text:string) => request<{id:string}>(`/student/workspaces/${wid}/library`,{method:'POST',body:json({title,text})}),
  librarySearch: (wid:string,q:string) => request<{items:{document_id:string;title:string;sha256:string;start:number;end:number;text:string}[]}>(`/student/workspaces/${wid}/library/search?q=${encodeURIComponent(q)}`),
  libraryRead: (wid:string,id:string) => request<{title:string;text:string;sha256:string}>(`/student/workspaces/${wid}/library/${id}`),
  libraryDelete: (wid:string,id:string) => request(`/student/workspaces/${wid}/library/${id}`,{method:'DELETE'}),
  classroomProgress: (wid:string, rid:string, signal?:AbortSignal) => request<{revision:number;resource_hash:string;body:Record<string,unknown>|null}>(`/student/workspaces/${wid}/classrooms/${rid}/progress`,{signal}),
  saveClassroomProgress: (wid:string,rid:string,body:components['schemas']['Playback'],key:string) => request<{revision:number;resource_hash:string;body:Record<string,unknown>}>(`/student/workspaces/${wid}/classrooms/${rid}/progress`,{method:'PUT',body:json(body),headers:{'Idempotency-Key':key}}),
  nextAction: (wid: string, signal?: AbortSignal) => request<components['schemas']['NextAction']>(`/student/workspaces/${wid}/next-action`, { signal }),
  generationPolicy: (signal?: AbortSignal) => request<components['schemas']['PolicyOut']>('/admin/generation-policy', { signal }),
  publishGenerationPolicy: (body: components['schemas']['PolicyIn']) => request<components['schemas']['PolicyOut']>('/admin/generation-policy', { method: 'POST', body: json(body) }),
  courseBuilds: (wid: string, offset = 0, signal?: AbortSignal) => request<components['schemas']['BuildsOut']>(`/student/workspaces/${wid}/course-builds?offset=${offset}`, { signal }),
  startCourseBuild: (wid: string, topic: string, key: string, options?: { agent_mode: 'bounded_v1' | 'rational_agent_v2'; learning_goal: string; target_concepts: string[] }) => request<components['schemas']['BuildOut']>(`/student/workspaces/${wid}/course-builds`, { method: 'POST', headers: { 'Idempotency-Key': key }, body: json({ topic, ...options }) }),
  diagnoseCourse: (wid: string, row: components['schemas']['BuildOut'], answers: { answer: string; reasoning: string }[], finish=false) => request<components['schemas']['BuildOut']>(`/student/workspaces/${wid}/course-builds/${row.build_id}/diagnostic`, { method: 'POST', body: json({ expected_revision: row.revision, answers, finish_diagnostic:finish }) }),
  reviseCoursePlan: (wid: string, row: components['schemas']['BuildOut'], focus: 'basis' | 'representation' | 'verification') => request<components['schemas']['BuildOut']>(`/student/workspaces/${wid}/course-builds/${row.build_id}/plan`, { method: 'POST', body: json({ expected_revision: row.revision, focus }) }),
  generateCourse: (wid: string, row: components['schemas']['BuildOut'], key: string, action = 'generate') => request<components['schemas']['BuildOut']>(`/student/workspaces/${wid}/course-builds/${row.build_id}/${action}`, { method: 'POST', headers: { 'Idempotency-Key': key }, body: json({ expected_revision: row.revision }) }),
  cancelCourseBuild: (wid: string, row: components['schemas']['BuildOut']) => request<components['schemas']['BuildOut']>(`/student/workspaces/${wid}/course-builds/${row.build_id}/cancel`, { method: 'POST', body: json({ expected_revision: row.revision }) }),
  activateCourseBuild: (wid: string, row: components['schemas']['BuildOut'], courseRevision: number) => request<components['schemas']['CourseContext']>(`/student/workspaces/${wid}/course-builds/${row.build_id}/activate`, { method: 'POST', body: json({ expected_revision: row.revision, course_revision: courseRevision }) }),
  adminCourseBuilds: (offset = 0, signal?: AbortSignal) => request<components['schemas']['BuildsOut']>(`/admin/course-builds?offset=${offset}`, { signal }),
  reviewCourseBuild: (row: components['schemas']['BuildOut'], approve: boolean, note: string) => request<components['schemas']['BuildOut']>(`/admin/course-builds/${row.build_id}/review`, { method: 'POST', body: json({ expected_revision: row.revision, approve, note }) }),
  learningResources: (wid: string, signal?: AbortSignal) => request<{ items: { resource_id: string; unit_id: string; title: string; html: string; sha256: string; cards:{text:string}[]; classroom: ClassroomManifest | null }[] }>(`/student/workspaces/${wid}/resources`, { signal }),
  knowledgeSources: (signal?: AbortSignal) => request<{ knowledge_points: Record<string, { title: string }>; documents: Record<string, unknown>[] }>('/admin/knowledge-sources', { signal }),
  addKnowledgeSource: (body: components['schemas']['SourceIn']) => request<{ source_id: string; version: string }>('/admin/knowledge-sources', { method: 'POST', body: json(body) }),
  setKnowledgeSourceState: (id: string, revision: number, enabled: boolean) => request<{ revision: number; enabled: boolean }>(`/admin/knowledge-sources/${encodeURIComponent(id)}/state`, { method: 'POST', body: json({ expected_revision: revision, enabled }) }),
  importOpenmaicZip: (id: string, file: File) => request<{ resource_id: string; status: string }>(`/admin/course-builds/${id}/openmaic-import`, { method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: file }),
  reimportOpenmaic: (id: string) => request<{ job_id: string; status: string; model_call: boolean }>(`/admin/openmaic-jobs/${id}/reimport`, { method: 'POST' }),
  resourceCandidates: (offset = 0, signal?: AbortSignal, buildId?: string) => request<{ items: { resource_id: string; build_id: string; workspace_id: string; title?: string; status: string; revision: number; html: string; body: { title: string; generator: string } }[] }>(`/admin/resources?offset=${offset}${buildId ? "&build_id="+encodeURIComponent(buildId) : ""}`, { signal }),
  reviewResource: (id: string, revision: number, approve: boolean, note: string) => request<{ status: string }>(`/admin/resources/${id}/review`, { method: 'POST', body: json({ expected_revision: revision, approve, note }) }),
  startOpenMAIC: (bid: string) => request<{ job_id: string; status: string }>(`/admin/course-builds/${bid}/openmaic`, { method: 'POST' }),
  openmaicJobs: (signal?: AbortSignal) => request<{ items: { job_id: string; build_id: string; budget_id: string; status: string; remote_id: string | null; error_code: string | null }[]; budgets: Record<string, unknown>[] }>('/admin/openmaic-jobs', { signal }),
  cancelOpenmaic: (id: string) => request<{ status: string }>(`/admin/openmaic-jobs/${id}/cancel`, { method: 'POST' }),
  searchCourseSources: (wid: string, bid: string, key: string) => request<{ operation_id: string; status: string }>(`/student/workspaces/${wid}/course-builds/${bid}/search`, { method: 'POST', headers: { 'Idempotency-Key': key } }),
  courseSearches: (wid: string, bid: string, signal?: AbortSignal) => request<{ items: { operation_id: string; status: string; result: { results: { title: string; url: string; excerpt: string }[]; notice: string } | null; credits: number | null }[] }>(`/student/workspaces/${wid}/course-builds/${bid}/search`, { signal }),
  course: (wid: string, signal?: AbortSignal) => request<components['schemas']['CourseContext']>(`/student/workspaces/${wid}/course`, { signal }),
  bindCourse: (wid: string, body: components['schemas']['BindCourse']) => request<components['schemas']['CourseContext']>(`/student/workspaces/${wid}/course`, { method: 'POST', body: json(body) }),
  selectCourseUnit: (wid: string, body: components['schemas']['SelectUnit']) => request<components['schemas']['CourseContext']>(`/student/workspaces/${wid}/course/active-unit`, { method: 'POST', body: json(body) }),
  registerContent: (body: components['schemas']['GeneratedContentSpec']) => request<{ unit_id: string; version: string; status: string }>('/admin/dynamic-content', { method: 'POST', body: json(body) }),
  dynamicContent: (offset = 0, signal?: AbortSignal) => request<{ items: { unit_id: string; version: string; validation: { status: string }; status: string }[] }>(`/admin/dynamic-content?offset=${offset}`, { signal }),
  modelRuntime: (signal?: AbortSignal) => request<components['schemas']['ModelRuntimeOut']>('/admin/model-runtime', { signal }),
  publishModel: (body: components['schemas']['PublishModel']) => request<components['schemas']['ModelRuntimeOut']>('/admin/model-runtime/publish', { method: 'POST', body: json(body) }),
  pauseModel: (revision: number) => request<components['schemas']['ModelRuntimeOut']>('/admin/model-runtime/pause', { method: 'POST', body: json({ expected_revision: revision }) }),
  cancelModelTask: (run: TutorRun) => request<TutorRun>(`/student/workspaces/${run.workspace_id}/tutoring/${run.run_id}/model-operations/${run.coaching!.operation_id}/cancel`, { method: 'POST' }),
  planningMaterials: (admin: boolean, offset: number, signal?: AbortSignal) => request<components['schemas']['MaterialsOut']>(`/${admin ? 'admin' : 'student'}/planning-materials?offset=${offset}`, { signal }),
  addPlanningMaterial: (input: MaterialInput) => request<PlanningMaterial>('/admin/planning-materials', { method: 'POST', body: json(input) }),
  releasePlanningMaterial: (material: PlanningMaterial, enabled: boolean) => request<PlanningMaterial>(`/admin/planning-materials/${material.material_id}/release`, { method: 'POST', body: json({ expected_revision: material.revision, enabled, acknowledge_development_only: true }) }),
  evolutions: (wid: string|undefined,offset: number,signal?:AbortSignal)=>request<components['schemas']['EvolutionsOut']>(wid?`/student/workspaces/${wid}/skill-evolutions?offset=${offset}`:`/admin/skill-evolutions?offset=${offset}`,{signal}),
  evolution: (id:string,wid?:string,signal?:AbortSignal)=>request<EvolutionRun>(wid?`/student/workspaces/${wid}/skill-evolutions/${id}`:`/admin/skill-evolutions/${id}`,{signal}),
  startEvolution: (wid:string,key:string,mode:'offline'|'semantic'='offline',allowPersonalTrial=false)=>request<EvolutionRun>(`/student/workspaces/${wid}/skill-evolutions`,{method:'POST',body:json({mode,allow_personal_trial:allowPersonalTrial}),headers:{'Idempotency-Key':key}}),
  decideEvolution: (run:EvolutionRun,action:'approve'|'reject'|'withdraw'|'propose',note:string,key:string)=>request<EvolutionRun>(action==='propose'?`/student/workspaces/${run.workspace_id}/skill-evolutions/${run.run_id}/proposal`:`/admin/skill-evolutions/${run.run_id}/decision`,{method:'POST',body:json({expected_revision:run.revision,report_sha256:run.report_sha256,note,...(action==='propose'?{}:{action})}),headers:{'Idempotency-Key':key}}),
  cancelEvolution: (run:EvolutionRun)=>request<EvolutionRun>(`/student/workspaces/${run.workspace_id}/skill-evolutions/${run.run_id}/cancel`,{method:'POST',body:json({expected_revision:run.revision})}),
  evolutionFact: (run:EvolutionRun,fid:string,admin:boolean,signal?:AbortSignal)=>request<components['schemas']['FactOut']>(admin?`/admin/skill-evolutions/${run.run_id}/facts/${fid}`:`/student/workspaces/${run.workspace_id}/facts/${fid}`,{signal}),
  strategies: (wid: string | undefined, offset: number, signal?: AbortSignal) => request<components['schemas']['StrategiesOut']>(wid ? `/student/workspaces/${wid}/strategies?offset=${offset}` : `/admin/strategies?offset=${offset}`, { signal }),
  strategy: (rid: string, wid?: string, signal?: AbortSignal) => request<StrategyRun>(wid ? `/student/workspaces/${wid}/strategies/${rid}` : `/admin/strategies/${rid}`, { signal }),
  startStrategy: (wid: string, body: StrategyStart, key: string) => request<StrategyRun>(`/student/workspaces/${wid}/strategies`, { method: 'POST', body: json(body), headers: { 'Idempotency-Key': key } }),
  decideStrategy: (run: StrategyRun, action: 'approve' | 'reject' | 'confirm' | 'decline', note: string, key: string, admin = false) => request<StrategyRun>(admin ? `/admin/strategies/${run.run_id}/decision` : `/student/workspaces/${run.workspace_id}/strategies/${run.run_id}/decision`, { method: 'POST', body: json({ action, note, expected_revision: run.revision, gate_id: run.gate_id }), headers: { 'Idempotency-Key': key } }),
  cancelStrategy: (run: StrategyRun) => request<StrategyRun>(`/student/workspaces/${run.workspace_id}/strategies/${run.run_id}/cancel`, { method: 'POST', body: json({ expected_revision: run.revision }) }),
  activeStrategy: (wid: string, signal?: AbortSignal) => request<ActiveStrategy>(`/student/workspaces/${wid}/active-strategy`, { signal }),
  rollbackStrategy: (active: ActiveStrategy, target_version: string | null, note: string, key: string) => request<ActiveStrategy>(`/student/workspaces/${active.workspace_id}/active-strategy/rollback`, { method: 'POST', body: json({ target_version, note, expected_revision: active.revision }), headers: { 'Idempotency-Key': key } }),
  learningInsights: (wid: string | undefined, days: 14 | 30 | 90, source: EvidenceSource, includeArchived = true, signal?: AbortSignal, assistance='all') => request<LearningInsights>((wid ? `/student/workspaces/${wid}/learning-insights` : '/student/learning-insights') + `?days=${days}&source=${source}&include_archived=${includeArchived}&assistance=${encodeURIComponent(assistance)}`, { signal }),
  evidence: (wid: string | undefined, days: 14 | 30 | 90, source: EvidenceSource, record_kind: EvidenceCategory, offset: number, signal?: AbortSignal, includeArchived = true, assistance='all', onDay='') => request<EvidenceReport>((wid ? `/student/workspaces/${wid}/evidence` : '/student/evidence') + `?days=${days}&source=${source}&record_kind=${record_kind}&offset=${offset}&include_archived=${includeArchived}&assistance=${encodeURIComponent(assistance)}&${onDay?'on_day='+encodeURIComponent(onDay):''}`, { signal }),
  activityCatalog: (admin = false, signal?: AbortSignal, workspaceId?: string) => request<components['schemas']['ActivityCatalogOut']>(`/${admin ? 'admin' : 'student'}/activity-catalog${workspaceId ? '?workspace_id='+workspaceId : ''}`, { signal }),
  releaseActivityPack: (content: ActivityContentInfo, enabled: boolean) => request<ActivityContentInfo>(`/admin/activity-catalog/${encodeURIComponent(content.unit_id)}/development-release`, { method: 'POST', body: json({ content_version: content.content_version, expected_revision: content.revision, enabled, acknowledge_development_only: true }) }),
  activityContent: (admin = false, signal?: AbortSignal) => request<ContentInfo>(`/${admin ? 'admin' : 'student'}/activity-content`, { signal }),
  releaseActivityContent: (content: ContentInfo, enabled: boolean) => request<ContentInfo>('/admin/activity-content/development-release', { method: 'POST', body: json({ content_version: content.content_version, expected_revision: content.revision, enabled, acknowledge_development_only: true }) }),
  activities: (wid: string, kind: ActivityKind, offset = 0, signal?: AbortSignal) => request<components['schemas']['ActivityRunsOut']>(`/student/workspaces/${wid}/${kind}?offset=${offset}`, { signal }),
  startActivity: (wid: string, kind: ActivityKind, unit_id: string, key: string) => request<ActivityRun>(`/student/workspaces/${wid}/${kind}`, { method: 'POST', body: json({ unit_id }), headers: { 'Idempotency-Key': key } }),
  activityInput: (run: ActivityRun, command: { action: 'contribute' | 'refine' | 'conclude' | 'verify'; answer: string; reasoning: string; request_model?: boolean }, key: string) => request<ActivityRun>(`/student/workspaces/${run.workspace_id}/${run.kind}/${run.run_id}/inputs`, { method: 'POST', body: json({ ...command, gate_id: run.gate_id, expected_revision: run.revision }), headers: { 'Idempotency-Key': key } }),
  developmentReviews: (target: string, version: string, signal?: AbortSignal) => request<ReviewState>(`/admin/development-reviews?target_id=${encodeURIComponent(target)}&version=${version}`, { signal }),
  saveDevelopmentReview: (body: components['schemas']['ReviewInput']) => request<ReviewState>('/admin/development-reviews', { method: 'POST', body: json(body) }),
  evaluationComparison: (left: string, right: string, signal?: AbortSignal) => request<components['schemas']['EvaluationComparisonOut']>(`/admin/evaluation-comparison?left_id=${encodeURIComponent(left)}&right_id=${encodeURIComponent(right)}`, { signal }),
  evaluationReports: (signal?: AbortSignal) => request<EvaluationIndex>('/admin/evaluation-reports', { signal }),
  evaluationReport: (id: string, signal?: AbortSignal) => request<EvaluationDetail>(`/admin/evaluation-reports/${encodeURIComponent(id)}`, { signal }),
  cancelActivityModel: (run: ActivityRun) => request<ActivityRun>(`/student/workspaces/${run.workspace_id}/${run.kind}/${run.run_id}/model-operations/${run.coaching!.operation_id}/cancel`, { method: 'POST' }),
  cancelActivity: (run: ActivityRun) => request<ActivityRun>(`/student/workspaces/${run.workspace_id}/${run.kind}/${run.run_id}/cancel`, { method: 'POST', body: json({ expected_revision: run.revision }) }),
  reviewCards: (wid: string, status: 'all' | 'due' | 'scheduled', offset: number, signal?: AbortSignal) => request<components['schemas']['CardsOut']>(`/student/workspaces/${wid}/review-cards?status=${status}&offset=${offset}`, { signal }),
  reviewCard: (wid: string, cid: string) => request<ReviewCard>(`/student/workspaces/${wid}/review-cards/${cid}`),
  recall: (card: ReviewCard, answer: string, reasoning: string, key: string) => request<RecallResult>(`/student/workspaces/${card.workspace_id}/review-cards/${card.card_id}/attempts`, { method: 'POST', body: json({ answer, reasoning, expected_revision: card.revision }), headers: { 'Idempotency-Key': key } }),
  unverifiable: (wid: string, offset: number, signal?: AbortSignal) => request<components['schemas']['UnknownsOut']>(`/student/workspaces/${wid}/unverifiable-answers?offset=${offset}`, { signal }),
  memory: (wid?: string, offset = 0, signal?: AbortSignal) => request<components['schemas']['MemoryOut']>(wid ? `/student/workspaces/${wid}/memory` : `/student/memory?offset=${offset}`, { signal }),
  learningFact: (wid: string, fid: string, signal?: AbortSignal) => request<components['schemas']['FactOut']>(`/student/workspaces/${wid}/facts/${fid}`, { signal }),
  memoryNote: (wid: string, fact_id: string, text: string, key: string) => request<components['schemas']['NoteOut']>(`/student/workspaces/${wid}/memory-notes`, { method: 'POST', body: json({ fact_id, text }), headers: { 'Idempotency-Key': key } }),
  contentCatalog: (admin = false, signal?: AbortSignal, workspaceId?: string) => request<components['schemas']['CatalogOut']>(`/${admin ? 'admin' : 'student'}/content-catalog${workspaceId ? '?workspace_id='+workspaceId : ''}`, { signal }),
  releaseCatalogUnit: (content: CatalogUnit, enabled: boolean) => request<CatalogUnit>(`/admin/content-catalog/${encodeURIComponent(content.unit_id)}/development-release`, { method: 'POST', body: json({ content_version: content.content_version, expected_revision: content.revision, enabled, acknowledge_development_only: true }) }),
  content: (admin = false, signal?: AbortSignal) => request<ContentInfo>(`/${admin ? 'admin' : 'student'}/content`, { signal }),
  releaseContent: (content: ContentInfo, enabled: boolean) => request<ContentInfo>('/admin/content/development-release', { method: 'POST', body: json({ content_version: content.content_version, expected_revision: content.revision, enabled, acknowledge_development_only: true }) }),
  answerReviews: (status: 'pending' | 'resolved', offset: number, signal?: AbortSignal) => request<components['schemas']['ReviewsOut']>(`/admin/answer-reviews?status=${status}&offset=${offset}`, { signal }),
  resolveAnswer: (review: AnswerReview, conclusion: 'correct' | 'incorrect' | 'unresolved', note: string) => request<AnswerReview>(`/admin/answer-reviews/${review.review_id}/resolve`, { method: 'POST', body: json({ expected_revision: review.revision, conclusion, note }) }),
  tutoring: (wid: string, signal?: AbortSignal) => request<components['schemas']['TutorRunsOut']>(`/student/workspaces/${wid}/tutoring`, { signal }),
  startTutor: (wid: string, unit_id: string, key: string) => request<TutorRun>(`/student/workspaces/${wid}/tutoring`, { method: 'POST', body: json({ unit_id }), headers: { 'Idempotency-Key': key } }),
  tutorInput: (run: TutorRun, command: TutorCommand, key: string) => request<TutorRun>(`/student/workspaces/${run.workspace_id}/tutoring/${run.run_id}/inputs`, { method: 'POST', body: json({ ...command, gate_id: run.gate_id, expected_revision: run.revision }), headers: { 'Idempotency-Key': key } }),
  tutorHistory: (run: TutorRun, after: number, signal?: AbortSignal) => request<TutorHistory>(`/student/workspaces/${run.workspace_id}/tutoring/${run.run_id}/history?after=${after}`, { signal }),
  cancelTutor: (run: TutorRun) => request<TutorRun>(`/student/workspaces/${run.workspace_id}/tutoring/${run.run_id}/cancel`, { method: 'POST', body: json({ expected_revision: run.revision }) }),
  runs: (wid: string, signal?: AbortSignal) => request<components['schemas']['RunsOut']>(`/student/workspaces/${wid}/runs`, { signal }),
  startPlan: (wid: string, goal: string, daily_minutes: number, key: string) => request<AgentRun>(`/student/workspaces/${wid}/runs`, { method: 'POST', body: json({ goal, daily_minutes }), headers: { 'Idempotency-Key': key } }),
  decidePlan: (wid: string, rid: string, decision: PlanDecision, key: string) => request<AgentRun>(`/student/workspaces/${wid}/runs/${rid}/resume`, { method: 'POST', body: json(decision), headers: { 'Idempotency-Key': key } }),
  cancelRun: (run: AgentRun) => request<AgentRun>(`/student/workspaces/${run.workspace_id}/runs/${run.run_id}/cancel`, { method: 'POST', body: json({ expected_revision: run.revision }) }),
  runEvents: (run: Pick<AgentRun, 'workspace_id' | 'run_id'>, after: number, signal?: AbortSignal, kind: 'runs' | 'tutoring' | ActivityKind = 'runs') => request<AgentEvent[]>(`/student/workspaces/${run.workspace_id}/${kind}/${run.run_id}/events`, { signal, headers: { 'Last-Event-ID': String(after) } }, async response => {
    const text = await response.text();
    return text.split('\n\n').flatMap(packet => {
      const data = packet.split('\n').find(line => line.startsWith('data: '));
      if (!data) return [];
      const event = JSON.parse(data.slice(6)) as AgentEvent;
      if (event.run_id !== run.run_id || !Number.isInteger(event.seq) || event.seq <= after) throw new ApiError(0, 'event_invalid', '事件序列不符合当前运行范围，请刷新');
      return [event];
    });
  }),
  me: (signal?: AbortSignal) => request<components['schemas']['MeOut']>('/auth/me', { signal }),
  login: (input: LoginInput) => request<components['schemas']['AuthOut']>('/auth/login', { method: 'POST', body: json(input) }),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  students: (offset = 0, signal?: AbortSignal) => request<components['schemas']['StudentsOut']>(`/admin/students?offset=${offset}&limit=20`, { signal }),
  createStudent: (input: StudentInput) => request<Account>('/admin/students', { method: 'POST', body: json(input) }),
  resetPassword: (id: string, password: string) => request<Account>(`/admin/students/${encodeURIComponent(id)}/reset-password`, { method: 'POST', body: json({ password }) }),
  disableStudent: (id: string) => request<Account>(`/admin/students/${encodeURIComponent(id)}/disable`, { method: 'POST' }),
  async workspaces(signal?: AbortSignal) {
    const items: Workspace[] = [];
    for (let offset = 0; ; offset += 100) {
      const page = await request<components['schemas']['WorkspacesOut']>(`/student/workspaces?offset=${offset}&limit=100`, { signal });
      items.push(...page.items);
      if (page.items.length < 100) return [...new Map(items.map(item => [item.workspace_id, item])).values()];
    }
  },
  createWorkspace: (input: WorkspaceInput, key: string) => request<Workspace>('/student/workspaces', { method: 'POST', body: json(input), headers: { 'Idempotency-Key': key } }),
  renameWorkspace: (workspace: Workspace, title: string) => request<Workspace>(`/student/workspaces/${workspace.workspace_id}`, { method: 'PATCH', body: json({ title, expected_revision: workspace.revision }) }),
  changeStatus: (workspace: Workspace, action: 'archive' | 'restore') => request<Workspace>(`/student/workspaces/${workspace.workspace_id}/${action}`, { method: 'POST', body: json({ expected_revision: workspace.revision }) }),
};

export type { ClassroomManifest, ClassroomElement, ClassroomScene } from './classroom-types';
