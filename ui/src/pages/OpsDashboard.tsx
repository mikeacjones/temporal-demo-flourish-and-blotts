import { useState, useEffect } from 'react'
import type { Order, Stats } from '../types'
import { fetchStats, fireBulkOrders, approveOrder, denyOrder, subscribeToOrders } from '../api'
import StatsBar from '../components/StatsBar'
import FilterPanel from '../components/FilterPanel'
import OrderTable from '../components/OrderTable'

const TEMPORAL_UI = import.meta.env.VITE_TEMPORAL_UI_URL || 'http://localhost:8233'

interface Filters {
  status: string
  repair_outcome: string
  requires_hitl: string
  failure_type: string
}

export default function OpsDashboard() {
  const [orders, setOrders] = useState<Order[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [filters, setFilters] = useState<Filters>({ status: '', repair_outcome: '', requires_hitl: '', failure_type: '' })
  const [bulkCount, setBulkCount] = useState(100)
  const [bulkLoading, setBulkLoading] = useState(false)
  const [bulkResult, setBulkResult] = useState<string | null>(null)
  const [liveConnected, setLiveConnected] = useState(false)
  const [actionFeedback, setActionFeedback] = useState<string | null>(null)

  // Stats refresh
  useEffect(() => {
    let cancelled = false
    async function loadStats() {
      try {
        const s = await fetchStats()
        if (!cancelled) setStats(s)
      } catch {}
    }
    loadStats()
    const id = setInterval(loadStats, 5000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  // Live SSE subscription
  useEffect(() => {
    const unsub = subscribeToOrders((incoming) => {
      setLiveConnected(true)
      setOrders(incoming)
    })
    return unsub
  }, [])

  // Filtered view
  const filtered = orders.filter(o => {
    if (filters.status && o.order_status !== filters.status) return false
    if (filters.repair_outcome && o.repair_outcome !== filters.repair_outcome) return false
    if (filters.requires_hitl === 'true' && !o.requires_hitl) return false
    if (filters.requires_hitl === 'false' && o.requires_hitl) return false
    if (filters.failure_type && o.failure_type !== filters.failure_type) return false
    return true
  })

  async function handleBulkOrders() {
    setBulkLoading(true)
    setBulkResult(null)
    try {
      const result = await fireBulkOrders(bulkCount)
      setBulkResult(`✅ Fired ${result.started} orders`)
    } catch (e: any) {
      setBulkResult(`❌ ${e.message}`)
    } finally {
      setBulkLoading(false)
    }
  }

  async function handleApprove(orderId: string) {
    try {
      await approveOrder(orderId)
      setActionFeedback(`✅ Approved order ${orderId}`)
      setTimeout(() => setActionFeedback(null), 3000)
    } catch (e: any) {
      setActionFeedback(`❌ ${e.message}`)
      setTimeout(() => setActionFeedback(null), 5000)
    }
  }

  async function handleDeny(orderId: string) {
    try {
      await denyOrder(orderId)
      setActionFeedback(`🚫 Denied order ${orderId}`)
      setTimeout(() => setActionFeedback(null), 3000)
    } catch (e: any) {
      setActionFeedback(`❌ ${e.message}`)
      setTimeout(() => setActionFeedback(null), 5000)
    }
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      {/* Header row */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="font-display text-2xl font-bold" style={{ color: 'var(--hp-navy)' }}>
            Order Operations Centre
          </h2>
          <p className="text-sm" style={{ color: '#666' }}>
            Real-time order monitoring · Agentic repair tracking · HITL management
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1 text-xs" style={{ color: liveConnected ? '#2d6a2d' : '#888' }}>
            <span className={`inline-block w-2 h-2 rounded-full ${liveConnected ? 'bg-green-500' : 'bg-gray-400'}`} />
            {liveConnected ? 'Live' : 'Connecting...'}
          </div>
          <a
            href={TEMPORAL_UI}
            target="_blank"
            rel="noreferrer"
            className="text-xs px-3 py-1.5 rounded font-semibold"
            style={{ backgroundColor: 'var(--hp-navy)', color: 'var(--hp-gold)' }}
          >
            🔮 Temporal Web UI →
          </a>
        </div>
      </div>

      {/* Stats */}
      <StatsBar stats={stats} />

      {/* Bulk order fire */}
      <div
        className="rounded-lg p-4 mb-4 flex flex-wrap items-center gap-3"
        style={{ backgroundColor: 'white', border: '1px solid #d4c9a8' }}
      >
        <div>
          <span className="font-semibold text-sm">🔥 Fire Bulk Orders</span>
          <span className="text-xs ml-2" style={{ color: '#888' }}>
            Simulates a rush of orders with randomised HP characters and failure distribution
          </span>
        </div>
        <div className="flex items-center gap-2 ml-auto">
          <input
            type="number"
            min={1}
            max={500}
            value={bulkCount}
            onChange={e => setBulkCount(Number(e.target.value))}
            className="w-20 rounded px-2 py-1.5 text-sm border text-center"
            style={{ borderColor: '#d4c9a8' }}
          />
          <button
            onClick={handleBulkOrders}
            disabled={bulkLoading}
            className="px-4 py-1.5 rounded font-semibold text-sm transition-opacity disabled:opacity-50"
            style={{ backgroundColor: 'var(--hp-dark-red)', color: 'white' }}
          >
            {bulkLoading ? 'Casting...' : `Fire ${bulkCount} Orders`}
          </button>
          {bulkResult && (
            <span className="text-sm" style={{ color: bulkResult.startsWith('✅') ? '#2d6a2d' : '#8b0000' }}>
              {bulkResult}
            </span>
          )}
        </div>
      </div>

      {/* Action feedback */}
      {actionFeedback && (
        <div
          className="rounded p-2 mb-3 text-sm font-semibold"
          style={{
            backgroundColor: actionFeedback.startsWith('✅') || actionFeedback.startsWith('🚫') ? '#f0f8f0' : '#fff0f0',
            border: '1px solid',
            borderColor: actionFeedback.startsWith('✅') || actionFeedback.startsWith('🚫') ? '#2d6a2d' : '#8b0000',
            color: actionFeedback.startsWith('✅') || actionFeedback.startsWith('🚫') ? '#2d6a2d' : '#8b0000',
          }}
        >
          {actionFeedback}
        </div>
      )}

      {/* Filters */}
      <FilterPanel filters={filters} onChange={setFilters} />

      {/* Order count */}
      <div className="flex items-center justify-between mb-2">
        <p className="text-sm" style={{ color: '#666' }}>
          {filtered.length} order{filtered.length !== 1 ? 's' : ''} shown
          {filtered.length !== orders.length && ` (${orders.length} total)`}
        </p>
        <p className="text-xs" style={{ color: '#aaa' }}>
          Updates every 3s via SSE
        </p>
      </div>

      {/* Table */}
      <OrderTable orders={filtered} onApprove={handleApprove} onDeny={handleDeny} />
    </div>
  )
}
