'use client'
import { CheckCycles } from './IndependentChecks'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useTranslation } from 'react-i18next'
import { listNotebookEntries, type NotebookEntry } from '@/lib/notebook-api'
import { practiceRoute } from '@/lib/learning-routes'

export default function ProjectPractice({ pathId }: { pathId: string }) {
  return <PracticeHistory key={pathId} pathId={pathId} />
}
function PracticeHistory({ pathId }: { pathId: string }) {
  const { t } = useTranslation()
  const [offset, setOffset] = useState(0)
  const [retry, setRetry] = useState(0)
  const requestKey = `${offset}:${retry}`
  const [page, setPage] = useState<{
    key: string
    rows: NotebookEntry[]
    total: number
    error: string
  } | null>(null)
  const busy = page?.key !== requestKey
  const rows = busy ? [] : page?.rows || []
  const total = page?.total || 0
  const error = busy ? '' : page?.error || ''
  useEffect(() => {
    let active = true
    listNotebookEntries({ mastery_path_id: pathId, limit: 30, offset })
      .then(data => {
        if (active) setPage({ key: requestKey, rows: data.items, total: data.total, error: '' })
      })
      .catch(e => {
        if (active)
          setPage({
            key: requestKey,
            rows: [],
            total: 0,
            error: e instanceof Error ? e.message : String(e),
          })
      })
    return () => {
      active = false
    }
  }, [pathId, offset, retry, requestKey])
  return (
    <section>
      <CheckCycles pathId={pathId} compact />
      <details className="mt-6">
        <summary className="cursor-pointer text-sm">历史题目与练习记录</summary>
        <h2 className="text-lg font-semibold">{t('项目练习')}</h2>
        <p className="my-3 text-sm text-muted-foreground">
          {t('只展示来源明确关联到当前项目的题目。')}
        </p>
        {error && (
          <div role="alert" className="my-3 text-sm text-destructive">
            {error}
            <button
              className="ml-3 rounded-lg border border-border px-3 py-1"
              disabled={busy}
              onClick={() => setRetry(n => n + 1)}
            >
              {t('Retry')}
            </button>
          </div>
        )}
        {busy ? (
          <p role="status">{t('正在读取…')}</p>
        ) : (
          !error && (
            <ul className="space-y-2">
              {rows.map(row => (
                <li key={row.id}>
                  <Link
                    href={practiceRoute(new URLSearchParams({ question: String(row.id) }))}
                    className="block rounded-xl border border-border p-4"
                  >
                    <p className="line-clamp-2">{row.question}</p>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {row.is_correct ? t('最近作答正确') : t('待巩固')}
                    </p>
                  </Link>
                </li>
              ))}
            </ul>
          )
        )}
        {!busy && !error && !rows.length && (
          <p className="py-6 text-muted-foreground">
            {t('暂无关联练习，项目陪学中完成的题目会收录在这里。')}
          </p>
        )}
        <div className="mt-4 flex gap-3">
          <button disabled={!offset || busy} onClick={() => setOffset(n => Math.max(0, n - 30))}>
            {t('上一页')}
          </button>
          <button
            disabled={busy || !!error || offset + rows.length >= total}
            onClick={() => setOffset(n => n + 30)}
          >
            {t('下一页')}
          </button>
        </div>
      </details>
    </section>
  )
}
