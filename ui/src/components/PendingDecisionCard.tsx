import { useState } from 'react'
import type { PendingDecision } from '../types'
import { submitCustomerDecision } from '../api'

interface Props {
  decision: PendingDecision
  onDelivered: () => void
}

export default function PendingDecisionCard({ decision, onDelivered }: Props) {
  const [submitting, setSubmitting] = useState<'approve' | 'deny' | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function send(choice: 'approve' | 'deny') {
    setSubmitting(choice)
    setError(null)
    try {
      await submitCustomerDecision(decision.order_id, choice)
      onDelivered()
    } catch (e: any) {
      setError(e?.message ?? 'Failed to submit decision')
      setSubmitting(null)
    }
  }

  const approveOpt = decision.options.find(o => o.value === 'approve')
  const denyOpt = decision.options.find(o => o.value === 'deny')

  return (
    <div
      className="rounded-lg p-5 mb-4"
      style={{
        backgroundColor: '#fffdf5',
        border: '2px solid var(--hp-gold)',
        boxShadow: '0 2px 8px rgba(212, 178, 74, 0.25)',
      }}
    >
      <div className="flex items-start gap-3 mb-3">
        <span style={{ fontSize: '24px' }}>🦉</span>
        <div className="flex-1">
          <p className="text-sm font-semibold" style={{ color: 'var(--hp-gold)' }}>
            An owl has arrived — we need your decision
          </p>
          <p className="font-display text-xl font-bold mt-1" style={{ color: 'var(--hp-navy)' }}>
            {decision.question}
          </p>
        </div>
      </div>

      <p className="text-sm mb-4" style={{ color: '#444', lineHeight: 1.5 }}>
        {decision.description}
      </p>

      {error && (
        <div
          className="text-sm p-2 rounded mb-3"
          style={{ backgroundColor: '#fee', color: '#8b0000', border: '1px solid #8b0000' }}
        >
          {error}
        </div>
      )}

      <div className="flex gap-3 flex-wrap">
        <button
          onClick={() => send('approve')}
          disabled={submitting !== null}
          className="px-5 py-2 rounded font-semibold text-sm disabled:opacity-60"
          style={{ backgroundColor: '#2d6a2d', color: 'white' }}
        >
          {submitting === 'approve' ? 'Sending…' : `✅ ${approveOpt?.label ?? 'Approve'}`}
        </button>
        <button
          onClick={() => send('deny')}
          disabled={submitting !== null}
          className="px-5 py-2 rounded font-semibold text-sm disabled:opacity-60"
          style={{ backgroundColor: '#8b0000', color: 'white' }}
        >
          {submitting === 'deny' ? 'Sending…' : `❌ ${denyOpt?.label ?? 'Deny & cancel'}`}
        </button>
      </div>

      <p className="text-xs mt-3" style={{ color: '#888' }}>
        We've also sent this to your email. Responding on either channel is fine — whichever
        arrives first wins.
      </p>
    </div>
  )
}
