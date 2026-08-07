import { Routes, Route, useLocation } from "react-router-dom"
import { useEffect } from "react"
import Nav from "./components/Nav"
import Footer from "./components/Footer"
import Home from "./pages/Home"
import About from "./pages/About"
import Visualizer from "./pages/Visualizer"

function ScrollToTop() {
  const { pathname } = useLocation()
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [pathname])
  return null
}

export default function App() {
  return (
    <div className="flex min-h-screen flex-col">
      <ScrollToTop />
      <Nav />
      <main className="flex-1">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/about" element={<About />} />
          <Route path="/visualizer" element={<Visualizer />} />
        </Routes>
      </main>
      <Footer />
    </div>
  )
}
