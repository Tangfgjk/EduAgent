import { test, expect, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function navigate(page: Page, name: string) {
  if ((page.viewportSize()?.width ?? 1440) < 700) await page.getByRole('button', { name: '打开导航' }).click();
  await page.getByRole('link', { name, exact: true }).click();
}

async function prepare(page: Page) {
  await page.goto('/student/');
  await page.getByLabel('账号', { exact: true }).fill('s03.one');
  await page.getByLabel('密码', { exact: true }).fill('S03-test-only-password!');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await navigate(page, '项目与会话');
  await page.getByRole('button', { name: '新建学习项目', exact: true }).click();
  await page.getByLabel('空间名称').fill('目标协商验收 ' + Date.now());
  await page.getByRole('button', { name: '保存空间' }).click();
  await expect(page.locator('.context-bar')).toContainText('目标协商验收');
  await navigate(page, '学习计划');
}

test('目标提交、刷新恢复、调整、确认与流程事件', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await prepare(page);
  await page.getByLabel('这次想学会什么？').fill('理解等式性质与移项的依据');
  await page.getByRole('button', { name: '生成计划草案' }).click();
  await expect(page.getByText('等待你确认', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: '理解等式性质与移项的依据' })).toBeVisible();
  await page.getByLabel('调整每天的学习时间').selectOption('25');
  await expect(page.getByRole('button', { name: '我确认这份计划' })).toBeDisabled();
  await page.getByRole('button', { name: '更新草案' }).click();
  await expect(page.getByText('每天 25 分钟 · 第 2 版草案', { exact: true })).toBeVisible();
  await page.getByText('查看流程记录', { exact: true }).click();
  await expect(page.locator('details li')).toHaveCount(4);
  mkdirSync('test-results/screens', { recursive: true });
  await page.screenshot({ path: 'test-results/screens/student-plan.png', fullPage: true });
  await page.getByRole('button', { name: '我确认这份计划' }).click();
  await expect(page.getByText('计划已确认', { exact: true })).toBeVisible();
  await expect(page.locator('details li')).toHaveCount(6);
  await page.reload();
  await expect(page.getByText('计划已确认', { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test('手机计划布局、取消与项目隔离', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await prepare(page);
  const originalWorkspace = new URL(page.url()).searchParams.get('workspace')!;
  await page.getByLabel('这次想学会什么？').fill('探索三角形内角和');
  await page.getByRole('button', { name: '生成计划草案' }).click();
  await expect(page.getByText('等待你确认', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '取消这次流程' }).click();
  await expect(page.getByText('已取消', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('已取消', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  mkdirSync('test-results/screens', { recursive: true });
  await page.screenshot({ path: 'test-results/screens/student-plan-mobile.png', fullPage: true });
  await navigate(page, '项目与会话');
  await page.getByRole('button', { name: '新建学习项目', exact: true }).click();
  await page.getByLabel('空间名称').fill('隔离的新项目 ' + Date.now());
  await page.getByRole('button', { name: '保存空间' }).click();
  await expect(page.locator('.context-bar')).toContainText('隔离的新项目');
  await navigate(page, '学习计划');
  await expect(page.getByText('先给自己一个小目标', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: '探索三角形内角和' })).toHaveCount(0);
  await page.goto('/student/plan?workspace=' + originalWorkspace);
  await expect(page.getByRole('heading', { name: '探索三角形内角和' })).toBeVisible();
});
