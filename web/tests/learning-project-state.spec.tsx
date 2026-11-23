import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import { it, expect, vi, beforeEach } from 'vitest'
import LearningProject from '@/components/learning/LearningProject'
const m = vi.hoisted(() => ({ pathId: 'p', request: vi.fn(), sessions: vi.fn(), push: vi.fn() }))
vi.mock('next/navigation', () => ({
  useParams: () => ({ pathId: m.pathId }),
  useRouter: () => ({ push: m.push }),
}))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ i18n: { language: 'zh' } }) }))
vi.mock('@/components/learning/GoalContractPanel', () => ({ default: () => null }))
vi.mock('@/components/learning/DiagnosticFactsPanel', () => ({ default: () => null }))
vi.mock('@/components/learning/DiagnosticImpact', () => ({ default: () => null }))
vi.mock('@/components/learning/ExternalDiagnosisPanel', () => ({ default: () => null }))
vi.mock('@/components/common/MarkdownRenderer', () => ({ default: () => null }))
vi.mock('@/components/learning/LearningShell', () => ({
  LearningShell: ({ title, children }: { title: string; children: React.ReactNode }) => (
    <main>
      <h1>{title}</h1>
      {children}
    </main>
  ),
}))
vi.mock('@/lib/learning-project-api', () => ({
  projectEndpoint: (id: string) => '/topics/' + id,
  projectRequest: m.request,
  learningProjectRoute: (id: string) => '/projects/' + id,
}))
vi.mock('@/lib/learning-api', () => ({ fetchMasteryTopicSessions: m.sessions }))
const project = (name: string) => ({
  topic: { name, metadata: { goal: 'Learn' }, map: { modules: [] }, sources: [], next: {} },
  location: {},
  chapters: {},
})
beforeEach(() => {
  m.pathId = 'p'
  m.request.mockReset()
  m.sessions.mockReset()
  m.sessions.mockResolvedValue([])
})
it('resets project tabs and conversations immediately when switching project', async () => {
  let finish!: (v: unknown) => void
  m.request.mockResolvedValueOnce(project('old project')).mockImplementationOnce(
    () =>
      new Promise(done => {
        finish = done
      })
  )
  m.sessions
    .mockResolvedValueOnce([{ session_id: 'old-chat', title: 'old conversation', updated_at: 1 }])
    .mockImplementationOnce(() => new Promise(() => {}))
  const page = render(<LearningProject />)
  await screen.findByRole('link', {name:/old conversation/})
  m.pathId = 'new'
  page.rerender(<LearningProject />)
  expect(screen.queryByText('old conversation')).toBeNull()
  await act(async () => finish(project('new project')))
  expect(screen.queryByText('old conversation')).toBeNull()
})
it('reports a failed conversation list and retries it without claiming there is no history', async () => {
  m.request.mockResolvedValue(project('project'))
  m.sessions
    .mockRejectedValueOnce(new Error('history network failure'))
    .mockResolvedValueOnce([{ session_id: 'chat', title: 'restored conversation', updated_at: 1 }])
  render(<LearningProject />)
  await screen.findByRole('alert')
  expect(screen.queryByText('还没有项目对话，从上方新建对话开始。')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '重试读取对话' }))
  await screen.findByRole('link', {name:/restored conversation/})
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
})
