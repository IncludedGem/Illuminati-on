import { useState, useEffect, useRef } from "react"

export default function Visualizer() {
  const wsRef = useRef(null)

  const [instrument, setInstrument] = useState({
    octave: 4,
    key: "C",
    sample: "Piano",
    volume: 50,
    keys: Array(8).fill(false),
  })

  useEffect(() => {
    wsRef.current = new WebSocket("ws://localhost:8765")

    wsRef.current.onopen = () => {
      console.log("Connected to Raspberry Pi")
    }

    wsRef.current.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)

        setInstrument({
          octave: data.octave,
          key: data.key,
          sample: data.sample,
          volume: data.volume,
          keys: data.keys,
        })
      } catch (err) {
        console.error("Invalid JSON:", err)
      }
    }

    wsRef.current.onclose = () => {
      console.log("Disconnected")
    }

    return () => wsRef.current?.close()
  }, [])

  return (
    
    <div className="mt-12 rounded-2xl border border-border bg-card p-8 shadow-sm">
      <div className="max-w-2xl">
        <p className="text-sm font-medium uppercase tracking-widest text-accent">
          Live Instrument
        </p>

        <h1 className="mt-4 text-balance text-4xl font-semibold tracking-tight md:text-5xl">
          Real-Time Instrument Dashboard
        </h1>
      </div>
      <h2 className="mt-4 text-3xl font-semibold mb-8">
        Live Instrument Status
      </h2>

      {/* Top Stats */}
      <div className="grid gap-6 md:grid-cols-4">
        <div className="rounded-xl border border-border p-5">
          <p className="text-sm text-muted-foreground">Current Octave</p>
          <p className="mt-2 text-3xl font-bold">
            {instrument.octave}
          </p>
        </div>

        <div className="rounded-xl border border-border p-5">
          <p className="text-sm text-muted-foreground">Current Key</p>
          <p className="mt-2 text-3xl font-bold">
            {instrument.key}
          </p>
        </div>

        <div className="rounded-xl border border-border p-5">
          <p className="text-sm text-muted-foreground">Current Sample</p>
          <p className="mt-2 text-2xl font-bold">
            {instrument.sample}
          </p>
        </div>

        <div className="rounded-xl border border-border p-5">
          <p className="text-sm text-muted-foreground">Volume</p>

          <div className="mt-3 h-4 w-full rounded-full bg-muted overflow-hidden">
            <div
              className="h-full bg-accent transition-all duration-100"
              style={{ width: `${instrument.volume}%` }}
            />
          </div>

          <p className="mt-2 text-xl font-semibold">
            {instrument.volume}%
          </p>
        </div>
      </div>

      {/* Keys */}
      <div className="mt-10">
        <h3 className="mb-4 text-xl font-semibold">
          Keys
        </h3>

        <div className="grid grid-cols-8 gap-4">
          {instrument.keys.map((pressed, i) => (
            <div
              key={i}
              className={`flex h-28 items-center justify-center rounded-xl border text-lg font-bold transition-all ${
                pressed
                  ? "bg-green-500 text-white border-green-500 scale-105"
                  : "bg-card border-border text-muted-foreground"
              }`}
            >
              {pressed ? "ON" : "OFF"}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}