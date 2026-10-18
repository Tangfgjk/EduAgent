import { confirmFixtureCourse } from './course-helper';
import { test, expect, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function login(page: Page, role: 'admin' | 'student', username: string) {
  await page.goto(`/${role}/`);
  await page.getByLabel('账号', { exact: true }).fill(username);
  await page.getByLabel('密码', { exact: true }).fill('S03-test-only-password!');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.locator('.app-shell')).toBeVisible();
}
async function navigate(page: Page, name: string) {
  if ((page.viewportSize()?.width ?? 1440) < 700) await page.getByRole('button', { name: '打开导航' }).click();
  await page.getByRole('link', { name, exact: true }).click();
}

for (const kind of ['roundtable', 'teach'] as const) {
  test(`概率${kind === 'teach' ? '我教 AI 窄屏' : '圆桌桌面'}：独立审核、选择、刷新和证据`, async ({ page, browser }) => {
    test.setTimeout(60_000);
    const admin = await browser.newPage();
    try {
      await login(admin, 'admin', 's03.admin');
      await navigate(admin, '知识点与题库');
      await admin.getByRole('button', { name: '审核概率圆桌与我教 AI · 开发活动', exact: true }).click();
      const dialog = admin.getByRole('dialog');
      await expect(dialog.getByText('参考数值：2/5', { exact: true })).toBeVisible();
      if (await dialog.getByRole('button', { name: '启用此互动包', exact: true }).count()) {
        await dialog.getByRole('checkbox').check();
        await dialog.getByRole('button', { name: '启用此互动包', exact: true }).click();
      } else await dialog.getByRole('button', { name: '关闭', exact: true }).click();
      await expect(dialog).toHaveCount(0);
    } finally { await admin.close(); }
    if (kind === 'teach') await page.setViewportSize({ width: 390, height: 844 });
    await login(page, 'student', 's03.one');
    await navigate(page, '项目与会话');
    await page.getByRole('button', { name: '新建临时会话', exact: true }).click();
    const title = '概率互动 ' + kind + ' ' + Date.now();
    await page.getByLabel('空间名称').fill(title);
    await page.getByRole('button', { name: '保存空间', exact: true }).click();
    const label = kind === 'teach' ? '我教 AI' : '圆桌讨论';
    await navigate(page, label);
    await page.getByLabel('选择互动单元').selectOption('math.g9.probability.activities.dev1');
    await confirmFixtureCourse(page);
    await page.getByRole('button', { name: '开始' + label, exact: true }).click();
    await expect(page.getByText('P-PRB-02', { exact: true })).toBeVisible();
    await expect(page.getByText('学生原始贡献', { exact: true })).toHaveCount(0);
    await page.getByLabel('我的数值表达', { exact: true }).fill('1/2');
    await page.getByLabel(kind === 'teach' ? '我的讲解' : '我的初步思路', { exact: true }).fill('我先按颜色种类计数，还需要核对是否等可能。');
    await page.getByRole('button', { name: '保存表达，邀请学伴', exact: true }).click();
    await expect(page.getByRole('heading', { name: '比较、纠错与核验', exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText('本轮内容：概率圆桌与我教 AI · 开发活动', { exact: true })).toBeVisible();
    await page.getByLabel('我的数值表达', { exact: true }).fill('2/5');
    await page.getByLabel('我的比较、纠错与检查依据', { exact: true }).fill('每个球等可能，共5个球，其中2个蓝球；颜色种类不能当作等可能基本结果。');
    await page.getByRole('button', { name: '保存核验，生成小结', exact: true }).click();
    await expect(page.getByText('数值核验通过', { exact: true })).toBeVisible();
    await expect(page.getByText('学生原始贡献', { exact: true })).toHaveCount(2);
    await expect(page.getByText('辅助后的核验 · 不是独立检测', { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    mkdirSync('test-results/screens', { recursive: true });
    await page.screenshot({ path: `test-results/screens/probability-${kind}.png`, fullPage: true });
    await page.getByRole('link', { name: '查看学习记忆与来源', exact: true }).click();
    await page.getByRole('tab', { name: '学习记忆', exact: true }).click();
    await expect(page.getByText(`${label}留下 2 次学生贡献；角色模板不作为证据，解释质量尚未评定。`, { exact: true })).toBeVisible();
  });
}
