import { useEffect, useState } from 'react'
import type { Order, PendingDecision } from '../types'
import { fetchOrder, fetchPendingDecision } from '../api'
import PendingDecisionCard from '../components/PendingDecisionCard'

interface Props {
  orderId: string
  onBack: () => void
}

const MAILHOG_URL = import.meta.env.VITE_MAILHOG_UI_URL ?? 'http://localhost:8025'

const STATUS_COPY: Record<string, { label: string; emoji: string; tone: 'info' | 'good' | 'warn' | 'bad' }> = {
  pending: { label: 'Received', emoji: '📬', tone: 'info' },
  processing: { label: 'Processing', emoji: '⚙️', tone: 'info' },
  payment_processing: { label: 'Taking payment at Gringotts', emoji: '🏦', tone: 'info' },
  verifying_credentials: { label: 'Verifying wizarding credentials', emoji: '📜', tone: 'info' },
  pick_and_pack: { label: 'Picking & packing your books', emoji: '📦', tone: 'info' },
  dispatching: { label: 'Dispatching your delivery', emoji: '🦉', tone: 'info' },
  repair_in_progress: { label: 'Our agent is resolving an issue', emoji: '🪄', tone: 'warn' },
  awaiting_hitl: { label: 'Awaiting human review', emoji: '👀', tone: 'warn' },
  awaiting_customer: { label: 'Awaiting your decision', emoji: '🦉', tone: 'warn' },
  awaiting_ops: { label: 'Awaiting operations review', emoji: '👀', tone: 'warn' },
  compensating: { label: 'Reversing your order', emoji: '↩️', tone: 'warn' },
  repair_complete: { label: 'Issue resolved — order continuing', emoji: '✨', tone: 'good' },
  completed: { label: 'Delivered', emoji: '✅', tone: 'good' },
  cancelled: { label: 'Cancelled', emoji: '❌', tone: 'bad' },
  cancelled_by_customer: { label: 'Cancelled at your request', emoji: '❌', tone: 'bad' },
  cancelled_by_ops: { label: 'Cancelled by operations', emoji: '❌', tone: 'bad' },
  cancelled_unresolved: { label: 'Cancelled — no response received', emoji: '⏱️', tone: 'bad' },
}

const TONE_BG: Record<'info' | 'good' | 'warn' | 'bad', string> = {
  info: '#e8f0fe', good: '#e8f5e9', warn: '#fff4e5', bad: '#fbeaea',
}
const TONE_FG: Record<'info' | 'good' | 'warn' | 'bad', string> = {
  info: '#2d5a8a', good: '#2d6a2d', warn: '#8b5a00', bad: '#8b0000',
}

export default function OrderStatus({ orderId, onBack }: Props) {
  const [order, setOrder] = useState<Order | null>(null)
  const [pending, setPending] = useState<PendingDecision | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    async function tick() {
      try {
        const [ord, pen] = await Promise.all([
          fetchOrder(orderId),
          fetchPendingDecision(orderId),
        ])
        if (cancelled) return
        setOrder(ord)
        setPending(pen)
        setError(null)
      } catch (e: any) {
        if (!cancelled) setError(e?.message ?? 'Unable to load order')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    tick()
    const t = setInterval(tick, 3000)
    return () => { cancelled = true; clearInterval(t) }
  }, [orderId])

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-8 text-center" style={{ color: '#888' }}>
        Loading order…
      </div>
    )
  }

  if (error || !order) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-8">
        <div
          className="rounded-lg p-5 text-center"
          style={{ backgroundColor: '#fbeaea', color: '#8b0000', border: '1px solid #8b0000' }}
        >
          <p className="font-semibold">We couldn't find order {orderId}</p>
          <p className="text-sm mt-2">{error ?? 'It may have been removed or never existed.'}</p>
        </div>
        <div className="text-center mt-4">
          <button
            onClick={onBack}
            className="px-4 py-2 rounded text-sm font-semibold"
            style={{ backgroundColor: 'var(--hp-navy)', color: 'var(--hp-gold)' }}
          >
            ← Back to the shop
          </button>
        </div>
      </div>
    )
  }

  const status = order.order_status ?? 'processing'
  const copy = STATUS_COPY[status] ?? { label: status, emoji: '•', tone: 'info' as const }

  return (
    <div className="max-w-3xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-4">
        <button
          onClick={onBack}
          className="text-sm"
          style={{ color: 'var(--hp-navy)' }}
        >
          ← Back to the shop
        </button>
        <div className="flex gap-2">
          <a
            href={order.temporal_url}
            target="_blank"
            rel="noreferrer"
            className="text-xs px-3 py-1.5 rounded font-semibold"
            style={{ backgroundColor: '#2d5a8a', color: 'white' }}
          >
            Watch in Temporal →
          </a>
          <a
            href={MAILHOG_URL}
            target="_blank"
            rel="noreferrer"
            className="text-xs px-3 py-1.5 rounded font-semibold"
            style={{ backgroundColor: 'var(--hp-gold)', color: 'var(--hp-navy)' }}
          >
            Inbox (MailHog) →
          </a>
        </div>
      </div>

      <div
        className="rounded-xl p-6 mb-4"
        style={{ backgroundColor: 'white', border: '1px solid #d4c9a8' }}
      >
        <p className="text-xs uppercase tracking-wide" style={{ color: '#888' }}>
          Order
        </p>
        <h1 className="font-display text-2xl font-bold" style={{ color: 'var(--hp-navy)' }}>
          {order.order_id}
        </h1>
        <p className="text-sm mt-1" style={{ color: '#555' }}>
          <strong>{order.book_title}</strong> · for {order.customer_name}
        </p>

        <div className="mt-4 flex items-center gap-3">
          <span
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded text-sm font-semibold"
            style={{ backgroundColor: TONE_BG[copy.tone], color: TONE_FG[copy.tone] }}
          >
            <span>{copy.emoji}</span>
            <span>{copy.label}</span>
          </span>
          {order.requires_hitl && (
            <span className="text-xs" style={{ color: '#8b5a00' }}>
              · Human-in-the-loop in progress
            </span>
          )}
        </div>
      </div>

      {pending && <PendingDecisionCard decision={pending} onDelivered={() => setPending(null)} />}

      {order.failure_type && order.failure_type !== 'none' && (
        <div className="rounded-lg p-4 text-sm" style={{ backgroundColor: '#fff', border: '1px solid #e0d8b8' }}>
          <p style={{ color: '#888', fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Detected issue
          </p>
          <p className="font-semibold mt-1" style={{ color: 'var(--hp-navy)' }}>
            {order.failure_type.split('_').join(' ')}
          </p>
          {order.repair_outcome && (
            <p className="text-xs mt-2" style={{ color: '#555' }}>
              Repair outcome: <strong>{order.repair_outcome.split('_').join(' ')}</strong>
              {order.repair_attempts > 0 && <> · {order.repair_attempts} repair attempt{order.repair_attempts === 1 ? '' : 's'}</>}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
