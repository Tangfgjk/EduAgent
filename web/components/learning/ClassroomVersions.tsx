'use client'
import { useState } from 'react'
import { projectEndpoint, projectRequest } from '@/lib/learning-project-api'
import { scopedUrl } from '@/lib/workspace-scope'
type Edition = { id: string; version: number; created: number; reason: string }
export default function ClassroomVersions({
  pathId,
  cid,
  version = 1,
}: {
  pathId: string
  cid: string
  version?: number
}) {
  const [editions, setEditions] = useState<Edition[] | null>(null),
    [error, setError] = useState('')
  return (
    <details
      className="my-4 rounded-xl border border-border p-3"
      onToggle={e => {
        if (e.currentTarget.open && !editions) {
          setError('')
          void projectRequest<Edition[]>(
            projectEndpoint(pathId) + '/classrooms/' + encodeURIComponent(cid) + '/editions'
          )
            .then(setEditions)
            .catch(() => setError('版本记录暂未加载，请收起后重试。'))
        }
      }}
    >
      <summary className="cursor-pointer text-sm">课件版本 · 当前第 {version} 版</summary>
      {error && (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {error}
        </p>
      )}
      {editions?.map(v => (
        <div className="mt-3 flex flex-wrap items-center gap-3 text-sm" key={v.id}>
          <span>
            第 {v.version} 版 · {v.reason}
          </span>
          <span className="text-xs text-muted-foreground">
            {new Date(v.created * 1000).toLocaleString('zh-CN')}
          </span>
          {v.id === cid ? (
            <span>当前版本</span>
          ) : (
            <a
              className="text-primary underline"
              href={scopedUrl(
                '/learning/projects/' +
                  encodeURIComponent(pathId) +
                  '/classroom?classroom=' +
                  encodeURIComponent(v.id)
              )}
            >
              打开此版本
            </a>
          )}
        </div>
      ))}
    </details>
  )
}
