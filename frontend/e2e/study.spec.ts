import { confirmFixtureCourse } from './course-helper';
import { test, expect, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function login(page: Page, role: 'student' | 'admin', username: string) {
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
async function enableContent(page: Page) {
  await login(page, 'admin', 's03.admin');
  await navigate(page, '知识点与题库');
  await expect(page.getByRole('heading', { name: '一元一次方程与应用 · 开发单元', exact: true })).toBeVisible();
  if (await page.getByRole('button', { name: '启用开发测试', exact: true }).count()) {
    await page.getByRole('checkbox').check();
    await page.getByRole('button', { name: '启用开发测试', exact: true }).click();
  }
  await expect(page.getByText('开发测试已启用', { exact: true })).toBeVisible();
}
async function answer(page: Page, value: string) {
  await page.getByLabel('我的数值答案').fill(value);
  await page.getByRole('button', { name: '检查我的解答', exact: true }).click();
  await expect(page.getByText('等待你的思考', { exact: true })).toBeVisible();
}
async function reflect(page: Page) {
  await page.getByLabel('这一步用到了什么依据？下次准备怎么检查？').fill('等式两边做相同运算，再代入检查');
  await page.getByRole('button', { name: '保存反思并继续' }).click();
  await expect(page.getByText('等待你的思考', { exact: true })).toBeVisible();
}

test('真实项目学习闭环：内容启用、诊断、提示、反思、检测与人工复核', async ({ page, browser }) => {
  test.setTimeout(70_000);
  const admin = await browser.newPage();
  try {
    await enableContent(admin);
    await login(page, 'student', 's03.one');
    await navigate(page, '项目与会话');
    await page.getByRole('button', { name: '新建学习项目', exact: true }).click();
    await page.getByLabel('空间名称').fill('方程闭环验收 ' + Date.now());
    await page.getByRole('button', { name: '保存空间' }).click();
    await expect(page.locator('.context-bar')).toContainText('方程闭环验收');
    await navigate(page, '学习计划');
    await page.getByLabel('这次想学会什么？').fill('理解等式性质与方程应用');
    await page.getByRole('button', { name: '生成计划草案' }).click();
    await page.getByRole('button', { name: '我确认这份计划' }).click();
    await expect(page.getByText('计划已确认', { exact: true })).toBeVisible();
    await navigate(page, '津津陪学');
    await confirmFixtureCourse(page);
    await page.getByRole('button', { name: '开始开发单元' }).click();
    await expect(page.getByRole('heading', { name: '先试一题', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '给我一点提示', exact: true })).toBeDisabled();
    await answer(page, 'x=6/2');
    await page.getByRole('button', { name: '继续下一步' }).click();
    await expect(page.getByText('P-EQ-01', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '给我一点提示', exact: true }).click();
    await expect(page.locator('.study-hint')).toHaveCount(1);
    await page.reload();
    await expect(page.locator('.study-hint')).toHaveCount(1);
    await answer(page, '6');
    await reflect(page);
    await expect(page.getByText('P-APP-01', { exact: true })).toBeVisible();
    await expect(page.locator('#math-answer')).toHaveCount(1);
    await expect(page.getByLabel('我的数值答案')).toBeEnabled();
    await answer(page, '暂时不知道如何表达');
    await expect(page.getByText('不可验证', { exact: true })).toBeVisible();
    await navigate(admin, '内容与解答复核');
    await admin.getByRole('button', { name: '复核这条解答' }).first().click();
    await admin.getByLabel('复核依据').fill('需要补充列式和数值，当前无法判断');
    await admin.getByRole('button', { name: '保存复核意见' }).click();
    await expect(admin.getByRole('dialog')).toHaveCount(0);
    await admin.getByRole('tab', { name: '已复核', exact: true }).click();
    await expect(admin.getByText('人工结论：仍无法判断')).toBeVisible();
    await answer(page, '7');
    await reflect(page);
    await expect(page.getByRole('heading', { name: '无辅助检测', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '给我一点提示', exact: true })).toBeDisabled();
    mkdirSync('test-results/screens', { recursive: true });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: 'test-results/screens/student-study.png', fullPage: true });
    await answer(page, '5');
    await page.getByRole('button', { name: '继续下一步' }).click();
    await expect(page.getByText('C-APP-01', { exact: true })).toBeVisible();
    await answer(page, '6');
    await page.getByRole('button', { name: '查看学习小结' }).click();
    await expect(page.getByRole('heading', { name: '学习小结', exact: true })).toBeVisible();
    await expect(page.getByText('独立检测：2 / 2 道数值验证通过。', { exact: false })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading', { name: '学习小结', exact: true })).toBeVisible();
  } finally { await admin.close(); }
});

test('手机临时陪学保存、项目隔离与取消', async ({ page, browser }) => {
  const admin = await browser.newPage();
  try { await enableContent(admin); } finally { await admin.close(); }
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, 'student', 's03.one');
  await navigate(page, '项目与会话');
  await page.getByRole('button', { name: '新建临时会话', exact: true }).click();
  await page.getByLabel('空间名称').fill('一次方程探索 ' + Date.now());
  await page.getByRole('button', { name: '保存空间' }).click();
  await expect(page.locator('.context-bar')).toContainText('一次方程探索');
  const wid = new URL(page.url()).searchParams.get('workspace')!;
  await navigate(page, '津津陪学');
  await confirmFixtureCourse(page);
  await page.getByRole('button', { name: '开始开发单元' }).click();
  await expect(page.getByRole('heading', { name: '先试一题', exact: true })).toBeVisible();
  await answer(page, '3');
  await page.getByRole('button', { name: '继续下一步' }).click();
  await expect(page.getByText('P-EQ-01', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '给我一点提示', exact: true }).click();
  await expect(page.locator('.study-hint')).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  mkdirSync('test-results/screens', { recursive: true });
  await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: 'test-results/screens/student-study-mobile.png', fullPage: true });
  await page.getByRole('button', { name: '结束这次陪学' }).click();
  await expect(page.getByText('已取消', { exact: true })).toBeVisible();
  await page.goto('/student/study?workspace=' + wid);
  await expect(page.getByText('已取消', { exact: true })).toBeVisible();
});
