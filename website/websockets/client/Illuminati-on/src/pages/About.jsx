const stats = [
  { value: "2021", label: "Founded" },
  { value: "40M+", label: "Rows charted daily" },
  { value: "12k", label: "Teams onboard" },
]

export default function About() {
  return (
    <div className="mx-auto max-w-6xl px-6 py-20">
      {/* Intro */}
      <div className="max-w-3xl">
        <p className="text-sm font-medium uppercase tracking-widest text-accent">
          About Prism
        </p>
        <h1 className="mt-4 text-balance text-4xl font-semibold leading-tight tracking-tight md:text-5xl">
          We believe good decisions start with clear data.
        </h1>
        <p className="mt-6 text-pretty text-lg leading-relaxed text-muted-foreground">
          Prism began with a simple frustration: the tools we used to understand our own
          numbers were slow, cluttered, and built for analysts instead of people. So we
          set out to build the visualization layer we always wanted — fast, honest, and
          beautiful by default.
        </p>
      </div>

      {/* Image */}
      <div className="mt-14 overflow-hidden rounded-2xl border border-border">
        <img
          src="/images/about-workspace.png"
          alt="A bright, minimal workspace with a laptop displaying charts"
          className="w-full object-cover"
          loading="lazy"
        />
      </div>

      {/* Stats */}
      <div className="mt-14 grid gap-8 border-y border-border py-10 sm:grid-cols-3">
        {stats.map((s) => (
          <div key={s.label}>
            <div className="text-4xl font-semibold tracking-tight">{s.value}</div>
            <div className="mt-1 text-sm text-muted-foreground">{s.label}</div>
          </div>
        ))}
      </div>

      {/* Story columns */}
      <div className="mt-14 grid gap-12 md:grid-cols-2">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Our approach</h2>
          <p className="mt-4 text-pretty leading-relaxed text-muted-foreground">
            Every chart Prism draws is designed to reveal the truth in your data, not to
            impress. We obsess over defaults — the right scale, the right labels, the
            right emphasis — so the clearest view is always the first one you see.
          </p>
        </div>
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Where we&apos;re headed</h2>
          <p className="mt-4 text-pretty leading-relaxed text-muted-foreground">
            We&apos;re building toward a world where anyone on a team can ask a question of
            their data and get a trustworthy answer in seconds. The visualizer you can
            try today is just the first step in that journey.
          </p>
        </div>
      </div>
    </div>
  )
}
