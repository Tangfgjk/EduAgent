import { useState } from 'react';
import type { EvidenceReport, LearningInsights, ObservationRef } from '@wenjin/api-client';

export function EvidenceCalendar({ report, selectedDay='', onSelectDay }: { report: EvidenceReport; selectedDay?: string; onSelectDay: (day:string)=>void }) {
  const days=Array.from({length:report.days},(_,i)=>new Date((report.starts_at+i*86400+8*3600)*1000).toISOString().slice(0,10));
  const months=[...new Set(days.map(d=>d.slice(0,7)))];
  const [chosenMonth,setMonth]=useState(selectedDay.slice(0,7)||months.at(-1)||'');
  const month=months.includes(chosenMonth)?chosenMonth:months.at(-1)!;
  const visible=days.filter(d=>d.startsWith(month));
  const pad=visible.length?(new Date(visible[0]+'T00:00:00+08:00').getUTCDay()+1)%7:0;
  const max=Math.max(1,...report.daily.map(d=>d.facts));
  return <section className="card section-gap"><div className="card-head"><h2>学习日历 · 活动分布</h2><label>月份<select aria-label="日历月份" value={month} onChange={e=>setMonth(e.target.value)}>{months.map(m=><option key={m}>{m}</option>)}</select></label></div><p className="card-note">北京时间；数字为事实条数，各来源分开。选择日期查看该日全部记录，可在下方分页。</p>{report.groups.map(group=><div key={group.source_kind}><h3>{group.source_kind==='synthetic'?'模拟开发':group.source_kind==='adult_test'?'成人测试':'真实学生'}</h3><div className="growth-calendar">{['日','一','二','三','四','五','六'].map(d=><small key={d}>{d}</small>)}{Array.from({length:pad},(_,i)=><span key={'pad'+i}/>)}{visible.map(day=>{const row=report.daily.find(d=>d.day===day&&d.source_kind===group.source_kind);return <button key={day} className="button small" style={{background:`rgba(47,112,87,${.06+.45*(row?.facts??0)/max})`}} onClick={()=>onSelectDay(day)} aria-pressed={selectedDay===day} aria-label={`${day}，${row?.facts??0}条事实`}><span>{Number(day.slice(-2))}<br/>{row?.facts??0} 条</span></button>;})}</div></div>)}
    {!report.groups.length&&<p>当前范围没有活动记录，可以先做一次学习尝试。</p>}
  </section>;
}

export function CompetencyRadar({ report, choose }: { report: LearningInsights; choose:(label:string,refs:ObservationRef[],total:number)=>void }) {
  const items=report.competencies, max=Math.max(1,...items.map(x=>x.candidate_count));
  const point=(i:number,r:number)=>[180+r*Math.sin(i*2*Math.PI/items.length),165-r*Math.cos(i*2*Math.PI/items.length)];
  const polygon=(r:number)=>items.map((_,i)=>point(i,r).join(',')).join(' ');
  return <div className="grid two section-gap"><svg viewBox="0 0 360 350" role="img" aria-label="六类素养候选材料分布图，不是能力评分">{[30,60,90,115].map(r=><polygon key={r} points={polygon(r)} fill="none" stroke="#c9dbd0"/>)}{items.map((item,i)=>{const [x,y]=point(i,115),[tx,ty]=point(i,142);return <g key={item.name}><line x1={180} y1={165} x2={x} y2={y} stroke="#dbe5db"/><text x={tx} y={ty} textAnchor="middle" fontSize={12}>{item.name}</text></g>;})}<polygon points={items.map((item,i)=>point(i,item.candidate_count/max*115).join(',')).join(' ')} fill="rgba(46,112,86,.18)" stroke="#287356" strokeWidth={2}/>{items.map((item,i)=>{const [x,y]=point(i,item.candidate_count/max*115);return <circle key={item.name} cx={x} cy={y} r={6} fill="#287356"><title>{item.name}：{item.candidate_count} 条候选，未评分</title></circle>;})}</svg><div><h3>素养证据分布，不是素养得分</h3><p>按当前窗口最大材料量缩放；维度可以重叠。没有记录是证据不足，不是能力为零。</p>{items.map(item=><button key={item.name} className="button small" onClick={()=>choose(item.name,item.refs,item.candidate_count)}>{item.name} · {item.candidate_count} 条 · 查看来源</button>)}</div></div>;
}
