export interface Book {
  id: string
  title: string
  author: string
  price_galleons: number
  description: string
  category: 'standard' | 'restricted' | 'dangerous' | 'rare'
  in_stock: number
  requires_ministry_approval: boolean
  cover_color: string
}

export interface CartItem {
  book: Book
  quantity: number
}

export interface Order {
  workflow_id: string
  order_id: string
  customer_name: string
  book_title: string
  order_status: string
  failure_type: string | null
  repair_outcome: string | null
  requires_hitl: boolean
  repair_attempts: number
  started_at: string | null
  close_time?: string | null
  execution_status: string
  temporal_url: string
}

export interface Stats {
  total: number
  completed: number
  awaiting_hitl: number
  auto_repaired: number
  hitl_approved: number
  hitl_denied: number
  cancelled: number
  in_progress: number
}

export interface PlaceOrderRequest {
  customer_name: string
  customer_email: string
  book_id: string
  quantity: number
  delivery_method: string
  delivery_address: string
  forced_failure?: string | null
}

export interface PendingDecisionOption {
  value: 'approve' | 'deny'
  label: string
}

export interface PendingDecision {
  order_id: string
  question: string
  description: string
  proposed_action: string
  options: PendingDecisionOption[]
}
