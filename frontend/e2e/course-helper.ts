import { expect, type Page } from '@playwright/test';

/** Existing scenarios explicitly confirm their fixture's course before starting.
 * The dedicated course.spec.ts exercises the actual confirmation UI. */
export async function confirmFixtureCourse(page: Page) {
  const wid = new URL(page.url()).searchParams.get('workspace')!;
  const cookies = await page.context().cookies();
  const student = { 'X-Wenjin-Client': 'student', 'X-CSRF-Token': cookies.find(c => c.name === 'wenjin_student_csrf')!.value };
  const course = await (await page.request.get(`/api/v1/student/workspaces/${wid}/course`, { headers: student })).json();
  if (course.active_unit_id) return;
  let unit = await page.getByLabel(new URL(page.url()).pathname.endsWith('/study') ? '选择开发单元' : '选择互动单元').inputValue();
  if (new URL(page.url()).pathname.endsWith('/study')) unit ||= 'math.g7.equations.dev1';
  else {
    const activities = await (await page.request.get('/api/v1/student/activity-catalog', { headers: student })).json();
    unit = activities.items.find((a: { unit_id: string }) => a.unit_id === unit)?.parent_unit_id;
  }
  expect(unit).toBeTruthy();
  // Old activity-only fixtures now also need their base course explicitly open.
  const auth = await (await page.request.post('/api/v1/auth/login', { headers: { 'X-Wenjin-Client': 'admin' }, data: { username: 's03.admin', password: 'S03-test-only-password!' } })).json();
  const admin = { 'X-Wenjin-Client': 'admin', 'X-CSRF-Token': auth.csrf_token };
  const catalog = await (await page.request.get('/api/v1/admin/content-catalog', { headers: admin })).json();
  const pack = catalog.items.find((p: { unit_id: string }) => p.unit_id === unit);
  if (!pack.enabled) expect((await page.request.post(`/api/v1/admin/content-catalog/${unit}/development-release`, { headers: admin, data: { content_version: pack.content_version, expected_revision: pack.revision, enabled: true, acknowledge_development_only: true } })).ok()).toBeTruthy();
  expect((await page.request.post(`/api/v1/student/workspaces/${wid}/course`, { headers: student, data: { topic: '自编开发验收主题', title: '验收课程', unit_ids: [unit], active_unit_id: unit, expected_revision: course.revision } })).ok()).toBeTruthy();
  await page.reload();
  await expect(page.getByLabel('当前课程单元')).toHaveValue(unit);
}
