import type { Book, CartItem } from '../types'

interface Props {
  book: Book
  cartItem?: CartItem
  onAddToCart: (book: Book) => void
}

const CATEGORY_BADGES: Record<string, { label: string; color: string }> = {
  standard: { label: 'Standard', color: '#2d6a2d' },
  dangerous: { label: '⚠ Dangerous', color: '#8b4513' },
  restricted: { label: '🔒 Restricted', color: '#8b0000' },
  rare: { label: '✨ Rare', color: '#4a1942' },
}

export default function BookCard({ book, cartItem, onAddToCart }: Props) {
  const badge = CATEGORY_BADGES[book.category] || CATEGORY_BADGES.standard
  const outOfStock = book.in_stock === 0

  return (
    <div
      className="rounded-lg shadow-md overflow-hidden flex flex-col"
      style={{ backgroundColor: 'white', border: '1px solid #d4c9a8' }}
    >
      {/* Book spine / cover */}
      <div
        className="h-40 flex items-center justify-center relative"
        style={{ backgroundColor: book.cover_color }}
      >
        <div className="text-center px-4">
          <div className="font-display text-sm font-bold leading-tight" style={{ color: 'rgba(255,255,255,0.95)' }}>
            {book.title}
          </div>
          <div className="text-xs mt-1" style={{ color: 'rgba(255,255,255,0.7)' }}>
            {book.author}
          </div>
        </div>
        <div
          className="absolute top-2 right-2 text-xs px-2 py-0.5 rounded-full font-semibold"
          style={{ backgroundColor: badge.color, color: 'white' }}
        >
          {badge.label}
        </div>
      </div>

      {/* Details */}
      <div className="p-3 flex flex-col flex-1">
        <p className="text-sm leading-snug flex-1" style={{ color: '#4a3728' }}>
          {book.description}
        </p>

        {book.requires_ministry_approval && (
          <p className="text-xs mt-2 font-semibold" style={{ color: '#8b0000' }}>
            🏛️ Ministry approval required
          </p>
        )}

        <div className="mt-3 flex items-center justify-between">
          <div>
            <span className="font-display text-lg font-bold" style={{ color: 'var(--hp-gold)' }}>
              {book.price_galleons}G
            </span>
            <span className="text-xs ml-2" style={{ color: '#888' }}>
              {book.in_stock} in stock
            </span>
          </div>

          <button
            onClick={() => onAddToCart(book)}
            disabled={outOfStock}
            className="px-3 py-1.5 rounded text-sm font-semibold transition-colors disabled:opacity-40"
            style={{
              backgroundColor: outOfStock ? '#ccc' : 'var(--hp-navy)',
              color: 'white',
            }}
          >
            {cartItem ? `In cart (${cartItem.quantity})` : 'Add to Cart'}
          </button>
        </div>
      </div>
    </div>
  )
}
