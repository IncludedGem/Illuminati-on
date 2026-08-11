import { useState, useEffect, useRef } from "react"
import FlowBackground from "./FlowBackground"
import LoopRing from "./LoopRing"
import { INSTRUMENTS, MODE_COLOR } from "./instrumentData"

// Mirrors the Pico's serial protocol exactly (see SERIAL PROTOCOL in
// main.py's module docstring): preset, octave, key, sample, mode,
// volume, cutoff, keys[8], loop, loop_pos.
//
// `loop` is one of five short codes from Looper.state_name() (loop.py):
// "empty", "rec", "play", "dub", "pause" -- not booleans, not full words.
// `loop_pos` is already 0-100 (Looper.progress()), not a 0-1 fraction.
// While `loop === "rec"`, loop_pos means buffer fill, not loop position
// -- there's no loop length yet to measure against.

const NOTE_LABELS = ["1", "2", "3", "4", "5", "6", "7", "8"]

export default function Visualizer() {
  const wsRef = useRef(null)

  const [instrument, setInstrument] = useState({
    preset: 1,
    octave: 4,
    key: "C",
    sample: "Sine",
    volume: 50,
    keys: Array(8).fill(false),
    cutoff: 100,
    loop: "empty",
    loop_pos: 0,
    mode: "Major",
  })

  const [connected, setConnected] = useState(false)

  // Tracks WHICH keys just transitioned false->true and WHEN, so the
  // background/ripple layer can fire a one-shot attack per press rather
  // than replaying on every unrelated state update (volume nudges,
  // pot reads, etc. all trigger a JSON message too).
  const prevKeysRef = useRef(Array(8).fill(false))
  const [pressEvents, setPressEvents] = useState([]) // {key, t}[]

  useEffect(() => {
    let cancelled = false

    function connect() {
      const ws = new WebSocket("ws://192.168.50.16:8765")
      wsRef.current = ws

      ws.onopen = () => !cancelled && setConnected(true)
      ws.onclose = () => {
        if (cancelled) return
        setConnected(false)
        // Simple retry -- the bridge/server may not be up yet, or the
        // Pico was unplugged. No point animating a permanently frozen
        // dashboard.
        setTimeout(connect, 1500)
      }
      ws.onerror = () => ws.close()

      ws.onmessage = (event) => {
        // Belt-and-braces: the bridge already strips the Pico's '#' marker
        // and drops debug prints, but a raw passthrough would otherwise
        // throw on every single message and freeze the dashboard while the
        // connection dot stayed green.
        const raw = String(event.data).trim().replace(/^#/, "").trim()
        if (!raw.startsWith("{")) return

        try {
          const data = JSON.parse(raw)

          if (Array.isArray(data.keys)) {
            const prev = prevKeysRef.current
            const now = performance.now()
            const newlyPressed = []
            for (let i = 0; i < 8; i++) {
              if (data.keys[i] && !prev[i]) newlyPressed.push(i)
            }
            if (newlyPressed.length) {
              setPressEvents((events) => [
                ...events,
                ...newlyPressed.map((i) => ({ key: i, t: now })),
              ])
            }
            prevKeysRef.current = data.keys
          }

          setInstrument((prev) => ({
            ...prev,
            preset: data.preset ?? prev.preset,
            octave: data.octave ?? prev.octave,
            key: data.key ?? prev.key,
            sample: data.sample ?? prev.sample,
            volume: data.volume ?? prev.volume,
            keys: data.keys ?? prev.keys,
            cutoff: data.cutoff ?? prev.cutoff,
            loop: data.loop ?? prev.loop,
            loop_pos: data.loop_pos ?? prev.loop_pos,
            mode: data.mode ?? prev.mode,
          }))
        } catch (err) {
          console.error("Invalid JSON:", err)
        }
      }
    }

    connect()
    return () => {
      cancelled = true
      wsRef.current?.close()
    }
  }, [])

  // Prune press events older than 3s so the array driving re-renders
  // doesn't grow forever during a long session.
  useEffect(() => {
    if (!pressEvents.length) return
    const id = setInterval(() => {
      const cutoff = performance.now() - 3000
      setPressEvents((events) => events.filter((e) => e.t > cutoff))
    }, 1000)
    return () => clearInterval(id)
  }, [pressEvents.length])

  const instrumentDef = INSTRUMENTS[instrument.sample] ?? INSTRUMENTS.Sine
  const modeColor = MODE_COLOR[instrument.mode] ?? MODE_COLOR.Major

  return (
    <div className="relative min-h-screen w-full overflow-hidden bg-black text-white">
      <FlowBackground
        instrumentDef={instrumentDef}
        modeColor={modeColor}
        volume={instrument.volume}
        cutoff={instrument.cutoff}
        pressEvents={pressEvents}
      />

      {/* Vignette so text stays legible over a busy background */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse at center, transparent 0%, transparent 40%, rgba(0,0,0,0.55) 100%)",
        }}
      />

      <div className="relative z-10 mx-auto flex min-h-screen max-w-5xl flex-col px-6 py-10">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.3em] text-white/50">
              Live Instrument
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight md:text-4xl">
              Pico Music Controller
            </h1>
          </div>

          <div className="flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-3 py-1.5 backdrop-blur-sm">
            <span
              className={`h-2 w-2 rounded-full transition-colors duration-500 ${
                connected ? "bg-emerald-400" : "bg-white/30"
              }`}
              style={
                connected
                  ? { boxShadow: "0 0 8px 2px rgba(52,211,153,0.7)" }
                  : undefined
              }
            />
            <span className="text-xs text-white/70">
              {connected ? "Live" : "Reconnecting…"}
            </span>
          </div>
        </div>

        {/* Big now-playing readout */}
        <div className="mt-10 flex flex-wrap items-end gap-x-10 gap-y-4">
          <Stat label="Key" value={instrument.key} size="huge" accent={modeColor.text} />
          <Stat label="Octave" value={instrument.octave} size="huge" accent={modeColor.text} />
          <Stat label="Mode" value={instrument.mode} size="large" />
          <Stat label="Sample" value={instrument.sample} size="large" />
          <Stat label="Preset" value={instrument.preset} size="large" />
        </div>

        {/* Volume / cutoff */}
        <div className="mt-8 grid gap-4 sm:grid-cols-2">
          <MeterBar label="Volume" value={instrument.volume} color={modeColor.rgb} />
          <MeterBar label="Filter cutoff" value={instrument.cutoff} color={modeColor.rgb} />
        </div>

        {/* Keys */}
        <div className="mt-10">
          <p className="mb-3 text-xs font-medium uppercase tracking-[0.2em] text-white/50">
            Keys
          </p>
          <div className="grid grid-cols-4 gap-3 sm:grid-cols-8">
            {instrument.keys.map((pressed, i) => (
              <KeyPad
                key={i}
                label={NOTE_LABELS[i]}
                pressed={pressed}
                accent={modeColor.rgb}
                releaseMs={instrumentDef.release}
              />
            ))}
          </div>
        </div>

        {/* Loop */}
        <div className="mt-12 flex flex-1 items-center justify-center py-8">
          <LoopRing
            loop={instrument.loop}
            loopPos={instrument.loop_pos}
            accent={modeColor.rgb}
          />
        </div>
      </div>
    </div>
  )
}

function Stat({ label, value, size = "large", accent }) {
  const sizeClass =
    size === "huge"
      ? "text-6xl md:text-7xl"
      : "text-2xl md:text-3xl"
  return (
    <div>
      <p className="text-[11px] font-medium uppercase tracking-[0.2em] text-white/45">
        {label}
      </p>
      <p
        className={`${sizeClass} font-semibold tabular-nums transition-colors duration-700`}
        style={accent ? { color: accent } : undefined}
      >
        {value}
      </p>
    </div>
  )
}

function MeterBar({ label, value, color }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-4 backdrop-blur-sm">
      <div className="flex items-baseline justify-between">
        <p className="text-xs font-medium uppercase tracking-[0.2em] text-white/50">
          {label}
        </p>
        <p className="text-sm tabular-nums text-white/70">{value}%</p>
      </div>
      <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-white/10">
        <div
          className="h-full rounded-full transition-[width] duration-150 ease-out"
          style={{
            width: `${value}%`,
            background: `linear-gradient(90deg, rgba(${color},0.4), rgba(${color},1))`,
            boxShadow: `0 0 12px rgba(${color},0.6)`,
          }}
        />
      </div>
    </div>
  )
}

function KeyPad({ label, pressed, accent, releaseMs }) {
  // Release timing is keyed off the CURRENT instrument's real envelope
  // (see instrumentData.js, ported from main.py's ENVELOPES), so a
  // Bell's slow ~1900ms release actually fades slowly here too, and a
  // Square's ~100ms release snaps off just as fast.
  return (
    <div
      className={`relative flex h-20 items-center justify-center rounded-xl border text-sm font-bold sm:h-24 ${
        pressed ? "scale-105 border-white/0" : "border-white/10"
      }`}
      style={{
        background: pressed
          ? `linear-gradient(160deg, rgba(${accent},0.9), rgba(${accent},0.5))`
          : "rgba(255,255,255,0.03)",
        boxShadow: pressed ? `0 0 24px 4px rgba(${accent},0.55)` : "none",
        transition: pressed
          ? "all 60ms ease-out"
          : `all ${Math.min(releaseMs, 900)}ms ease-in`,
      }}
    >
      <span className={pressed ? "text-white" : "text-white/40"}>{label}</span>
    </div>
  )
}