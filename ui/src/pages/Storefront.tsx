import { useState, useEffect } from 'react'
import type { Book, CartItem } from '../types'
import { fetchCatalog } from '../api'
import BookCard from '../components/BookCard'
import Cart from '../components/Cart'
import CheckoutModal from '../components/CheckoutModal'

interface Props {
  onTrackOrder: (orderId: string) => void
}

export default function Storefront({ onTrackOrder }: Props) {
  const [books, setBooks] = useState<Book[]>([])
  const [cart, setCart] = useState<CartItem[]>([])
  const [showCheckout, setShowCheckout] = useState(false)
  const [confirmation, setConfirmation] = useState<{ orderId: string; temporalUrl: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<'all' | 'standard' | 'restricted' | 'dangerous'>('all')

  useEffect(() => {
    fetchCatalog()
      .then(setBooks)
      .finally(() => setLoading(false))
  }, [])

  function addToCart(book: Book) {
    setCart(prev => {
      const existing = prev.find(i => i.book.id === book.id)
      if (existing) {
        return prev.map(i => i.book.id === book.id ? { ...i, quantity: i.quantity + 1 } : i)
      }
      return [...prev, { book, quantity: 1 }]
    })
  }

  function removeFromCart(bookId: string) {
    setCart(prev => prev.filter(i => i.book.id !== bookId))
  }

  function handleOrderSuccess(orderId: string, temporalUrl: string) {
    setConfirmation({ orderId, temporalUrl })
    setCart([])
    setShowCheckout(false)
  }

  const filtered = filter === 'all' ? books : books.filter(b => b.category === filter)

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      {/* Hero */}
      <div
        className="rounded-xl p-6 mb-6 text-center"
        style={{ backgroundColor: 'var(--hp-navy)', backgroundImage: 'radial-gradient(ellipse at center, #2a3060 0%, var(--hp-navy) 70%)' }}
      >
        <h2 className="font-display text-3xl font-bold mb-2" style={{ color: 'var(--hp-gold)' }}>
          Welcome to Flourish & Blotts
        </h2>
        <p className="max-w-xl mx-auto" style={{ color: '#a0a8c8' }}>
          The finest purveyor of magical literature in Diagon Alley. From standard school texts
          to the most dangerously enchanted tomes — if it's printed on wizarding parchment, we stock it.
        </p>
        <p className="text-xs mt-2" style={{ color: '#5a6080' }}>
          ⚠️ Flourish & Blotts accepts no responsibility for escaped books, Ministry raids, or owl-related delays.
        </p>
      </div>

      {/* Order confirmation banner */}
      {confirmation && (
        <div
          className="rounded-lg p-4 mb-4 flex items-center justify-between"
          style={{ backgroundColor: '#e8f5e9', border: '1px solid #2d6a2d' }}
        >
          <div>
            <p className="font-semibold" style={{ color: '#2d6a2d' }}>
              ✅ Order {confirmation.orderId} placed successfully!
            </p>
            <p className="text-sm" style={{ color: '#555' }}>
              Your order is now being processed by the Temporal OMS.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => onTrackOrder(confirmation.orderId)}
              className="text-sm px-3 py-1.5 rounded font-semibold"
              style={{ backgroundColor: 'var(--hp-gold)', color: 'var(--hp-navy)' }}
            >
              Track your order →
            </button>
            <a
              href={confirmation.temporalUrl}
              target="_blank"
              rel="noreferrer"
              className="text-sm px-3 py-1.5 rounded font-semibold"
              style={{ backgroundColor: '#2d5a8a', color: 'white' }}
            >
              Watch in Temporal →
            </a>
            <button onClick={() => setConfirmation(null)} className="text-sm" style={{ color: '#888' }}>✕</button>
          </div>
        </div>
      )}

      <div className="flex gap-6">
        {/* Catalog */}
        <div className="flex-1">
          {/* Category filter */}
          <div className="flex gap-2 mb-4">
            {(['all', 'standard', 'dangerous', 'restricted'] as const).map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className="px-3 py-1.5 rounded text-sm font-semibold transition-colors capitalize"
                style={{
                  backgroundColor: filter === f ? 'var(--hp-navy)' : 'white',
                  color: filter === f ? 'var(--hp-gold)' : '#666',
                  border: '1px solid #d4c9a8',
                }}
              >
                {f === 'all' ? 'All Books' : f}
              </button>
            ))}
            <span className="ml-auto text-sm self-center" style={{ color: '#888' }}>
              {filtered.length} title{filtered.length !== 1 ? 's' : ''}
            </span>
          </div>

          {loading ? (
            <div className="text-center py-12" style={{ color: '#888' }}>Loading catalogue...</div>
          ) : (
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
              {filtered.map(book => (
                <BookCard
                  key={book.id}
                  book={book}
                  cartItem={cart.find(i => i.book.id === book.id)}
                  onAddToCart={addToCart}
                />
              ))}
            </div>
          )}
        </div>

        {/* Cart sidebar */}
        <div className="w-72 flex-shrink-0">
          <div className="sticky top-4">
            <Cart
              items={cart}
              onRemove={removeFromCart}
              onCheckout={() => setShowCheckout(true)}
            />
          </div>
        </div>
      </div>

      {showCheckout && cart.length > 0 && (
        <CheckoutModal
          items={cart}
          onClose={() => setShowCheckout(false)}
          onSuccess={handleOrderSuccess}
        />
      )}
    </div>
  )
}
