import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { test, expect, vi, beforeEach } from 'vitest'
import LearningSupport from '@/components/learning/LearningSupport'
vi.mock('@/components/learning/GoalContractPanel', () => ({ default: () => null }))
const m = vi.hoisted(() => ({ request: vi.fn(), push: vi.fn(), prompt: vi.fn(), pathId: 'p' }))
vi.mock('next/navigation', () => ({
  useParams: () => ({ pathId: m.pathId }),
  useRouter: () => ({ push: m.push }),
}))
vi.mock('@/components/learning/LearningShell', () => ({
  LearningShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}))
vi.mock('@/lib/learning-project-api', () => ({
  projectRequest: m.request,
  projectEndpoint: (id: string) => '/topics/' + id,
  learningProjectRoute: (id: string) => '/project/' + id,
}))
vi.mock('@/lib/pending-prompt', () => ({ setPendingPrompt: m.prompt }))
const point = {
  id: 'k',
  name: '代入法',
  observed: false,
  independent_n: 0,
  assisted_n: 0,
  latest_result: null,
  evidence: [],
  support: {
    level: 1,
    label: '按需给一个方向提示',
    mode: 'adaptive',
    reason: '尚未安排减少帮助',
    actual_level: 2,
    history: [],
    rules: '计划不抹去当前题帮助',
  },
  check: { phase_label: '独立核查', status: 'ready', completed_cycles: 0 },
}
const portrait = {
  name: '项目',
  goal: '独立解方程',
  source_revision: 8,
  self_report: {},
  objective_id: 'k',
  objectives: [point],
  strategy: {
    delivery_count: 0,
    catalogue: [{ id: 'reason_first', name: '先说明理由' }],
    limitations: ['只使用本人项目数据'],
    consent: false,
  },
}
beforeEach(() => {
  m.pathId = 'p'
  m.request.mockReset()
  m.push.mockReset()
  m.prompt.mockReset()
  m.request.mockResolvedValue(portrait)
})

test('unknown performance remains unknown and planned help is distinct from actual help', async () => {
  render(<LearningSupport />)
  expect(await screen.findByText(/这是未知，不代表不会/)).toBeVisible()
  expect(screen.getByText(/当前题实际帮助：2 \/ 3/)).toBeVisible()
  expect(screen.queryByText(/掌握率/)).toBeNull()
})

test('edits only self-reported profile with revision guard including explicit clearing', async () => {
  render(<LearningSupport />)
  fireEvent.click(await screen.findByRole('button', { name: '补充或修改' }))
  fireEvent.change(screen.getByRole('textbox', { name: '我目前会什么' }), {
    target: { value: '会解简单方程' },
  })
  fireEvent.click(screen.getByRole('button', { name: '保存我的学习信息' }))
  await waitFor(() =>
    expect(m.request).toHaveBeenCalledWith(
      '/topics/p/learning-support/profile',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({
          source_revision: 8,
          fields: {
            prior_knowledge: '会解简单方程',
            preferences: '',
          },
        }),
      })
    )
  )
})

test('learner can restore support without changing current task evidence', async () => {
  render(<LearningSupport />)
  fireEvent.click(await screen.findByRole('button', { name: '多给些支持' }))
  await waitFor(() =>
    expect(m.request).toHaveBeenCalledWith(
      '/topics/p/learning-support/choice',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ objective_id: 'k', action: 'restore_support' }),
      })
    )
  )
  expect(await screen.findByText(/当前题实际使用过的帮助与作答记录保持可追溯/)).toBeVisible()
})

test('strategy trial is opt in and empty data never becomes an effectiveness percentage', async () => {
  render(<LearningSupport />)
  fireEvent.click(screen.getByRole('button', { name: '教学方式试用' }))
  expect(await screen.findByText(/暂没有足够的可比后测/)).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '开启本人项目的策略试用' }))
  await waitFor(() =>
    expect(m.request).toHaveBeenCalledWith(
      '/topics/p/wenjin/consent',
      expect.objectContaining({ body: JSON.stringify({ enabled: true }) })
    )
  )
})

test('discusses strategy using the existing tutor instead of claiming weights were trained', async () => {
  render(<LearningSupport />)
  fireEvent.click(screen.getByRole('button', { name: '教学方式试用' }))
  fireEvent.click(await screen.findByRole('button', { name: '请津津评估下一步教学方式' }))
  expect(m.prompt).toHaveBeenCalledWith(expect.stringContaining('自主判断'), 'mastery_opening')
  expect(m.push).toHaveBeenCalledWith(expect.stringContaining('/learning/mastery/p/sessions'))
})

test('does not display a late portrait from the previous project', async () => {
  let finish!: (v: unknown) => void
  m.request
    .mockImplementationOnce(
      () =>
        new Promise(resolve => {
          finish = resolve
        })
    )
    .mockResolvedValueOnce({
      ...portrait,
      name: 'new project',
      objectives: [{ ...point, name: 'new objective' }],
    })
  const page = render(<LearningSupport />)
  m.pathId = 'new'
  page.rerender(<LearningSupport />)
  await screen.findByRole('option', { name: 'new objective' })
  await act(async () => finish(portrait))
  expect(screen.queryByRole('option', { name: '代入法' })).toBeNull()
  expect(screen.getByRole('option', { name: 'new objective' })).toBeInTheDocument()
})
