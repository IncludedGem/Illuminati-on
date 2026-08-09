// Circular loop-status display. Deliberately encodes the real state
// distinctions from loop.py rather than treating "loop_pos" the same
// way in every state:
//
//   rec    -- ring fills clockwise from 0. This is BUFFER CONSUMED
//             (Looper.progress() during RECORDING), not position in a
//             loop -- there's no loop length yet to measure against.
//   play   -- a beacon orbits the ring at loop_pos: true position in a
//   dub       completed loop.
//   pause  -- static, dimmed ring. Recorded but not moving.
//   empty  -- hidden entirely; nothing to show.
const SIZE = 220
const STROKE = 10
const R = (SIZE - STROKE) / 2
const CIRCUMFERENCE = 2 * Math.PI * R

const LABELS = {
  empty: "No loop",
  rec: "Recording",
  play: "Playing",
  dub: "Overdubbing",
  pause: "Paused",
}

export default function LoopRing({ loop, loopPos, accent }) {
  if (loop === "empty") {
    return (
      <div className="flex flex-col items-center gap-3 text-white/30">
        <div
          className="rounded-full border border-dashed border-white/15"
          style={{ width: SIZE, height: SIZE }}
        />
        <p className="text-xs font-medium uppercase tracking-[0.2em]">
          No loop recorded
        </p>
      </div>
    )
  }

  const frac = Math.max(0, Math.min(100, loopPos)) / 100
  const isRecording = loop === "rec"
  const isMoving = loop === "play" || loop === "dub"
  const isPaused = loop === "pause"

  // Position of the orbiting beacon (play/dub) or the fill's leading
  // edge (rec), in ring coordinates. 12 o'clock is progress = 0.
  const angle = frac * Math.PI * 2 - Math.PI / 2
  const beaconX = SIZE / 2 + R * Math.cos(angle)
  const beaconY = SIZE / 2 + R * Math.sin(angle)

  return (
    <div className="flex flex-col items-center gap-4">
      <div className="relative" style={{ width: SIZE, height: SIZE }}>
        <svg width={SIZE} height={SIZE} className="-rotate-90">
          {/* Track */}
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={R}
            fill="none"
            stroke="rgba(255,255,255,0.08)"
            strokeWidth={STROKE}
          />
          {/* Fill: solid progress for rec/pause, a shorter comet trail for play/dub */}
          {isMoving ? (
            <circle
              cx={SIZE / 2}
              cy={SIZE / 2}
              r={R}
              fill="none"
              stroke={`rgb(${accent})`}
              strokeWidth={STROKE}
              strokeLinecap="round"
              strokeDasharray={`${CIRCUMFERENCE * 0.16} ${CIRCUMFERENCE}`}
              strokeDashoffset={-frac * CIRCUMFERENCE}
              style={{
                filter: `drop-shadow(0 0 6px rgba(${accent},0.9))`,
                transition: "stroke-dashoffset 90ms linear",
              }}
            />
          ) : (
            <circle
              cx={SIZE / 2}
              cy={SIZE / 2}
              r={R}
              fill="none"
              stroke={`rgb(${accent})`}
              strokeWidth={STROKE}
              strokeLinecap="round"
              strokeDasharray={CIRCUMFERENCE}
              strokeDashoffset={CIRCUMFERENCE * (1 - frac)}
              opacity={isPaused ? 0.5 : 1}
              style={{
                filter: isRecording
                  ? `drop-shadow(0 0 6px rgba(${accent},0.7))`
                  : "none",
                transition: "stroke-dashoffset 150ms linear",
              }}
            />
          )}
        </svg>

        {isMoving && (
          <div
            className="absolute h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full"
            style={{
              left: beaconX,
              top: beaconY,
              background: `rgb(${accent})`,
              boxShadow: `0 0 14px 4px rgba(${accent},0.8)`,
              transition: "left 90ms linear, top 90ms linear",
            }}
          />
        )}

        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <p className="text-3xl font-bold tabular-nums">
            {loop === "rec" || isMoving ? `${Math.round(loopPos)}%` : ""}
          </p>
          {isRecording && (
            <span
              className="mt-1 h-2 w-2 animate-pulse rounded-full"
              style={{ background: `rgb(${accent})` }}
            />
          )}
        </div>
      </div>

      <p className="text-xs font-medium uppercase tracking-[0.2em] text-white/60">
        {LABELS[loop] ?? loop}
        {isRecording && (
          <span className="ml-2 text-white/35">· buffer</span>
        )}
      </p>
    </div>
  )
}
