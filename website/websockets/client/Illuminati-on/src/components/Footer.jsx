import { Link } from "react-router-dom"
import { Triangle } from "lucide-react"

export default function Footer() {
  return (
    <footer className="border-t border-border">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 py-10 sm:flex-row">
        <div className="flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded bg-foreground text-background">
            <Triangle className="h-3 w-3 fill-background" strokeWidth={0} />
          </span>
          <span className="text-sm font-medium">Prism</span>
        </div>

        <nav className="flex items-center gap-6 text-sm text-muted-foreground">
          <Link to="/" className="transition-colors hover:text-foreground">
            Home
          </Link>
          <Link to="/about" className="transition-colors hover:text-foreground">
            About
          </Link>
          <Link to="/visualizer" className="transition-colors hover:text-foreground">
            Visualizer
          </Link>
        </nav>

        <p className="text-sm text-muted-foreground">
          &copy; {new Date().getFullYear()} Prism Labs
        </p>
      </div>
    </footer>
  )
}
