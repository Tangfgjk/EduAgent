import { test, expect, type Page } from '@playwright/test';

async function login(page: Page, role: 'student' | 'admin') {
  await page.goto(`/${role}/`);
  await page.getByLabel('账号', { exact: true }).fill(role === 'admin' ? 's03.admin' : 's03.one');
  await page.getByLabel('密码', { exact: true }).fill('S03-test-only-password!');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.locator('.app-shell')).toBeVisible();
  const cookies = await page.context().cookies();
  return { 'X-Wenjin-Client': role, 'X-CSRF-Token': cookies.find(c => c.name === `wenjin_${role}_csrf`)!.value };
}

test('主题生成、管理员审核、三路线同课、课堂导入与安全资源预览', async ({ page }, info) => {
  test.setTimeout(120_000);
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  const admin = await login(page, 'admin');
  const model = await (await page.request.get('/api/v1/admin/model-runtime', { headers: admin })).json();
  expect((await page.request.post('/api/v1/admin/model-runtime/publish', { headers: admin, data: { expected_revision: model.revision, vendor: 'fake', prompt_version: 'probability.followup.v3', max_output_tokens: 512, account_daily_calls: 0, site_daily_calls: 0, acknowledge_development_only: true } })).ok()).toBeTruthy();
  await page.goto('/admin/generation');
  await page.getByRole('checkbox', { name: '课程候选生成', exact: false }).check();
  await page.getByRole('checkbox', { name: '通用陪学', exact: false }).check();
  await page.getByRole('checkbox', { name: '确认已核对新增数据范围与预算', exact: false }).check();
  await page.getByRole('button', { name: '发布用途配置', exact: true }).click();
  await expect(page.getByRole('checkbox', { name: '确认已核对新增数据范围与预算', exact: false })).not.toBeChecked();
  const student = await login(page, 'student');
  const space = await (await page.request.post('/api/v1/student/workspaces', { headers: { ...student, 'Idempotency-Key': crypto.randomUUID() }, data: { kind: 'temporary', title: '自由主题完整验收' } })).json();
  const wid = space.workspace_id;
  await page.goto('/student/courses?workspace='+wid);
  await page.getByLabel('我想学什么？').fill('负数乘除和运算顺序');
  await page.getByRole('button', { name: '解析主题并准备诊断' }).click();
  await expect(page.getByLabel('诊断 1 答案')).toBeVisible();
  await page.getByLabel('诊断 1 答案').fill('0');
  await page.getByLabel('诊断 2 答案').fill('0');
  await page.getByRole('button', { name: '保存诊断并查看课程范围' }).click();
  await expect(page.getByRole('button', { name: '确认范围并生成课程' })).toBeVisible();
  await page.getByRole('button', { name: '确认范围并生成课程' }).click();
  await expect(page.getByText('数学检查通过 · 内容待开发审核', { exact: true })).toBeVisible();
  const adminPage = await page.context().newPage();
  try {
    await adminPage.goto('/admin/generation');
    const section = adminPage.locator('section.card').filter({ has: adminPage.getByRole('heading', { name: '负数乘除和运算顺序', exact: true }) });
    await section.getByLabel('审核依据').fill('浏览器验收：核对自编课程的条件、数值、讲解、来源与检测隔离，仅开发测试');
    await section.getByRole('checkbox', { name: '已核对条件', exact: false }).check();
    await section.getByRole('button', { name: '审核并发布开发版本' }).click();
    await expect(section.getByText('ready', { exact: true })).toBeVisible();
    await section.getByText('导入 OpenMAIC 课堂导出包', { exact: true }).click();
    await section.getByLabel('课堂 ZIP 文件').setInputFiles({ name: 'self-authored.maic.zip', mimeType: 'application/zip', buffer: Buffer.from('UEsDBBQAAAAIAMiEUl2KBBQ3qQAAALAAAAANAAAAbWFuaWZlc3QuanNvbqtWSssvyk0sCUstKs7Mz1OyUjDUUVAqLklMTwWyq5XyEnNBDKVnW/tfLO94OnPF0/V7nrYufbG15dmuCUq1ILXJqXmpxUA10dVKJZklORDlixuezV/6bO7CJ7u3KQEVJefnlaTmlYCNLEmtADGUXs5c8mzqhic7ep/s2PVy9rb3e3qetnY8n9P9fM2yp/3bn+zoe7F/wvN1018u3PV0V//jhial2trYWgBQSwECFAAUAAAACADIhFJdigQUN6kAAACwAAAADQAAAAAAAAAAAAAAgAEAAAAAbWFuaWZlc3QuanNvblBLBQYAAAAAAQABADsAAADUAAAAAAA=', 'base64') });
    await section.getByRole('button', { name: '安全导入待审资源' }).click();
    await expect(section.getByText('已导入，等待资源审核。')).toBeVisible();
    const resource = adminPage.locator('section.card').filter({ has: adminPage.getByRole('heading', { name: '浏览器导入资源 · pending_review', exact: true }) });
    await resource.getByLabel('审核依据').fill('浏览器导入的自编资源，核对条件与文本，仅用于开发测试');
    await resource.getByRole('checkbox', { name: '已核对条件', exact: false }).check();
    await resource.getByRole('button', { name: '审核并发布开发版本' }).click();
    await expect(adminPage.getByRole('heading', { name: '浏览器导入资源 · approved', exact: true })).toBeVisible();
    await page.reload();
    await page.getByRole('button', { name: '确认使用这份课程' }).click();
    await expect(page.getByText('课程已由你确认，三条学习路线使用同一单元。')).toBeVisible();
    await page.screenshot({ path: info.outputPath('generated-course-desktop.png'), fullPage: true });
    await page.getByText(/浏览器导入资源 · /).click();
    await expect(page.frameLocator('iframe[title="资源预览：浏览器导入资源"]').getByRole('heading', { name: '浏览器导入资源' })).toBeVisible();
    const unit = (await (await page.request.get(`/api/v1/student/workspaces/${wid}/course`, { headers: student })).json()).active_unit_id;
    for (const route of ['study', 'roundtable', 'teach']) {
      await page.goto(`/student/${route}?workspace=${wid}`);
      await expect(page.getByLabel('当前课程单元')).toHaveValue(unit);
    }
    await page.getByRole('button', { name: '开始我教 AI', exact: true }).click();
    await expect(page.getByLabel('我的数值表达')).toBeVisible();
    await page.getByLabel('我的数值表达').fill('0');
    await page.getByLabel('我的讲解', { exact: false }).fill('先看符号，再按运算顺序处理，最后检查计算条件');
    await page.getByRole('checkbox', { name: '让 AI 根据本次讲解选择追问方向', exact: true }).check();
    await page.getByRole('button', { name: '保存表达，邀请学伴', exact: true }).click();
    await expect(page.getByText('学伴追问已保存', { exact: false }).first()).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)).toBeTruthy();
    await page.screenshot({ path: info.outputPath('generated-teach-mobile.png'), fullPage: true });
    expect(errors).toEqual([]);
  } finally { await adminPage.close(); }
});
