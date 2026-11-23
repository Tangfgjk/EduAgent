import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { test, expect, vi, beforeEach } from 'vitest'
import TeachAI from '@/components/learning/TeachAI'
const m = vi.hoisted(() => ({ request: vi.fn(), push: vi.fn(), query: '', prompt: vi.fn() }))
vi.mock('next/navigation', () => ({
  useParams: () => ({ pathId: 'p' }),
  useRouter: () => ({ push: m.push }),
  useSearchParams: () => new URLSearchParams(m.query),
}))
vi.mock('@/components/learning/LearningShell', () => ({
  LearningShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}))
vi.mock('@/components/common/MarkdownRenderer', () => ({
  default: ({ content }: { content: string }) => <p>{content}</p>,
}))
vi.mock('@/lib/learning-project-api', () => ({
  projectRequest: m.request,
  projectEndpoint: (id: string) => '/topics/' + id,
  learningProjectRoute: (id: string) => '/project/' + id,
}))
vi.mock('@/lib/pending-prompt', () => ({ setPendingPrompt: m.prompt }))
const initial = {
  id: 's',
  objective_id: 'k',
  objective_name: '方程的条件',
  persona: 'curious',
  student: { name: '小津', description: '会追问理由' },
  state: 'active',
  help_delivered: false,
  messages: [{ id: 'a', role: 'ai_student', text: '请你教教我', at: 1000 }],
  report: null,
}
const project = {
  topic: { map: { modules: [{ knowledge_points: [{ id: 'k', name: '方程的条件' }] }] } },
}
function setup(session: unknown = initial) {
  m.request.mockImplementation(async (path: string) =>
    path.endsWith('/project') ? project : path.endsWith('/teach-ai') ? [] : session
  )
}
beforeEach(() => {
  m.query = ''
  m.request.mockReset()
  m.push.mockReset()
  m.prompt.mockReset()
  Element.prototype.scrollIntoView = vi.fn()
})

test('starts teaching on an existing objective without requiring uploads', async () => {
  setup()
  m.request.mockImplementation(async (path: string, init?: RequestInit) =>
    init?.method === 'POST' ? initial : path.endsWith('/project') ? project : []
  )
  render(<TeachAI />)
  fireEvent.click(await screen.findByRole('button', { name: '开始讲解' }))
  await waitFor(() =>
    expect(m.request).toHaveBeenCalledWith(
      '/topics/p/teach-ai',
      expect.objectContaining({ method: 'POST' })
    )
  )
  expect(await screen.findByText('请你教教我')).toBeVisible()
  expect(m.push).toHaveBeenCalledWith(expect.stringContaining('teaching_session=s'))
})

test('restores saved dialogue and submits a human explanation', async () => {
  m.query = 'teaching_session=s'
  setup()
  render(<TeachAI />)
  fireEvent.change(await screen.findByRole('textbox', { name: '我的讲解' }), {
    target: { value: '两个未知数的次数必须符合条件' },
  })
  fireEvent.click(screen.getByRole('button', { name: '发送讲解' }))
  await waitFor(() =>
    expect(m.request).toHaveBeenCalledWith(
      '/topics/p/teach-ai/s/turn',
      expect.objectContaining({ body: expect.stringContaining('两个未知数的次数必须符合条件') })
    )
  )
  expect(m.request.mock.calls.find(([, i]) => i?.method === 'POST')?.[1].body).toContain(
    '"kind":"explain"'
  )
})

test('report links to human source and existing independent check workflow', async () => {
  m.query = 'teaching_session=s'
  setup({
    ...initial,
    state: 'finished',
    messages: [
      ...initial.messages,
      { id: 'human1', role: 'human', text: '我的实际讲解', at: 1001 },
    ],
    report: {
      summary: '仅评价本次讲解，仍需独立核查',
      human_turn_count: 1,
      assisted: false,
      models: ['test'],
      next: '用新任务验证',
      claims: [
        {
          dimension: 'concept',
          status: 'supported',
          observation: '本次解释了条件',
          human_turn_ids: ['human1'],
          source_ids: [],
        },
      ],
    },
  })
  render(<TeachAI />)
  expect(await screen.findByRole('region', { name: '讲解回顾' })).toBeVisible()
  expect(screen.getByRole('link', { name: '查看讲解原话 1' })).toHaveAttribute(
    'href',
    '#teach-turn-human1'
  )
  expect(screen.getByRole('link', { name: '用新任务独立核查' }).getAttribute('href')).toContain(
    '/checks?objective=k'
  )
  expect(screen.queryByRole('button', { name: '发送讲解' })).toBeNull()
})

test('retry keeps the original request identity and does not resubmit a new turn', async () => {
  m.query = 'teaching_session=s'
  setup({
    ...initial,
    latest_request: {
      id: 'original',
      kind: 'explain',
      text: '已保存的解释',
      state: 'failed',
      retryable: true,
      error: '稍后重试',
    },
  })
  render(<TeachAI />)
  fireEvent.click(await screen.findByRole('button', { name: '重试上次生成' }))
  await waitFor(() =>
    expect(m.request).toHaveBeenCalledWith(
      '/topics/p/teach-ai/s/turn',
      expect.objectContaining({
        body: JSON.stringify({ request_id: 'original', kind: 'explain', text: '已保存的解释' }),
      })
    )
  )
})

test('in-flight generation can be paused and stale sources prevent new submission', async () => {
  m.query = 'teaching_session=s'
  setup({
    ...initial,
    latest_request: { id: 'r', kind: 'explain', text: '解释', state: 'running', retryable: false },
  })
  const view = render(<TeachAI />)
  fireEvent.click(await screen.findByRole('button', { name: '暂停，稍后继续' }))
  await waitFor(() =>
    expect(m.request).toHaveBeenCalledWith(
      '/topics/p/teach-ai/s/control',
      expect.objectContaining({ body: JSON.stringify({ action: 'pause' }) })
    )
  )
  view.unmount()
  setup({ ...initial, stale: true })
  render(<TeachAI />)
  expect(await screen.findByText(/知识点或资料已变化/)).toBeVisible()
  expect(screen.queryByRole('button', { name: '发送讲解' })).toBeNull()
})
