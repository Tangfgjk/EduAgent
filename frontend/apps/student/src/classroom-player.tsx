import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Formula } from './math-formula';
import { api } from '@wenjin/api-client';
import { ErrorNotice, Loading } from '@wenjin/ui';
import {Link} from 'react-router-dom';
import './classroom.css';

import type { ClassroomManifest, ClassroomElement as Element } from '@wenjin/api-client';


export function ClassroomPlayer({ wid, resourceId, hash, manifest }: { wid: string; resourceId: string; hash: string; manifest: ClassroomManifest }) {
  const [sceneIndex,setScene] = useState(0), [step,setStep] = useState(0), [playing,setPlaying] = useState(false), [answer,setAnswer] = useState('');
  const [seconds,setSeconds] = useState(0), [localVoice,setVoice] = useState(false), [loaded,setLoaded] = useState(false);
  const [positions,setPositions] = useState<Record<string,number>>({});
  const [failedMedia,setFailedMedia] = useState<string[]>([]);
  const revision = useRef(0), hydrated = useRef(false), mediaRef=useRef<HTMLMediaElement|null>(null), root=useRef<HTMLElement>(null);
  const lastSaved=useRef(''), pendingSave=useRef('');
  const query = useQuery({ queryKey:['classroom-progress',wid,resourceId,hash], queryFn:({signal})=>api.classroomProgress(wid,resourceId,signal) });
  useEffect(()=>{ if (!query.data || hydrated.current) return; hydrated.current=true;revision.current=query.data.revision;
    const saved=query.data.body; if(saved&&query.data.resource_hash===hash){setScene(Math.min(manifest.scenes.length-1,Number(saved.scene)||0));setStep(Number(saved.action)||0);setSeconds(Number(saved.media_seconds)||0);setPositions((saved.media_positions??{}) as Record<string,number>);}setLoaded(true);
  },[query.data,hash,manifest.scenes.length]);
  const scene=manifest.scenes[sceneIndex], action=scene.actions[step];
  const signature=JSON.stringify([sceneIndex,step,seconds,positions]);
  const save=useMutation({mutationFn:(question?:{id:string;answer:string})=>api.saveClassroomProgress(wid,resourceId,{
    expected_revision:revision.current,resource_hash:hash,scene:sceneIndex,action:step,media_seconds:seconds,media_positions:positions,answer:'',...(question?{question_id:question.id,answer:question.answer}:{})
  },crypto.randomUUID()),onMutate:()=>{pendingSave.current=signature;},onSuccess:data=>{revision.current=data.revision;lastSaved.current=pendingSave.current;}});
  useEffect(()=>{ if(!loaded||save.isPending||save.isError||lastSaved.current===signature)return;const timer=window.setTimeout(()=>save.mutate(undefined),1000);return()=>window.clearTimeout(timer);},[signature,loaded,save.isPending,save.isError]);
  function advance(){ if(step+1<scene.actions.length)setStep(s=>s+1);else if(sceneIndex+1<manifest.scenes.length){setScene(s=>s+1);setStep(0);setSeconds(0);}else setPlaying(false); }
  useEffect(()=>{ if(!playing||action?.kind==='question')return;const timer=window.setTimeout(advance,Math.max(2500,(action?.text?.length??0)*180));return()=>window.clearTimeout(timer);},[playing,sceneIndex,step]);
  useEffect(()=>{ if(!localVoice||!playing||action?.kind!=='speech'||!('speechSynthesis' in window))return;
    const voice=window.speechSynthesis.getVoices().find(v=>v.localService&&v.lang.startsWith('zh'));
    if(!voice)return;const utterance=new SpeechSynthesisUtterance(action.text);utterance.voice=voice;window.speechSynthesis.speak(utterance);
    return()=>window.speechSynthesis.cancel();
  },[localVoice,playing,sceneIndex,step]);
  useEffect(()=>()=>{if('speechSynthesis' in window)window.speechSynthesis.cancel();},[]);
  useEffect(()=>{root.current?.querySelectorAll('audio,video').forEach(node=>(node as HTMLMediaElement).pause());setAnswer('');},[sceneIndex]);
  const visibility=new Map<string,boolean>();
  scene.actions.slice(0,step+1).forEach(a=>{if(a.element_id&&(a.kind==='hide'||a.kind==='show'))visibility.set(a.element_id,a.kind==='show');});
  function mediaElement(id:string,kind:string,src:string,captions=''){
    const positionKey=sceneIndex+':'+id;
    const props={controls:true,preload:'metadata',src,
      onLoadedMetadata:(event:React.SyntheticEvent<HTMLMediaElement>)=>{const node=event.currentTarget;node.currentTime=Math.min(positions[positionKey]??0,Number.isFinite(node.duration)?node.duration:0);mediaRef.current=node;},
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
      {e.kind==='latex'?<Formula text={e.text??''}/>:e.kind==='text'?e.text:e.kind==='audio'||e.kind==='video'?<><span>{e.captions}</span>{mediaElement(e.id,e.kind,e.src!,e.captions)}</>:null}
    </div></foreignObject>{action?.element_id===e.id&&<rect width={e.width} height={e.height} fill="none" stroke="#d79b35" strokeWidth={4} pointerEvents="none"/>}</g>;
  }
  if(query.isPending)return <Loading text="恢复课堂进度…"/>;
  if(query.isError)return <ErrorNotice error={query.error} retry={()=>{void query.refetch();}}/>;
  return <section ref={root} className="classroom-player" aria-label="互动课堂播放器"><div className="classroom-controls"><strong>{manifest.title}</strong><span>场景 {sceneIndex+1}/{manifest.scenes.length}</span><button className="button small" disabled={!loaded} onClick={()=>{setPlaying(!playing);if(playing)root.current?.querySelectorAll('audio,video').forEach(node=>(node as HTMLMediaElement).pause());}}>{playing?'暂停':'播放课堂'}</button><button className="button small" disabled={playing} onClick={advance}>下一步</button><button className="button small" disabled={save.isPending} onClick={()=>save.mutate(undefined)}>保存进度</button><span role="status">{save.isPending?'保存中':save.isSuccess?'进度已保存':''}</span></div>
    <div className="classroom-layout"><nav aria-label="课堂场景">{manifest.scenes.map((s,i)=><button className="button small" key={s.id} aria-current={i===sceneIndex?'step':undefined} onClick={()=>{setPlaying(false);setScene(i);setStep(0);setSeconds(0);}}>{i+1}. {s.title}</button>)}</nav><div><svg role="img" aria-label={scene.title} viewBox={`0 0 ${scene.width} ${scene.height}`} style={{background:scene.background}}>{scene.elements.map(element)}</svg><div className="classroom-caption" aria-live="polite">{action?.text??'按下一步查看讲解；可以在场景列表中切换。'}</div>
      {action?.kind==='question'&&<form onSubmit={event=>{event.preventDefault();setPlaying(false);save.mutate({id:action.id!,answer});}}><label>课堂互动回答<input value={answer} maxLength={1500} required onChange={event=>setAnswer(event.target.value)}/></label><button className="button" disabled={save.isPending}>检查本次回答</button></form>}
      {!!(save.data?.body?.check??query.data?.body?.check)&&<><p role="status">课堂数值核验：{String(((save.data?.body?.check??query.data?.body?.check) as {status:string}).status)}；不计入独立检测或掌握证明。</p><Link className="button" to={`/study?workspace=${wid}`}>回到当前课程练习，核对自己的方法</Link></>}
      {scene.media?.map(asset=><figure key={asset.id}>{asset.kind==='image'?<img src={asset.src} alt={asset.alt} style={{width:'100%',maxHeight:400,objectFit:'contain'}}/>:mediaElement(asset.id,asset.kind,asset.src,asset.captions)}<figcaption>{asset.captions}</figcaption></figure>)}
      <details><summary>无障碍文字与全部讲解</summary><h3>{scene.title}</h3>{scene.elements.filter(e=>e.text).map(e=><p key={e.id}>{e.kind==='latex'?<Formula text={e.text!}/>:e.text}</p>)}{scene.actions.filter(a=>a.kind==='speech').map((a,i)=><p key={i}>{a.text}</p>)}</details>
    </div></div><label className="inline"><input type="checkbox" checked={localVoice} onChange={event=>setVoice(event.target.checked)}/>使用本机语音朗读（需要设备支持中文音色）</label><ErrorNotice error={save.error}/>
    {!!manifest.unsupported.length&&<p className="notice">部分素材暂不可用，你仍可阅读文字与公式。</p>}
    <p className="card-note">独立检测期间请独立完成作答，课堂辅助将暂时关闭。</p>
  </section>;
}
