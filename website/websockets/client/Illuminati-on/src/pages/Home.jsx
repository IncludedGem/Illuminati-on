import { Link } from "react-router-dom"
import { ArrowRight, LineChart, Layers, Share2 } from "lucide-react"

const features = [
  {
    icon: LineChart,
    title: "Instant charts",
    body: "Drop in a spreadsheet or connect a source and Prism suggests the clearest way to show it — no config required.",
  },
  {
    icon: Layers,
    title: "Layered exploration",
    body: "Filter, group, and pivot on the fly. Every view stays fast, even across millions of rows.",
  },
  {
    icon: Share2,
    title: "Share anywhere",
    body: "Publish a live link or embed a view in your docs. Your team always sees the latest numbers.",
  },
]

export default function Home() {
  return (
    <div>
      {/* Hero */}
      <section className="mx-auto max-w-6xl px-6 pb-16 pt-20 md:pt-28">
        <div className="mx-auto max-w-3xl text-center">
          <span className="inline-flex items-center rounded-full border border-border bg-muted px-3 py-1 text-xs font-medium text-muted-foreground">
            Now in public beta
          </span>
          <h1 className="mt-6 text-balance text-4xl font-semibold leading-tight tracking-tight md:text-6xl">
            See your data clearly.
          </h1>
          <p className="mx-auto mt-5 max-w-xl text-pretty text-lg leading-relaxed text-muted-foreground">
            Prism turns raw numbers into clean, interactive visualizations. Explore,
            understand, and share insights in minutes — not spreadsheets.
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

        <div className="mt-16 overflow-hidden rounded-2xl border border-border bg-card shadow-sm">
          <img
            src="/images/hero-dashboard.png"
            alt="Prism dashboard showing line and bar charts with metric cards"
            className="w-full"
            loading="eager"
          />
        </div>
      </section>

      {/* Features */}
      <section className="border-t border-border bg-muted/40">
        <div className="mx-auto max-w-6xl px-6 py-20">
          <div className="max-w-2xl">
            <h2 className="text-balance text-3xl font-semibold tracking-tight md:text-4xl">
              Everything you need to make data make sense
            </h2>
            <p className="mt-4 text-pretty leading-relaxed text-muted-foreground">
              Prism handles the tedious parts of analysis so you can focus on the story
              your numbers are telling.
            </p>
          </div>

          <div className="mt-12 grid gap-6 md:grid-cols-3">
            {features.map((f) => (
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

      {/* CTA */}
      <section className="mx-auto max-w-6xl px-6 py-24">
        <div className="rounded-3xl border border-border bg-foreground px-8 py-16 text-center text-background">
          <h2 className="text-balance text-3xl font-semibold tracking-tight md:text-4xl">
            Ready to look at your data differently?
          </h2>
          <p className="mx-auto mt-4 max-w-md text-pretty leading-relaxed text-background/70">
            Try the interactive visualizer right now. No account, no setup.
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
