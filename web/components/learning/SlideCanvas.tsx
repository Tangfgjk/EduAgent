'use client'
export type Element = {
  type: string
  x: number
  y: number
  w?: number
  h?: number
  fill?: string
  color?: string
  lines?: string[]
  size?: number
  bold?: boolean
  x2?: number
  y2?: number
  data?: string
}
export default function SlideCanvas({ elements, title }: { elements: Element[]; title: string }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-border">
      <svg viewBox="0 0 1200 675" className="w-full min-w-[720px]" role="img" aria-label={title}>
        {elements.map((e, i) =>
          e.type === 'rect' ? (
            <rect key={i} x={e.x} y={e.y} width={e.w} height={e.h} fill={e.fill} />
          ) : e.type === 'line' ? (
            <line key={i} x1={e.x} y1={e.y} x2={e.x2} y2={e.y2} stroke={e.color} strokeWidth={3} />
          ) : e.type === 'image' ? (
            <image
              key={i}
              x={e.x}
              y={e.y}
              width={e.w}
              height={e.h}
              href={'data:image/png;base64,' + e.data}
            />
          ) : e.type === 'text' ? (
            <text
              key={i}
              fill={e.color}
              fontSize={e.size}
              fontWeight={e.bold ? 'bold' : 'normal'}
              fontFamily="Microsoft YaHei, sans-serif"
            >
              {e.lines?.map((text, n) => (
                <tspan key={n} x={e.x} y={e.y + (e.size || 24) + n * (e.size || 24) * 1.25}>
                  {text}
                </tspan>
              ))}
            </text>
          ) : null
        )}
      </svg>
    </div>
  )
}
