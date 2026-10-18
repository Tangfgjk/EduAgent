import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@wenjin/api-client';
import { ErrorNotice, Success } from '@wenjin/ui';

export function DynamicContentPanel() {
  const client = useQueryClient(), [text, setText] = useState(''), [offset, setOffset] = useState(0);
  const rows = useQuery({ queryKey: ['dynamic-content', offset], queryFn: ({ signal }) => api.dynamicContent(offset, signal) });
  const register = useMutation({ mutationFn: () => api.registerContent(JSON.parse(text)), onSuccess: () => {
    void client.invalidateQueries({ queryKey: ['dynamic-content'] });
    void client.invalidateQueries({ queryKey: ['admin-catalog'] });
    void client.invalidateQueries({ queryKey: ['admin-activity-catalog'] });
  } });
  return <section className="card section-gap"><h2>动态内容登记</h2><p>登记内容进入共享开发库，不读取学生数据。结构与参考数值可解析性检查不能证明题意或答案正确；登记后须在下方核对题目、保存开发审核依据，再分别发布陪学及互动版本。</p>
    <details><summary>导入 content.v1 JSON</summary><p className="card-note">参考仓库 examples/dynamic-content.rational.json。ID 以 dynamic. 开头且不可复用，改版须用新 ID；不接受 HTML、脚本、自评通过或自动发布字段。sources 仅声明来源，授权与数学正确性仍需人工核对。</p><label className="field">开发内容 JSON<textarea rows={12} value={text} maxLength={150000} onChange={e => setText(e.target.value)}/></label><button className="button" disabled={!text.trim() || register.isPending} onClick={() => register.mutate()}>登记不可变内容版本</button></details>
    <ErrorNotice error={register.error}/>{register.data && <Success>已登记 {register.data.unit_id}，版本 {register.data.version.slice(0, 12)}；待开发审核及发布。</Success>}
    <ErrorNotice error={rows.error}/><ul>{rows.data?.items.map(row => <li key={row.unit_id}>{row.unit_id} · {row.version.slice(0, 12)} · {row.status === 'enabled_development' ? '开发已启用' : row.status === 'reviewed_not_enabled' ? '已开发核对 / 未启用' : '结构已检查 / 数学内容待核对'}</li>)}</ul>
    <div className="actions"><button className="button small" disabled={offset === 0 || rows.isFetching} onClick={() => setOffset(Math.max(0, offset - 20))}>上一批</button><button className="button small" disabled={rows.isFetching || (rows.data?.items.length ?? 0) < 20} onClick={() => setOffset(offset + 20)}>下一批</button></div>
  </section>;
}
