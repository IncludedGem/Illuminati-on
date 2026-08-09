import { Link } from "react-router-dom"
import { ArrowRight, Music } from "lucide-react"
import AmbientBackground from "./AmbientBackground"

const songs = [
  {
    icon: Music,
    title: "Song 1",
    body: "By: Artist",
  },
  {
    icon: Music,
    title: "Song 2",
    body: "By: Artist",
  },
]

export default function Home() {
  return (
    <div>
      {/* Hero */}
      <section className="relative overflow-hidden">
        <AmbientBackground />

        {/* Fade so the wash softens toward the section edges instead of
            cutting off abruptly against the page background below. */}
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "linear-gradient(to bottom, transparent 0%, transparent 70%, var(--background, #fff) 100%)",
          }}
        />

        <div className="relative mx-auto max-w-6xl px-6 pb-16 pt-20 md:pt-28">
          <div className="mx-auto max-w-3xl text-center">
            <h1 className="mt-6 text-balance text-4xl font-semibold leading-tight tracking-tight md:text-6xl">
              Illuminati-on
            </h1>
            <p className="mx-auto mt-5 max-w-xl text-pretty text-lg leading-relaxed text-muted-foreground">
              The most versatile instrument.
            </p>
            <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Link
                to="/visualizer"
                className="inline-flex items-center gap-2 rounded-full bg-foreground px-6 py-3 text-sm font-medium text-background transition-opacity hover:opacity-90"
              >
                Open the visualizer
                <ArrowRight className="h-4 w-4" />
              </Link>
              <Link
                to="/about"
                className="inline-flex items-center gap-2 rounded-full border border-border px-6 py-3 text-sm font-medium text-foreground transition-colors hover:bg-muted"
              >
                Learn more
              </Link>
            </div>
          </div>

          <div className="mt-16 flex justify-center">
            <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
              <img
                src="/images/placeholder-instrument.png"
                alt="Picture of instrument"
                className="h-128 w-128 object-cover"
                loading="eager"
              />
            </div>
          </div>
        </div>
      </section>

      {/* Features */}
      {/* Song list temporarily disabled -- re-enable by uncommenting
          the block below once there's a real set list to show. */}
      {/*
      <section className="border-t border-border bg-muted/40">
        <div className="mx-auto max-w-6xl px-6 py-20">
          <div className="max-w-2xl">
            <h2 className="text-balance text-3xl font-semibold tracking-tight md:text-4xl">
              Our Set-list
            </h2>
            <p className="mt-4 text-pretty leading-relaxed text-muted-foreground">
              Carefully curated songs to be played by Illuminai-on.
            </p>
          </div>

          <div className="mt-12 grid gap-6 md:grid-cols-2">
            {songs.map((f) => (
              <div
                key={f.title}
                className="rounded-2xl border border-border bg-card p-6"
              >
                <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent-soft text-accent">
                  <f.icon className="h-5 w-5" />
                </span>
                <h3 className="mt-5 text-lg font-semibold tracking-tight">{f.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                  {f.body}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>
      */}

      {/* CTA */}
      <section className="mx-auto max-w-6xl px-6 py-24">
        <div className="rounded-3xl border border-border bg-foreground px-8 py-16 text-center text-background">
          <h2 className="text-balance text-3xl font-semibold tracking-tight md:text-4xl">
            Ready to look at the instrument differently?
          </h2>
          <p className="mx-auto mt-4 max-w-md text-pretty leading-relaxed text-background/70">
            Try the visualizer right now.
          </p>
          <Link
            to="/visualizer"
            className="mt-8 inline-flex items-center gap-2 rounded-full bg-background px-6 py-3 text-sm font-medium text-foreground transition-opacity hover:opacity-90"
          >
            Start visualizing
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </section>
    </div>
  )
}
