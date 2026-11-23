import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import GoalContractPanel from '@/components/learning/GoalContractPanel'
import { initI18n, ensureLanguage } from '@/i18n/init'
const translations = initI18n('zh')
const m = vi.hoisted(() => ({ request: vi.fn() }))
vi.mock('@/lib/learning-project-api', () => ({
  projectEndpoint: (p: string) => '/projects/' + p,
  projectRequest: (...args: unknown[]) => m.request(...args),
}))
const pending = {
  source_revision: 3,
  status: 'needs_confirmation',
  current: null,
  proposal: { level: 'apply', summary: '用方程解决问题', reason: '与你的目标一致' },
  suggested_summary: '用方程解决问题',
  levels: [
    {
      level: 'understand',
      abilities: ['recognition', 'explanation'],
      minimum_distinct_tasks: 2,
      delayed_transfer_required: false,
    },
    {
      level: 'apply',
      abilities: ['explanation', 'application'],
      minimum_distinct_tasks: 2,
      delayed_transfer_required: false,
    },
    {
      level: 'transfer',
      abilities: ['application', 'transfer'],
      minimum_distinct_tasks: 2,
      delayed_transfer_required: true,
    },
  ],
  history: [],
  progress: { total: 4, demonstrated: 0, model_feedback_used: false },
}
const confirmed = {
  ...pending,
  status: 'confirmed',
  source_revision: 4,
  current: {
    version: 1,
    assessment_version: 1,
    level: 'apply',
    summary: '用方程解决问题',
    time_budget: '',
    confirmed_at: 100,
  },
  proposal: null,
  history: [
    {
      version: 1,
      assessment_version: 1,
      level: 'apply',
      summary: '用方程解决问题',
      time_budget: '',
      confirmed_at: 100,
    },
  ],
}
beforeEach(async () => {
  m.request.mockReset()
  await ensureLanguage('zh')
  await translations.changeLanguage('zh')
})
it('shows an agent proposal without confirming it and explains the selected criteria', async () => {
  m.request.mockResolvedValue(pending)
  render(<GoalContractPanel pathId="p" />)
  const radio = await screen.findByRole('radio', { name: /独立应用/ })
  expect(radio).toBeChecked()
  expect(m.request.mock.calls.filter(([, o]) => o?.method === 'POST')).toHaveLength(0)
  fireEvent.click(screen.getByRole('radio', { name: /迁移解决问题/ }))
  expect(screen.getByText('还需要完成延迟复习和迁移验证。')).toBeVisible()
  expect(screen.getByRole('button', { name: '确认目标与标准' })).toBeEnabled()
})
it('only confirms after the learner clicks and includes a revision and retry id', async () => {
  m.request.mockResolvedValueOnce(pending).mockResolvedValueOnce(confirmed)
  const changed = vi.fn()
  render(<GoalContractPanel pathId="p" onUpdated={changed} />)
  fireEvent.click(await screen.findByRole('button', { name: '确认目标与标准' }))
  expect(await screen.findByText('目标已由你确认')).toBeVisible()
  const [url, options] = m.request.mock.calls[1]
  expect(url).toBe('/projects/p/goal-contract')
  expect(JSON.parse(options.body)).toMatchObject({
    source_revision: 3,
    level: 'apply',
    summary: '用方程解决问题',
  })
  expect(JSON.parse(options.body).request_id).toBeTruthy()
  expect(changed).toHaveBeenCalledOnce()
})
it('keeps the editable target after conflict and refreshes the live revision', async () => {
  m.request
    .mockResolvedValueOnce(pending)
    .mockRejectedValueOnce(new Error('学习记录已更新'))
    .mockResolvedValueOnce({ ...pending, source_revision: 8 })
  render(<GoalContractPanel pathId="p" />)
  fireEvent.click(await screen.findByRole('button', { name: '确认目标与标准' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('学习记录已更新')
  expect(screen.getByLabelText('我希望最终能做到什么')).toHaveValue('用方程解决问题')
  fireEvent.click(screen.getByRole('button', { name: '刷新目标' }))
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  expect(m.request).toHaveBeenCalledTimes(3)
})
it('offers the prior target as an editable proposal without replaying a confirmation', async () => {
  m.request.mockResolvedValue(confirmed)
  render(<GoalContractPanel pathId="p" />)
  fireEvent.click(await screen.findByText('目标修改记录'))
  fireEvent.click(screen.getByRole('button', { name: '参考此目标重新确认' }))
  expect(screen.getByLabelText('我希望最终能做到什么')).toHaveValue('用方程解决问题')
  expect(m.request.mock.calls.filter(([, o]) => o?.method === 'POST')).toHaveLength(0)
})
it("ignores an older project's late response", async () => {
  let finish!: (v: unknown) => void
  m.request
    .mockImplementationOnce(
      () =>
        new Promise(resolve => {
          finish = resolve
        })
    )
    .mockResolvedValueOnce({ ...pending, suggested_summary: '新的目标', proposal: null })
  const view = render(<GoalContractPanel pathId="old" />)
  view.rerender(<GoalContractPanel pathId="new" />)
  expect(await screen.findByLabelText('我希望最终能做到什么')).toHaveValue('新的目标')
  await act(async () => finish(pending))
  expect(screen.getByLabelText('我希望最终能做到什么')).toHaveValue('新的目标')
})
