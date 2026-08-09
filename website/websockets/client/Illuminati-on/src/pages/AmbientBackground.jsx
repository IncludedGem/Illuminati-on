import { useEffect, useRef } from "react"

// A quiet, ambient version of the visualizer page's flow-field
// background (see FlowBackground.jsx) -- same rendering technique
// (low-res noise buffer upscaled with bilinear smoothing, so it reads
// as a soft continuous wash rather than a hard-edged grid), but tuned
// way down: slower drift, muted saturation, low contrast, confined to
// sitting behind the hero rather than the whole page.
//
// Deliberately has no audio-reactivity. The visualizer page's version
// bends and ripples in response to real hardware state (key presses,
// volume, filter cutoff) coming over a WebSocket -- there's no
// equivalent live signal on the marketing homepage, so this is a
// self-contained ambient loop instead of faking a reaction to nothing.
export default function AmbientBackground() {
  const canvasRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas.getContext("2d")
    let raf = 0
    let width = 0
    let height = 0

    const BUFFER_SCALE = 1 / 14
    let bw = 0
    let bh = 0
    let imgData = null
    let buf = null

    const offscreen = document.createElement("canvas")
    const octx = offscreen.getContext("2d")

    function resize() {
      width = canvas.clientWidth
      height = canvas.clientHeight
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = width * dpr
      canvas.height = height * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.imageSmoothingEnabled = true

      bw = Math.max(2, Math.round(width * BUFFER_SCALE))
      bh = Math.max(2, Math.round(height * BUFFER_SCALE))
      offscreen.width = bw
      offscreen.height = bh
      imgData = octx.createImageData(bw, bh)
      buf = imgData.data
    }
    resize()
    window.addEventListener("resize", resize)

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

    const start = performance.now()

    // A couple of fixed, muted hues to drift between -- named so the
    // palette is a deliberate choice, not whatever a hue-rotation
    // formula happens to land on. Kept close together in hue (both
    // warm) rather than spanning the full rainbow, since this sits
    // quietly behind marketing copy rather than being the point of the
    // page the way it is on /visualizer.
    const HUE_A = 26 // warm amber
    const HUE_B = 340 // dusty rose

    // Linear interpolation between two hues going the LONG way around
    // the color wheel whenever they're closer the other direction --
    // 26 -> 340 the naive way crosses 90/180/270 (green/cyan/blue) even
    // though the short arc through 0/360 (red) never leaves warm
    // territory. This walks the shorter arc so the drift always stays
    // between amber and rose, never dips into cool hues.
    function shortestHueLerp(a, b, mix) {
      const diff = (((b - a + 180) % 360) + 360) % 360 - 180
      return (((a + diff * mix) % 360) + 360) % 360
    }

    function frame(now) {
      const t = (now - start) / 1000

      const cx = bw / 2
      const cy = bh / 2
      const maxDist = Math.sqrt(cx * cx + cy * cy)

      // Slow drift only -- no volume/cutoff/ripple inputs to react to.
      const noiseScale = 0.06
      const timeScale = 0.035 // much slower than the visualizer's (0.12-0.6)

      let p = 0
      for (let y = 0; y < bh; y++) {
        for (let x = 0; x < bw; x++) {
          const n =
            noise2D(x * noiseScale + t * timeScale, y * noiseScale - t * timeScale * 0.6) *
              0.5 +
            0.5

          const dx = x - cx
          const dy = y - cy
          const dist = Math.sqrt(dx * dx + dy * dy)

          // Gentle vignette so the wash fades toward the edges instead
          // of filling the hero uniformly.
          const vignette = 1 - (dist / maxDist) * 0.55
          const brightness = Math.max(0, Math.min(1, n)) * vignette

          // Drift slowly between the two named hues rather than a full
          // rotation -- reads as "breathing," not "cycling." Uses the
          // shortest arc (see shortestHueLerp above) so it never
          // crosses into cool colors on its way between them.
          const mix = (Math.sin(t * 0.08) + 1) / 2
          const hue = shortestHueLerp(HUE_A, HUE_B, mix)

          // Saturated and warm enough to actually read as "a touch of
          // spice" against a light page, while staying well under the
          // visualizer's full-intensity saturation (~0.55-0.95) and
          // dark background -- this needs to sit behind readable text,
          // not compete with it.
          const sat = 0.5
          const light = 0.82 + brightness * 0.13

          const [r8, g8, b8] = hslToRgb(hue, sat, light)
          buf[p++] = r8
          buf[p++] = g8
          buf[p++] = b8
          buf[p++] = 255
        }
      }

      octx.putImageData(imgData, 0, 0)
      ctx.imageSmoothingEnabled = true
      ctx.imageSmoothingQuality = "high"
      ctx.drawImage(offscreen, 0, 0, bw, bh, 0, 0, width, height)

      raf = requestAnimationFrame(frame)
    }

    raf = requestAnimationFrame(frame)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener("resize", resize)
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 h-full w-full"
      aria-hidden="true"
    />
  )
}

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
