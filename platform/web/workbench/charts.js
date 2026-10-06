/* Accessible SVG projections. All values come from current API responses. */
'use strict';
window.WenjinCharts = (() => {
  const colors=['#3fb950','#58a6ff','#bc8cff','#d29922','#f85149'];
  function svgNode(tag,attributes={}) { const node=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [key,value] of Object.entries(attributes))node.setAttribute(key,String(value));return node; }
  function line(container,series,description) {
    container.replaceChildren();const valid=series.filter(s=>s.points.some(p=>Number.isFinite(p.value)));
    if(!valid.length){container.textContent='暂无有效数据；完成学习后这里会展示证据趋势。';return;}
    const svg=svgNode('svg',{viewBox:'0 0 600 180',role:'img','aria-label':description});
    const title=svgNode('title');title.textContent=description;svg.append(title);
    for(const [value,label] of [[0,'0%'],[.5,'50%'],[1,'100%']]){const y=145-value*120;svg.append(svgNode('line',{x1:40,x2:580,y1:y,y2:y,stroke:'#30363d'}));const t=svgNode('text',{x:0,y:y+4,fill:'#8b949e','font-size':11});t.textContent=label;svg.append(t);}
    const sharedMax=Math.max(1,...valid.flatMap(s=>s.points.filter(p=>Number.isFinite(p.value)).map(p=>p.index)));
    const legend=document.createElement('div');legend.className='chart-legend';
    const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='查看图表数据与来源';details.append(summary);
    const table=document.createElement('table');table.className='chart-table';const head=document.createElement('tr');for(const label of ['序列','作答序号','估计/比例','时间','证据来源']){const th=document.createElement('th');th.textContent=label;head.append(th);}table.append(head);
    valid.forEach((s,index)=>{const color=colors[index%colors.length],points=s.points.filter(p=>Number.isFinite(p.value));const max=sharedMax;const coords=points.map(p=>[40+(p.index/max)*530,145-Math.max(0,Math.min(1,p.value))*120]);svg.append(svgNode('polyline',{points:coords.map(p=>p.join(',')).join(' '),fill:'none',stroke:color,'stroke-width':2.5}));coords.forEach(([cx,cy],i)=>{const dot=svgNode('circle',{cx,cy,r:3.5,fill:color});const tip=svgNode('title');tip.textContent=s.label+' · '+(points[i].value*100).toFixed(1)+'% · '+(points[i].evidence_ref||'');dot.append(tip);svg.append(dot);});const key=document.createElement('span');key.style.setProperty('--series-color',color);key.textContent=s.label;legend.append(key);for(const p of points){const tr=document.createElement('tr');for(const v of [s.label,p.index,(p.value*100).toFixed(1)+'%',p.at?new Date(p.at).toLocaleString():'—',p.evidence_ref||'—']){const td=document.createElement('td');td.textContent=String(v);tr.append(td);}table.append(tr);}});
    const wrap=document.createElement('div');wrap.className='table-wrap';wrap.append(table);details.append(wrap);container.append(svg,legend,details);
  }
  function bars(container,rows,description){container.replaceChildren();if(!rows.length){container.textContent='暂无有效学习状态';return;}const height=rows.length*35+15,svg=svgNode('svg',{viewBox:`0 0 600 ${height}`,role:'img','aria-label':description});svg.style.height=height+'px';rows.forEach((r,i)=>{const y=8+i*35,label=svgNode('text',{x:0,y:y+15,fill:'#8b949e','font-size':11});label.textContent=r.label;svg.append(label,svgNode('rect',{x:190,y,width:320,height:20,rx:3,fill:'#262c36'}),svgNode('rect',{x:190,y,width:320*Math.max(0,Math.min(1,r.value)),height:20,rx:3,fill:colors[i%colors.length]}));const n=svgNode('text',{x:525,y:y+15,fill:'#e6edf3','font-size':12});n.textContent=(r.value*100).toFixed(1)+'%';svg.append(n);});container.append(svg);}
  return {line,bars};
})();
