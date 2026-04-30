import { useState } from 'react'
import type { CartItem } from '../types'
import { placeOrder } from '../api'

interface Props {
  items: CartItem[]
  onClose: () => void
  onSuccess: (orderId: string, temporalUrl: string) => void
}

const DELIVERY_METHODS = [
  { value: 'owl_post', label: '🦉 Owl Post — Standard (3-5 days)', note: 'Reliable. May encounter Nifflers.' },
  { value: 'floo_network', label: '🔥 Floo Network Express — Next Day', note: 'Fast. Pronunciation errors not covered.' },
  { value: 'portkey_express', label: '✨ Portkey Express — Same Day', note: 'Premium. Requires Gringotts pre-approval.' },
]

const DEMO_FAILURES = [
  { value: '', label: 'Natural (book-specific behaviour)' },
  { value: 'monster_book_escape', label: '📚 Monster Book Escape' },
  { value: 'ministry_approval_required', label: '🏛️ Ministry Approval Required' },
  { value: 'floo_misdirected', label: '🔥 Floo Misdirection' },
  { value: 'owl_intercepted', label: '🦉 Owl Intercepted' },
  { value: 'gringotts_failure', label: '🏦 Gringotts Failure' },
  { value: 'restricted_section', label: '🔒 Restricted Section' },
  { value: 'inventory_mismatch', label: '📦 Inventory Mismatch' },
  { value: 'warehouse_failure', label: '🏭 Warehouse Failure' },
]

export default function CheckoutModal({ items, onClose, onSuccess }: Props) {
  const [form, setForm] = useState({
    customer_name: '',
    customer_email: '',
    delivery_method: 'owl_post',
    delivery_address: '',
    forced_failure: '',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const total = items.reduce((sum, i) => sum + i.book.price_galleons * i.quantity, 0)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!form.customer_name || !form.customer_email || !form.delivery_address) {
      setError('Please fill in all required fields')
      return
    }

    setLoading(true)
    setError('')

    try {
      // Place one order per item
      let lastResult: any
      for (const item of items) {
        lastResult = await placeOrder({
          customer_name: form.customer_name,
          customer_email: form.customer_email,
          book_id: item.book.id,
          quantity: item.quantity,
          delivery_method: form.delivery_method,
          delivery_address: form.delivery_address,
          forced_failure: form.forced_failure || null,
        })
      }
      onSuccess(lastResult.order_id, lastResult.temporal_url)
    } catch (err: any) {
      setError(err.message || 'Something went wrong')
    } finally {
      setLoading(false)
    }
  }

  const selectedDelivery = DELIVERY_METHODS.find(d => d.value === form.delivery_method)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ backgroundColor: 'rgba(0,0,0,0.6)' }}>
      <div className="rounded-lg shadow-2xl w-full max-w-lg max-h-screen overflow-y-auto" style={{ backgroundColor: 'var(--hp-parchment)' }}>
        <div className="px-6 py-4 flex items-center justify-between" style={{ backgroundColor: 'var(--hp-navy)' }}>
          <h2 className="font-display font-bold" style={{ color: 'var(--hp-gold)' }}>Checkout</h2>
          <button onClick={onClose} style={{ color: 'var(--hp-gold)' }}>✕</button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {/* Order summary */}
          <div className="rounded p-3" style={{ backgroundColor: 'white', border: '1px solid #d4c9a8' }}>
            {items.map(({ book, quantity }) => (
              <div key={book.id} className="flex justify-between text-sm py-0.5">
                <span>{quantity}× {book.title}</span>
                <span className="font-semibold">{(book.price_galleons * quantity).toFixed(1)}G</span>
              </div>
            ))}
            <div className="flex justify-between font-bold pt-2 border-t mt-2" style={{ borderColor: '#d4c9a8' }}>
              <span>Total</span>
              <span style={{ color: 'var(--hp-gold)' }}>{total.toFixed(1)}G</span>
            </div>
          </div>

          <div>
            <label className="block text-sm font-semibold mb-1">Customer Name *</label>
            <input
              className="w-full rounded px-3 py-2 text-sm border focus:outline-none"
              style={{ borderColor: '#d4c9a8', backgroundColor: 'white' }}
              value={form.customer_name}
              onChange={e => setForm(f => ({ ...f, customer_name: e.target.value }))}
              placeholder="e.g. Harry Potter"
            />
          </div>

          <div>
            <label className="block text-sm font-semibold mb-1">Owl Post Email *</label>
            <input
              className="w-full rounded px-3 py-2 text-sm border focus:outline-none"
              style={{ borderColor: '#d4c9a8', backgroundColor: 'white' }}
              type="email"
              value={form.customer_email}
              onChange={e => setForm(f => ({ ...f, customer_email: e.target.value }))}
              placeholder="harry@hogwarts.wiz"
            />
          </div>

          <div>
            <label className="block text-sm font-semibold mb-1">Delivery Method *</label>
            <select
              className="w-full rounded px-3 py-2 text-sm border focus:outline-none"
              style={{ borderColor: '#d4c9a8', backgroundColor: 'white' }}
              value={form.delivery_method}
              onChange={e => setForm(f => ({ ...f, delivery_method: e.target.value }))}
            >
              {DELIVERY_METHODS.map(d => (
                <option key={d.value} value={d.value}>{d.label}</option>
              ))}
            </select>
            {selectedDelivery && (
              <p className="text-xs mt-1" style={{ color: '#666' }}>{selectedDelivery.note}</p>
            )}
          </div>

          <div>
            <label className="block text-sm font-semibold mb-1">Delivery Address *</label>
            <input
              className="w-full rounded px-3 py-2 text-sm border focus:outline-none"
              style={{ borderColor: '#d4c9a8', backgroundColor: 'white' }}
              value={form.delivery_address}
              onChange={e => setForm(f => ({ ...f, delivery_address: e.target.value }))}
              placeholder="4 Privet Drive, Little Whinging, Surrey"
            />
          </div>

          <div>
            <label className="block text-sm font-semibold mb-1">
              🧪 Demo: Force Failure Scenario
            </label>
            <select
              className="w-full rounded px-3 py-2 text-sm border focus:outline-none"
              style={{ borderColor: '#d4c9a8', backgroundColor: 'white' }}
              value={form.forced_failure}
              onChange={e => setForm(f => ({ ...f, forced_failure: e.target.value }))}
            >
              {DEMO_FAILURES.map(d => (
                <option key={d.value} value={d.value}>{d.label}</option>
              ))}
            </select>
          </div>

          {error && (
            <p className="text-sm font-semibold" style={{ color: '#8b0000' }}>{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 rounded font-display font-bold transition-opacity disabled:opacity-50"
            style={{ backgroundColor: 'var(--hp-navy)', color: 'var(--hp-gold)' }}
          >
            {loading ? 'Casting order spell...' : 'Place Order via Gringotts'}
          </button>
        </form>
      </div>
    </div>
  )
}
