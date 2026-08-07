import { Link } from "react-router-dom"
import { Triangle } from "lucide-react"

export default function Footer() {
  return (
    <footer className="border-t border-border">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 py-10 sm:flex-row">
        <div className="flex items-center gap-2">
          <span className="flex h-10 w-10 items-center justify-center">
            <img
              src="\favicon.svg"
              alt="Band logo"
              className="h-full w-full object-contain"
            />
          </span>
          <span className="text-sm font-medium">Illuminati-on</span>
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
          &copy; {new Date().getFullYear()} Illuminati-on Band
        </p>
      </div>
    </footer>
  )
}
