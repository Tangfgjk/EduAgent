import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import ClassroomEditor from '@/components/learning/ClassroomEditor'
import ClassroomVersions from '@/components/learning/ClassroomVersions'
const { request, push } = vi.hoisted(() => ({ request: vi.fn(), push: vi.fn() }))
vi.mock('next/navigation', () => ({
  useParams: () => ({ pathId: 'p', cid: 'c' }),
  useRouter: () => ({ push }),
  useSearchParams: () => new URLSearchParams(),
}))
vi.mock('@/lib/learning-project-api', () => ({
  projectRequest: request,
  projectEndpoint: (id: string) => '/topics/' + id,
}))
vi.mock('@/components/learning/LearningShell', () => ({
  LearningShell: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
}))
vi.mock('@/components/common/MarkdownRenderer', () => ({
  default: ({ content }: { content: string }) => <div>{content}</div>,
}))
vi.mock('@/components/learning/SlideCanvas', () => ({
  default: ({ title }: { title: string }) => <div>预览画面：{title}</div>,
}))
const original = {
  id: 'd',
  revision: 1,
  parent_id: 'c',
  parent_version: 1,
  state: 'draft',
  changed_page_ids: [],
  pages: [
    {
      id: 'lecture',
      title: '定义说明',
      kind: 'slide',
      canvas: [],
      content: '两个条件',
      steps: [],
      narration: '旧讲稿',
      visual: {
        title: '定义说明',
        content: '两个条件',
        layout: 'concept',
        formula: '',
        panels: [{ heading: '条件', text: '两个未知数' }],
        notes: '',
        spoken_script: '旧讲稿',
        lines: [],
      },
    },
    { id: 'q', title: '随堂检查', kind: 'quiz', content: '检查', narration: '', steps: [] },
  ],
}
afterEach(() => {
  vi.resetAllMocks()
})
function setup(conflict = false) {
  request.mockImplementation(async (path: string, init?: RequestInit) => {
    if (init?.method === 'PUT') {
      if (conflict) throw Error('草稿已变化，请重新加载')
      const body = JSON.parse(init.body as string),
        d = structuredClone(original)
      d.revision = 2
      const page = d.pages[0]
      Object.assign(page, body.changes)
      if (page.visual) Object.assign(page.visual, body.changes)
      return { ...d, changed_page_ids: ['lecture'] }
    }
    if (path.endsWith('/publish')) return { id: 'new-c' }
    return structuredClone(original)
  })
}
test('loads the persisted draft, saves only changed fields and previews', async () => {
  setup()
  render(<ClassroomEditor />)
  const title = await screen.findByLabelText('页面标题')
  expect(title).toHaveValue('定义说明')
  expect(request).toHaveBeenCalledWith('/topics/p/classrooms/c/editor', { method: 'POST' })
  fireEvent.change(title, { target: { value: '新的定义说明' } })
  expect(screen.getByText('本页有未保存修改')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '保存并预览' }))
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/topics/p/classrooms/c/editor/d',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({
          revision: 1,
          page_id: 'lecture',
          changes: { title: '新的定义说明' },
        }),
      })
    )
  )
  await screen.findByText('预览画面：新的定义说明')
})
test('publishing saves dirty content first and uses returned revision exactly once', async () => {
  setup()
  render(<ClassroomEditor />)
  fireEvent.change(await screen.findByLabelText('正文说明'), { target: { value: '更完整的解释' } })
  const publish = screen.getByRole('button', { name: '发布新版本并打开' })
  fireEvent.click(publish)
  fireEvent.click(publish)
  await waitFor(() =>
    expect(push).toHaveBeenCalledWith(expect.stringContaining('classroom=new-c&scene=lecture'))
  )
  const calls = request.mock.calls.filter(([p]) => String(p).endsWith('/publish'))
  expect(calls).toHaveLength(1)
  expect(JSON.parse(calls[0][1].body)).toMatchObject({ revision: 2, reason: '调整课件讲解内容' })
})
test('a save conflict preserves typed content and never publishes', async () => {
  setup(true)
  render(<ClassroomEditor />)
  fireEvent.change(await screen.findByLabelText('正文说明'), { target: { value: '保留我的修改' } })
  fireEvent.click(screen.getByRole('button', { name: '发布新版本并打开' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('草稿已变化')
  expect(screen.getByLabelText('正文说明')).toHaveValue('保留我的修改')
  expect(request.mock.calls.some(([p]) => String(p).endsWith('/publish'))).toBe(false)
})
test('quiz contracts are read only and unsaved content is not silently discarded', async () => {
  setup()
  render(<ClassroomEditor />)
  await screen.findByLabelText('正文说明')
  expect(screen.getByRole('button', { name: /随堂检查 · 测验只读/ })).toBeDisabled()
  fireEvent.change(screen.getByLabelText('正文说明'), { target: { value: '未保存内容' } })
  fireEvent.click(screen.getByRole('button', { name: '← 返回课堂' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('尚未保存')
  expect(push).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '放弃本页修改' }))
  expect(screen.getByLabelText('正文说明')).toHaveValue('两个条件')
})
test('loads versions on demand and links each version to its own classroom', async () => {
  request.mockResolvedValue([
    { id: 'c', version: 2, created: 1, reason: '修正说明' },
    { id: 'old', version: 1, created: 1, reason: '首次生成' },
  ])
  render(<ClassroomVersions pathId="p" cid="c" version={2} />)
  expect(request).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText('课件版本 · 当前第 2 版'))
  expect(await screen.findByText('第 1 版 · 首次生成')).toBeVisible()
  expect(screen.getByRole('link', { name: '打开此版本' })).toHaveAttribute(
    'href',
    expect.stringContaining('classroom=old')
  )
})

test('duplicating a page selects the new persisted page without touching the original', async () => {
  request.mockImplementation(async (path: string) => {
    if (path.endsWith('/pages'))
      return {
        ...structuredClone(original),
        revision: 2,
        changed_page_ids: ['copy'],
        page_order_changed: true,
        selected_page_id: 'copy',
        pages: [
          original.pages[0],
          { ...structuredClone(original.pages[0]), id: 'copy', title: '新页：定义说明' },
          original.pages[1],
        ],
      }
    return structuredClone(original)
  })
  render(<ClassroomEditor />)
  await screen.findByLabelText('页面标题')
  fireEvent.click(screen.getByRole('button', { name: '复制为新页' }))
  await waitFor(() => expect(screen.getByLabelText('页面标题')).toHaveValue('新页：定义说明'))
  expect(request).toHaveBeenCalledWith(
    '/topics/p/classrooms/c/editor/d/pages',
    expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ revision: 1, action: 'duplicate', page_id: 'lecture' }),
    })
  )
})
