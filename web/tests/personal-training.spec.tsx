import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { it, expect, vi } from 'vitest'
import PersonalTraining from '@/components/learning/PersonalTraining'
const { request } = vi.hoisted(() => ({ request: vi.fn() }))
vi.mock('@/lib/learning-project-api', () => ({
  projectRequest: request,
  projectEndpoint: (id: string) => '/topics/' + id,
}))
it('trains with current project revision and explains insufficient data', async () => {
  request.mockImplementation(async (path: string, init?: RequestInit) =>
    init?.method === 'POST'
      ? { id: 'j', status: 'queued', source_revision: 8 }
      : path.endsWith('/jobs')
        ? []
        : [
            {
              id: 'r',
              status: 'insufficient_data',
              created_at: 1,
              n: 4,
              sequences: 1,
              reason: '需要更多独立记录',
              parameters: {},
              activated: false,
            },
          ]
  )
  render(<PersonalTraining pathId="p" revision={8} eligible={4} />)
  await waitFor(() => expect(screen.getByRole('button', { name: '训练并更新诊断' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: '训练并更新诊断' }))
  expect(await screen.findByText('需要更多独立记录')).toBeVisible()
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/topics/p/diagnosis/training',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ source_revision: 8 }) })
    )
  )
  expect(screen.getByText(/未启用候选参数/)).toBeVisible()
})

it('allows zero-data review without suggesting model activation', async () => {
  request.mockImplementation(async (path: string, init?: RequestInit) =>
    init?.method === 'POST' ? { id: 'zero', status: 'queued', source_revision: 2 } : []
  )
  render(<PersonalTraining pathId="empty" revision={2} eligible={0} />)
  expect(screen.getByText(/数据不足不代表你没有掌握/)).toBeVisible()
  const button = await screen.findByRole('button', { name: '训练并更新诊断' })
  expect(button).toBeEnabled()
  fireEvent.click(button)
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/topics/empty/diagnosis/training',
      expect.objectContaining({ method: 'POST' })
    )
  )
})

it('does not claim there are no models or allow training before its status is loaded', async () => {
  let resolve!: (v: unknown) => void
  request.mockImplementation((path: string) =>
    path.endsWith('/jobs')
      ? Promise.resolve([])
      : new Promise(done => {
          resolve = done
        })
  )
  render(<PersonalTraining pathId="p" revision={8} eligible={4} />)
  expect(screen.queryByText('暂无训练记录，诊断仍采用默认参数。')).toBeNull()
  expect(screen.getByRole('button', { name: /读取训练状态/ })).toBeDisabled()
  await act(async () => resolve([]))
  expect(screen.getByRole('button', { name: '训练并更新诊断' })).toBeEnabled()
})
it('recovers a failed status load through an explicit retry', async () => {
  request.mockRejectedValue(new Error('temporary outage'))
  render(<PersonalTraining pathId="p" revision={8} eligible={4} />)
  await screen.findByRole('alert')
  request.mockResolvedValue([])
  fireEvent.click(screen.getByRole('button', { name: '重新读取训练状态' }))
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  expect(screen.getByRole('button', { name: '训练并更新诊断' })).toBeEnabled()
})
