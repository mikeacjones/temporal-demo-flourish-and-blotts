import { useEffect, useState } from 'react'
import Storefront from './pages/Storefront'
import OpsDashboard from './pages/OpsDashboard'
import OrderStatus from './pages/OrderStatus'

type Page =
  | { name: 'storefront' }
  | { name: 'ops' }
  | { name: 'order'; orderId: string }

function parseLocation(): Page {
  const m = window.location.pathname.match(/^\/orders\/([^/]+)/)
  if (m) return { name: 'order', orderId: decodeURIComponent(m[1]) }
  if (window.location.pathname.startsWith('/ops')) return { name: 'ops' }
  return { name: 'storefront' }
}

export default function App() {
  const [page, setPage] = useState<Page>(() => parseLocation())

  useEffect(() => {
    const onPop = () => setPage(parseLocation())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  function navigate(p: Page, path: string) {
    window.history.pushState({}, '', path)
    setPage(p)
  }

  const goShop = () => navigate({ name: 'storefront' }, '/')
  const goOps = () => navigate({ name: 'ops' }, '/ops')
  const goOrder = (orderId: string) => navigate({ name: 'order', orderId }, `/orders/${orderId}`)

  return (
    <div className="min-h-screen" style={{ backgroundColor: 'var(--hp-parchment)' }}>
      <header style={{ backgroundColor: 'var(--hp-navy)' }} className="shadow-lg">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <button onClick={goShop} className="text-left">
            <h1 className="font-display text-xl font-bold" style={{ color: 'var(--hp-gold)' }}>
              Flourish & Blotts
            </h1>
            <p className="text-xs" style={{ color: '#a0a8c8' }}>
              Finest Wizarding Books Since 1734 · Est. Diagon Alley
            </p>
          </button>
          <nav className="flex gap-2">
            <button
              onClick={goShop}
              className="px-4 py-2 rounded text-sm font-semibold transition-colors"
              style={{
                backgroundColor: page.name === 'storefront' ? 'var(--hp-gold)' : 'transparent',
                color: page.name === 'storefront' ? 'var(--hp-navy)' : 'var(--hp-gold)',
                border: '1px solid var(--hp-gold)',
              }}
            >
              📚 Shop
            </button>
            <button
              onClick={goOps}
              className="px-4 py-2 rounded text-sm font-semibold transition-colors"
              style={{
                backgroundColor: page.name === 'ops' ? 'var(--hp-gold)' : 'transparent',
                color: page.name === 'ops' ? 'var(--hp-navy)' : 'var(--hp-gold)',
                border: '1px solid var(--hp-gold)',
              }}
            >
              🔮 Ops Dashboard
            </button>
          </nav>
        </div>
      </header>

      <main>
        {page.name === 'storefront' && <Storefront onTrackOrder={goOrder} />}
        {page.name === 'ops' && <OpsDashboard />}
        {page.name === 'order' && <OrderStatus orderId={page.orderId} onBack={goShop} />}
      </main>
    </div>
  )
}
