# Flourish & Blotts OMS — Agentic Repair Demo

Brainstormed and planned by Michael Jones + Joshua Smith. Built by Claude.

---

## Concept

Flourish & Blotts, the finest wizarding bookshop in Diagon Alley, is launching online e-commerce.
Orders flow through a Temporal-powered OMS. When something goes wrong (Monster Books escape,
Ministry approvals needed, owls get intercepted), an AI repair agent kicks in. Three outcomes:

1. **Auto-repair** — agent fixes it with tools; transparent to the customer, order continues.
2. **Customer decision** — e.g. "Book of Shadows is out of stock. Accept *Advanced Hex Codex*
   as a substitute?" Customer receives an **email** (captured by a local MailHog inbox for the
   demo) with Approve/Deny links, and the prompt also appears on their `/orders/:id` page.
3. **Ops escalation** — agent opens a Slack thread with a diagnosis + proposed plan; an ops
   operator approves/denies. The Slack conversation itself is a durable Temporal workflow.

If any HITL path denies (or the agent can't find a path), the order is cleanly reversed via
**saga compensation** — payment refunded, inventory released, dispatch recalled — all durable
and visible in the Temporal UI.

**The story**: "We brainstormed this last week and here's our demo in 3 business days."
**The message**: Temporal helps you go fast. Agent-native reliability is built-in.
**The debugging story**: *"Why did the agent do that?"* is answerable by stepping through the
workflow history, event by event, in the Temporal UI.

---

## Architecture

### Workflow Chain

```
OrderWorkflow (durable saga: tracks executed steps + compensations)
  └─ OMS steps run in sequence (payment → credentials → pick/pack → dispatch)
     └─ Step fails (non-retryable) → child: OrderRepairWorkflow
           └─ Agentic loop (Claude + tools)
                ├─ Auto-resolvable → calls tools directly → repair succeeds
                ├─ Needs customer decision → child: CustomerConfirmationWorkflow
                │     ├─ approved → agent executes plan → repair succeeds
                │     └─ denied / timeout → repair returns cancelled
                ├─ Needs ops approval → child: SlackConversationWorkflow
                │     ├─ approved → agent executes plan → repair succeeds
                │     └─ denied / timeout → repair returns cancelled
                └─ No path found → repair returns cancelled

  On repair = succeeded → OrderWorkflow continues to next OMS step (transparent)
  On repair = cancelled → OrderWorkflow runs compensations in reverse, order fails
```

### Why multiple workflows (child decomposition)

Each child exists for a *structural* reason — not just code organization:

- **OrderRepairWorkflow**: partitions the agent loop's verbose history (Claude turns, tool
  calls, retries) out of the main order history. It's also the demo's narrative centerpiece:
  when an operator asks *"why did the agent do X?"*, opening the repair workflow in the
  Temporal UI and scrolling through its events **is** the explanation. Collapsing this into
  OrderWorkflow would bury the reasoning trail among routine OMS events.
- **CustomerConfirmationWorkflow** / **SlackConversationWorkflow**: each can sit up to 24h
  waiting on a human. Independent lifetimes, independent signal-routing surfaces, and each
  owns a single conversation's worth of events. Merging them into the repair workflow would
  mix HITL transcripts with agent-reasoning history and couple their lifetimes.

All HITL child workflows use the default `parent_close_policy=TERMINATE`: if the parent order
is cancelled externally, any in-flight HITL conversation is torn down with it.

### Workflow Definitions

#### `OrderWorkflow`

- Executes 4 OMS steps sequentially: `process_payment`, `verify_credentials`, `pick_and_pack`,
  `dispatch_delivery`.
- Maintains `compensations: list[Compensation]` — each successful step appends its
  compensation (e.g. `process_payment` → `refund_payment`). On cancellation, runs them in
  **reverse order** inside a `try`/`finally` so compensation still runs on workflow
  cancellation.
- Activities use the **default retry policy** (`maximum_attempts=3`, exponential backoff).
  Domain failures (`MonsterBookEscape`, `MinistryApprovalRequired`, `InventoryMismatch`, etc.)
  are raised as `ApplicationError(type="...", non_retryable=True)` inside the activity — these
  skip retries and surface directly to the workflow. Transient infra errors (5xx, network,
  timeouts) retry with backoff and never invoke the repair path.
- On non-retryable `ActivityError`: starts `OrderRepairWorkflow` as child, awaits result.
  - `result.status == "repaired"` → continue to next OMS step (transparent to customer).
  - `result.status == "cancelled"` → run compensations in reverse, set terminal status.
- Sets custom search attributes at every status transition.

#### `OrderRepairWorkflow`

- Runs the agentic loop. Each Claude API call and each tool execution is a separate activity.
- Anthropic client configured with `max_retries=0` — Temporal owns all retry/backoff.
- Claude calls auto-execute tools freely (activities with their own allowlisted dispatch).
- Claude calls `request_customer_confirmation(...)` → starts `CustomerConfirmationWorkflow`
  as child, awaits result. Agent sees the outcome as an ordinary tool result and continues.
- Claude calls `escalate_to_human(...)` → starts `SlackConversationWorkflow` as child,
  awaits result.
- When an approved plan returns from either HITL path, the repair workflow **validates** it
  before dispatching (see [Plan Validation](#plan-validation) below).
- Returns `RepairResult(status="repaired" | "cancelled", reason, plan_executed)` to parent.
- Updates `RepairAttempts`, `RequiresHITL`, `RepairOutcome` search attributes.
- Safety valve: if the agent loop exceeds `MAX_TURNS` (default 20), `continue_as_new` to
  reset history. Unlikely in the demo but keeps the workflow safe if Claude loops.

#### `CustomerConfirmationWorkflow`

- Surfaces a decision to the ordering customer through two parallel channels:
  1. **Email** (primary) — sent via `send_customer_confirmation_email` activity to a local
     **MailHog** SMTP container. The email contains signed Approve/Deny links pointing at the
     API. Ops can open MailHog's web UI at `localhost:8025` to read the email during the demo
     exactly as a real customer would.
  2. **`/orders/:id` page** (fallback) — the same question surfaces live on the customer's
     order page via the SSE stream.
- `@workflow.signal receive_customer_decision` handlers **only mutate workflow state** (set
  `self.decision`, append to message log). The main `run` coroutine drives side effects
  (sending email, posting reminders).
- `run` uses `await workflow.wait_condition(lambda: self.decision is not None,
  timeout=timedelta(hours=24))` — on timeout, auto-deny.
- Optional reminder: after 4h with no response, dispatch a second `send_customer_confirmation_email`
  activity (durable nudge — Temporal guarantees it runs even if the worker crashes in between).
- Exposes `@workflow.query get_pending_decision()` so the API's SSE stream can surface the
  question to the customer UI without a shared DB.
- Returns `CustomerDecisionResult(status="approved" | "denied" | "timeout", note)`.

#### `SlackConversationWorkflow`

- Posts the initial Slack message with diagnosis + proposed plan + [Approve] [Deny] buttons.
- The initial message embeds `order_id` in a Block Kit `block_id` (hidden context block),
  so the bot can route replies by **constructing the workflow ID** `slack-conv-{order_id}`
  — synchronous, no Visibility lookup, no eventual-consistency race.
- `@workflow.signal` handlers (`receive_slack_message`, `receive_slack_action`) **only mutate
  state**. The main `run` coroutine awaits on `workflow.wait_condition`s and dispatches
  activities (Claude calls, Slack posts) from there — never from inside a signal handler.
- On incoming message: dispatches `process_conversation_message` activity (Claude interprets
  and may update the plan), then posts Claude's reply back to the thread.
- `workflow.wait_condition(..., timeout=timedelta(hours=24))` → auto-deny on expiry.
- Returns `SlackConversationResult(status="approved" | "denied" | "timeout", final_plan)`.

### Workflow IDs

| Workflow | ID Pattern |
|---|---|
| OrderWorkflow | `order-{uuid4}` |
| OrderRepairWorkflow | `repair-{order_id}` |
| CustomerConfirmationWorkflow | `customer-confirm-{order_id}` |
| SlackConversationWorkflow | `slack-conv-{order_id}` |

All HITL workflows are uniquely keyed on `order_id`. Deterministic IDs mean the Slack bot
and the customer-facing UI can route interactions without a Visibility query.

### Slack Bot Signal Routing

1. Bot receives a Slack thread reply or button click.
2. Bot reads `order_id` from the thread-root message's `block_id` / context metadata.
3. Bot sends the signal directly to workflow ID `slack-conv-{order_id}`.
4. On `WorkflowNotFoundException`, bot posts a friendly "this thread has already closed"
   message back to Slack.

No shared state between services. No Visibility query on the critical path.

### Customer Signal Routing

Two entry points feed the same signal:

**Email (primary)**
1. Workflow dispatches `send_customer_confirmation_email` activity on start. The email links
   are `{API_BASE}/hitl/{order_id}/decision?result=approved&token={hmac}` where `token` is an
   HMAC of `(order_id, decision, expiry)` signed with `HITL_TOKEN_SECRET`.
2. Customer clicks a link in the MailHog inbox → API endpoint validates the HMAC + expiry →
   API sends `receive_customer_decision` signal to `customer-confirm-{order_id}`.
3. API returns a simple "Thanks — your order is being updated" HTML page.

**Order-status page (fallback)**
1. Customer's `/orders/:id` page streams status via SSE from the API.
2. When an active `CustomerConfirmationWorkflow` exists, the API queries it
   (`get_pending_decision`) and surfaces the question + options inline.
3. Customer clicks [Approve] / [Deny] → same API endpoint → same signal.

Signed tokens mean the email links are safe to share in the demo without letting anyone
forge approvals. The same signal handler serves both channels — whichever arrives first wins;
the second call sees the workflow has already decided and returns an idempotent "already
handled" response.

### Activity Retry Policy

- **OMS step activities**: default policy (3 attempts, exponential backoff). Domain failures
  raised as `ApplicationError(type="...", non_retryable=True)` inside the activity skip retries.
- **Claude API activity**: non-retryable on 401/403/invalid-input/content-policy; retryable
  (with `next_retry_delay` parsed from `Retry-After` header on 429) for rate limits, 5xx,
  and connection errors.
- **Tool activities**: default policy + domain-specific non-retryables.
- **Saga compensation activities**: default policy. A permanently-failed compensation
  surfaces as a workflow failure — deliberately loud, so it's obvious in the UI.

### Activity Timeouts

| Activity class | `start_to_close_timeout` |
|---|---|
| OMS step simulations | 30s |
| Claude API call | 60s |
| Agent tool execution (most) | 30s |
| Agent tool execution (`dispatch_house_elf`, `reroute_via_floo`) | 120s |
| Slack post / thread reply | 30s |
| `send_customer_confirmation_email` (SMTP to MailHog) | 30s |
| Saga compensation activities | 60s |

`schedule_to_close_timeout` left at default (infinity). No heartbeating needed at these
durations; if any activity grows past ~60s, add `heartbeat_timeout` and call
`activity.heartbeat()` periodically.

---

## Plan Validation

When an approved plan returns from either HITL workflow, the repair workflow:

1. Parses the plan into `list[PlannedToolCall]` (Pydantic-validated).
2. Checks each `tool_name` against the auto-execute allowlist.
3. Validates each call's arguments against the tool's Pydantic arg schema.
4. On any validation failure, rejects the entire plan and returns `cancelled` to the parent
   with `reason="invalid_approved_plan"`.

This protects against an approved plan referencing an unknown tool or supplying malformed
arguments — no "the LLM told me to do it" exploits.

---

## Saga Compensations

| OMS Step | Compensation | Triggered When |
|---|---|---|
| `process_payment` | `refund_payment` | Repair returns `cancelled`, if payment already succeeded |
| `verify_credentials` | *(none — read-only)* | — |
| `pick_and_pack` | `release_inventory_reservation` | Repair returns `cancelled`, if pick already succeeded |
| `dispatch_delivery` | `recall_delivery` | Repair returns `cancelled`, if dispatch already succeeded |

Compensations run in **reverse order** of the forward path. Each compensation is retried
under the default policy; a permanently-failed compensation fails the workflow loudly so the
condition is visible in the UI (no silent money-stuck states).

---

## Failure Scenarios

### Book-triggered (deterministic per order)

| Failure | Trigger | Step | Repair Path |
|---|---|---|---|
| `monster_book_escape` | Monster Book of Monsters ordered | pick_and_pack | Auto — containment charm + house elf |
| `ministry_approval_required` | Moste Potente Potions / Secrets of Darkest Art | verify_credentials | Ops HITL (Slack) |
| `restricted_section` | Restricted books without credentials | verify_credentials | Ops HITL (Slack) |
| `gringotts_failure` | Large orders or Portkey Express | process_payment | Mixed — small issues auto, large go to ops |

### Random / delivery failures

| Failure | Trigger | Step | Repair Path |
|---|---|---|---|
| `owl_intercepted` | Owl Post delivery method | dispatch_delivery | Auto — house elf retrieval |
| `floo_misdirected` | Floo Network delivery method | dispatch_delivery | Auto — reroute |
| `inventory_mismatch` | Low-stock item, substitute available | pick_and_pack | **Customer HITL** — offer substitute; deny → saga |
| `warehouse_failure` | Random 15% chance | pick_and_pack | Auto — requeue |
| `payment_timeout` | Random 10% chance | process_payment | Auto — retry via Gringotts |

### Bulk Order Distribution (100 orders)

The "Fire 100 Orders" action simulates real customer traffic (not a replay exercise).

| Scenario | Weight | Expected Outcome |
|---|---|---|
| Clean orders | 35% | auto-completed |
| Monster Book escape | 15% | auto-repaired |
| Floo misdirected | 12% | auto-repaired |
| Owl intercepted | 10% | auto-repaired |
| Inventory mismatch | 8% | customer HITL (bulk run auto-approves after 5s) |
| Gringotts failure | 8% | mixed |
| Ministry approval | 7% | ops HITL |
| Restricted Section | 5% | ops HITL |

---

## Agent Tools

### Auto-execute (no approval needed)

- `check_inventory(item_id)` — returns stock levels
- `apply_containment_charm(order_id, item_id)` — restrains escaped magical items
- `dispatch_house_elf(order_id, task)` — magical manual intervention
- `reroute_via_floo(order_id, destination)` — alternative delivery
- `update_order_status(order_id, status, message)` — status update
- `contact_customer(order_id, message)` — **one-way** customer notification
- `substitute_item(order_id, original_item_id, substitute_item_id, reason)` — item swap
  (the agent should gate this behind `request_customer_confirmation` for customer-visible
  substitutions; direct use is reserved for like-for-like SKU swaps where the customer is
  indifferent)
- `verify_customer_credentials(customer_id, requirement_type)` — credential check

### Customer HITL

- `request_customer_confirmation(order_id, question, options, proposed_action)` — asks the
  customer directly via the order-status page. Returns `approved` / `denied` / `timeout`.

### Ops HITL

- `escalate_to_human(context, proposed_plan, rationale, urgency)` — Slack escalation.

---

## Custom Search Attributes

Registered on worker startup via Temporal operator service. **Operational visibility only** —
never used for signal routing or business-logic decisions.

| Attribute | Type | Purpose |
|---|---|---|
| `OrderId` | Keyword | Correlate order + repair + HITL workflows |
| `CustomerName` | Keyword | UI filtering |
| `BookTitle` | Keyword | UI filtering / demo clarity |
| `OrderStatus` | Keyword | `processing` / `repair_in_progress` / `awaiting_customer` / `awaiting_ops` / `compensating` / `completed` / `cancelled_by_customer` / `cancelled_by_ops` / `cancelled_unresolved` |
| `FailureType` | Keyword | Filter by failure scenario |
| `RepairOutcome` | Keyword | `auto_repaired` / `customer_approved` / `customer_denied` / `ops_approved` / `ops_denied` / `unresolved` |
| `RequiresHITL` | Bool | Quick filter for auto vs human-assisted |
| `RepairAttempts` | Int | How many agentic loops ran |

Note: Slack thread timestamp is **not** a search attribute — routing is deterministic via
workflow ID. If ops-browsing of thread links is useful, expose it as a Memo.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Workflow engine | Temporal (Python SDK `temporalio`) |
| Data converter | `temporalio.contrib.pydantic.pydantic_data_converter` (for typed Claude responses + Pydantic models) |
| AI agent | Anthropic Claude (`claude-sonnet-4-6`) via `anthropic` SDK with `max_retries=0` (Temporal owns retries) |
| Slack integration | Slack Bolt for Python, Socket Mode (no public URL needed) |
| Email (customer HITL) | MailHog (fake SMTP server + web inbox at `localhost:8025`); Python `aiosmtplib` to send |
| API server | FastAPI + uvicorn (with SSE for live UI updates) |
| Frontend | Vite + React + TypeScript + Tailwind CSS v4 |
| Containerization | Docker Compose |
| Temporal server | Temporal CLI `temporal server start-dev` (host machine) |

---

## Replay Testing (CI Guardrail)

The bulk-order fire simulates traffic; it is not a replay test. As the demo evolves, workflow
changes risk non-determinism errors (NDEs) for already-running workflows.

- Capture reference histories: `temporal workflow show -w <id> --output json` into
  `tests/replay_histories/`.
- CI step runs `Worker.run_replay_history` against every captured history. Any NDE introduced
  by a workflow-code change fails the build before it can ship.
- Nice demo moment: *"Here's yesterday's order history. Let's change the workflow — watch CI
  catch the NDE."*

---

## Project Structure

```
/
├── shared/                              # Python shared package (models + catalog)
│   ├── __init__.py
│   ├── models.py                        # Order, Item, RepairResult, CustomerDecisionResult, ...
│   └── catalog.py
├── worker/                              # Temporal worker
│   ├── Dockerfile
│   ├── workflows/
│   │   ├── order_workflow.py
│   │   ├── order_repair_workflow.py
│   │   ├── customer_confirmation_workflow.py
│   │   └── slack_conversation_workflow.py
│   ├── activities/
│   │   ├── order_activities.py          # OMS step simulations
│   │   ├── compensation_activities.py   # saga compensations
│   │   ├── claude_activities.py         # Claude API wrapper (max_retries=0)
│   │   ├── repair_activities.py         # Tool implementations
│   │   ├── slack_activities.py          # Slack message sending
│   │   └── email_activities.py          # SMTP → MailHog, HMAC-signed Approve/Deny links
│   ├── templates/
│   │   └── hitl_email.html              # Customer HITL email template
│   ├── agent/
│   │   └── tools.py                     # Tool schemas (Pydantic) + dispatch + allowlist
│   ├── config.py
│   └── main.py
├── tests/
│   ├── replay_histories/                # Captured histories for NDE regression tests
│   └── test_replay.py                   # CI replay harness
├── api/                                 # FastAPI REST + SSE server
│   ├── Dockerfile
│   └── main.py
├── slack_bot/                           # Slack Bolt (Socket Mode)
│   ├── Dockerfile
│   └── app.py                           # Routes via workflow ID from block metadata
├── ui/                                  # Vite + React
│   ├── Dockerfile
│   ├── package.json
│   ├── vite.config.ts
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── types.ts
│       ├── api.ts
│       ├── pages/
│       │   ├── Storefront.tsx           # Customer-facing book shop
│       │   ├── OrderStatus.tsx          # Per-order live status + pending-decision UI
│       │   └── OpsDashboard.tsx         # Business ops view
│       └── components/
│           ├── BookCard.tsx
│           ├── Cart.tsx
│           ├── CheckoutModal.tsx
│           ├── OrderTable.tsx
│           ├── StatsBar.tsx
│           ├── PendingDecisionCard.tsx  # Approve/Deny for customer HITL
│           └── FilterPanel.tsx
├── scripts/
│   ├── start-codespace.sh
│   └── register-search-attrs.sh
├── .devcontainer/
│   └── devcontainer.json
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── slack-app-manifest.yml
├── Makefile
└── .gitignore
```

---

## Container Layout (Docker Compose)

```
docker-compose.yml
├── worker      # Python: Temporal worker (workflows + activities + agent loop)
├── api         # Python: FastAPI REST + SSE + HITL decision endpoint
├── slack-bot   # Python: Slack Bolt in Socket Mode
├── mailhog     # mailhog/mailhog: fake SMTP (1025) + inbox web UI (8025)
└── ui          # Node: Vite + React (Nginx in prod mode)
```

Temporal dev server runs on the **host machine** via `temporal server start-dev`.
All services use `network_mode: host` so `localhost:PORT` works uniformly inside
containers, on the host, and in the Approve/Deny links inside HITL emails — no
`host.docker.internal` / service-name split to reason about.
(Docker Desktop 4.29+ supports host networking on macOS/Windows; Linux and
GitHub Codespaces support it natively.)

### Startup (local Docker)
```bash
temporal server start-dev           # Terminal 1 — host machine
cp .env.example .env                # Fill in API keys
docker compose up                   # Terminal 2
```

### Startup (GitHub Codespace)
Everything starts automatically on container creation.

---

## UI Design

### Storefront (`/`)
- Flourish & Blotts branding (navy + gold, wizarding fonts)
- Book catalog grid with covers, descriptions, prices in Galleons
- Shopping cart with quantity selector
- Checkout modal: customer name, email, delivery method (Owl Post / Floo Network / Portkey Express)
- Order confirmation with workflow ID and deep link to `/orders/:id`

### Order Status (`/orders/:id`)
- Live order status via SSE (drives from API queries against the OrderWorkflow)
- **Pending Decision card** surfaces active `CustomerConfirmationWorkflow` questions, e.g.
  *"Book of Shadows is out of stock. Accept Advanced Hex Codex as a substitute? [Yes] [No, cancel my order]"*
- On Deny → the customer sees order status move through `compensating` → `cancelled_by_customer`
  with line items showing "refund issued", "inventory released", etc. as each compensation
  activity completes.

### Ops Dashboard (`/ops`)
- Stats bar: total orders / auto-repaired / awaiting customer / awaiting ops / completed / cancelled
- Live order table (SSE)
- Filter panel: OrderStatus, RepairOutcome, RequiresHITL, FailureType
- "Fire 100 Orders" button with random HP character names and the distribution above
- Click-through deep links to Temporal Web UI (`localhost:8233`) — this is the
  *"why did the agent do that?"* answer: operators can step through the repair workflow
  history event-by-event
- Link to MailHog inbox (`localhost:8025`) for inspecting customer-HITL emails during demos
- Approve/Deny buttons for ops HITL (direct API fallback when Slack isn't configured)

---

## Environment Variables

```bash
# AI
ANTHROPIC_API_KEY=sk-ant-...

# Slack (Socket Mode)
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_CHANNEL_ID=C...

# Temporal (host networking makes localhost work everywhere)
TEMPORAL_HOST=localhost:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_UI_URL=http://localhost:8233

# API
API_PORT=8000
API_BASE_URL=http://localhost:8000      # Used to build Approve/Deny links in emails

# Customer HITL (email)
SMTP_HOST=localhost
SMTP_PORT=1025
MAILHOG_UI_URL=http://localhost:8025
HITL_FROM_EMAIL=orders@flourish-and-blotts.test
HITL_TOKEN_SECRET=change-me-in-demo     # HMAC secret for Approve/Deny link tokens
```

---

## Slack App Manifest

Socket Mode app. Required OAuth scopes:
- `chat:write` — send messages
- `channels:history` — read thread messages
- `channels:read` — look up channel info

Required event subscriptions (Socket Mode):
- `message.channels` — receive channel messages

Interactive components:
- Approve/Deny buttons (Block Kit Actions). The initial message includes `order_id` in a
  hidden context block's `block_id` for deterministic workflow-ID routing — the bot never
  needs a Visibility query to find the workflow.

---

## GitHub Codespace Configuration

`.devcontainer/devcontainer.json`:
- Base image: `mcr.microsoft.com/devcontainers/python:3.12`
- Features: Node.js 20
- `postCreateCommand`: install Python deps, Node deps, Temporal CLI
- `postStartCommand`: run `scripts/start-codespace.sh`
- Forwarded ports: 3000 (UI), 8000 (API), 8025 (MailHog inbox), 8233 (Temporal Web UI)

`scripts/start-codespace.sh`:
1. Start `temporal server start-dev` in background
2. Wait 3s for Temporal to be ready
3. Register custom search attributes
4. Start worker (`python -m worker.main`)
5. Start API (`uvicorn api.main:app`)
6. Start Slack bot (if env vars set)
7. Start UI dev server (`npm run dev`)
