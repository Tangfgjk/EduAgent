import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { expect, it, vi, beforeEach } from 'vitest'
import ProjectPractice from '@/components/learning/ProjectPractice'
const { list } = vi.hoisted(() => ({ list: vi.fn() }))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (s: string) => s }) }))
vi.mock('@/lib/notebook-api', () => ({ listNotebookEntries: list }))
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))
vi.mock('@/lib/learning-project-api', () => ({
  projectRequest: () => Promise.resolve({ objectives: [], due_count: 0, rules: [] }),
  projectEndpoint: (id: string) => '/projects/' + id,
}))
it('requests only explicitly associated project questions', async () => {
  list.mockResolvedValue({
    items: [{ id: 8, question: 'Project task', is_correct: true }],
    total: 1,
  })
  render(<ProjectPractice pathId="p-one" />)
  fireEvent.click(screen.getByText('历史题目与练习记录'))
  expect(await screen.findByText('Project task')).toBeVisible()
  expect(list).toHaveBeenCalledWith({ mastery_path_id: 'p-one', limit: 30, offset: 0 })
  expect(screen.getByRole('link', { name: /Project task/ })).toHaveAttribute(
    'href',
    expect.stringContaining('question=8')
  )
})

beforeEach(() => {
  list.mockReset()
})
it('starts the new project at its first page and clears old rows while loading', async () => {
  const items = Array.from({ length: 30 }, (_, id) => ({
    id,
    question: 'old question ' + id,
    is_correct: true,
  }))
  let finish!: (v: unknown) => void
  list
    .mockResolvedValueOnce({ items, total: 60 })
    .mockResolvedValueOnce({ items, total: 60 })
    .mockImplementationOnce(
      () =>
        new Promise(resolve => {
          finish = resolve
        })
    )
  const page = render(<ProjectPractice pathId="old" />)
  fireEvent.click(screen.getByText('历史题目与练习记录'))
  await screen.findByText('old question 0')
  fireEvent.click(screen.getByRole('button', { name: '下一页' }))
  await waitFor(() =>
    expect(list).toHaveBeenLastCalledWith({ mastery_path_id: 'old', limit: 30, offset: 30 })
  )
  await waitFor(() => expect(screen.getByRole('button', { name: '上一页' })).toBeEnabled())
  page.rerender(<ProjectPractice pathId="new" />)
  await waitFor(() =>
    expect(list).toHaveBeenLastCalledWith({ mastery_path_id: 'new', limit: 30, offset: 0 })
  )
  expect(screen.queryByText('old question 0')).toBeNull()
  await act(async () => finish({ items: [], total: 0 }))
})
it('offers a retry after loading fails instead of claiming there are no exercises', async () => {
  list
    .mockRejectedValueOnce(new Error('network unavailable'))
    .mockResolvedValueOnce({
      items: [{ id: 8, question: 'recovered question', is_correct: true }],
      total: 1,
    })
  render(<ProjectPractice pathId="p" />)
  fireEvent.click(screen.getByText('历史题目与练习记录'))
  await screen.findByRole('alert')
  expect(screen.queryByText('暂无关联练习，项目陪学中完成的题目会收录在这里。')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByText('recovered question')).toBeVisible()
})
