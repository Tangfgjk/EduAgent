import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { test, expect, vi, beforeEach } from 'vitest'
import { CheckCycles } from '@/components/learning/IndependentChecks'
const { request, push, prompt } = vi.hoisted(() => ({
  request: vi.fn(),
  push: vi.fn(),
  prompt: vi.fn(),
}))
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ pathId: 'p' }),
}))
vi.mock('@/lib/pending-prompt', () => ({ setPendingPrompt: prompt }))
vi.mock('@/lib/mastery-mode', () => ({
  MASTERY_OPENING_SCOPE: 'mastery_opening',
  masterySessionRoute: (id: string, mode: string) => `/new/${id}/${mode}`,
}))
vi.mock('@/lib/learning-project-api', () => ({
  projectRequest: request,
  projectEndpoint: (id: string) => '/topics/' + id,
  learningProjectRoute: (id: string) => '/project/' + id,
}))
const row = {
  objective_id: 'k',
  objective_name: '代入法',
  module_id: 'm',
  phase: 'initial',
  phase_label: '独立核查',
  status: 'ready',
  due_at: null,
  completed_cycles: 0,
  feedback: '',
  evidence: [],
}
beforeEach(() => {
  request.mockReset()
  push.mockReset()
  prompt.mockReset()
})
test('starts a new task using existing tutor grading path', async () => {
  request.mockImplementation((path: string) =>
    Promise.resolve(
      path.endsWith('/start')
        ? { pending: false, prompt: '登记新题并等待回答', session_id: '' }
        : { objectives: [row], due_count: 0, rules: [] }
    )
  )
  render(<CheckCycles pathId="p" />)
  fireEvent.click(await screen.findByRole('button', { name: '开始独立核查' }))
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/topics/p/checks/start',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ objective_id: 'k' }) })
    )
  )
  expect(prompt).toHaveBeenCalledWith('登记新题并等待回答', 'mastery_opening')
  expect(push).toHaveBeenCalledWith('/new/p/study')
})
test('delayed review cannot be started before due time', async () => {
  request.mockResolvedValue({
    objectives: [
      { ...row, phase: 'review', phase_label: '延迟复习', status: 'waiting', due_at: 1900000000 },
    ],
    due_count: 0,
    rules: [],
  })
  render(<CheckCycles pathId="p" />)
  expect(await screen.findByRole('button', { name: '等待复习到期' })).toBeDisabled()
  expect(screen.getByText(/提前学习不替代/)).toBeVisible()
})
test('pending task resumes original conversation instead of replacing it', async () => {
  request.mockImplementation((path: string) =>
    Promise.resolve(
      path.endsWith('/start')
        ? { pending: true, session_id: 'old-session' }
        : { objectives: [row], due_count: 0, rules: [] }
    )
  )
  render(<CheckCycles pathId="p" />)
  fireEvent.click(await screen.findByRole('button', { name: '开始独立核查' }))
  await waitFor(() => expect(push).toHaveBeenCalledWith(expect.stringContaining('old-session')))
  expect(prompt).not.toHaveBeenCalled()
})
