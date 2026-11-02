import {useEffect,useRef,useState} from 'react';
import {useMutation,useQuery,useQueryClient} from '@tanstack/react-query';
import {api,type Workspace} from '@wenjin/api-client';
import {ErrorNotice,Heading,Loading,Modal} from '@wenjin/ui';
import {FileText,Plus,Search} from 'lucide-react';
import {MathText} from './math-formula';
import './library.css';

type Document={title:string;text:string;sha256:string};
type Editor={id?:string;original?:Document};
const sourceSlice=(text:string,start:number,end?:number)=>Array.from(text).slice(start,end).join('');
function Reading({text}:{text:string}){
  // Limited Markdown; React escapes prose and no remote embeds or HTML execute.
  return <div className="library-prose">{text.split('\n').map((line,i)=>{const h=/^#{1,3}\s+(.+)$/.exec(line);return h?<h3 key={i}><MathText text={h[1]}/></h3>:<p key={i}><MathText text={line||'\u00a0'}/></p>;})}</div>;
}
export function LibraryPage({workspace}:{workspace:Workspace}){
  const wid=workspace.workspace_id,client=useQueryClient(),writable=workspace.status==='active';
  const [q,setQ]=useState(''),[submitted,setSubmitted]=useState(''),[selected,setSelected]=useState(''),[editor,setEditor]=useState<Editor|null>(null),[notice,setNotice]=useState(''),[raw,setRaw]=useState(false);
  const [match,setMatch]=useState<{start:number;end:number;sha:string}|null>(null),matchRef=useRef<HTMLElement>(null);
  const list=useQuery({queryKey:['local-library',wid],queryFn:()=>api.libraryList(wid)});
  const doc=useQuery({queryKey:['local-document',wid,selected],enabled:!!selected,queryFn:()=>api.libraryRead(wid,selected),gcTime:0});
  const search=useQuery({queryKey:['local-search',wid,submitted],queryFn:()=>api.librarySearch(wid,submitted),enabled:!!submitted});
  useEffect(()=>{if(match&&!doc.isFetching)matchRef.current?.scrollIntoView({block:'center'});},[match,doc.isFetching]);
  function refresh(){for(const key of ['local-library','local-search','local-document'])void client.invalidateQueries({queryKey:[key,wid]});}
  function open(id:string,hit?:{start:number;end:number;sha256:string}){setRaw(false);setMatch(hit?{start:hit.start,end:hit.end,sha:hit.sha256}:null);setSelected(id);}
  const remove=useMutation({mutationFn:(id:string)=>api.libraryDelete(wid,id),onSuccess:()=>{setSelected('');setNotice('资料已删除。');refresh();}});
  return <div className="library-page"><Heading title="我的本地知识库" kicker="READ, FIND, AND TRACE" description="收藏笔记，查找原文，让每一次学习有据可循。" actions={<button className="button primary" disabled={!writable} onClick={()=>setEditor({})}><Plus size={16}/>添加资料</button>}/>
    <p className="card-note">仅当前项目可见 · 保存在本机服务 · 不发送给外部模型</p>
    <section className="card library-list"><form className="library-toolbar" onSubmit={e=>{e.preventDefault();setSubmitted(q.trim());}}><label className="search-field"><Search size={18}/><input aria-label="检索关键词" maxLength={160} value={q} onChange={e=>{setQ(e.target.value);setSubmitted('');}} placeholder="搜索标题或正文"/></label><button className="button" disabled={!q.trim()}>本地检索</button>{q&&<button type="button" className="button text" onClick={()=>{setQ('');setSubmitted('');}}>清除</button>}</form>
      {notice&&<p role="status" className="notice">{notice}</p>}<ErrorNotice error={list.error}/><ErrorNotice error={remove.error}/>
      {submitted?<><ErrorNotice error={search.error}/>{search.isFetching?<Loading text="正在查找资料…"/>:search.data?.items.length?search.data.items.map(hit=><article className="library-result" key={hit.document_id}><h2>{hit.title}</h2><p className="library-excerpt">{hit.text}</p><button className="button small" onClick={()=>open(hit.document_id,hit)}>查看来源全文</button></article>):search.isSuccess&&<p role="status" className="library-empty">没有找到相关原文。试试标题中的词，或换一个关键词。</p>}</>:
      list.isPending?<Loading/>:list.data?.items.length?<><p className="card-note">本项目资料 · {list.data.items.length} 份</p>{list.data.items.map(d=><div className="library-row" key={d.id}><FileText size={22}/><button className="library-document" onClick={()=>open(d.id)}><strong>{d.title}</strong><small>{d.characters.toLocaleString()} 字</small></button><button className="button small" disabled={!writable||remove.isPending} onClick={()=>{if(window.confirm(`删除「${d.title}」？此操作无法撤销。`))remove.mutate(d.id);}}>删除</button></div>)}</>:list.isSuccess&&<div className="library-empty"><FileText size={32}/><h2>从一份笔记开始</h2><p>添加课堂笔记或参考资料，之后可以随时查找和阅读。</p><button className="button" disabled={!writable} onClick={()=>setEditor({})}>添加第一份资料</button></div>}
    </section><details className="library-help"><summary>支持的文件与使用说明</summary><p>可粘贴文字或导入400KB以内的UTF-8 .txt / .md，每份最多10万字，每空间最多100份。暂不支持PDF、图片OCR和模型问答。资料未经专业核验，不作为参考答案；独立检测期间不开放检索和阅读。</p></details>
    {selected&&!editor&&<Modal title="阅读资料" onClose={()=>setSelected('')}><div className="library-reader">{doc.isFetching?<Loading/>:doc.isError?<ErrorNotice error={doc.error}/>:doc.data&&<><div className="library-reader-heading"><h2>{doc.data.title}</h2><button className="button small" disabled={!writable} onClick={()=>setEditor({id:selected,original:doc.data})}>编辑资料</button></div><label className="inline"><input type="checkbox" checked={raw} onChange={e=>{setRaw(e.target.checked);setMatch(null);}}/>查看原文</label>
      {match&&match.sha===doc.data.sha256?<pre className="library-original">{sourceSlice(doc.data.text,0,match.start)}<mark ref={matchRef}>{sourceSlice(doc.data.text,match.start,match.end)}</mark>{sourceSlice(doc.data.text,match.end)}</pre>:raw?<pre className="library-original">{doc.data.text}</pre>:<Reading text={doc.data.text}/>}
      {match&&<button className="button text" onClick={()=>setMatch(null)}>阅读排版后的全文</button>}<details><summary>来源详情</summary><p className="card-note">内容版本：{doc.data.sha256.slice(0,12)}</p></details></>}</div></Modal>}
    {editor&&<DocumentEditor key={editor.id??'new'} workspace={workspace} editor={editor} close={()=>setEditor(null)} saved={(id,duplicate)=>{setEditor(null);setNotice(duplicate?'此内容已存在，已为你打开原资料。':editor.id?'资料已更新。':'资料已保存到本机知识库。');refresh();if(duplicate||editor.id)open(id);}}/>}
  </div>;
}
function DocumentEditor({workspace,editor,close,saved}:{workspace:Workspace;editor:Editor;close:()=>void;saved:(id:string,duplicate:boolean)=>void}){
  const [title,setTitle]=useState(editor.original?.title??''),[text,setText]=useState(editor.original?.text??''),[fileError,setFileError]=useState(''),[reading,setReading]=useState(false);
  const save=useMutation({mutationFn:()=>editor.id&&editor.original?api.libraryEdit(workspace.workspace_id,editor.id,title,text,editor.original):api.libraryAdd(workspace.workspace_id,title,text),onSuccess:r=>saved(r.id,r.duplicate)});
  const busy=save.isPending||reading,dirty=title!==(editor.original?.title??'')||text!==(editor.original?.text??'');
  function dismiss(){if(!dirty||window.confirm('放弃尚未保存的修改？'))close();}
  return <Modal title={editor.id?'编辑资料':'添加资料'} busy={busy} onClose={dismiss}><form className="library-editor" onSubmit={e=>{e.preventDefault();if(!busy)save.mutate();}}><p className="card-note">粘贴笔记，或导入UTF-8文本文件。内容仅保存在当前项目。</p>
    <label className="field">资料标题<input autoFocus required maxLength={120} value={title} disabled={busy} onChange={e=>setTitle(e.target.value)}/></label><label className="field">导入文本文件<input type="file" accept=".txt,.md,text/plain,text/markdown" disabled={busy} onChange={async e=>{const file=e.target.files?.[0];e.target.value='';if(!file)return;setReading(true);setFileError('');try{if(!/\.(txt|md)$/i.test(file.name)||file.size>400000)throw Error('仅支持400KB以内的UTF-8文本文件');const value=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());if(value.length>100000)throw Error('每份资料最多10万字');setText(value);setTitle(file.name.slice(0,120));}catch(err){setFileError(err instanceof Error?err.message:'无法读取文件');}finally{setReading(false);}}}/></label>{fileError&&<p role="alert">{fileError}</p>}
    <div className="field"><label htmlFor="library-document-body">资料正文</label><textarea id="library-document-body" required maxLength={100000} rows={10} value={text} disabled={busy} onChange={e=>setText(e.target.value)}/></div><ErrorNotice error={save.error}/><div className="dialog-footer"><button type="button" className="button" disabled={busy} onClick={dismiss}>取消</button><button className="button primary" disabled={busy||!title.trim()||!text.trim()}>{save.isPending?'正在保存…':editor.id?'保存修改':'保存到当前项目'}</button></div></form></Modal>;
}
