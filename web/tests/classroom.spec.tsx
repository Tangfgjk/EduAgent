vi.mock('@/components/learning/ClassroomMedia', () => ({ default: () => null }))
import { StrictMode } from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { test, it, expect, vi, afterEach } from 'vitest'
import Classroom from '@/components/learning/Classroom'
vi.mock('@/components/common/MarkdownRenderer', () => ({
  default: ({ content }: { content: string }) => <div>{content}</div>,
}))
const { request } = vi.hoisted(() => ({ request: vi.fn() }))
vi.mock('next/navigation', () => ({
  useParams: () => ({ pathId: 'p' }),
  useRouter: () => ({ push: vi.fn() }),
}))
vi.mock('@/components/learning/LearningShell', () => ({
  LearningShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}))
vi.mock('@/lib/learning-project-api', () => ({
  projectRequest: request,
  projectEndpoint: (id: string) => '/topics/' + id,
  learningProjectRoute: (id: string) => '/learning/projects/' + id,
}))
afterEach(() => window.history.replaceState({}, '', '/'))
const base = {
  id: 'c',
  title: '方程课堂',
  state: 'awaiting_confirmation',
  outline: [{ title: '定义', objective_id: 'k', kind: 'slide', goal: '理解定义' }],
  pages: [],
  position: 0,
  answers: {},
  conversation: [],
}
test('outline requires confirmation before content generation', async () => {
  request.mockImplementation(async (_p: string, init?: RequestInit) =>
    init ? { ...base, state: 'queued_pages' } : [base]
  )
  render(<Classroom />)
  await screen.findByText('确认这堂课怎么学')
  fireEvent.change(await screen.findByLabelText('第1页标题'), { target: { value: '新的定义标题' } })
  fireEvent.click(screen.getByRole('button', { name: '确认大纲并生成' }))
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/topics/p/classrooms/c/confirm',
      expect.objectContaining({ method: 'POST', body: expect.stringContaining('新的定义标题') })
    )
  )
})
test('quiz pauses next page and offers explicit unscored skip', async () => {
  const course = {
    ...base,
    state: 'ready',
    pages: [
      {
        id: 'scene-0',
        title: '检查定义',
        kind: 'quiz',
        goal: '应用',
        content: '按条件判断',
        steps: [],
        source_ids: [],
        question: '哪个符合定义？',
        options: ['甲', '乙'],
      },
      {
        id: 'scene-1',
        title: '总结',
        kind: 'slide',
        goal: '回顾',
        content: '总结',
        steps: [],
        source_ids: [],
      },
    ],
  }
  request.mockResolvedValue([course])
  render(<Classroom />)
  await screen.findByText('哪个符合定义？')
  expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '跳过本题（不计分）' })).toBeVisible()
  expect(screen.getByRole('button', { name: '提交回答' })).toBeDisabled()
  fireEvent.click(screen.getByLabelText('甲'))
  expect(screen.getByRole('button', { name: '提交回答' })).toBeEnabled()
})

test('invalid classroom deep link cannot show another classroom', async () => {
  window.history.replaceState({}, '', '/?classroom=missing')
  request.mockImplementation(async (path: string) => {
    if (path.endsWith('/missing')) throw new Error('课堂不存在')
    return [base]
  })
  render(<Classroom />)
  expect(await screen.findByRole('alert')).toHaveTextContent('课堂不存在')
  expect(screen.queryByText('确认这堂课怎么学')).toBeNull()
  window.history.replaceState({}, '', '/')
})
test('cancelled classroom shows resume without endless waiting', async () => {
  request.mockImplementation(async (_p: string, init?: RequestInit) =>
    init ? { ...base, state: 'queued_outline' } : [{ ...base, state: 'cancelled' }]
  )
  render(<Classroom />)
  const button = await screen.findByRole('button', { name: '恢复生成' })
  expect(screen.getByText(/生成已取消，已准备内容保留/)).toBeVisible()
  fireEvent.click(button)
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/topics/p/classrooms/c/control',
      expect.objectContaining({ body: JSON.stringify({ action: 'resume' }) })
    )
  )
})
test('whiteboard resumes expanded steps', async () => {
  request.mockResolvedValue([
    {
      ...base,
      state: 'ready',
      whiteboard_steps: { 'scene-0': 1 },
      pages: [
        {
          id: 'scene-0',
          title: '板书',
          kind: 'whiteboard',
          goal: '理解',
          content: '开始',
          steps: ['第一步', '第二步', '第三步'],
          source_ids: [],
        },
      ],
    },
  ])
  render(<Classroom />)
  await screen.findByText('1. 第一步')
  expect(screen.getByText('2. 第二步')).toBeVisible()
  expect(screen.queryByText('3. 第三步')).toBeNull()
})

test('exact scene survives repeated initial loads', async () => {
  window.history.replaceState({}, '', '/?classroom=c&scene=scene-1')
  const course = {
    ...base,
    state: 'ready',
    pages: [
      {
        id: 'scene-0',
        title: '第一页',
        kind: 'slide',
        goal: '了解',
        content: '旧位置',
        steps: [],
        source_ids: [],
      },
      {
        id: 'scene-1',
        title: '精确白板来源',
        kind: 'whiteboard',
        goal: '理解',
        content: '精确来源',
        steps: ['一步', '二步'],
        source_ids: [],
      },
    ],
  }
  request.mockImplementation(async (path: string, init?: RequestInit) =>
    path.endsWith('/classrooms') ? [course] : course
  )
  render(
    <StrictMode>
      <Classroom />
    </StrictMode>
  )
  expect(await screen.findByRole('heading', { name: '精确白板来源' })).toBeVisible()
  await waitFor(() => expect(request.mock.calls.length).toBeGreaterThanOrEqual(4))
  expect(screen.getByRole('heading', { name: '精确白板来源' })).toBeVisible()
  window.history.replaceState({}, '', '/')
})

it('new teaching deck follows original curriculum and has no second confirmation', async () => {
  const course = {
    ...base,
    mode: 'curriculum_ppt',
    state: 'ready',
    curriculum: [{ module_id: 'm', module_name: '原章节', objective_id: 'k', title: '原知识点' }],
    pages: [
      {
        id: 'scene-0',
        title: '视觉定义页',
        kind: 'slide',
        goal: '理解',
        content: '解释',
        steps: [],
        source_ids: [],
        canvas: [{ type: 'rect', x: 0, y: 0, w: 1200, h: 675, fill: '#fff' }],
      },
    ],
  }
  request.mockImplementation(async (path: string) =>
    path.endsWith('/project')
      ? {
          topic: {
            map: {
              modules: [
                { id: 'm', name: '原章节', knowledge_points: [{ id: 'k', name: '原知识点' }] },
              ],
            },
          },
        }
      : [course]
  )
  render(<Classroom />)
  await screen.findByText('项目学习目录')
  expect(screen.queryByText('确认这堂课怎么学')).toBeNull()
  expect(screen.getByRole('img', { name: '视觉定义页' })).toBeVisible()
  expect(screen.getByRole('button', { name: '学完了，让津津检查理解' })).toBeVisible()
})


test('delete requires confirmation and returns to setup for a fresh classroom', async () => {
  window.history.replaceState({}, '', '/?classroom=c&scene=scene-0&dt_workspace=')
  request.mockClear()
  const course = { ...base, state: 'cancelled', pages: [{ id: 'scene-0', title: '旧课件', kind: 'slide', content: '定义说明', steps: [], source_ids: [] }] }
  request.mockImplementation(async (path: string, init?: RequestInit) => {
    if (init?.method === 'DELETE') return { id: 'c', deleted: true }
    if (init?.method === 'POST') return { ...base, id: 'new-classroom', state: 'queued_pages' }
    return path.endsWith('/classrooms') ? [course] : course
  })
  render(<Classroom />)
  fireEvent.click(await screen.findByRole('button', { name: '删除课堂' }))
  expect(screen.getByRole('alertdialog')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '取消' }))
  expect(request.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '删除课堂' }))
  fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
  await waitFor(() => expect(request).toHaveBeenCalledWith('/topics/p/classrooms/c', { method: 'DELETE' }))
  expect(await screen.findByText(/课堂已删除/)).toBeVisible()
  expect(screen.queryByRole('button', { name: '删除课堂' })).toBeNull()
  expect(window.location.search).not.toContain('classroom=')
  expect(window.location.search).not.toContain('scene=')
  expect(window.location.search).toContain('dt_workspace=')
  expect(screen.getByText('准备一堂新课 / 选择历史课堂').closest('details')).toHaveAttribute('open')
  fireEvent.click(screen.getByRole('button', { name: '按已有目录生成详解PPT' }))
  await waitFor(() => expect(request).toHaveBeenCalledWith('/topics/p/classrooms', expect.objectContaining({
    method: 'POST', body: JSON.stringify({ mode: 'curriculum_ppt', module_id: '', objective_ids: [] }),
  })))
  window.history.replaceState({}, '', '/')
})

test('failed delete keeps the classroom available and shows the error', async () => {
  request.mockImplementation(async (path: string, init?: RequestInit) => {
    if (init?.method === 'DELETE') throw new Error('连接中断，未删除')
    return path.endsWith('/classrooms') ? [{ ...base, state: 'cancelled' }] : base
  })
  render(<Classroom />)
  fireEvent.click(await screen.findByRole('button', { name: '删除课堂' }))
  fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('未删除')
  expect(screen.getByRole('button', { name: '删除课堂' })).toBeVisible()
  expect(screen.getByRole('button', { name: '确认删除' })).toBeEnabled()
})
