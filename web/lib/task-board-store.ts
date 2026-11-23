'use client'

import { useSyncExternalStore } from 'react'
import { startVisibleEventStream } from './visible-event-stream'
import { activeWorkspaceId } from './workspace-scope'
import {
  getTaskBoard,
  normalizeTaskBoard,
  subscribeTaskBoardChanges,
  taskBoardUrl,
  type TaskBoard,
} from './task-board-api'

interface State {
  board: TaskBoard | null
  error: boolean
}
const empty: State = { board: null, error: false }
let state = empty
const listeners = new Set<() => void>()
let generation = 0
let refreshing: Promise<void> | undefined
let owners = 0
let stop: (() => void) | undefined
let syncWorkspaceId: string | undefined

function accept(board: TaskBoard) {
  // A delayed GET or mutation must not roll back a newer streamed snapshot.
  if (state.board && board.revision < state.board.revision) return
  state = { board: normalizeTaskBoard(board), error: false }
  listeners.forEach(listener => listener())
}
export async function refreshTaskBoard() {
  if (refreshing) return refreshing
  const currentGeneration = generation
  refreshing = (async () => {
    try {
      const board = await getTaskBoard(undefined, syncWorkspaceId ?? activeWorkspaceId())
      if (generation === currentGeneration) accept(board)
    } catch {
      if (generation === currentGeneration) {
        state = { ...state, error: true }
        listeners.forEach(listener => listener())
      }
    } finally {
      if (generation === currentGeneration) refreshing = undefined
    }
  })()
  return refreshing
}

/** One connection per workspace board, owned by the workspace shell and reset with its scope. */
export function startTaskBoardSync({ stream = true }: { stream?: boolean } = {}) {
  if (++owners === 1) {
    const workspaceId = activeWorkspaceId()
    syncWorkspaceId = workspaceId
    const currentGeneration = generation
    const isCurrent = () => generation === currentGeneration
    const unsubscribe = subscribeTaskBoardChanges((board, responseWorkspaceId) => {
      if (isCurrent() && responseWorkspaceId === workspaceId) accept(board)
    })
    const stopStream = startVisibleEventStream({
      url: taskBoardUrl('/events', workspaceId),
      enabled: stream,
      interval: 3000,
      refresh: () => {
        if (isCurrent()) void refreshTaskBoard()
      },
      onMessage: event => {
        if (!isCurrent()) return
        try {
          accept(JSON.parse(event.data))
        } catch {
          void refreshTaskBoard()
        }
      },
    })
    stop = () => {
      unsubscribe()
      stopStream()
    }
  }
  let released = false
  return () => {
    if (released) return
    released = true
    if (--owners === 0) {
      stop?.()
      stop = undefined
      state = empty
      syncWorkspaceId = undefined
      generation++
      refreshing = undefined
    }
  }
}
function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
export function useTaskBoard() {
  return useSyncExternalStore(
    subscribe,
    () => state,
    () => empty
  )
}
