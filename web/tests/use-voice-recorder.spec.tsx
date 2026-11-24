import {act, cleanup, renderHook, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {useVoiceRecorder} from "@/hooks/useVoiceRecorder";
const {api}=vi.hoisted(()=>({api:vi.fn()}));
vi.mock("@/lib/api",()=>({apiFetch:api,apiUrl:(s:string)=>s}));
class Recorder {
  static instances: Recorder[]=[];
  static isTypeSupported=()=>true;
  state="inactive";mimeType="audio/webm;codecs=opus";
  ondataavailable: ((event:{data:Blob})=>void)|null=null;
  onstop: (()=>void)|null=null;
  onerror: (()=>void)|null=null;
  constructor(){Recorder.instances.push(this)}
  start(){this.state="recording"}
  stop(){this.state="inactive";this.ondataavailable?.({data:new Blob(["audio"])});this.onstop?.()}
}
let stopTrack: ReturnType<typeof vi.fn>;
let getMedia: ReturnType<typeof vi.fn>;
beforeEach(()=>{
 stopTrack=vi.fn();getMedia=vi.fn(async()=>({getTracks:()=>[{stop:stopTrack}]}));
 Object.defineProperty(navigator,"mediaDevices",{configurable:true,value:{getUserMedia:getMedia}});
 Object.defineProperty(window,"isSecureContext",{configurable:true,value:true});
 vi.stubGlobal("MediaRecorder",Recorder);Recorder.instances=[];
 api.mockReset();api.mockImplementation(async(_url:string,init?:RequestInit)=>({ok:true,json:async()=>init?.method==='POST'?{text:'我想学习一元一次方程'}:{stt:{available:true}}}));
});
afterEach(()=>{cleanup();vi.unstubAllGlobals()});
it('explains the HTTPS requirement before asking for microphone permission',async()=>{
 Object.defineProperty(window,"isSecureContext",{configurable:true,value:false});
 const {result}=renderHook(()=>useVoiceRecorder(vi.fn()));await act(async()=>{await result.current.start()});
 expect(result.current.error).toContain('HTTPS');expect(getMedia).not.toHaveBeenCalled();
});
it('records, posts the clip, and puts editable text into the composer',async()=>{
 const transcript=vi.fn();const {result}=renderHook(()=>useVoiceRecorder(transcript));
 await act(async()=>{await result.current.start()});expect(result.current.state).toBe('recording');
 act(()=>result.current.stop());await waitFor(()=>expect(transcript).toHaveBeenCalledWith('我想学习一元一次方程'));
 expect(stopTrack).toHaveBeenCalled();expect(result.current.state).toBe('idle');
 expect(api.mock.calls.some(([_url,init])=>init?.method==='POST')).toBe(true);
});
it('discards an unfinished clip on navigation instead of uploading it',async()=>{
 const {result,unmount}=renderHook(()=>useVoiceRecorder(vi.fn()));await act(async()=>{await result.current.start()});
 unmount();await Promise.resolve();expect(stopTrack).toHaveBeenCalled();expect(api.mock.calls.some(([_url,init])=>init?.method==='POST')).toBe(false);
});
it('does not open two microphones while a permission request is pending',async()=>{
 let finish: ((stream:MediaStream)=>void)|undefined;
 getMedia.mockImplementation(()=>new Promise(resolve=>{finish=resolve}));
 const {result}=renderHook(()=>useVoiceRecorder(vi.fn()));let first:Promise<void>;
 act(()=>{first=result.current.start();void result.current.start()});
 await waitFor(()=>expect(getMedia).toHaveBeenCalledTimes(1));
 await act(async()=>{finish?.({getTracks:()=>[{stop:stopTrack}]} as unknown as MediaStream);await first});
 expect(Recorder.instances).toHaveLength(1);
});
it('does not request microphone access when speech recognition is unavailable',async()=>{
 api.mockResolvedValue({ok:true,json:async()=>({stt:{available:false}})});
 const {result}=renderHook(()=>useVoiceRecorder(vi.fn()));await act(async()=>{await result.current.start()});
 expect(result.current.error).toContain('语音识别');expect(getMedia).not.toHaveBeenCalled();
});
