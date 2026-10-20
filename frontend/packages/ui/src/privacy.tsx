import {useState} from 'react';
import {useMutation,useQuery} from '@tanstack/react-query';
import {api} from '@wenjin/api-client';
import {ErrorNotice,Success} from './index';


export function PrivacyControls(){
 const [ack,setAck]=useState(false);
 const download=useMutation({mutationFn:()=>api.exportPrivacy(),onSuccess:value=>{
  const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
  const link=document.createElement('a');link.href=url;link.download='my-learning-records.json';link.click();window.setTimeout(()=>URL.revokeObjectURL(url),1000);
 }});
 const erase=useMutation({mutationFn:()=>api.requestErasure()});
 return <section className="card section-gap"><h2>我的数据</h2><p>可导出本人空间、原始表达和学习观察；不包含参考答案、密钥或其他账号资料。开发数据尚未启用统一自动过期删除。</p><button className="button" disabled={download.isPending} onClick={()=>download.mutate()}>下载我的学习记录</button>{download.isSuccess&&<Success>已准备 JSON 导出文件，请妥善保管。</Success>}
 <details className="section-gap"><summary>申请删除账号和学习数据</summary><p>申请后立即停用账号并退出所有登录。数据须由维护人员停服、备份后删除；备份及外部服务产物还需单独清理。此操作不把“已申请”显示成“已删除”。</p><label className="inline"><input type="checkbox" checked={ack} onChange={event=>setAck(event.target.checked)}/>我已保存需要的数据，确认停用账号并申请删除</label><button className="button danger" disabled={!ack||erase.isPending||erase.isSuccess} onClick={()=>erase.mutate()}>停用账号并提交删除申请</button>{erase.isSuccess&&<Success>{erase.data.notice}</Success>}</details><ErrorNotice error={download.error??erase.error}/></section>;
}


export function OperationalReadiness(){
 const query=useQuery({queryKey:['operational-readiness'],queryFn:({signal})=>api.readiness(signal)});
 const erasures=useQuery({queryKey:['privacy-erasure-queue'],queryFn:({signal})=>api.erasureRequests(signal)});
 return <section className="card section-gap"><h2>覆盖、运维与试点准备</h2><p>题族、Provider、任务队列和外部验收关口来自当前运行数据。真实学生教学效果保持待验证，媒体费用须独立授权。</p><button className="button" disabled={query.isFetching} onClick={()=>{void query.refetch();void erasures.refetch();}}>刷新能力与运行状态</button><details className="section-gap"><summary>查看当前能力与队列台账</summary><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{query.data?JSON.stringify(query.data,null,2):'读取中…'}</pre></details><h3 className="section-gap">待处理数据删除申请</h3><p>{erasures.data?.notice}</p>{erasures.data?.items.length?<ul>{erasures.data.items.map(row=><li key={row.request_id}>{row.username} · 账号ID {row.account_id} · 待本机维护（申请不等于已删除）</li>)}</ul>:<p>暂无待处理申请。</p>}<ErrorNotice error={query.error??erasures.error}/></section>;
}
