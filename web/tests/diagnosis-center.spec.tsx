import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import DiagnosisCenter from '@/components/learning/DiagnosisCenter'
const m = vi.hoisted(() => ({ request: vi.fn(), pathId: 'p' }))
const { request } = m
vi.mock('@/components/learning/GoalContractPanel', () => ({ default: () => null }))
vi.mock('next/navigation', () => ({ useParams: () => ({ pathId: m.pathId }) }))
vi.mock('@/components/learning/LearningShell', () => ({
  LearningShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))
vi.mock('@/lib/learning-project-api', () => ({
  projectRequest: m.request,
  projectEndpoint: (id: string) => '/topics/' + id,
  learningProjectRoute: () => '/learning/projects/p',
}))
const quality = {
  total: 1,
  eligible: 1,
  excluded: 0,
  sequences: 1,
  results: { correct: 1 },
  assistance: { independent: 1 },
  exclusions: {},
  skills: { 方程: 1 },
}
const row = {
  observation_id: 'e',
  objective_name: '方程',
  ability: 'application',
  answer: '2',
  result: 'correct',
  assistance: 'independent',
  validity: 'recorded',
  occurred_at: 1,
  eligibility: 'eligible',
  exclusion_reasons: [],
  session_id: 's',
  turn_id: 't',
  question_id: 'q',
  feedback: '正确',
  context: { task_prompt: '2x=4', grading_method: 'deterministic' },
  events: [],
}
beforeEach(() => {
  m.pathId = 'p'
  request.mockReset()
  request.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path.includes('/source?'))
      return {
        title: '原对话',
        located: true,
        messages: [{ id: 1, role: 'user', content: '2', matched: true }],
      }
    if (init?.method === 'POST')
      return {
        id: 'd',
        created_at: 1,
        source_revision: 2,
        digest: 'hash',
        quality,
        rows: [],
        provenance: [row],
      }
    if (path.endsWith('/datasets')) return []
    return {
      name: '方程',
      source_revision: 2,
      rows: [row],
      quality,
      limitations: [],
      self_reports: [],
    }
  })
  HTMLElement.prototype.scrollIntoView = vi.fn()
})
describe('诊断中心', () => {
  it('opens exact source and closes with Escape', async () => {
    render(<DiagnosisCenter />)
    fireEvent.click(screen.getByRole('button', { name: '证据溯源' }))
    fireEvent.click(await screen.findByText('查看证据与原对话'))
    expect(await screen.findByText(/来源轮次/)).toBeVisible()
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })
  it('creates a revision-bound immutable snapshot', async () => {
    render(<DiagnosisCenter />)
    await screen.findByText('学习证据分布')
    fireEvent.click(screen.getByRole('button', { name: '数据质量' }))
    fireEvent.click(screen.getByRole('button', { name: '保存不可变快照' }))
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        '/topics/p/diagnosis/datasets',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ source_revision: 2, excluded_ids: [], synthetic: false }),
        })
      )
    )
    expect(await screen.findByText('数据集 d')).toBeVisible()
  })
  it('does not create a dataset after excluding its only eligible row', async () => {
    render(<DiagnosisCenter />)
    await screen.findByText('学习证据分布')
    fireEvent.click(screen.getByRole('button', { name: '数据质量' }))
    fireEvent.click(screen.getByLabelText(/排除 方程/))
    expect(screen.getByRole('button', { name: '保存不可变快照' })).toBeDisabled()
  })
})

it('discards old diagnosis requests after a project switch', async () => {
  let finish!: (v: unknown) => void
  const catalogue = {
    name: 'old project',
    source_revision: 2,
    rows: [row],
    quality,
    limitations: [],
    self_reports: [],
  }
  request.mockImplementation((path: string) =>
    path === '/topics/p/diagnosis/evidence'
      ? new Promise(resolve => {
          finish = resolve
        })
      : Promise.resolve(
          path.endsWith('/datasets')
            ? []
            : { ...catalogue, rows: [{ ...row, objective_name: 'new objective' }] }
        )
  )
  const page = render(<DiagnosisCenter />)
  m.pathId = 'new'
  page.rerender(<DiagnosisCenter />)
  await screen.findByText('学习证据分布')
  fireEvent.click(screen.getByRole('button', { name: '证据溯源' }))
  await screen.findByRole('cell', { name: /new objective/ })
  await act(async () => finish(catalogue))
  expect(screen.queryByRole('cell', { name: /方程/ })).toBeNull()
  expect(screen.getByRole('cell', { name: /new objective/ })).toBeInTheDocument()
})
