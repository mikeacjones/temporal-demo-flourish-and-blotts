import type { Order } from '../types'

interface Props {
  orders: Order[]
  onApprove: (orderId: string) => void
  onDeny: (orderId: string) => void
}

const STATUS_COLORS: Record<string, string> = {
  completed: '#2d6a2d',
  cancelled: '#8b0000',
  awaiting_hitl: '#b8860b',
  repair_in_progress: '#2d5a8a',
  processing: '#5a3a7c',
  payment_processing: '#5a3a7c',
  verifying_credentials: '#5a3a7c',
  pick_and_pack: '#5a3a7c',
  dispatching: '#5a3a7c',
}

const OUTCOME_COLORS: Record<string, string> = {
  auto_repaired: '#2d5a8a',
  hitl_approved: '#2d6a2d',
  hitl_denied: '#8b0000',
  unresolved: '#888',
}

const FAILURE_EMOJI: Record<string, string> = {
  monster_book_escape: '📚💥',
  ministry_approval_required: '🏛️',
  floo_misdirected: '🔥🌀',
  gringotts_failure: '🏦',
  owl_intercepted: '🦉',
  restricted_section: '🔒',
  inventory_mismatch: '📦',
  warehouse_failure: '🏭',
  payment_timeout: '💳',
  none: '',
}

function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span
      className="inline-block px-2 py-0.5 rounded-full text-xs font-semibold"
      style={{ backgroundColor: color, color: 'white' }}
    >
      {label}
    </span>
  )
}

export default function OrderTable({ orders, onApprove, onDeny }: Props) {
  if (orders.length === 0) {
    return (
      <div className="text-center py-12" style={{ color: '#888' }}>
        <p className="text-4xl mb-2">📭</p>
        <p>No orders found. Fire some orders or adjust your filters.</p>
      </div>
    )
  }

  return (
    <div className="overflow-x-auto rounded-lg shadow" style={{ border: '1px solid #d4c9a8' }}>
      <table className="w-full text-sm">
        <thead>
          <tr style={{ backgroundColor: 'var(--hp-navy)', color: 'var(--hp-gold)' }}>
            <th className="text-left px-3 py-2 font-display font-semibold">Order</th>
            <th className="text-left px-3 py-2 font-display font-semibold">Customer</th>
            <th className="text-left px-3 py-2 font-display font-semibold">Book</th>
            <th className="text-left px-3 py-2 font-display font-semibold">Status</th>
            <th className="text-left px-3 py-2 font-display font-semibold">Failure</th>
            <th className="text-left px-3 py-2 font-display font-semibold">Repair</th>
            <th className="text-left px-3 py-2 font-display font-semibold">Actions</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((order, i) => {
            const isHITL = order.order_status === 'awaiting_hitl'
            return (
              <tr
                key={order.workflow_id}
                style={{
                  backgroundColor: i % 2 === 0 ? 'white' : '#faf7f0',
                  borderBottom: '1px solid #e8e0d0',
                }}
              >
                <td className="px-3 py-2">
                  <a
                    href={order.temporal_url}
                    target="_blank"
                    rel="noreferrer"
                    className="font-mono text-xs hover:underline"
                    style={{ color: '#2d5a8a' }}
                  >
                    {order.order_id}
                  </a>
                  {order.repair_attempts > 0 && (
                    <span className="ml-1 text-xs" style={{ color: '#888' }}>
                      ({order.repair_attempts} repair{order.repair_attempts !== 1 ? 's' : ''})
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 max-w-32 truncate">{order.customer_name}</td>
                <td className="px-3 py-2 max-w-40 truncate italic">{order.book_title}</td>
                <td className="px-3 py-2">
                  <Badge
                    label={order.order_status.replace(/_/g, ' ')}
                    color={STATUS_COLORS[order.order_status] || '#888'}
                  />
                </td>
                <td className="px-3 py-2">
                  {order.failure_type && order.failure_type !== 'none' ? (
                    <span>
                      {FAILURE_EMOJI[order.failure_type] || '⚠️'}{' '}
                      <span className="text-xs">{order.failure_type.replace(/_/g, ' ')}</span>
                    </span>
                  ) : (
                    <span style={{ color: '#ccc' }}>—</span>
                  )}
                </td>
                <td className="px-3 py-2">
                  {order.repair_outcome ? (
                    <Badge
                      label={order.repair_outcome.replace(/_/g, ' ')}
                      color={OUTCOME_COLORS[order.repair_outcome] || '#888'}
                    />
                  ) : (
                    <span style={{ color: '#ccc' }}>—</span>
                  )}
                </td>
                <td className="px-3 py-2">
                  {isHITL && (
                    <div className="flex gap-1">
                      <button
                        onClick={() => onApprove(order.order_id)}
                        className="px-2 py-0.5 rounded text-xs font-semibold"
                        style={{ backgroundColor: '#2d6a2d', color: 'white' }}
                      >
                        ✅ Approve
                      </button>
                      <button
                        onClick={() => onDeny(order.order_id)}
                        className="px-2 py-0.5 rounded text-xs font-semibold"
                        style={{ backgroundColor: '#8b0000', color: 'white' }}
                      >
                        ❌ Deny
                      </button>
                    </div>
                  )}
                  {!isHITL && (
                    <a
                      href={order.temporal_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs hover:underline"
                      style={{ color: '#2d5a8a' }}
                    >
                      View →
                    </a>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
