import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Formula, MathText } from './math-formula';
import { api } from '@wenjin/api-client';
import { ErrorNotice, Loading } from '@wenjin/ui';
import {Link} from 'react-router-dom';
import './classroom.css';

import type { ClassroomManifest, ClassroomElement as Element } from '@wenjin/api-client';


export function ClassroomPlayer({ wid, resourceId, hash, manifest }: { wid: string; resourceId: string; hash: string; manifest: ClassroomManifest }) {
  const [sceneIndex,setScene] = useState(0), [step,setStep] = useState(0), [playing,setPlaying] = useState(false), [answer,setAnswer] = useState('');
  const [seconds,setSeconds] = useState(0), [localVoice,setVoice] = useState(false), [loaded,setLoaded] = useState(false);
  const [voiceAvailable,setVoiceAvailable]=useState(false), [fullscreenError,setFullscreenError]=useState('');
  useEffect(()=>{if(!('speechSynthesis' in window))return;const refresh=()=>setVoiceAvailable(window.speechSynthesis.getVoices().some(v=>v.localService&&v.lang.startsWith('zh')));refresh();window.speechSynthesis.addEventListener('voiceschanged',refresh);return()=>window.speechSynthesis.removeEventListener('voiceschanged',refresh);},[]);
  const [positions,setPositions] = useState<Record<string,number>>({});
  const [failedMedia,setFailedMedia] = useState<string[]>([]);
  const revision = useRef(0), hydrated = useRef(false), mediaRef=useRef<HTMLMediaElement|null>(null), root=useRef<HTMLElement>(null);
  const lastSaved=useRef(''), pendingSave=useRef('');
  const query = useQuery({ queryKey:['classroom-progress',wid,resourceId,hash], queryFn:({signal})=>api.classroomProgress(wid,resourceId,signal) });
  useEffect(()=>{ if (!query.data || hydrated.current) return; hydrated.current=true;revision.current=query.data.revision;
    const saved=query.data.body; if(saved&&query.data.resource_hash===hash){setScene(Math.min(manifest.scenes.length-1,Number(saved.scene)||0));setStep(Math.max(0,Math.min(manifest.scenes[Math.min(manifest.scenes.length-1,Number(saved.scene)||0)].actions.length-1,Number(saved.action)||0)));setSeconds(Number(saved.media_seconds)||0);setPositions((saved.media_positions??{}) as Record<string,number>);}setLoaded(true);
  },[query.data,hash,manifest.scenes.length]);
  const scene=manifest.scenes[sceneIndex], action=scene.actions[step];
  const speechAsset=action?.kind==='speech'?scene.media?.find(m=>m.kind==='audio'&&m.captions.trim()===action.text?.trim()):undefined;
  const signature=JSON.stringify([sceneIndex,step,seconds,positions]);
  const save=useMutation({mutationFn:(question?:{id:string;answer:string})=>api.saveClassroomProgress(wid,resourceId,{
    expected_revision:revision.current,resource_hash:hash,scene:sceneIndex,action:step,media_seconds:seconds,media_positions:positions,answer:'',...(question?{question_id:question.id,answer:question.answer}:{})
  },crypto.randomUUID()),onMutate:()=>{pendingSave.current=signature;},onSuccess:data=>{revision.current=data.revision;lastSaved.current=pendingSave.current;}});
  useEffect(()=>{ if(!loaded||save.isPending||save.isError||lastSaved.current===signature)return;const timer=window.setTimeout(()=>save.mutate(undefined),1000);return()=>window.clearTimeout(timer);},[signature,loaded,save.isPending,save.isError]);
  function advance(){ if(step+1<scene.actions.length)setStep(s=>s+1);else if(sceneIndex+1<manifest.scenes.length){setScene(s=>s+1);setStep(0);setSeconds(0);}else setPlaying(false); }
  useEffect(()=>{ if(!playing||action?.kind==='question'||speechAsset||(localVoice&&voiceAvailable&&action?.kind==='speech'))return;const timer=window.setTimeout(advance,action?.kind==='speech'?Math.max(3500,(action?.text?.length??0)*220):350);return()=>window.clearTimeout(timer);},[playing,sceneIndex,step,localVoice,voiceAvailable,speechAsset?.id]);
  useEffect(()=>{ if(speechAsset||!localVoice||!playing||action?.kind!=='speech'||!('speechSynthesis' in window))return;
    const voice=window.speechSynthesis.getVoices().find(v=>v.localService&&v.lang.startsWith('zh'));
    if(!voice)return;const utterance=new SpeechSynthesisUtterance(action.text);utterance.voice=voice;utterance.onend=()=>advance();utterance.onerror=()=>setPlaying(false);window.speechSynthesis.speak(utterance);
    return()=>window.speechSynthesis.cancel();
  },[localVoice,voiceAvailable,playing,sceneIndex,step,speechAsset?.id]);
  useEffect(()=>{if(!playing||!speechAsset)return;const audio=new Audio(speechAsset.src);audio.onended=advance;audio.onerror=()=>{setPlaying(false);setFailedMedia(old=>[...old,speechAsset.id]);};void audio.play().catch(()=>setPlaying(false));return()=>{audio.pause();audio.onended=null;audio.src='';};},[playing,sceneIndex,step,speechAsset?.id]);
  useEffect(()=>()=>{if('speechSynthesis' in window)window.speechSynthesis.cancel();},[]);
  useEffect(()=>{root.current?.querySelectorAll('audio,video').forEach(node=>(node as HTMLMediaElement).pause());setAnswer('');},[sceneIndex]);
  const visibility=new Map<string,boolean>();
  scene.actions.filter(a=>a.kind==='show'&&a.element_id).forEach(a=>visibility.set(a.element_id!,false));
  scene.actions.slice(0,step+1).forEach(a=>{if(a.element_id&&(a.kind==='hide'||a.kind==='show'))visibility.set(a.element_id,a.kind==='show');});
  function mediaElement(id:string,kind:string,src:string,captions=''){
    const positionKey=sceneIndex+':'+id;
    const props={controls:true,preload:'metadata',src,
      onLoadedMetadata:(event:React.SyntheticEvent<HTMLMediaElement>)=>{const node=event.currentTarget;node.currentTime=Math.min(positions[positionKey]??0,Number.isFinite(node.duration)?node.duration:0);mediaRef.current=node;},
      onPlay:()=>setPlaying(false),
      onPause:(event:React.SyntheticEvent<HTMLMediaElement>)=>{const value=event.currentTarget.currentTime;setPositions(old=>({...old,[positionKey]:value}));},
      onTimeUpdate:(event:React.SyntheticEvent<HTMLMediaElement>)=>{if(event.currentTarget.paused||!loaded)return;const value=Math.floor(event.currentTarget.currentTime/5)*5;setPositions(old=>old[positionKey]===value?old:{...old,[positionKey]:value});},
      onError:()=>setFailedMedia(old=>old.includes(id)?old:[...old,id])};
    return <>{kind==='video'?<video {...props} style={{width:'100%'}}/>:<audio {...props}/>} {failedMedia.includes(id)&&<p role="status">媒体暂不可播放；可继续阅读：{captions||'本场景的文字讲解与公式'}。其他课堂内容仍可使用。</p>}</>;
  }
  function element(e:Element){
    if(visibility.get(e.id)===false)return null;
    const transform=`translate(${e.left} ${e.top}) rotate(${e.rotation} ${e.width/2} ${e.height/2})`;
    if(e.kind==='shape')return <g key={e.id} transform={transform}>{e.path?<svg width={e.width} height={e.height} viewBox={`0 0 ${e.view_box?.[0]??1} ${e.view_box?.[1]??1}`}><path d={e.path} fill={e.fill}/></svg>:<rect width={e.width} height={e.height} fill={e.fill}/>}</g>;
    if(e.kind==='image')return <image key={e.id} transform={transform} width={e.width} height={e.height} href={e.src} aria-label={e.alt}/>;
    return <g key={e.id} transform={transform}><foreignObject width={e.width} height={e.height}><div className="classroom-element" style={{fontSize:e.font_size??20,color:e.color}}>
      {e.kind==='latex'?<Formula text={e.text??''}/>:e.kind==='text'?(e.runs?.length?<div>{e.runs.map((r,i)=><span key={i} style={{fontSize:r.size,color:r.color,fontWeight:r.bold?700:undefined,fontStyle:r.italic?'italic':undefined,display:r.align?'block':undefined,textAlign:r.align}}><MathText text={r.text}/></span>)}</div>:<MathText text={e.text??''}/>):e.kind==='audio'||e.kind==='video'?<><span>{e.captions}</span>{mediaElement(e.id,e.kind,e.src!,e.captions)}</>:null}
    </div></foreignObject>{action?.element_id===e.id&&<rect width={e.width} height={e.height} fill="none" stroke="#d79b35" strokeWidth={4} pointerEvents="none"/>}</g>;
  }
  if(query.isPending)return <Loading text="恢复课堂进度…"/>;
  if(query.isError)return <ErrorNotice error={query.error} retry={()=>{void query.refetch();}}/>;
  return <section ref={root} className="classroom-player" aria-label="互动课堂播放器"><div className="classroom-controls"><strong>{manifest.title}</strong><span aria-label="当前课堂页">第 {sceneIndex+1} / {manifest.scenes.length} 页 · 步骤 {Math.min(step+1,scene.actions.length)} / {scene.actions.length}</span><button className="button small" onClick={()=>{const el=root.current;if(document.fullscreenElement){void document.exitFullscreen();}else if(el?.requestFullscreen){void el.requestFullscreen().catch(()=>setFullscreenError('当前浏览器不支持全屏，请使用窗口最大化。'));}else setFullscreenError('当前浏览器不支持全屏。');}}>全屏课堂</button><button className="button small" disabled={!sceneIndex} onClick={()=>{setPlaying(false);setScene(i=>i-1);setStep(0);}}>上一页</button><button className="button small" disabled={sceneIndex>=manifest.scenes.length-1} onClick={()=>{setPlaying(false);setScene(i=>i+1);setStep(0);}}>下一页</button><button className="button small" disabled={!loaded} onClick={()=>{setPlaying(!playing);if(playing)root.current?.querySelectorAll('audio,video').forEach(node=>(node as HTMLMediaElement).pause());}}>{playing?'暂停':'播放课堂'}</button><button className="button small" disabled={playing} onClick={advance}>下一步</button><button className="button small" disabled={save.isPending} onClick={()=>save.mutate(undefined)}>保存进度</button><span role="status">{save.isPending?'保存中':save.isSuccess?'进度已保存':''}</span></div>
    <div className="classroom-layout"><nav aria-label="课堂场景">{manifest.scenes.map((s,i)=><button className="button small" key={s.id} aria-label={`${i+1}. ${s.title}`} aria-current={i===sceneIndex?'step':undefined} onClick={()=>{setPlaying(false);setScene(i);setStep(0);setSeconds(0);}}><span className="slide-thumbnail" aria-hidden="true"><b>{i+1}</b><small>{s.title}</small></span><span>{s.title}</span></button>)}</nav><div className="classroom-stage"><div className={scene.media?.length?'classroom-slide-frame with-media':'classroom-slide-frame'}><svg role="img" aria-label={scene.title} viewBox={`0 0 ${scene.width} ${scene.height}`} style={{background:scene.background}}>{scene.elements.map(element)}</svg>      <div className="classroom-media-stage">{scene.media?.map(asset=><figure key={asset.id}>{asset.kind==='image'?<img src={asset.src} alt={asset.alt} style={{width:'100%',maxHeight:400,objectFit:'contain'}}/>:mediaElement(asset.id,asset.kind,asset.src,asset.captions)}<figcaption>{asset.captions}</figcaption></figure>)}</div></div><div className="mobile-slide-reading">{scene.elements.filter(e=>e.kind==='latex'&&visibility.get(e.id)!==false).map(e=><Formula key={e.id} text={e.text??''}/>)}</div><div className="classroom-caption" aria-live="polite">{action?.text?<MathText text={action.text}/>:'按下一步查看讲解；可以在场景列表中切换。'}</div>
      {action?.kind==='question'&&<form onSubmit={event=>{event.preventDefault();setPlaying(false);save.mutate({id:action.id!,answer});}}><label className="field">课堂互动回答<input value={answer} maxLength={1500} required onChange={event=>setAnswer(event.target.value)}/></label><button className="button" disabled={save.isPending}>检查本次回答</button><button type="button" className="button" onClick={()=>{setPlaying(false);setScene(i=>Math.max(0,i-1));setStep(0);}}>请求提示：回看逐步演示</button></form>}
      {!!(save.data?.body?.check??query.data?.body?.check)&&((save.data?.body?.check??query.data?.body?.check) as {scene:number;question_id:string}).scene===sceneIndex&&((save.data?.body?.check??query.data?.body?.check) as {question_id:string}).question_id===action?.id&&<><p role="status">课堂数值核验：{({passed:'数值符合参考',failed:'再检查一下运算',unverifiable:'尚不能确定，请明确最终数值'} as Record<string,string>)[String(((save.data?.body?.check??query.data?.body?.check) as {status:string}).status)]??'待核验'}；不计入独立检测或掌握证明。</p><Link className="button" to={`/review?workspace=${wid}`}>查看本项目复习安排</Link><Link className="button" to={`/study?workspace=${wid}`} >回到当前课程练习，核对自己的方法</Link></>}

      <details><summary>无障碍文字与全部讲解</summary><h3>{scene.title}</h3>{scene.elements.filter(e=>e.text).map(e=><p key={e.id}>{e.kind==='latex'?<Formula text={e.text!}/>:<MathText text={e.text!}/>}</p>)}{scene.actions.filter(a=>a.kind==='speech').map((a,i)=><p key={i}><MathText text={a.text??''}/></p>)}</details>
    </div></div><label className="inline"><input type="checkbox" checked={localVoice} onChange={event=>setVoice(event.target.checked)}/>使用本机语音朗读（需要设备支持中文音色）</label><ErrorNotice error={save.error}/>
    {fullscreenError&&<p role="status">{fullscreenError}</p>}{localVoice&&!voiceAvailable&&<p role="status">未找到本机中文音色，使用文字阅读节奏；不调用云端配音。</p>}
    {!!manifest.quarantined_media&&<p className="notice">已隔离 {manifest.quarantined_media} 份未经视觉与教学检查的补充素材，正文与公式仍可使用。</p>}
    {!!scene.layout_issues?.length&&<p className="notice">此页可能存在排版问题，可展开无障碍文字阅读完整内容。</p>}
    {!!manifest.unsupported.length&&<p className="notice">部分素材暂不可用，你仍可阅读文字与公式。</p>}
    <p className="card-note">独立检测期间请独立完成作答，课堂辅助将暂时关闭。</p>
  </section>;
}
