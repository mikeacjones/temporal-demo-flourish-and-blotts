interface Filters {
  status: string
  repair_outcome: string
  requires_hitl: string
  failure_type: string
}

interface Props {
  filters: Filters
  onChange: (filters: Filters) => void
}

const STATUS_OPTIONS = [
  '', 'processing', 'payment_processing', 'verifying_credentials',
  'pick_and_pack', 'dispatching', 'repair_in_progress', 'awaiting_hitl',
  'completed', 'cancelled',
]

const OUTCOME_OPTIONS = ['', 'auto_repaired', 'hitl_approved', 'hitl_denied', 'unresolved']

const FAILURE_OPTIONS = [
  '', 'none', 'monster_book_escape', 'ministry_approval_required',
  'floo_misdirected', 'gringotts_failure', 'owl_intercepted',
  'restricted_section', 'inventory_mismatch', 'warehouse_failure', 'payment_timeout',
]

const selectClass = "rounded px-2 py-1.5 text-sm border focus:outline-none"
const selectStyle = { borderColor: '#d4c9a8', backgroundColor: 'white' }

export default function FilterPanel({ filters, onChange }: Props) {
  function update(key: keyof Filters, value: string) {
    onChange({ ...filters, [key]: value })
  }

  return (
    <div className="flex flex-wrap gap-3 items-center mb-4">
      <span className="text-sm font-semibold">Filter:</span>

      <div className="flex items-center gap-1">
        <label className="text-xs text-gray-600">Status</label>
        <select className={selectClass} style={selectStyle} value={filters.status} onChange={e => update('status', e.target.value)}>
          {STATUS_OPTIONS.map(o => <option key={o} value={o}>{o || 'All'}</option>)}
        </select>
      </div>

      <div className="flex items-center gap-1">
        <label className="text-xs text-gray-600">Repair</label>
        <select className={selectClass} style={selectStyle} value={filters.repair_outcome} onChange={e => update('repair_outcome', e.target.value)}>
          {OUTCOME_OPTIONS.map(o => <option key={o} value={o}>{o || 'All'}</option>)}
        </select>
      </div>

      <div className="flex items-center gap-1">
        <label className="text-xs text-gray-600">HITL</label>
        <select className={selectClass} style={selectStyle} value={filters.requires_hitl} onChange={e => update('requires_hitl', e.target.value)}>
          <option value="">All</option>
          <option value="true">Yes</option>
          <option value="false">No</option>
        </select>
      </div>

      <div className="flex items-center gap-1">
        <label className="text-xs text-gray-600">Failure</label>
        <select className={selectClass} style={selectStyle} value={filters.failure_type} onChange={e => update('failure_type', e.target.value)}>
          {FAILURE_OPTIONS.map(o => <option key={o} value={o}>{o || 'All'}</option>)}
        </select>
      </div>

      <button
        onClick={() => onChange({ status: '', repair_outcome: '', requires_hitl: '', failure_type: '' })}
        className="text-xs px-2 py-1.5 rounded border transition-colors"
        style={{ borderColor: '#d4c9a8', color: '#666' }}
      >
        Clear
      </button>
    </div>
  )
}
