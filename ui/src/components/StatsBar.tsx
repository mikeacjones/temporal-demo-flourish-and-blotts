import type { Stats } from '../types'

interface Props {
  stats: Stats | null
}

const TILES = [
  { key: 'total', label: 'Total Orders', color: 'var(--hp-navy)', textColor: 'white' },
  { key: 'completed', label: 'Completed', color: 'var(--hp-green)', textColor: 'white' },
  { key: 'auto_repaired', label: 'Auto-Repaired 🤖', color: '#2d5a8a', textColor: 'white' },
  { key: 'awaiting_hitl', label: 'Awaiting HITL ⏳', color: '#b8860b', textColor: 'white' },
  { key: 'hitl_approved', label: 'HITL Approved ✅', color: '#4a7c4a', textColor: 'white' },
  { key: 'hitl_denied', label: 'HITL Denied ❌', color: '#8b0000', textColor: 'white' },
  { key: 'in_progress', label: 'In Progress ⚙️', color: '#5a3a7c', textColor: 'white' },
  { key: 'cancelled', label: 'Cancelled', color: '#666', textColor: 'white' },
] as const

export default function StatsBar({ stats }: Props) {
  return (
    <div className="grid grid-cols-4 lg:grid-cols-8 gap-2 mb-4">
      {TILES.map(tile => (
        <div
          key={tile.key}
          className="rounded-lg p-3 text-center shadow-sm"
          style={{ backgroundColor: tile.color, color: tile.textColor }}
        >
          <div className="font-display text-2xl font-bold">
            {stats ? (stats[tile.key] ?? 0) : '—'}
          </div>
          <div className="text-xs mt-0.5 opacity-90 leading-tight">{tile.label}</div>
        </div>
      ))}
    </div>
  )
}
