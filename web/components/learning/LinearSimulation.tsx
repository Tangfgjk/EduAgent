'use client'
import { useState } from 'react'
type State = {
  prediction?: string
  choices?: number[]
  explanation?: string
  result?: { correct: boolean; explanation: string }[]
}
export default function LinearSimulation({
  spec,
  state,
  busy,
  submit,
}: {
  spec: { a: number; b: number; c: number; title: string }
  state: State
  busy: boolean
  submit: (phase: string, text: string, choices: number[]) => void
}) {
  const [x, setX] = useState(state.choices?.[0] || 0),
    [y, setY] = useState(state.choices?.[1] || 0),
    [text, setText] = useState('')
  const left = spec.a * x + spec.b * y
  const px = (n: number) => 180 + n * 15,
    py = (n: number) => 180 - n * 15
  return (
    <section className="mt-3 space-y-3">
      <h4 className="font-semibold">
        {spec.title}：{spec.a}x + {spec.b}y = {spec.c}
      </h4>
      <p className="text-sm">预测 → 移动点并代入 → 解释。图中的直线是所有满足方程的点。</p>
      {!state.prediction ? (
        <>
          <textarea
            aria-label="方程预测"
            className="w-full rounded border p-2"
            value={text}
            onChange={e => setText(e.target.value)}
            placeholder="预测：哪些坐标点能使等式成立？"
          />
          <button
            disabled={busy || !text.trim()}
            className="rounded border p-2"
            onClick={() => {
              submit('predict', text, [])
              setText('')
            }}
          >
            保存预测
          </button>
        </>
      ) : (
        <>
          <svg
            viewBox="0 0 360 360"
            className="mx-auto w-full max-w-sm rounded-xl bg-background"
            role="img"
            aria-label="方程直线和可移动坐标点"
          >
            <defs>
              <clipPath id="equation-plot">
                <rect x="20" y="20" width="320" height="320" />
              </clipPath>
            </defs>
            <path d="M20 180H340M180 20V340" stroke="currentColor" opacity=".4" />
            <g clipPath="url(#equation-plot)">
              {spec.b !== 0 ? (
                <line
                  x1={px(-10)}
                  y1={py((spec.c + 10 * spec.a) / spec.b)}
                  x2={px(10)}
                  y2={py((spec.c - 10 * spec.a) / spec.b)}
                  stroke="#438f72"
                  strokeWidth="3"
                />
              ) : (
                <line
                  x1={px(spec.c / spec.a)}
                  x2={px(spec.c / spec.a)}
                  y1="20"
                  y2="340"
                  stroke="#438f72"
                  strokeWidth="3"
                />
              )}
              <circle cx={px(x)} cy={py(y)} r="6" fill={left === spec.c ? '#16a34a' : '#e88927'} />
            </g>
            <text x="22" y="350" fill="currentColor">
              P({x}, {y})；左边={left}，右边={spec.c}
            </text>
          </svg>
          <label className="block">
            x = {x}
            <input
              aria-label="坐标x"
              type="range"
              min="-10"
              max="10"
              value={x}
              disabled={!!state.explanation}
              onChange={e => setX(Number(e.target.value))}
              className="ml-3"
            />
          </label>
          <label className="block">
            y = {y}
            <input
              aria-label="坐标y"
              type="range"
              min="-10"
              max="10"
              value={y}
              disabled={!!state.explanation}
              onChange={e => setY(Number(e.target.value))}
              className="ml-3"
            />
          </label>
          {!state.explanation && (
            <button
              className="rounded border p-2"
              disabled={busy}
              onClick={() => submit('operate', '', [x, y])}
            >
              记录这次操作
            </button>
          )}
          {state.result?.map((r, i) => (
            <p key={i} className="text-sm">
              {r.explanation}
            </p>
          ))}
          {state.choices && !state.explanation && (
            <>
              <textarea
                aria-label="方程解释"
                className="w-full rounded border p-2"
                value={text}
                onChange={e => setText(e.target.value)}
                placeholder="为什么这个点在或不在直线上？与预测一致吗？"
              />
              <button
                className="rounded border p-2"
                disabled={busy || !text.trim()}
                onClick={() => submit('explain', text, [])}
              >
                保存解释
              </button>
            </>
          )}
          {state.explanation && <p className="text-sm">你的解释：{state.explanation}</p>}
        </>
      )}
    </section>
  )
}
