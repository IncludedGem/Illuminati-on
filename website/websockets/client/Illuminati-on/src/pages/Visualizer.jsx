import { useMemo, useState } from "react"
import { BarChart3, Activity } from "lucide-react"

const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"]

const datasets = {
  revenue: { label: "Revenue", unit: "$k", values: [28, 34, 31, 45, 52, 49, 63, 71] },
  users: { label: "Active users", unit: "", values: [1.2, 1.5, 1.9, 2.1, 2.6, 3.0, 3.4, 4.1].map((v) => v * 1000) },
  sessions: { label: "Sessions", unit: "", values: [4200, 5100, 4800, 6300, 7200, 6900, 8800, 9600] },
}

const CHART_W = 720
const CHART_H = 320
const PAD = { top: 24, right: 16, bottom: 36, left: 16 }

function formatValue(v, unit) {
  if (unit === "$k") return `$${v}k`
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`
  return `${v}`
}

export default function Visualizer() {
  const [metric, setMetric] = useState("revenue")
  const [chartType, setChartType] = useState("bar")

  const data = datasets[metric]

  const { points, bars, max } = useMemo(() => {
    const max = Math.max(...data.values) * 1.15
    const innerW = CHART_W - PAD.left - PAD.right
    const innerH = CHART_H - PAD.top - PAD.bottom
    const step = innerW / data.values.length

    const bars = data.values.map((v, i) => {
      const h = (v / max) * innerH
      const bw = step * 0.5
      return {
        x: PAD.left + i * step + (step - bw) / 2,
        y: PAD.top + innerH - h,
        w: bw,
        h,
        value: v,
      }
    })

    const points = data.values.map((v, i) => {
      const x = PAD.left + i * step + step / 2
      const y = PAD.top + innerH - (v / max) * innerH
      return { x, y, value: v }
    })

    return { points, bars, max }
  }, [data])

  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ")
  const areaPath =
    `M ${points[0].x} ${CHART_H - PAD.bottom} ` +
    points.map((p) => `L ${p.x} ${p.y}`).join(" ") +
    ` L ${points[points.length - 1].x} ${CHART_H - PAD.bottom} Z`

  const total = data.values.reduce((a, b) => a + b, 0)
  const change = ((data.values[data.values.length - 1] - data.values[0]) / data.values[0]) * 100

  return (
    <div className="mx-auto max-w-6xl px-6 py-16">
      <div className="max-w-2xl">
        <p className="text-sm font-medium uppercase tracking-widest text-accent">
          Live demo
        </p>
        <h1 className="mt-4 text-balance text-4xl font-semibold tracking-tight md:text-5xl">
          The Prism visualizer
        </h1>
        <p className="mt-4 text-pretty text-lg leading-relaxed text-muted-foreground">
          This is real, interactive Prism. Switch metrics and chart types below to see how
          quickly the same data reshapes into a clear story.
        </p>
      </div>

      <div className="mt-12 grid gap-6 lg:grid-cols-[1fr_260px]">
        {/* Chart card */}
        <div className="order-2 rounded-2xl border border-border bg-card p-6 lg:order-1">
          <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="text-sm text-muted-foreground">{data.label}</div>
              <div className="mt-1 text-3xl font-semibold tracking-tight">
                {formatValue(total, data.unit)}
                <span className="ml-1 text-base font-normal text-muted-foreground">
                  total
                </span>
              </div>
            </div>
            <span
              className={`rounded-full px-2.5 py-1 text-sm font-medium ${
                change >= 0
                  ? "bg-accent-soft text-accent"
                  : "bg-muted text-muted-foreground"
              }`}
            >
              {change >= 0 ? "+" : ""}
              {change.toFixed(1)}%
            </span>
          </div>

          <svg
            viewBox={`0 0 ${CHART_W} ${CHART_H}`}
            className="w-full"
            role="img"
            aria-label={`${data.label} over the last ${months.length} months`}
          >
            {/* gridlines */}
            {[0.25, 0.5, 0.75, 1].map((t) => {
              const y = PAD.top + (CHART_H - PAD.top - PAD.bottom) * (1 - t)
              return (
                <line
                  key={t}
                  x1={PAD.left}
                  x2={CHART_W - PAD.right}
                  y1={y}
                  y2={y}
                  stroke="currentColor"
                  className="text-border"
                  strokeWidth={1}
                />
              )
            })}

            {chartType === "bar" &&
              bars.map((b, i) => (
                <rect
                  key={i}
                  x={b.x}
                  y={b.y}
                  width={b.w}
                  height={b.h}
                  rx={4}
                  className="fill-accent"
                >
                  <title>{`${months[i]}: ${formatValue(b.value, data.unit)}`}</title>
                </rect>
              ))}

            {chartType === "line" && (
              <>
                <path d={areaPath} className="fill-accent-soft" />
                <path
                  d={linePath}
                  fill="none"
                  className="stroke-accent"
                  strokeWidth={2.5}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                {points.map((p, i) => (
                  <circle key={i} cx={p.x} cy={p.y} r={4} className="fill-accent">
                    <title>{`${months[i]}: ${formatValue(p.value, data.unit)}`}</title>
                  </circle>
                ))}
              </>
            )}

            {/* x labels */}
            {points.map((p, i) => (
              <text
                key={i}
                x={p.x}
                y={CHART_H - 12}
                textAnchor="middle"
                className="fill-muted-foreground text-[11px]"
              >
                {months[i]}
              </text>
            ))}
          </svg>
          <span className="sr-only">Peak value {formatValue(max, data.unit)}</span>
        </div>

        {/* Controls */}
        <div className="order-1 flex flex-col gap-6 lg:order-2">
          <div className="rounded-2xl border border-border bg-card p-5">
            <h2 className="text-sm font-semibold">Metric</h2>
            <div className="mt-3 flex flex-col gap-2">
              {Object.keys(datasets).map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setMetric(key)}
                  className={`rounded-lg px-3 py-2 text-left text-sm font-medium transition-colors ${
                    metric === key
                      ? "bg-foreground text-background"
                      : "text-muted-foreground hover:bg-muted"
                  }`}
                >
                  {datasets[key].label}
                </button>
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-border bg-card p-5">
            <h2 className="text-sm font-semibold">Chart type</h2>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => setChartType("bar")}
                className={`flex flex-col items-center gap-1.5 rounded-lg px-3 py-3 text-xs font-medium transition-colors ${
                  chartType === "bar"
                    ? "bg-foreground text-background"
                    : "text-muted-foreground hover:bg-muted"
                }`}
              >
                <BarChart3 className="h-5 w-5" />
                Bar
              </button>
              <button
                type="button"
                onClick={() => setChartType("line")}
                className={`flex flex-col items-center gap-1.5 rounded-lg px-3 py-3 text-xs font-medium transition-colors ${
                  chartType === "line"
                    ? "bg-foreground text-background"
                    : "text-muted-foreground hover:bg-muted"
                }`}
              >
                <Activity className="h-5 w-5" />
                Line
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
