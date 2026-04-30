import type { CartItem } from '../types'

interface Props {
  items: CartItem[]
  onRemove: (bookId: string) => void
  onCheckout: () => void
}

export default function Cart({ items, onRemove, onCheckout }: Props) {
  const total = items.reduce((sum, i) => sum + i.book.price_galleons * i.quantity, 0)

  if (items.length === 0) {
    return (
      <div
        className="rounded-lg p-4 text-center"
        style={{ backgroundColor: 'white', border: '1px solid #d4c9a8' }}
      >
        <p className="text-sm" style={{ color: '#888' }}>Your cart is empty</p>
      </div>
    )
  }

  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{ backgroundColor: 'white', border: '1px solid #d4c9a8' }}
    >
      <div className="px-4 py-2" style={{ backgroundColor: 'var(--hp-navy)' }}>
        <h3 className="font-display text-sm font-bold" style={{ color: 'var(--hp-gold)' }}>
          Your Cart ({items.length} item{items.length !== 1 ? 's' : ''})
        </h3>
      </div>

      <div className="divide-y" style={{ borderColor: '#e8e0d0' }}>
        {items.map(({ book, quantity }) => (
          <div key={book.id} className="flex items-center gap-3 px-4 py-2">
            <div
              className="w-8 h-10 rounded flex-shrink-0"
              style={{ backgroundColor: book.cover_color }}
            />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold truncate">{book.title}</p>
              <p className="text-xs" style={{ color: '#888' }}>
                {quantity} × {book.price_galleons}G
              </p>
            </div>
            <span className="font-bold text-sm" style={{ color: 'var(--hp-gold)' }}>
              {(book.price_galleons * quantity).toFixed(1)}G
            </span>
            <button
              onClick={() => onRemove(book.id)}
              className="text-xs ml-1"
              style={{ color: '#8b0000' }}
            >
              ✕
            </button>
          </div>
        ))}
      </div>

      <div className="px-4 py-3 border-t" style={{ borderColor: '#d4c9a8' }}>
        <div className="flex justify-between mb-3">
          <span className="font-semibold">Total</span>
          <span className="font-display font-bold" style={{ color: 'var(--hp-gold)' }}>
            {total.toFixed(1)}G
          </span>
        </div>
        <button
          onClick={onCheckout}
          className="w-full py-2 rounded font-display font-bold text-sm transition-colors"
          style={{ backgroundColor: 'var(--hp-navy)', color: 'var(--hp-gold)' }}
        >
          Proceed to Checkout
        </button>
      </div>
    </div>
  )
}
