import type { Book, Order, Stats, PlaceOrderRequest, PendingDecision } from './types'

const BASE = '/api'

export async function fetchCatalog(): Promise<Book[]> {
  const res = await fetch(`${BASE}/catalog`)
  if (!res.ok) throw new Error('Failed to fetch catalog')
  return res.json()
}

export async function placeOrder(req: PlaceOrderRequest): Promise<{ order_id: string; workflow_id: string; temporal_url: string }> {
  const res = await fetch(`${BASE}/orders`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchOrders(filters?: {
  status?: string
  repair_outcome?: string
  requires_hitl?: boolean
  failure_type?: string
}): Promise<Order[]> {
  const params = new URLSearchParams()
  if (filters?.status) params.set('status', filters.status)
  if (filters?.repair_outcome) params.set('repair_outcome', filters.repair_outcome)
  if (filters?.requires_hitl !== undefined) params.set('requires_hitl', String(filters.requires_hitl))
  if (filters?.failure_type) params.set('failure_type', filters.failure_type)
  const res = await fetch(`${BASE}/orders?${params}`)
  if (!res.ok) throw new Error('Failed to fetch orders')
  return res.json()
}

export async function fetchStats(): Promise<Stats> {
  const res = await fetch(`${BASE}/stats`)
  if (!res.ok) throw new Error('Failed to fetch stats')
  return res.json()
}

export async function fireBulkOrders(count: number): Promise<{ started: number }> {
  const res = await fetch(`${BASE}/orders/bulk`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ count }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function approveOrder(orderId: string): Promise<void> {
  const res = await fetch(`${BASE}/orders/${orderId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_name: 'Ops Dashboard' }),
  })
  if (!res.ok) throw new Error(await res.text())
}

export async function denyOrder(orderId: string): Promise<void> {
  const res = await fetch(`${BASE}/orders/${orderId}/deny`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_name: 'Ops Dashboard' }),
  })
  if (!res.ok) throw new Error(await res.text())
}

export function subscribeToOrders(onData: (orders: Order[]) => void): () => void {
  const es = new EventSource(`${BASE}/orders/stream`)
  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data)
      if (Array.isArray(data)) onData(data)
    } catch {}
  }
  return () => es.close()
}

export async function fetchOrder(orderId: string): Promise<Order> {
  const res = await fetch(`${BASE}/orders/${orderId}`)
  if (!res.ok) throw new Error('Order not found')
  return res.json()
}

export async function fetchPendingDecision(orderId: string): Promise<PendingDecision | null> {
  const res = await fetch(`${BASE}/orders/${orderId}/pending-decision`)
  if (!res.ok) return null
  const body = await res.json()
  return body.pending ?? null
}

export async function submitCustomerDecision(
  orderId: string,
  decision: 'approve' | 'deny',
): Promise<void> {
  const res = await fetch(`${BASE}/orders/${orderId}/customer-decision`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision }),
  })
  if (!res.ok) throw new Error(await res.text())
}
