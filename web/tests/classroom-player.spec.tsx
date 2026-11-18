import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { test, expect, vi } from 'vitest'
import Player from '@/components/learning/ClassroomPlayer'
vi.mock('@/lib/api',()=>({apiFetch:vi.fn(async()=>({ok:true,text:async()=> 'WEBVTT\n\n00:00:10.000 --> 00:00:20.000\n现在讲解第一步'}))}))
const timeline=[{page_id:'a',index:3,title:'定义',start:0,end:30},{page_id:'b',index:4,title:'例题',start:30,end:60}]

test('restores position, shows captions, saves current pause position and navigates by timeline',async()=>{
  const save=vi.fn(async()=>{}),navigate=vi.fn()
  const {container}=render(<Player kind="voice" source="/voice.wav" subtitles="/captions.vtt" timeline={timeline} playback={{seconds:12,rate:1.25}} save={save} navigate={navigate}/>)
  const audio=container.querySelector('audio')!
  Object.defineProperty(audio,'duration',{value:60,configurable:true})
  fireEvent.loadedMetadata(audio)
  expect(audio.currentTime).toBe(12);expect(audio.playbackRate).toBe(1.25)
  fireEvent.timeUpdate(audio)
  expect(await screen.findByText('现在讲解第一步')).toBeVisible()
  fireEvent.click(screen.getByRole('button',{name:'0:30 · 例题'}))
  expect(audio.currentTime).toBe(30)
  fireEvent.seeked(audio)
  await waitFor(()=>expect(save).toHaveBeenCalledWith(30,1.25))
  fireEvent.click(screen.getByRole('button',{name:'查看当前讲解页'}))
  expect(navigate).toHaveBeenCalledWith(4)
  audio.currentTime=35;fireEvent.pause(audio)
  await waitFor(()=>expect(save).toHaveBeenCalledWith(35,1.25))
})

test('video uses real caption track and can hand current page to tutor',()=>{
  vi.spyOn(HTMLMediaElement.prototype,'pause').mockImplementation(()=>{})
  const tutor=vi.fn(),save=vi.fn(async()=>{})
  const {container}=render(<Player kind="video" source="/lesson.mp4" subtitles="/captions.vtt" timeline={timeline} playback={{}} save={save} tutor={tutor}/>)
  expect(container.querySelector('video track')).toHaveAttribute('src','/captions.vtt')
  fireEvent.click(screen.getByRole('button',{name:'暂停并向津津提问'}))
  expect(tutor).toHaveBeenCalledWith(3)
  vi.restoreAllMocks()
})
