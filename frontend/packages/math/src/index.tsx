const dimensions = ['数学抽象', '逻辑推理', '数学建模', '直观想象', '数学运算', '数据分析'];
export function EvidenceRadar({ pendingReason = '等待学习证据' }: { pendingReason?: string }) {
  const vertices = (radius: number) => Array.from({ length: 6 }, (_, index) => {
    const angle = index * Math.PI / 3 - Math.PI / 2;
    return `${180 + radius * Math.cos(angle)},${140 + radius * Math.sin(angle)}`;
  }).join(' ');
  return <svg className="evidence-radar" viewBox="0 0 360 290" role="img" aria-label={`六维数学素养框架，尚无评分，${pendingReason}`}>
    {[30, 60, 90].map(radius => <polygon key={radius} points={vertices(radius)} fill="none" stroke="currentColor" opacity=".16"/>)}
    {dimensions.map((label, index) => { const angle = index * Math.PI / 3 - Math.PI / 2; return <g key={label}><line x1="180" y1="140" x2={180 + 90 * Math.cos(angle)} y2={140 + 90 * Math.sin(angle)} stroke="currentColor" opacity=".16"/><text x={180 + 126 * Math.cos(angle)} y={145 + 116 * Math.sin(angle)} textAnchor="middle" fill="currentColor" fontSize="12">{label}</text></g>; })}
    <rect x="123" y="118" width="114" height="44" rx="9" fill="#fffefa"/><text x="180" y="145" textAnchor="middle" fill="currentColor" fontSize="12">{pendingReason}</text>
  </svg>;
}

export function LearningCycle() {
  return <ol className="learning-cycle">{['明确目标', '确认计划', '尝试与验证', '反思与检测'].map((label, index) => <li key={label}><span>0{index + 1}</span>{label}</li>)}</ol>;
}
