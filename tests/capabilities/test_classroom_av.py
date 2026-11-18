import json
import wave
import pytest
from deeptutor.learning import classroom_av as av


def page(id='a'):
    return {'id':id,'title':'方程讲解','content':'看这个等式。','kind':'slide','steps':[],
            'visual':{'title':'方程讲解','layout':'concept','content':'看这个等式。','panels':[{'heading':'检查','text':'代入两个未知数进行验证。'}], 'formula':r'2x+3y=8','notes':'','source_ids':[],'lines':[]}}


def test_speech_reads_math_and_keeps_formula_and_panels():
    text=av.script(page())
    assert '代入两个未知数' in text and '2x加3y等于8' in text
    text=av.speech(r'\begin{cases}\frac{1}{2}x+y=3\\x^2-y=-2\end{cases}')
    assert '2分之1' in text and '的平方' in text and '等于负2' in text
    assert '\\' not in text and 'begin' not in text
    assert av.chunks('甲。乙；丙。')==['甲。','乙；','丙。']


def test_shared_audio_cache_timeline_and_real_video_tracks(tmp_path,monkeypatch):
    calls=[]
    def local(parts,root,rate,check):
        calls.append(parts)
        for i,_ in enumerate(parts):
            check()
            with wave.open(str(root/f'raw-{i}.wav'),'wb') as f:
                f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(bytes(2400))
    monkeypatch.setattr(av,'local_segments',local)
    body={'kind':'voice','pages':[page('a'),page('b')],'indices':[3,4],'engine':'local','rate':1}
    voice=av.produce(body,tmp_path/'voice')
    # Identical spoken content across pages and media kinds is synthesized only once.
    assert len(calls)==1
    assert [x['index'] for x in voice['timeline']]==[3,4]
    assert voice['timeline'][1]['start']==voice['timeline'][0]['end']
    assert abs(av.duration(tmp_path/'voice/narration.wav')-voice['duration'])<.001
    video=av.produce(dict(body,kind='video'),tmp_path/'video')
    assert len(calls)==1 and video['duration']==voice['duration']
    assert (tmp_path/'video/lesson.mp4').stat().st_size>1000
    av.run([av.ffmpeg(),'-v','error','-i',str(tmp_path/'video/lesson.mp4'),'-map','0:v','-map','0:a','-f','null','-'])
    # Embedded subtitle track is readable; export does not rely on external VTT alone.
    av.run([av.ffmpeg(),'-v','error','-i',str(tmp_path/'video/lesson.mp4'),'-map','0:s:0','-f','webvtt',str(tmp_path/'embedded.vtt')])
    assert '方程讲解' in (tmp_path/'embedded.vtt').read_text(encoding='utf-8')
    # Completed parts survive a retry and don't require another speech request.
    again=av.produce(dict(body,kind='video'),tmp_path/'video')
    assert len(calls)==1 and again['duration']==video['duration']


def test_cancel_stops_before_any_synthesis(tmp_path,monkeypatch):
    calls=[];monkeypatch.setattr(av,'local_segments',lambda *a: calls.append(a))
    def cancelled(**p):raise av.Cancelled()
    with pytest.raises(av.Cancelled):av.produce({'kind':'voice','pages':[page()]},tmp_path,cancelled)
    assert calls==[]



def test_realistic_private_directory_does_not_exceed_windows_paths(tmp_path,monkeypatch):
    def local(parts,root,rate,check):
        for i,_ in enumerate(parts):
            with wave.open(str(root/f"raw-{i}.wav"),"wb") as audio:
                audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(24000);audio.writeframes(bytes(2400))
    monkeypatch.setattr(av,"local_segments",local)
    root=tmp_path/"assets"/("s"*64)/("c"*32)/("k"*32)
    value=av.produce({"kind":"voice","pages":[page()]},root)
    assert all((root/name).is_file() for name in value["files"])
    for path in (root.parent.parent/".audio-cache").rglob("*"):
        if path.is_file():assert path.stat().st_size>0



def test_video_emphasizes_panel_only_when_its_heading_is_spoken():
    p=page();cues=[[0,2,"方程讲解"],[2,3,"检查。"],[3,6,"代入两个未知数进行验证。"]]
    value=av.video_filter(p,cues,6)
    assert "drawbox=" in value and "between(t,2.000,6.000)" in value
    assert "drawbox=" not in av.video_filter(p,[[0,4,"没有对应讲解段落"]],4)



def test_cancel_terminates_active_subprocess():
    import sys,time
    calls=[]
    def check():
        calls.append(True)
        if len(calls)>1:raise av.Cancelled()
    started=time.monotonic()
    with pytest.raises(av.Cancelled):
        av.run([sys.executable,"-c","import time; time.sleep(30)"],check=check)
    assert time.monotonic()-started<3
