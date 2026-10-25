import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

async function login(page: Page, role: 'student' | 'admin') {
  await page.goto(`/${role}/`);
  await page.getByLabel('账号', { exact: true }).fill(role === 'admin' ? 's03.admin' : 's03.one');
  await page.getByLabel('密码', { exact: true }).fill('S03-test-only-password!');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.locator('.app-shell')).toBeVisible();
  const cookies = await page.context().cookies();
  return { 'X-Wenjin-Client': role, 'X-CSRF-Token': cookies.find(c => c.name === `wenjin_${role}_csrf`)!.value };
}

test('动态内容登记审核与课程绑定：三条路线同单元、改名不串课、刷新和跨空间隔离', async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  const headers = await login(page, 'admin');
  await page.goto('/admin/content');
  await page.getByText('导入 content.v1 JSON', { exact: true }).click();
  const spec = JSON.parse(readFileSync('../examples/dynamic-content.rational.json', 'utf8'));
  const suffix = Date.now().toString();
  spec.unit_id += '.'+suffix;
  for (const activity of spec.activities) activity.unit_id += '.'+suffix;
  await page.getByLabel('开发内容 JSON').fill(JSON.stringify(spec));
  await page.getByRole('button', { name: '登记不可变内容版本' }).click();
  await expect(page.getByText('已登记 '+spec.unit_id, { exact: false })).toBeVisible();
  // The existing review UI resolves new records without registry constants.
  for (const [route, unitId] of [['content-catalog', spec.unit_id], ...spec.activities.map((a: { unit_id: string }) => ['activity-catalog', a.unit_id])]) {
    const catalog = await (await page.request.get('/api/v1/admin/'+route, { headers })).json();
    const unit = catalog.items.find((u: { unit_id: string }) => u.unit_id === unitId);
    expect(unit.enabled).toBeFalsy();
    const review = await page.request.post('/api/v1/admin/development-reviews', { headers, data: { kind: 'content', target_id: unitId, version: unit.content_version, expected_revision: 0, conclusion: 'development_ready', note: '浏览器验收：自编数值与条件已人工核对，仅开发测试' } });
    expect(review.ok()).toBeTruthy();
    expect((await page.request.post(`/api/v1/admin/${route}/${unitId}/development-release`, { headers, data: { content_version: unit.content_version, expected_revision: 0, enabled: true, acknowledge_development_only: true } })).ok()).toBeTruthy();
  }
  await page.goto('/admin/config');
  await expect(page.getByRole('heading', { name: '按用途核对外发范围与额度' })).toBeVisible();
  const student = await login(page, 'student');
  const created = await page.request.post('/api/v1/student/workspaces', { headers: { ...student, 'Idempotency-Key': crypto.randomUUID() }, data: { kind: 'temporary', title: '名称不必匹配主题 '+suffix } });
  const space = await created.json(), wid = space.workspace_id;
  await page.goto('/student/study?workspace='+wid);
  await page.getByLabel('学习主题', { exact: true }).fill('有理数运算和每一步依据');
  await page.getByLabel('课程名称', { exact: true }).fill('显式有理数课程');
  await page.getByRole('checkbox', { name: spec.title, exact: true }).check();
  await page.getByRole('button', { name: '确认课程绑定', exact: true }).click();
  await expect(page.getByLabel('当前课程单元')).toHaveValue(spec.unit_id);
  await expect(page.getByLabel('选择开发单元')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: spec.title, exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('course-desktop.png'), fullPage: true });
  await page.reload();
  await expect(page.getByLabel('当前课程单元')).toHaveValue(spec.unit_id);
  expect((await page.request.patch('/api/v1/student/workspaces/'+wid, { headers: student, data: { title: '改名成概率也不串课', expected_revision: 1 } })).ok()).toBeTruthy();
  for (const kind of ['roundtable', 'teach']) {
    await page.goto(`/student/${kind}?workspace=${wid}`);
    await expect(page.getByLabel('当前课程单元')).toHaveValue(spec.unit_id);
    await expect(page.getByLabel('选择互动单元')).toHaveCount(0);
    await expect(page.getByRole('heading', { name: spec.activities.find((a: { kind: string }) => a.kind === kind).title, exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: kind === 'teach' ? '开始我教 AI' : '开始圆桌讨论', exact: true })).toBeEnabled();
  }
  await page.getByRole('button', { name: '开始我教 AI', exact: true }).click();
  await expect(page.getByLabel('我的数值表达')).toBeVisible();
  for (const route of ['review', 'evidence', 'planner', 'plan']) {
    await page.goto(`/student/${route}?workspace=${wid}`);
    await expect(page.getByLabel('当前课程单元')).toHaveValue(spec.unit_id);
  }
  const another = await (await page.request.post('/api/v1/student/workspaces', { headers: { ...student, 'Idempotency-Key': crypto.randomUUID() }, data: { kind: 'temporary', title: '另一个空间' } })).json();
  await page.goto('/student/roundtable?workspace='+another.workspace_id);
  await expect(page.getByText('请确认此空间的学习主题和课程', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '开始圆桌讨论', exact: true })).toBeDisabled();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath('course-mobile-unbound.png'), fullPage: true });
  await page.goto('/student/teach?workspace='+wid);
  await expect(page.getByLabel('当前课程单元')).toHaveValue(spec.unit_id);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)).toBeTruthy();
});


test('手动绑定明确提示必填项，填写后可提交', async ({page}) => {
  const headers = await login(page, 'student');
  const response = await page.request.post('/api/v1/student/workspaces', {headers:{...headers,'Idempotency-Key':crypto.randomUUID()},data:{kind:'temporary',title:'课程绑定必填引导'}});
  expect(response.ok()).toBeTruthy();
  const space = await response.json();
  await page.goto('/student/study?workspace='+space.workspace_id);
  const form = page.getByRole('region',{name:'课程上下文'});
  await form.getByRole('checkbox').first().check();
  await expect(form.getByRole('button',{name:'确认课程绑定',exact:true})).toBeDisabled();
  await expect(form.getByRole('status')).toContainText('学习主题至少填写2个字');
  await expect(form.getByRole('status')).toContainText('课程名称至少填写2个字');
  await form.getByLabel('学习主题',{exact:true}).fill('概率');
  await form.getByLabel('课程名称',{exact:true}).fill('概率入门');
  await expect(form.getByRole('button',{name:'确认课程绑定',exact:true})).toBeEnabled();
  await form.getByRole('button',{name:'确认课程绑定',exact:true}).click();
  await expect(page.getByLabel('当前课程单元')).toBeVisible();
});
