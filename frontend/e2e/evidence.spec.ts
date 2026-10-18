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
async function create(page: Page, title: string, temporary = false) {
  await navigate(page, '项目与会话');
  await page.getByRole('button', { name: temporary ? '新建临时会话' : '新建学习项目', exact: true }).click();
  await page.getByLabel('空间名称').fill(title);
  await page.getByRole('button', { name: '保存空间', exact: true }).click();
  await expect(page.locator('.context-bar')).toContainText(title);
}
async function answer(page: Page, value: string) {
  await page.getByLabel('我的数值答案', { exact: true }).fill(value);
  await page.getByRole('button', { name: '检查我的解答', exact: true }).click();
  await expect(page.getByText('等待你的思考', { exact: true })).toBeVisible();
}

test('成长证据：真实阶段统计、可交互趋势、来源自述与归档只读', async ({ page, browser }) => {
  test.setTimeout(75_000);
  const admin = await browser.newPage();
  try {
    await login(admin, 'admin', 's03.admin');
    await navigate(admin, '知识点与题库');
    await expect(admin.getByRole('heading', { name: '一元一次方程与应用 · 开发单元', exact: true })).toBeVisible();
    if (await admin.getByRole('button', { name: '启用开发测试', exact: true }).count()) {
      await admin.getByRole('checkbox').check();
      await admin.getByRole('button', { name: '启用开发测试', exact: true }).click();
    }
    await expect(admin.getByText('开发测试已启用', { exact: true })).toBeVisible();
  } finally { await admin.close(); }
  await login(page, 'student', 's03.one');
  const title = '成长证据验收 ' + Date.now();
  await create(page, title);
  await navigate(page, '学习计划');
  await page.getByLabel('这次想学会什么？').fill('保留方程学习过程与独立检测依据');
  await page.getByRole('button', { name: '生成计划草案', exact: true }).click();
  await page.getByRole('button', { name: '我确认这份计划', exact: true }).click();
  await expect(page.getByText('计划已确认', { exact: true })).toBeVisible();
  await navigate(page, '津津陪学');
  await confirmFixtureCourse(page);
  await page.getByRole('button', { name: '开始开发单元', exact: true }).click();
  await answer(page, '3');
  await page.getByRole('button', { name: '继续下一步', exact: true }).click();
  await expect(page.getByText('P-EQ-01', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '给我一点提示', exact: true }).click();
  await expect(page.getByRole('button', { name: '再给我一级提示', exact: true })).toBeEnabled();
  await answer(page, '6');
  await page.getByLabel('这一步用到了什么依据？下次准备怎么检查？').fill('两边同时进行相同运算');
  await page.getByRole('button', { name: '保存反思并继续', exact: true }).click();
  await expect(page.getByText('P-APP-01', { exact: true })).toBeVisible();
  await answer(page, '7');
  await page.getByLabel('这一步用到了什么依据？下次准备怎么检查？').fill('包装费只出现一次');
  await page.getByRole('button', { name: '保存反思并继续', exact: true }).click();
  await expect(page.getByText('C-EQ-01', { exact: true })).toBeVisible();
  await answer(page, '5');
  await navigate(page, '成长证据');
  await page.getByRole('tab', { name: '当前空间局部', exact: true }).click();
  await expect(page.getByText('当前筛选 7 条。', { exact: false })).toBeVisible();
  const overview = page.locator('section.card').filter({ has: page.getByRole('heading', { name: '模拟数据 · 过程概览', exact: true }) });
  await expect(overview.locator('.evidence-kpis')).toContainText('1 / 1');
  await expect(page.getByRole('heading',{name:'知识点地图 · 记录覆盖'})).toBeVisible();
  await expect(page.locator('.observation-bars')).toContainText('辅助后练习');
  await expect(page.locator('.observation-bars')).toContainText('首次曝光检测');
  await expect(page.getByRole('group',{name:'六个观察方向候选材料条数'}).getByRole('button')).toHaveCount(6);
  await expect(page.getByRole('heading',{name:'支架时间线'})).toBeVisible();
  await page.getByRole('button',{name:/一元一次方程与应用.*7条事实/}).click();
  await expect(page.getByRole('region',{name:'图形来源钻取'})).toContainText('共7条');
  await page.getByRole('region',{name:'图形来源钻取'}).getByRole('button',{name:'查看这条原始材料'}).first().click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button',{name:'关闭弹窗',exact:true}).click();
  await page.getByRole('combobox', { name: '趋势指标', exact: true }).selectOption('hints');
  await page.getByRole('button', { name: /提示请求 1 次$/ }).click();
  await expect(page.getByRole('status').filter({ hasText: '提示请求 1 次' })).toBeVisible();
  await page.getByRole('button', { name: '逻辑推理', exact: false }).click();
  await expect(page.getByText('逻辑推理 · 未评定', { exact: true })).toBeVisible();
  await page.getByRole('combobox', { name: '记录类型', exact: true }).selectOption('checkpoint');
  await expect(page.locator('.evidence-records > li')).toHaveCount(1);
  await page.getByRole('button', { name: '查看原始来源', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('我的答案：5');
  await page.getByLabel('补充或纠正说明（学生自述）').fill('这一次我先独立代入核验');
  await page.getByRole('button', { name: '保存我的说明', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('学生自述：这一次我先独立代入核验');
  await page.getByRole('button', { name: '关闭弹窗', exact: true }).click();
  await expect(page.getByText('窗口内 7 条事实、1 条自述、0 条人工数值复核，分别保存。',{exact:true})).toBeVisible();
  await page.getByRole('combobox', { name: '记录类型', exact: true }).selectOption('self_report');
  await expect(page.locator('.evidence-records')).toContainText('这一次我先独立代入核验');
  await navigate(page, '项目与会话');
  await page.getByRole('button', { name: '归档 ' + title, exact: true }).click();
  await page.getByRole('button', { name: '确认归档', exact: true }).click();
  await navigate(page, '成长证据');
  await page.getByRole('tab', { name: '当前空间局部', exact: true }).click();
  await page.getByRole('combobox', { name: '记录类型', exact: true }).selectOption('self_report');
  await expect(page.locator('.evidence-records')).toContainText('已归档');
  await page.getByRole('button', { name: '查看原始来源', exact: true }).click();
  await expect(page.getByRole('button', { name: '保存我的说明', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '关闭弹窗', exact: true }).click();
  await page.getByRole('combobox', { name: '记录类型', exact: true }).selectOption('all');
  await page.getByRole('tab',{name:'全局宏观',exact:true}).click();
  await page.getByRole('checkbox',{name:'全局包含归档项目'}).uncheck();
  await expect(page.getByText('当前范围还没有过程证据',{exact:true})).toBeVisible();
  await expect(page.locator('.knowledge-node.has-evidence')).toHaveCount(0);
  await page.getByRole('checkbox',{name:'全局包含归档项目'}).check();
  await expect(page.locator('.knowledge-node.has-evidence')).toHaveCount(1);
  mkdirSync('test-results/screens', { recursive: true });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'test-results/screens/student-evidence-live.png', fullPage: true });
});

test('手机证据：空态、来源筛选、时间切换及临时空间排除', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, 'student', 's03.one');
  const title = '局部空证据 ' + Date.now();
  await create(page, title, true);
  await navigate(page, '成长证据');
  await page.getByRole('tab', { name: '当前空间局部', exact: true }).click();
  await expect(page.getByText('当前范围还没有过程证据', { exact: true })).toBeVisible();
  await page.getByRole('combobox', { name: '时间范围', exact: true }).selectOption('14');
  await page.getByRole('combobox', { name: '数据来源', exact: true }).selectOption('real_student');
  await expect(page.getByText('尚无评分', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: '全局宏观', exact: true }).click();
  await expect(page.locator('.evidence-records')).toHaveCount(0);
  await page.getByRole('combobox', { name: '数据来源', exact: true }).selectOption('synthetic');
  await expect(page.getByText('尚无评分', { exact: true })).toBeVisible();
  await expect(page.locator('.evidence-records > li').filter({ hasText: title })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  mkdirSync('test-results/screens', { recursive: true });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'test-results/screens/student-evidence-mobile.png', fullPage: true });
});
