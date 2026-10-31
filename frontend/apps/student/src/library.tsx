import {useState} from 'react';
import {useMutation,useQuery} from '@tanstack/react-query';
import {api,type Workspace} from '@wenjin/api-client';
import {ErrorNotice,Heading,Loading} from '@wenjin/ui';

export function LibraryPage({workspace}:{workspace:Workspace}){
  const wid=workspace.workspace_id;
  const [title,setTitle]=useState(''),[text,setText]=useState(''),[q,setQ]=useState(''),[selected,setSelected]=useState(''),[fileError,setFileError]=useState('');
  const list=useQuery({queryKey:['local-library',wid],queryFn:()=>api.libraryList(wid)});
  const doc=useQuery({queryKey:['local-document',wid,selected],enabled:!!selected,queryFn:()=>api.libraryRead(wid,selected)});
  const add=useMutation({mutationFn:()=>api.libraryAdd(wid,title,text),onSuccess:()=>{setTitle('');setText('');void list.refetch();}});
  const search=useMutation({mutationFn:()=>api.librarySearch(wid,q)});
  const remove=useMutation({mutationFn:(id:string)=>api.libraryDelete(wid,id),onSuccess:()=>{setSelected('');search.reset();void list.refetch();}});
  return <><Heading title="我的本地知识库" kicker="READ, FIND, AND TRACE" description="按当前项目保存笔记和参考资料，在本机检索原文并查看出处。资料未经专业核验，不作为参考答案或掌握证据。"/>
    <p className="notice">资料保存在本机服务的数据库，当前不发送给外部模型。只接收 UTF-8 的 .txt / .md；PDF、图片OCR和模型问答尚未开放。临时会话的资料仍只属于该空间。</p>
    <section className="card"><h2>查找资料</h2><form className="actions" onSubmit={e=>{e.preventDefault();search.mutate();}}><label>检索关键词<input required maxLength={160} value={q} onChange={e=>{setQ(e.target.value);search.reset();}} placeholder="例如：乘除顺序"/></label><button className="button primary" disabled={search.isPending}>本地检索</button></form><ErrorNotice error={search.error}/>
      {search.isSuccess&&(search.data.items.length?search.data.items.map(hit=><article className="soft-box section-gap" key={hit.document_id+':'+hit.start}><strong>{hit.title}</strong><p style={{whiteSpace:'pre-wrap'}}>{hit.text}</p><p className="card-note">来源位置：字符 {hit.start+1}—{hit.end} · 版本 {hit.sha256.slice(0,8)}</p><button className="button small" onClick={()=>setSelected(hit.document_id)}>查看来源全文</button></article>):<p role="status">没有找到相关原文。请换一个关键词；不会编造答案。</p>)}
    </section>
    <div className="grid two section-gap"><section className="card"><h2>本项目资料</h2>{list.isPending&&<Loading/>}<ErrorNotice error={list.error}/>{list.data?.items.map(d=><div className="setting-row" key={d.id}><button className="button text" onClick={()=>setSelected(d.id)}>{d.title} · {d.characters}字</button><button className="button small" disabled={remove.isPending||workspace.status!=='active'} onClick={()=>{if(window.confirm('删除这份本地资料？已有备份副本需单独管理。'))remove.mutate(d.id);}}>删除</button></div>)}{list.data&&!list.data.items.length&&<p>还没有资料，可以在右侧保存一份自编笔记。</p>}<ErrorNotice error={remove.error}/></section>
    <section className="card"><h2>保存参考资料</h2><form onSubmit={e=>{e.preventDefault();add.mutate();}}><label className="field">资料标题<input required maxLength={120} value={title} onChange={e=>setTitle(e.target.value)}/></label><label className="field">导入文本文件<input type="file" accept=".txt,.md,text/plain,text/markdown" disabled={workspace.status!=='active'} onChange={async e=>{const file=e.target.files?.[0];if(!file)return;setFileError('');try{if(!/\.(txt|md)$/i.test(file.name)||file.size>400000)throw Error('仅支持400KB以内的UTF-8文本文件');const bytes=await file.arrayBuffer();const value=new TextDecoder('utf-8',{fatal:true}).decode(bytes);if(value.length>100000)throw Error('每份资料最多10万字');setText(value);setTitle(file.name.slice(0,120));}catch(err){setFileError(err instanceof Error?err.message:'无法读取文件');}finally{e.target.value='';}}}/></label>{fileError&&<p role="alert">{fileError}</p>}<label className="field">资料正文<textarea required maxLength={100000} rows={8} value={text} onChange={e=>setText(e.target.value)}/></label><button className="button primary" disabled={add.isPending||workspace.status!=='active'}>保存到当前项目</button><ErrorNotice error={add.error}/>{add.isSuccess&&<p role="status">资料已保存到本机知识库。</p>}</form></section></div>
    {selected&&<section className="card section-gap"><button className="button small" onClick={()=>setSelected('')}>关闭来源</button><ErrorNotice error={doc.error}/>{doc.data&&<><h2>{doc.data.title}</h2><p className="card-note">内容版本：{doc.data.sha256.slice(0,12)}</p><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',fontFamily:'inherit'}}>{doc.data.text}</pre></>}</section>}
  </>;
}
