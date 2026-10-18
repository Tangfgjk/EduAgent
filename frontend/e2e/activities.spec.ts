import { confirmFixtureCourse } from './course-helper';
import { test, expect, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function login(page: Page, role: 'student' | 'admin', name: string) {
  await page.goto(`/${role}/`);
  await page.getByLabel('账号', { exact: true }).fill(name);
  await page.getByLabel('密码', { exact: true }).fill('S03-test-only-password!');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.locator('.app-shell')).toBeVisible();
}
async function navigate(page: Page, name: string) {
  if ((page.viewportSize()?.width ?? 1440) < 700) await page.getByRole('button', { name: '打开导航' }).click();
  await page.getByRole('link', { name, exact: true }).click();
}
async function enable(page: Page) {
  await login(page, 'admin', 's03.admin');
  await navigate(page, '知识点与题库');
  await expect(page.getByRole('heading', { name: '圆桌与我教 AI · 互动模板审核' })).toBeVisible();
  const releaseStatus = page.getByText(/^互动开发(未启用|已启用)$/);
  await expect(releaseStatus).toBeVisible();
  if ((await releaseStatus.textContent()) === '互动开发未启用') {
    await page.getByText('查看互动题与完整角色模板', { exact: true }).click();
    await expect(page.getByText('角色是固定模板', { exact: false })).toHaveCount(0);
    await page.getByRole('button', { name: '审核互动模板', exact: true }).click();
    await page.getByRole('dialog').getByRole('checkbox').check();
    await page.getByRole('button', { name: '启用互动开发', exact: true }).click();
  }
  await expect(page.getByText('互动开发已启用', { exact: true })).toBeVisible();
}

for (const kind of ['roundtable', 'teach'] as const) {
  test(kind === 'roundtable' ? '桌面圆桌：学生先表达、观点区分、刷新恢复和正式项目来源' : '手机我教 AI：预设误区、TeachReport、临时空间隔离与归档只读', async ({ page, browser }) => {
    test.setTimeout(60_000);
    const admin = await browser.newPage();
    try { await enable(admin); } finally { await admin.close(); }
    if (kind === 'teach') await page.setViewportSize({ width: 390, height: 844 });
    await login(page, 'student', 's03.one');
    const title = 'S07 ' + kind + ' ' + Date.now();
    await navigate(page, '项目与会话');
    await page.getByRole('button', { name: kind === 'teach' ? '新建临时会话' : '新建学习项目', exact: true }).click();
    await page.getByLabel('空间名称').fill(title);
    await page.getByRole('button', { name: '保存空间', exact: true }).click();
    await expect(page.locator('.context-bar')).toContainText(title);
    const label = kind === 'teach' ? '我教 AI' : '圆桌讨论';
    await navigate(page, label);
    // This suite covers the original equation template, independent of the newest default pack.
    await page.getByLabel('选择互动单元').selectOption('math.g7.equations.activities.dev1');
    await confirmFixtureCourse(page);
    await page.getByRole('button', { name: '开始' + label, exact: true }).click();
    await expect(page.getByText('等待你的第一段表达', { exact: true })).toBeVisible();
    await page.getByLabel('我的数值表达', { exact: true }).fill('6');
    await page.getByLabel(kind === 'teach' ? '我的讲解' : '我的初步思路', { exact: true }).fill('等式两边同时加上7，再除以2。');
    await page.getByRole('button', { name: '保存表达，邀请学伴', exact: true }).click();
    await expect(page.getByRole('heading', { name: '比较、纠错与核验', exact: true })).toBeVisible();
    await expect(page.getByText(kind === 'teach' ? '预设误区 · 待纠正' : '模板观点 · 待核验', { exact: true })).toHaveCount(kind === 'teach' ? 1 : 3);
    await page.reload();
    await expect(page.getByText('等式两边同时加上7，再除以2。', { exact: true })).toBeVisible();
    await page.getByLabel('我的数值表达', { exact: true }).fill('12/2');
    await page.getByLabel('我的比较、纠错与检查依据', { exact: true }).fill('移项代表两边做相同运算，代入得12−7=5。');
    await page.getByRole('button', { name: '保存核验，生成小结', exact: true }).click();
    await expect(page.getByRole('heading', { name: kind === 'teach' ? 'TeachReport · 讲解记录' : '圆桌讨论小结', exact: true })).toBeVisible();
    await expect(page.getByText('数值核验通过', { exact: true })).toBeVisible();
    await expect(page.getByText('学生原始贡献', { exact: true })).toHaveCount(2);
    await expect(page.getByText('辅助后的核验 · 不是独立检测', { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText('学生原始贡献', { exact: true })).toHaveCount(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    mkdirSync('test-results/screens', { recursive: true });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: `test-results/screens/student-${kind}.png`, fullPage: true });
    await page.getByRole('link', { name: '查看学习记忆与来源', exact: true }).click();
    await page.getByRole('tab', { name: '学习记忆', exact: true }).click();
    await expect(page.getByText(`${label}留下 2 次学生贡献；角色模板不作为证据，解释质量尚未评定。`, { exact: true })).toBeVisible();
    if (kind === 'teach') {
      await page.getByRole('tab', { name: '全局正式项目', exact: true }).click();
      await expect(page.getByRole('heading', { name: title, exact: true })).toHaveCount(0);
      await navigate(page, '项目与会话');
      await page.getByRole('tab', { name: /临时会话/ }).click();
      await page.getByRole('button', { name: '归档 ' + title, exact: true }).click();
      await page.getByRole('button', { name: '确认归档', exact: true }).click();
      await navigate(page, label);
      await expect(page.getByRole('button', { name: '开始' + label, exact: true })).toBeDisabled();
      await expect(page.getByText('学生原始贡献', { exact: true })).toHaveCount(2);
    }
  });
}
