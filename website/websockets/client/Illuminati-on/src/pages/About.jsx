const stats = [
  { value: "2026", label: "Founded" },
  { value: "4", label: "Members" },
  { value: "+$40M", label: "In Charity Donations" },
]

export default function About() {
  return (
    <div className="mx-auto max-w-6xl px-6 py-20">
      {/* Intro */}
      <div className="max-w-3xl">
        <p className="text-sm font-medium uppercase tracking-widest text-accent">
          About Us
        </p>
        <h1 className="mt-4 text-balance text-4xl font-semibold leading-tight tracking-tight md:text-5xl">
          Illuminati-on, the best band to ever grace this Earth.
        </h1>
      </div>

      {/* Image */}
      <div className="mt-14 overflow-hidden rounded-2xl border border-border">
        <img
          src="/images/band.png"
          alt="A picture of our band"
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

      {/* First 2 Members */}
      <div className="mt-14 grid gap-12 md:grid-cols-2">
        {/* Left Card */}
        <div className="flex flex-col items-center text-center">
          <img
            src="/images/miles.PNG"
            alt="Miles Photo"
            className="mb-6 h-128 w-128 rounded-xl object-cover"
          />

          <h2 className="text-2xl font-semibold tracking-tight">
            Miles Huang
          </h2>

          <p className="mt-4 text-pretty leading-relaxed text-muted-foreground">
            Every chart Prism draws is designed to reveal the truth in your data,
            not to impress. We obsess over defaults—the right scale, the right
            labels, the right emphasis—so the clearest view is always the first
            one you see.
          </p>
        </div>

        {/* Right Card */}
        <div className="flex flex-col items-center text-center">
          <img
            src="/images/yosef.PNG"
            alt="Yosef Photo"
            className="mb-6 h-128 w-128 rounded-xl object-cover"
          />

          <h2 className="text-2xl font-semibold tracking-tight">
            Yosef Lowy
          </h2>

          <p className="mt-4 text-pretty leading-relaxed text-muted-foreground">
            We&apos;re building toward a world where anyone on a team can ask a
            question of their data and get a trustworthy answer in seconds. The
            visualizer you can try today is just the first step in that journey.
          </p>
        </div>
      </div>

      {/* Second 2 Members */}
      <div className="mt-14 grid gap-12 md:grid-cols-2">
        {/* Left Card */}
        <div className="flex flex-col items-center text-center">
          <img
            src="\images\ray.PNG"
            alt="Ray Photo"
            className="mb-6 h-128 w-128 rounded-xl object-cover"
          />

          <h2 className="text-2xl font-semibold tracking-tight">
            Ray Cillo
          </h2>

          <p className="mt-4 text-pretty leading-relaxed text-muted-foreground">
            Every chart Prism draws is designed to reveal the truth in your data,
            not to impress. We obsess over defaults—the right scale, the right
            labels, the right emphasis—so the clearest view is always the first
            one you see.
          </p>
        </div>

        {/* Right Card */}
        <div className="flex flex-col items-center text-center">
          <img
            src="/images/isaiah.PNG"
            alt="Isaiah Photo"
            className="mb-6 h-128 w-128 rounded-xl object-cover"
          />

          <h2 className="text-2xl font-semibold tracking-tight">
            Isaiah Dorado
          </h2>

          <p className="mt-4 text-pretty leading-relaxed text-muted-foreground">
            We&apos;re building toward a world where anyone on a team can ask a
            question of their data and get a trustworthy answer in seconds. The
            visualizer you can try today is just the first step in that journey.
          </p>
        </div>
      </div>



    </div>
  )
}
