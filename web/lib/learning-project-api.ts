import { apiFetch, apiUrl } from '@/lib/api'
import { scopedUrl } from '@/lib/workspace-scope'
import type { MasteryTopic, TopicSourceInput } from '@/lib/learning-api'

export function learningProjectRoute(id: string, chapter?: string, workspace?: string) {
  return scopedUrl('/learning/projects/' + encodeURIComponent(id) + (chapter ? '/chapters/' + encodeURIComponent(chapter) : ''), workspace)
}

export async function projectRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(apiUrl('/api/mastery-paths' + path), init)
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请重试。')
  }
  return response.json()
}

export type LearningProject = {
  topic: MasteryTopic
  location: { chapter_id?: string; position?: number; surface?: string }
  chapters: Record<string, { created_at: number }>
}

export async function uploadProjectFile(file: File): Promise<TopicSourceInput> {
  const body = new FormData()
  body.append('file', file)
  return projectRequest<TopicSourceInput>('/project-materials', { method: 'POST', body })
}

export function projectEndpoint(id: string) {
  return '/topics/' + encodeURIComponent(id)
}
