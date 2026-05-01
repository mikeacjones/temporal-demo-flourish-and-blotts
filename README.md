## Credits

Brainstormed and planned by Michael Jones + Joshua Smith. 

Built collaboratively with Claude using the [Temporal developer skill](https://temporal.io/blog/introducing-temporal-developer-skill).

---

# Flourish & Blotts — Temporal OMS with Agentic Repair

A working Temporal demo: a wizarding-themed bookshop's order management system, with an
**agentic repair workflow** that uses Claude to diagnose and resolve order failures, plus a
**Slack ops agent** that lets operators introspect and act on live workflow state. Built to
showcase three Temporal-flavored ideas at once:

1. **Saga compensation** for forward-and-rollback durability — pay → reserve → pack → dispatch,
   each step with a corresponding compensation that runs cleanly when downstream fails.
2. **Agentic workflows** where an LLM is just another step inside a durable, replayable
   Temporal execution. You can step through the agent's tool calls in the Temporal UI like any
   other activity.
3. **Human-in-the-loop** at multiple layers — customer email approvals (MailHog), in-page
   `/orders/:id` confirmations, and ops Slack threads — each backed by a child workflow that
   waits durably for hours.

The repair agent calls tools through a **declarative tool harness** (`shared/agent_harness/`)
that enforces governance policies (e.g. "every mutating tool must carry a human-confirmation
guard") at module-import time. Adding a new mutating tool without an approval guard fails the
worker on startup — the policy can't be skipped by the LLM.

```
   ┌──────────────────────────────────────────────────────┐
   │  Customer places order via UI                        │
   └──────────────────────┬───────────────────────────────┘
                          ▼
   ┌──────────────────────────────────────────────────────┐
   │  OrderWorkflow (saga)                                │
   │  process_payment → verify_credentials                │
   │   → pick_and_pack → dispatch_delivery                │
   └──────────────────────┬───────────────────────────────┘
                  step fails│
                          ▼
   ┌──────────────────────────────────────────────────────┐
   │  OrderRepairWorkflow (agentic)                       │
   │  Claude loops on call_claude → tool_use → repeat     │
   │  Tools dispatched through declarative harness        │
   └─────┬───────────────────┬───────────────────┬────────┘
         │                   │                   │
         ▼ auto-resolve      ▼ ask customer      ▼ escalate
   ┌─────────────┐  ┌──────────────────┐  ┌──────────────────┐
   │ tool calls  │  │ CustomerConfirm  │  │ SlackConversation│
   │ run; agent  │  │ Workflow         │  │ Workflow         │
   │ returns     │  │ (email + page)   │  │ (multi-turn ops) │
   └─────────────┘  └──────────────────┘  └──────────────────┘
```

---

## What you'll see in a 5-minute demo

1. Place an order via the UI at `http://localhost:3000`. Optionally pick a `forced_failure`
   from the dropdown to deterministically trigger a specific repair scenario.
2. Watch the order step through the saga in the Temporal UI at `http://localhost:8233`.
3. When the failure step raises, an `OrderRepairWorkflow` child kicks off. Open it.
4. Each Claude call is one `call_claude` activity. Each tool call is a child workflow or
   another activity. The full event history is the agent's transcript — no separate logs.
5. Depending on the failure type, you'll see one of:
   - **Auto-resolution** — the agent applied a containment charm / dispatched a house elf /
     rerouted via Floo and the order continues.
   - **Customer prompt** — open MailHog (`http://localhost:8025`) for the email, OR
     `http://localhost:3000/orders/<order_id>` for the same prompt as a UI element. Either
     channel, whichever you click first, signals the workflow.
   - **Ops escalation** — the agent posts a diagnosis and proposed plan to your configured
     Slack channel. Approve/Deny in Slack; on approval the agent runs the validated plan
     steps; on denial the order is compensated.
6. Mention `@ops-agent` in the same Slack channel and ask "what's broken?" — a separate
   long-lived `OpsAgentConversationWorkflow` per thread answers from Temporal Visibility,
   posts confirmation cards for any mutating action, and stays open for 24 hours of idle.

---

## Quick start

### Prerequisites

- Docker Desktop (or Docker Engine + Compose v2) for the worker / API / UI / MailHog stack.
- The Temporal CLI for the local dev server: `brew install temporal` or
  [download a release](https://github.com/temporalio/cli/releases).
- An [Anthropic API key](https://console.anthropic.com) (for `call_claude`).
- *Optional, for the ops agent:* a Slack workspace + a Slack app you control. The
  `slack-app-manifest.yml` in this repo has the exact scopes / events / actions wiring.
- Python 3.13.12 (pinned via `.python-version`) only if you want to run things bare-metal;
  the Docker stack handles its own.

### Configure secrets

```bash
cp .env.example .env
# then edit .env and fill in:
#   ANTHROPIC_API_KEY=sk-ant-…
#   SLACK_BOT_TOKEN=xoxb-…       (optional)
#   SLACK_APP_TOKEN=xapp-…       (optional, for socket mode)
#   SLACK_CHANNEL_ID=C…          (optional)
#   HITL_TOKEN_SECRET=…anything-non-empty…
```

### Run

In two separate terminals:

```bash
# Terminal 1 — Temporal dev server (binds 127.0.0.1:7233 + UI on :8233)
make dev

# Terminal 2 — register custom search attributes (one-time per Temporal namespace)
make setup-attrs

# Terminal 2, after setup-attrs — boot the rest
make docker-up
```

That's it. Open:

| URL | What's there |
|---|---|
| `http://localhost:3000` | Storefront + ops dashboard |
| `http://localhost:8233` | Temporal UI (workflow histories) |
| `http://localhost:8025` | MailHog inbox (customer HITL emails) |
| `http://localhost:8000/docs` | FastAPI docs (catalog, orders, inventory APIs) |

Tear down with `make docker-down`. Or `make clean` to also wipe the Docker images and the
UI's `node_modules`.

#### Bare-metal (no Docker)

```bash
make codespace
```

Runs the worker / API / UI / Slack bot directly on the host using `scripts/start-codespace.sh`.
Useful in GitHub Codespaces or any environment where Docker-in-Docker is awkward.

---

## Architecture

### Services

| Service | Tech | Purpose |
|---|---|---|
| `api` | FastAPI + Pydantic | REST endpoints for catalog, orders, inventory, customer-HITL landing pages. Talks to Temporal via the Python SDK. |
| `worker` | `temporalio` Python SDK + Anthropic SDK | Hosts all `@workflow.defn` and `@activity.defn` definitions. Calls the Anthropic API for Claude. |
| `ui` | Vite + React | Storefront, order detail page, ops dashboard, live order stream via SSE. |
| `slack-bot` | Slack Bolt (Python, socket mode) | Receives `app_mention`, button clicks, picker selections. Routes them as Temporal signals. |
| `mailhog` | mailhog/mailhog | Captures customer-HITL emails for the demo. No real SMTP. |

Temporal itself runs on the host (`make dev`) — containers reach it through
`host.docker.internal:7233` (Docker Desktop) or the `host-gateway` extra-host on Linux.

### Workflows

| Workflow | Lifecycle | What it owns |
|---|---|---|
| `OrderWorkflow` | One-shot, ~seconds | The saga. Steps through `process_payment → verify_credentials → pick_and_pack → dispatch_delivery`, runs compensations on failure, spawns `OrderRepairWorkflow` for repair-eligible failures. |
| `OrderRepairWorkflow` | One-shot, bounded by 10 agent iterations | The repair agent loop. Calls Claude, dispatches tools through the harness, returns a typed `OrderRepairResult`. |
| `CustomerConfirmationWorkflow` | Up to 25h | Customer HITL. Sends an Approve/Deny email with signed token URLs, exposes a query for the order page UI, waits on a `receive_customer_decision` signal. Reminder email at 4h. |
| `SlackConversationWorkflow` | Up to 25h | Multi-turn ops negotiation. Posts the agent's proposed plan to Slack, allows operators to amend it via thread replies (the bot processes those through Claude), waits on Approve/Deny clicks. |
| `OpsAgentConversationWorkflow` | Up to 24h idle | Long-lived per-Slack-thread agent. Reactive — sits in `wait_condition` on an inbox, runs a turn cycle on each `receive_slack_message` signal, closes itself on idle timeout. |

### Activities

`worker/activities/` — the actual I/O.

| File | Activities |
|---|---|
| `order_activities.py` | The saga's forward steps: `process_payment`, `verify_credentials`, `pick_and_pack`, `dispatch_delivery`. Each may raise an `ApplicationError` (forced failures are non-retryable; random transient flakes are retryable). |
| `compensation_activities.py` | The saga's rollback steps: `refund_payment`, `release_inventory_reservation`, `recall_delivery`. |
| `claude_activities.py` | `call_claude` — the only activity that talks to the Anthropic API. Retry policy lives at the call site in workflow code. |
| `repair_activities.py` | `execute_repair_tool` (generic dispatch by name for the repair-toolkit's autonomous tools) and `execute_approved_plan_step` (runs ops-approved plan steps after escalation). |
| `slack_activities.py` | The legacy `SlackConversationWorkflow` activities: post initial message, post reply, process operator messages through Claude. |
| `ops_activities.py` | Read activities for the ops agent (Temporal Visibility queries, catalog fetch), mutating activities (`cancel_order`, `adjust_inventory`), and Slack output activities (confirmation card, picker, plain reply, rich Block-Kit reply, thread-closed notice). |
| `email_activities.py` | `send_customer_confirmation_email` — fires SMTP at MailHog. |

---

## The declarative tool harness

The repair agent and the ops Slack agent both call tools through `shared/agent_harness/` — a
workflow-safe declarative model where each tool is a single `ToolDef` object, not a
hand-written JSON schema. The same shared `run_agent_turn` helper drives both agentic loops;
the workflows themselves are thin envelopes that own their lifecycle.

```python
# shared/agent_harness/tooldef.py — one declarative shape per tool
@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    args_model: type[BaseModel]          # Pydantic — JSON schema + runtime validator
    category: ToolCategory               # READ / MUTATING / AUTONOMOUS / HITL_INTERACTION / SLACK_OUTPUT
    guards: tuple[Guard, ...] = ()       # workflow-safe coroutines that may Pass or Reject
    impl: Callable | None = None         # @activity.defn — for activity-backed tools
    interaction: HitlInteraction | None = None  # workflow coroutine — for HITL/state-only tools
    timeout: timedelta = timedelta(seconds=30)
    terminates_loop: bool = False        # only valid on HITL_INTERACTION
    make_impl_input: Callable | None = None  # adapter when activity input ≠ args
```

### The five tool categories

| Category | Required guards | Example tools |
|---|---|---|
| `READ` | none | `list_inventory`, `describe_order`, `get_workflow_history` |
| `MUTATING` | at least one human-confirmation guard | `cancel_order` (ops, with `ops_confirmation`), `substitute_item` (repair, with `substitute_item_customer_confirmation`) |
| `AUTONOMOUS` | none | `apply_containment_charm`, `dispatch_house_elf`, `reroute_via_floo` (repair-only — agent has discretion) |
| `HITL_INTERACTION` | none — the response IS the result | `request_customer_confirmation`, `post_order_picker`, `escalate_to_human` |
| `SLACK_OUTPUT` | none | `post_rich_reply` |

### Policy enforcement at import time

`shared/agent_harness/policy.py` declares which guard kinds each category requires. The
`register_tool(...)` wrapper validates this at module-import time — meaning a `MUTATING`
ToolDef without a `CUSTOMER_CONFIRMATION` or `OPS_CONFIRMATION` guard **crashes the worker on
startup** with a clear `ToolPolicyError`. The policy can't be skipped by the LLM, can't drift
out of sync with a frozenset, and can't be silently turned off.

```python
CATEGORY_POLICIES = {
    ToolCategory.MUTATING: frozenset({
        GuardKind.CUSTOMER_CONFIRMATION,
        GuardKind.OPS_CONFIRMATION,
    }),
    # READ / AUTONOMOUS / HITL_INTERACTION / SLACK_OUTPUT: no required kinds
}
```

This is the design principle: **deterministic harness-level enforcement over prompt-engineered
nudging.** Telling Claude in the system prompt "please ask the operator before cancelling"
is fragile; making `cancel_order` literally undispatchable without an `OPS_CONFIRMATION` guard
is structural.

### Guards as plain async functions

A guard is just a workflow-safe coroutine. It can call activities, await futures resolved by
signal handlers, or spawn child workflows. Tag with `@guard(kind=...)` so the policy validator
can see what it provides:

```python
@guard(kind=GuardKind.OPS_CONFIRMATION)
async def ops_confirmation(tu: ClaudeToolUse, ctx: AgentCtx) -> GuardOutcome:
    # Post Slack confirmation card → await operator click → collapse buttons
    future = asyncio.Future()
    ctx.pending_actions[tu.id] = future
    post_result = await workflow.execute_activity(post_confirmation_card, ...)
    decision = await future                      # signal handler resolves this
    await workflow.execute_activity(collapse_buttons, ...)
    return Pass() if decision == "confirm" else Reject(reason="Operator declined…")
```

Same pattern for `substitute_item_customer_confirmation`, which spawns a
`CustomerConfirmationWorkflow` child instead of posting to Slack.

### What the agent never sees

When a customer-confirmable tool like `substitute_item` is called, the harness:
1. Pydantic-validates the args.
2. Runs the customer-confirmation guard. The guard spawns a child workflow that emails the
   customer and shows the order page UI; it waits durably (up to 25 hours) for a response.
3. On approval, runs the tool's `interaction` (which performs the workflow-state mutation).
4. Returns a `ToolResult` to Claude.

The agent calls `substitute_item` as if it were a single tool. It doesn't see the customer
prompt. It can't forget to ask first; the harness asks structurally.

---

## Failure scenarios

`order.forced_failure` lets you pick a deterministic demo path from the storefront. Each
maps to a step that raises a non-retryable `ApplicationError`, which `OrderWorkflow` catches
and routes through `OrderRepairWorkflow`.

| `forced_failure` | Step that fails | Repair path |
|---|---|---|
| `monster_book_escape` | `pick_and_pack` | Auto: `apply_containment_charm` + `dispatch_house_elf` |
| `floo_misdirected` | `dispatch_delivery` | Auto: `reroute_via_floo` |
| `owl_intercepted` | `dispatch_delivery` | Auto: `dispatch_house_elf` to retrieve, then `reroute_via_floo` |
| `inventory_mismatch` | `pick_and_pack` | Customer: agent calls `substitute_item` (harness asks customer to approve a substitute) |
| `ministry_approval_required` | `verify_credentials` | Customer: agent calls `request_customer_confirmation` (the customer's attestation IS the resolution) |
| `restricted_section` | `verify_credentials` | Customer: same shape as Ministry approval |
| `gringotts_failure` | `process_payment` | Ops escalation: agent diagnoses, posts plan to Slack, ops approves a workaround |
| `payment_timeout` | `process_payment` | Ops escalation, similar to gringotts |
| `warehouse_failure` | `pick_and_pack` | Ops escalation |

Random natural failures (no `forced_failure` set) for `process_payment` are now **retryable**
— Temporal automatically retries the activity per its default policy and the next sample
almost always succeeds. The failure stays visible in workflow history (good for showing off
retry semantics) but never wakes up the agent.

---

## The ops Slack agent

A separate flow from the repair agent. Mention `@ops-agent` (or your bot user) in any channel
the bot belongs to:

```
@ops-agent what's been failing in the last hour?
```

Behind the scenes:

1. The Slack bot signals `OpsAgentConversationWorkflow` (started fresh per thread).
2. The workflow wakes up, drains the message into its conversation buffer, and calls Claude
   through the same `run_agent_turn` helper the repair agent uses.
3. Claude calls **read tools** (`list_orders`, `describe_order`, `get_workflow_history`,
   `aggregate_repair_failures`, `list_inventory`, `get_book`, …) directly — no confirmation.
4. For **mutating tools** (`cancel_order`, `adjust_inventory`, repair-toolkit tools used in
   ops mode), the harness's `ops_confirmation` guard posts a Block-Kit confirmation card.
   The operator confirms or denies; the workflow only proceeds if confirmed.
5. For **picker tools** (`post_order_picker`), the workflow posts an interactive dropdown of
   in-flight orders and awaits the click as the tool's result.
6. The thread stays open with a 24-hour idle timeout. After that the workflow posts a
   "thread closed" notice and exits cleanly.

Because the entire conversation is a Temporal workflow, you can step through every operator
message, every tool call, and every confirmation in the Temporal UI — including replays.

---

## Project structure

```
.
├── api/                     # FastAPI service: catalog + orders + inventory APIs + HITL landing pages
│   ├── main.py
│   └── Dockerfile
├── docker-compose.yml       # Worker + api + ui + slack-bot + mailhog
├── docs/
│   └── superpowers/
│       ├── plans/           # Implementation plans
│       └── specs/           # Design docs (the tool-harness spec is in here)
├── Makefile                 # dev / setup-attrs / docker-up / codespace / clean
├── PLAN.md                  # The original demo brainstorm
├── requirements.txt         # Pinned Python deps (Python 3.13.12)
├── scripts/
│   └── start-codespace.sh   # Bare-metal local runner (no Docker)
├── shared/
│   ├── agent_harness/       # The declarative tool harness — workflow-safe primitives
│   │   ├── tooldef.py       #   ToolDef + ToolCategory
│   │   ├── guards.py        #   Pass / Reject / GuardKind / @guard / Guard / HitlInteraction
│   │   ├── policy.py        #   CATEGORY_POLICIES / validate_tool / ToolPolicyError
│   │   ├── registry.py      #   register_tool (validation pass-through)
│   │   ├── ctx.py           #   AgentCtx (pending_actions, channel, thread_ts, domain_input/state)
│   │   └── loop.py          #   run_agent_turn + dispatch_tool
│   ├── catalog.py           # The book inventory (workflow-safe pure data)
│   ├── hitl_tokens.py       # HMAC-signed Approve/Deny URLs
│   └── models.py            # Dataclasses crossing Temporal payloads
├── slack_bot/               # Slack Bolt app — receives mentions, button clicks, picker selections
├── slack-app-manifest.yml   # Slack app config (scopes, event subscriptions, interactivity)
├── ui/                      # Vite + React storefront and ops dashboard
└── worker/
    ├── main.py              # Worker entry point — registers all workflows + activities
    ├── config.py            # Env-driven configuration
    ├── workflows/
    │   ├── order_workflow.py                    # The saga
    │   ├── order_repair_workflow.py             # Repair agent envelope (thin — calls run_agent_turn)
    │   ├── customer_confirmation_workflow.py    # Email + page HITL
    │   ├── slack_conversation_workflow.py       # Multi-turn ops negotiation
    │   └── ops_agent_conversation_workflow.py   # Per-thread ops agent
    ├── agent/
    │   ├── tool_args.py     # Pydantic args models for every tool
    │   ├── guards.py        # ops_confirmation + substitute_item_customer_confirmation
    │   ├── interactions.py  # request_customer_confirmation / post_order_picker / escalate / substitute_item
    │   ├── repair_tools.py  # REPAIR_TOOLS = list[ToolDef]
    │   ├── ops_tools.py     # OPS_TOOLS = list[ToolDef] + build_ops_system_prompt
    │   ├── repair_state.py  # RepairAgentState dataclass
    │   ├── tools.py         # Legacy: only CONVERSATION_TOOLS for the slack-conversation update_plan tool
    │   ├── executor.py      # Parallel tool dispatcher (asyncio.gather over per-tool calls)
    │   └── validator.py     # validate_plan_steps for ops-approved plan execution
    └── activities/          # All @activity.defn — see the Activities table above
```

---

## Configuration

| Env var | Required? | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | The Claude API key. |
| `HITL_TOKEN_SECRET` | yes (any non-empty value) | HMAC secret for signing customer-HITL Approve/Deny URLs. Defaults to the literal string `change-me-in-demo`. |
| `SLACK_BOT_TOKEN` | only for ops Slack agent | `xoxb-…` Bot User OAuth token. |
| `SLACK_APP_TOKEN` | only for ops Slack agent | `xapp-…` for socket mode. |
| `SLACK_CHANNEL_ID` | only for ops escalations | The channel ID (e.g. `C012ABCDE`) where `escalate_to_human` posts. |
| `TEMPORAL_HOST` | no | Default `localhost:7233` (or `host.docker.internal:7233` from inside the docker network). |
| `API_BASE_URL` | no | Worker → API HTTP base, used for inventory/catalog calls. Default `http://localhost:8000`; docker-compose overrides to `http://api:8000` so the worker can reach the API container. |
| `API_PUBLIC_URL` | no | Public-facing URL used to build customer-clickable HITL email links. Default falls back to `API_BASE_URL`; docker-compose overrides to `http://localhost:8000` so the recipient's host browser can resolve it. |
| `SMTP_HOST` / `SMTP_PORT` | no | MailHog defaults: `mailhog:1025`. |
| `MAILHOG_UI_URL` | no | The host-accessible MailHog web UI for the storefront's "Open MailHog" button. Default `http://localhost:8025`. |
| `TEMPORAL_UI_URL` | no | Default `http://localhost:8233` for the storefront's deep links into the Temporal UI. |

Slack setup is optional — the demo runs perfectly without it; you just won't see the ops
escalation path or the `@ops-agent` flow. To enable, install the manifest at
`slack-app-manifest.yml` into a workspace you control and set the three Slack env vars above.

---

## Useful commands

| Command | What it does |
|---|---|
| `make dev` | Start a local Temporal dev server on `:7233` with UI on `:8233`. Run first. |
| `make setup-attrs` | Register the custom search attributes (`OrderId`, `OrderStatus`, `FailureType`, `RepairOutcome`, `RequiresHITL`, `RepairAttempts`, …) in the dev namespace. One-time. |
| `make docker-up` | Build and start worker + api + ui + slack-bot + mailhog. |
| `make docker-down` | Stop the docker stack. |
| `make logs` | Tail all container logs. |
| `make codespace` | Bare-metal alternative to `docker-up` — runs everything on the host. |
| `make clean` | Stop everything, remove built images and the UI's `node_modules`/`dist`. |

---

## Debugging tips

- **"Why did the agent do that?"** Open the relevant `OrderRepairWorkflow` in the Temporal UI
  and walk the events. `call_claude` activity inputs/outputs show you the exact prompt and
  response. Each tool call is its own scheduled activity; tool results appear as the next
  user-message in the next `call_claude` input.
- **Customer-HITL not advancing?** Check that the customer's email link points at
  `http://localhost:8000` (host-accessible), not `http://api:8000` (docker-internal). The
  split is governed by `API_BASE_URL` vs `API_PUBLIC_URL`.
- **`ToolPolicyError` on worker startup?** A `ToolDef` is missing a required guard for its
  category. The error names the tool and the missing kind. Add the guard or change the
  category.
- **MailHog inbox empty?** SMTP failed silently. `docker compose logs worker` shows the
  `send_customer_confirmation_email` activity error.
- **Slack `@ops-agent` not responding?** The bot needs Socket Mode enabled and the right
  scopes — see `slack-app-manifest.yml` for the canonical config.
- **Random gringotts/payment failures during the demo?** They're now retryable transient
  errors, not repair-eligible failures. You'll see the retry in workflow history but the
  agent won't be invoked. To trigger the ops escalation path on payment, set
  `forced_failure=gringotts_failure` on the order.

---

## Out of scope

The demo is intentionally narrow.

- **No tests.** This is a demo; verification is manual and history-based.
- **No real payment / inventory backend.** The "OMS" is a list of dataclasses backed by
  the FastAPI process; restarting the API resets it. The Gringotts API is wholly fictional.
- **No continue-as-new in any agent loop.** Repair runs are bounded at 10 iterations;
  ops conversations end on a 24-hour idle timeout. For longer-lived production agents you'd
  add CAN with a compaction step before the restart.
- **No agentic SDK.** Pure `temporalio`, `anthropic`, and Pydantic. The harness is a
  ~500-line workflow-safe module, not a framework.
- **Idempotency keys are in-process.** `cancel_order`/`adjust_inventory` cache `tool_use_id`
  in a worker-local dict. Restart the worker and that protection is gone. Production would
  use a real KV / database.
