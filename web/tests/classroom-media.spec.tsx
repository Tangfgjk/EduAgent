import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { vi, test, expect, beforeEach, afterEach } from 'vitest'
import Media, { parseCaptions } from '@/components/learning/ClassroomMedia'
const { request, blobRequest } = vi.hoisted(() => ({ request: vi.fn(), blobRequest: vi.fn() }))
vi.mock('@/lib/api', () => ({
  apiUrl: (path: string) => path,
  requestBlob: blobRequest,
  apiFetch: vi.fn(),
}))
const objectUrl = Object.getOwnPropertyDescriptor(URL, 'createObjectURL')
const revokeUrl = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL')
beforeEach(() => {
  request.mockReset()
  blobRequest.mockReset().mockResolvedValue(new Blob(['file']))
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:export') })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})
afterEach(() => {
  vi.restoreAllMocks()
  if (objectUrl) Object.defineProperty(URL, 'createObjectURL', objectUrl)
  else Reflect.deleteProperty(URL, 'createObjectURL')
  if (revokeUrl) Object.defineProperty(URL, 'revokeObjectURL', revokeUrl)
  else Reflect.deleteProperty(URL, 'revokeObjectURL')
})
const pdf = (state = 'ready') => ({ key: 'pdf-key', kind: 'pdf', index: 0, state, files: state === 'ready' ? ['lesson.pdf'] : [], interaction: {} })
vi.mock('@/lib/learning-project-api', () => ({
  projectRequest: request,
  projectEndpoint: (id: string) => '/topics/' + id,
}))
test('queues PDF export through authenticated project endpoint', async () => {
  request.mockImplementation((_path, init) => Promise.resolve(init?.method === 'POST' ? pdf('queued') : []))
  render(<Media pathId="p" cid="c" index={0} ready />)
  fireEvent.click(screen.getByRole('button', { name: '导出PDF' }))
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/topics/p/classrooms/c/assets',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ kind: 'pdf', index: 0 }) })
    )
  )
})
test('simulation starts with prediction and keeps classification hidden', async () => {
  request.mockResolvedValue([
    {
      key: 'a',
      kind: 'simulation',
      index: 0,
      state: 'ready',
      files: [],
      interaction: {},
      spec: {
        title: '分类练习',
        instructions: '判断条件',
        categories: ['符合', '不符合'],
        items: [{ label: '甲' }, { label: '乙' }],
      },
    },
  ])
  render(<Media pathId="p" cid="c" index={0} ready />)
  await screen.findByText('分类练习')
  expect(screen.queryByLabelText('分类1')).toBeNull()
  fireEvent.change(screen.getByLabelText('模拟预测'), { target: { value: '先看次数' } })
  fireEvent.click(screen.getByRole('button', { name: '保存预测，开始操作' }))
  await waitFor(() =>
    expect(request).toHaveBeenCalledWith(
      '/topics/p/classrooms/c/assets/a/interact',
      expect.objectContaining({
        body: JSON.stringify({ phase: 'predict', text: '先看次数', choices: [] }),
      })
    )
  )
})
test('subtitle timestamps retain exact cue text and timing', () => {
  expect(parseCaptions('WEBVTT\n\n00:00:01.000 --> 00:00:03.500\n中文讲解')).toEqual([
    { start: 1, end: 3.5, text: '中文讲解' },
  ])
})


test('export immediately downloads an already generated file', async () => {
  request.mockImplementation((_path, init) => Promise.resolve(init?.method === 'POST' ? pdf() : [pdf()]))
  render(<Media pathId="p" cid="c" index={0} ready />)
  fireEvent.click(screen.getByRole('button', { name: '导出PDF' }))
  await waitFor(() => expect(blobRequest).toHaveBeenCalledWith('/api/mastery-paths/topics/p/classrooms/c/assets/pdf-key/files/lesson.pdf'))
  expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1)
  expect(await screen.findByRole('status')).toHaveTextContent('已开始下载')
})

test('queued export downloads once it becomes ready and announces progress', async () => {
  let requested = false
  request.mockImplementation((_path, init) => {
    if (init?.method === 'POST') { requested = true; return Promise.resolve(pdf('queued')) }
    return Promise.resolve(requested ? [pdf()] : [])
  })
  render(<Media pathId="p" cid="c" index={0} ready />)
  fireEvent.click(screen.getByRole('button', { name: '导出PDF' }))
  await waitFor(() => expect(blobRequest).toHaveBeenCalledTimes(1))
  expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1)
  expect(await screen.findByRole('status')).toHaveTextContent('已开始下载')
})

test('download failures are visible and the existing file can be retried', async () => {
  request.mockImplementation((_path, init) => Promise.resolve(init?.method === 'POST' ? pdf() : [pdf()]))
  blobRequest.mockRejectedValueOnce(new Error('文件下载失败，请重试'))
  render(<Media pathId="p" cid="c" index={0} ready />)
  fireEvent.click(screen.getByRole('button', { name: '导出PDF' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('文件下载失败')
  expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('link', { name: '下载PDF' }))
  await waitFor(() => expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1))
})


test('audio and video can be prepared for available pages before full course is ready',async()=>{
  request.mockClear()
  request.mockImplementation((path,init)=>Promise.resolve(path.includes('capabilities')?{local:true,configured:false}:init?.method==='POST'?{key:'a',kind:'voice',state:'queued',files:[]}:[]))
  render(<Media pathId="p" cid="c" index={2} ready={false}/>)
  expect(screen.getByRole('button',{name:'导出PPTX'})).toBeDisabled()
  fireEvent.change(screen.getByLabelText('音视频生成范围'),{target:{value:'chapter'}})
  fireEvent.click(screen.getByRole('button',{name:'生成配音'}))
  await waitFor(()=>expect(request).toHaveBeenCalledWith('/topics/p/classrooms/c/assets',expect.objectContaining({body:JSON.stringify({kind:'voice',index:2,range:'chapter',engine:'local',rate:1})})))
})

test('running resources expose cancel and interrupted resources expose resume',async()=>{
  const running={key:'a',kind:'video',index:0,range:'page',indices:[0],state:'running',files:[],interaction:{},progress:{stage:'正在准备配音',completed:1,total:2}}
  request.mockImplementation((path,init)=>Promise.resolve(path.includes('capabilities')?{local:true,configured:false}:init?.method==='POST'?{}:[running]))
  render(<Media pathId="p" cid="c" index={0} ready/>)
  fireEvent.click(await screen.findByRole('button',{name:'取消此任务'}))
  await waitFor(()=>expect(request).toHaveBeenCalledWith('/topics/p/classrooms/c/assets/a/control',expect.objectContaining({body:JSON.stringify({action:'cancel'})})))
  expect(screen.getByLabelText('资源生成进度')).toHaveAttribute('value','1')
})
