"use client";
import {useEffect,useState} from 'react';
import Link from 'next/link';
import {useTranslation} from 'react-i18next';
import {listNotebookEntries,type NotebookEntry} from '@/lib/notebook-api';
import {practiceRoute} from '@/lib/learning-routes';
export default function ProjectPractice({pathId}:{pathId:string}){
 const {t}=useTranslation();const [rows,setRows]=useState<NotebookEntry[]>([]);const [error,setError]=useState('');const [offset,setOffset]=useState(0);const [busy,setBusy]=useState(true);
 useEffect(()=>{let active=true;listNotebookEntries({mastery_path_id:pathId,limit:30,offset}).then(data=>{if(active){setRows(data.items);setError('')}}).catch(e=>{if(active)setError(String(e))}).finally(()=>{if(active)setBusy(false)});return()=>{active=false}},[pathId,offset]);
 return <section><h2 className="text-lg font-semibold">{t('项目练习')}</h2><p className="my-3 text-sm text-muted-foreground">{t('只展示来源明确关联到当前项目的题目。')}</p>{error&&<p role="alert">{error}</p>}{busy?<p>{t('正在读取…')}</p>:<ul className="space-y-2">{rows.map(row=><li key={row.id}><Link href={practiceRoute(new URLSearchParams({question:String(row.id)}))} className="block rounded-xl border border-border p-4"><p className="line-clamp-2">{row.question}</p><p className="mt-2 text-xs text-muted-foreground">{row.is_correct?t('最近作答正确'):t('待巩固')}</p></Link></li>)}</ul>}{!busy&&!rows.length&&<p className="py-6 text-muted-foreground">{t('暂无关联练习，项目陪学中完成的题目会收录在这里。')}</p>}<div className="mt-4 flex gap-3"><button disabled={!offset||busy} onClick={()=>{setBusy(true);setOffset(n=>Math.max(0,n-30))}}>{t('上一页')}</button><button disabled={rows.length<30||busy} onClick={()=>{setBusy(true);setOffset(n=>n+30)}}>{t('下一页')}</button></div></section>
}
