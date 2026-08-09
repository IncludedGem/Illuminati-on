import { useEffect, useRef } from "react"

// A deforming flow field, warped by the CURRENT instrument's real
// harmonic waveform (see instrumentData.js), colored by the current
// mode, and rippled by real key-press events. Runs entirely inside a
// single requestAnimationFrame loop on canvas -- deliberately outside
// React's render cycle, since React re-rendering 60x/sec for a purely
// visual effect would fight the "keep it smooth" priority.
//
// RENDER STRATEGY: values are computed on a coarse offscreen grid and
// written into an ImageData buffer, then that buffer is drawn scaled up
// onto the visible canvas with the browser's own bilinear image
// smoothing (imageSmoothingEnabled). This is what makes the field read
// as a continuous, bending wash instead of a hard-edged checkerboard --
// filling every screen pixel's own noise value directly would look
// smoother pixel-to-pixel but cost 50-100x more fill work per frame,
// which is the tradeoff "keep it smooth" (frame rate) argues against.
//
// Live values (volume, cutoff, instrument, pressEvents) are read out of
// a ref each frame rather than captured in useEffect's closure, so the
// animation loop itself never restarts when props change.
export default function FlowBackground({ instrumentDef, modeColor, volume, cutoff, pressEvents }) {
  const canvasRef = useRef(null)
  const stateRef = useRef({ instrumentDef, modeColor, volume, cutoff, pressEvents })
  stateRef.current = { instrumentDef, modeColor, volume, cutoff, pressEvents }

  const ripplesRef = useRef([]) // {angle, born}[]
  const seenPressIdsRef = useRef(new Set())

  useEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas.getContext("2d")
    let raf = 0
    let width = 0
    let height = 0

    // The buffer we actually compute noise into. Small on purpose --
    // this is the knob that trades sharpness for frame budget. Small
    // "buffer pixels" upscaled with bilinear smoothing reads as a soft
    // continuous field; going much coarser starts to look blurry, much
    // finer starts to cost real frame time.
    // Base fraction of screen resolution the noise buffer renders at.
    // ADAPTIVE: if measured frame time shows the device can't keep up,
    // scaleFactor below shrinks further at runtime (see the perf-check
    // block in frame()) -- a fixed constant here would either look soft
    // on a fast machine or stutter on a slow one; measuring the actual
    // device once at startup fits both.
    const BASE_BUFFER_SCALE = 1 / 10
    let scaleFactor = 1 // multiplies BASE_BUFFER_SCALE; adaptive step-down only
    let bw = 0
    let bh = 0
    let imgData = null
    let buf = null // Uint8ClampedArray view into imgData.data

    const offscreen = document.createElement("canvas")
    const octx = offscreen.getContext("2d", { willReadFrequently: false })

    function resize() {
      width = canvas.clientWidth
      height = canvas.clientHeight
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = width * dpr
      canvas.height = height * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.imageSmoothingEnabled = true

      const scale = BASE_BUFFER_SCALE * scaleFactor
      bw = Math.max(2, Math.round(width * scale))
      bh = Math.max(2, Math.round(height * scale))
      offscreen.width = bw
      offscreen.height = bh
      imgData = octx.createImageData(bw, bh)
      buf = imgData.data
    }
    resize()
    window.addEventListener("resize", resize)

    // Cheap value-noise (not full simplex) -- fine for a soft organic
    // look at this resolution, much cheaper per-sample than simplex.
    function hash(x, y) {
      const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453123
      return s - Math.floor(s)
    }
    function fade(t) {
      return t * t * (3 - 2 * t)
    }
    function noise2D(x, y) {
      const xi = Math.floor(x)
      const yi = Math.floor(y)
      const xf = x - xi
      const yf = y - yi
      const a = hash(xi, yi)
      const b = hash(xi + 1, yi)
      const c = hash(xi, yi + 1)
      const d = hash(xi + 1, yi + 1)
      const u = fade(xf)
      const v = fade(yf)
      return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v
    }

    // Fractal sum of noise octaves -- adds the "bending, twisting"
    // detail a single noise octave alone reads as too smooth/flat. 2
    // octaves (not 3): profiling showed each octave costs real
    // per-pixel time across the whole buffer, and the visual gain from
    // a 3rd octave was marginal against that cost.
    function fbm(x, y) {
      let sum = 0
      let amp = 0.65
      let freq = 1
      for (let o = 0; o < 2; o++) {
        sum += noise2D(x * freq, y * freq) * amp
        amp *= 0.5
        freq *= 2.1
      }
      return sum
    }

    let hueRotationDeg = 0
    let lastT = 0
    const start = performance.now()
    // Reused every frame -- allocating a new Uint8ClampedArray(1080)
    // every frame would add GC churn for no benefit.
    const hueLUT = new Uint8ClampedArray(360 * 3)

    // Adaptive quality: sample real frame times for the first couple of
    // seconds, and if this device can't hold close to 60fps at the base
    // resolution, step the buffer down once and stay there. Checked
    // periodically (not every frame) so a single slow frame -- a GC
    // pause, a tab losing focus -- doesn't trigger an unnecessary
    // downgrade.
    let perfSamples = []
    let perfChecked = false
    const PERF_CHECK_AFTER_MS = 2000
    const PERF_TARGET_FRAME_MS = 22 // ~45fps floor before stepping down

    function maybeAdaptQuality(now, frameMs) {
      if (perfChecked) return
      perfSamples.push(frameMs)
      if (now - start < PERF_CHECK_AFTER_MS) return
      perfChecked = true
      const avg = perfSamples.reduce((a, b) => a + b, 0) / perfSamples.length
      if (avg > PERF_TARGET_FRAME_MS && scaleFactor > 0.4) {
        scaleFactor = Math.max(0.4, scaleFactor * 0.6)
        resize()
      }
    }

    function frame(now) {
      const { instrumentDef, modeColor, volume, cutoff, pressEvents } = stateRef.current
      const t = (now - start) / 1000
      const dt = t - lastT || 0
      lastT = t

      // Register any new press events as ripples.
      for (const evt of pressEvents) {
        const id = `${evt.key}-${evt.t}`
        if (!seenPressIdsRef.current.has(id)) {
          seenPressIdsRef.current.add(id)
          ripplesRef.current.push({ angle: (evt.key / 8) * Math.PI * 2, born: evt.t })
        }
      }
      if (seenPressIdsRef.current.size > 64) {
        seenPressIdsRef.current = new Set([...seenPressIdsRef.current].slice(-32))
      }
      const releaseMs = Math.min(instrumentDef.release, 1600)
      const totalMs = instrumentDef.attack + instrumentDef.decay + releaseMs
      ripplesRef.current = ripplesRef.current.filter((r) => now - r.born < totalMs + 400)

      const volFrac = volume / 100
      const cutFrac = cutoff / 100
      hueRotationDeg += (8 + volFrac * 45) * dt

      const baseHue = rgbToHue(modeColor.rgb)
      const noiseScale = 0.05 + volFrac * 0.05
      const timeScale = 0.12 + volFrac * 0.45
      const waveSamples = instrumentDef.samples
      const waveLen = waveSamples.length

      const cx = bw / 2
      const cy = bh / 2
      const ringRadius = Math.min(bw, bh) * 0.34
      const ringWidth = Math.min(bw, bh) * 0.26
      const maxDist = Math.sqrt(cx * cx + cy * cy)

      // Precompute a 360-entry hue->RGB lookup ONCE per frame instead of
      // running the full HSL->RGB conversion (branches + multiplies) at
      // every one of the ~10-40k buffer pixels. Saturation/lightness
      // still vary per-pixel (brightness depends on the noise value
      // there), so this table only fixes the hue axis -- but hue is the
      // expensive, branch-heavy part; sat/light scaling is applied
      // afterward with cheap arithmetic on the looked-up RGB.
      const sat = 0.55 + cutFrac * 0.4
      for (let h = 0; h < 360; h++) {
        const [r8, g8, b8] = hslToRgb(h, sat, 0.5) // fixed mid-lightness reference
        hueLUT[h * 3] = r8
        hueLUT[h * 3 + 1] = g8
        hueLUT[h * 3 + 2] = b8
      }

      const ripples = ripplesRef.current
      const hasRipples = ripples.length > 0
      const attack = instrumentDef.attack
      const decay = instrumentDef.decay
      const sustain = instrumentDef.sustain

      let p = 0
      for (let y = 0; y < bh; y++) {
        const ny = y * noiseScale - t * timeScale * 0.7
        const dy = y - cy
        for (let x = 0; x < bw; x++) {
          let n = fbm(x * noiseScale + t * timeScale, ny) * 0.5 + 0.5 // -> ~[0,1]

          const dx = x - cx
          const dist = Math.sqrt(dx * dx + dy * dy)
          const angle = Math.atan2(dy, dx)

          const waveIdx =
            (((((angle + Math.PI) / (Math.PI * 2)) * waveLen + t * 10) % waveLen) +
              waveLen) %
            waveLen
          const i0 = Math.floor(waveIdx)
          const i1 = (i0 + 1) % waveLen
          const frac = waveIdx - i0
          const waveVal = waveSamples[i0] * (1 - frac) + waveSamples[i1] * frac

          const ringFalloff = Math.max(0, 1 - Math.abs(dist - ringRadius) / ringWidth)
          n += waveVal * 0.4 * ringFalloff

          if (hasRipples) {
            let ripple = 0
            for (let ri = 0; ri < ripples.length; ri++) {
              const r = ripples[ri]
              const age = now - r.born
              let envVal
              if (age < attack) {
                envVal = age / Math.max(attack, 1)
              } else if (age < attack + decay) {
                const dAge = age - attack
                envVal = 1 - (1 - sustain) * (dAge / Math.max(decay, 1))
              } else {
                const rAge = age - attack - decay
                envVal = sustain * Math.max(0, 1 - rAge / Math.max(releaseMs, 1))
              }
              let angleDiff = Math.abs(angle - r.angle)
              if (angleDiff > Math.PI) angleDiff = Math.PI * 2 - angleDiff
              const angularFalloff = Math.max(0, 1 - angleDiff / 1.3)
              const wave = Math.sin(dist * 0.15 - age * 0.018)
              ripple += envVal * angularFalloff * Math.max(0, wave) * 0.7
            }
            n += ripple
          }

          // Gentle vignette so edges recede and the center reads as
          // the focal point, rather than uniform noise edge-to-edge.
          n *= 1 - (dist / maxDist) * 0.35

          const brightness = clamp01(n) * (0.35 + cutFrac * 0.75)
          const hue = ((baseHue + hueRotationDeg + dist * 0.6) % 360 + 360) % 360
          const light = 0.06 + brightness * 0.5

          // Scale the LUT's mid-lightness RGB toward the pixel's actual
          // target lightness. Cheap linear scale instead of a second
          // full HSL->RGB conversion per pixel.
          const lightScale = light / 0.5
          const hi = (hue | 0) * 3
          buf[p++] = Math.min(255, hueLUT[hi] * lightScale) | 0
          buf[p++] = Math.min(255, hueLUT[hi + 1] * lightScale) | 0
          buf[p++] = Math.min(255, hueLUT[hi + 2] * lightScale) | 0
          buf[p++] = 255
        }
      }

      octx.putImageData(imgData, 0, 0)
      ctx.imageSmoothingEnabled = true
      ctx.imageSmoothingQuality = "high"
      ctx.drawImage(offscreen, 0, 0, bw, bh, 0, 0, width, height)

      maybeAdaptQuality(now, performance.now() - now)

      raf = requestAnimationFrame(frame)
    }

    raf = requestAnimationFrame(frame)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener("resize", resize)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />
}

function clamp01(v) {
  return v < 0 ? 0 : v > 1 ? 1 : v
}

function rgbToHue(rgbStr) {
  const [r, g, b] = rgbStr.split(",").map(Number)
  const rn = r / 255,
    gn = g / 255,
    bn = b / 255
  const max = Math.max(rn, gn, bn)
  const min = Math.min(rn, gn, bn)
  const d = max - min
  if (d === 0) return 0
  let h
  if (max === rn) h = ((gn - bn) / d) % 6
  else if (max === gn) h = (bn - rn) / d + 2
  else h = (rn - gn) / d + 4
  h *= 60
  return h < 0 ? h + 360 : h
}

// HSL -> RGB (0-255 ints), avoiding per-pixel string parsing/formatting
// (ctx.fillStyle = `hsl(...)` per pixel is what made the previous
// version slow AND blocky -- direct byte writes into ImageData is both
// faster and what enables the smoothing upscale).
function hslToRgb(h, s, l) {
  h = ((h % 360) + 360) % 360
  const c = (1 - Math.abs(2 * l - 1)) * s
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1))
  const m = l - c / 2
  let r = 0,
    g = 0,
    b = 0
  if (h < 60) [r, g, b] = [c, x, 0]
  else if (h < 120) [r, g, b] = [x, c, 0]
  else if (h < 180) [r, g, b] = [0, c, x]
  else if (h < 240) [r, g, b] = [0, x, c]
  else if (h < 300) [r, g, b] = [x, 0, c]
  else [r, g, b] = [c, 0, x]
  return [
    Math.round((r + m) * 255),
    Math.round((g + m) * 255),
    Math.round((b + m) * 255),
  ]
}
