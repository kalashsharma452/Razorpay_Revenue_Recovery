# Revenue Recovery Intelligence Engine

An end-to-end engine that detects failed Razorpay payments, classifies *why* they
failed, and decides — with an ML model — which recovery action is most likely to
collect the money without harassing the customer.

This repository contains a live two-tier system (React/Dashboard frontend,
FastAPI/PostgreSQL backend) plus a sealed synthetic evaluation harness that
quantifies the ML policy's uplift over a naive retry baseline.

> **Read first:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) is the full
> end-to-end trace with every hop mapped to `file:line`.

---

## The Problem

Failed payments are raw revenue loss. Most recovery systems fire an indiscriminate
"retry level 1 / retry level 2" sequence:

- retries happen even when the root cause makes them pointless (e.g. insufficient
  funds, expired card) — annoying the customer,
- correct interventions are never tried (e.g. a payment link for a declined card),
- and banking on repeated retries costs money and goodwill with no decision audit.

The goal: **decide** instead of **spray**. Given the failure signal plus
observable customer history, pick the single best next action — a retry at the
right delay, an alternative-payment link, a message, or a deliberate stop — and
record the reasoning for every decision.

---

## Key Capabilities & End-to-End Flow

1. **Create order → pay.** The checkout creates a Razorpay order (Test Mode) and
   a customer is auto-created if unknown.
2. **Webhook ingestion.** `payment.failed` / `payment.captured` /
   `payment_link.paid` events are HMAC-SHA256 verified, deduplicated by event id,
   and persisted. Recovery is dispatched as a **background task** — never
   synchronously inside the webhook.
3. **Root-cause classification.** Failed attempts are classified into one of
   `temporary_failure`, `bank_unavailable`, `processor_timeout`,
   `insufficient_funds`, `card_expired`, `invalid_payment_method`,
   `repeated_failure`, or `unknown` from structured error signals
   (`app/recovery/root_cause.py`).
4. **ML scoring.** A 13-feature context (customer history + payment context +
   simulation-clock time) is scored against **5 candidate actions** by the GB
   model: `P(recovery)` for `RETRY_LATER_1h/6h/24h`,
   `ALTERNATIVE_PAYMENT_0h`, `CUSTOMER_MESSAGE_0h`.
5. **Policy layer.** Only actions permitted for the root cause are eligible; the
   highest-scoring one must clear `MIN_CONFIDENCE = 0.40`. Otherwise → **STOP**.
   A hard cap of 3 recovery actions per order also forces STOP.
6. **Scheduler executes.** An APScheduler job polls (every 30s, driven by the
   simulation clock) and executes due actions — creating a new retry session, a
   Razorpay Payment Link, recording a customer message, or halting.
7. **Attribution.** A later successful payment is credited to AI only when there
   is hard evidence (payment-link refs, order-id match, message refs within 72h);
   otherwise it is `native_checkout`, never falsely claimed.
8. **Observe.** The dashboard shows the decision, the scores, the root cause,
   and a unified chronological timeline per order.

---

## Architecture Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│ FRONTEND  React + React Router + Vite (dev :5173)                      │
│  /                     Checkout — create test payment                  │
│  /dashboard            Overview — live stats + sealed evaluation        │
│  /dashboard/orders     Orders list / detail + event timeline + Retry    │
│  /dashboard/analytics  AI vs baseline analytics                         │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │ REST (CORS: localhost:5173)
┌────────────────────────────────▼───────────────────────────────────────┐
│ BACKEND  FastAPI + SQLAlchemy + APScheduler (Python 3.11)              │
│  /orders · /webhooks · /admin (clock, scheduler)                       │
│  /dashboard · /diagnostics/model · /health                             │
│   recovery engine: root_cause.py → pipeline.py → predictor.py          │
│                    → scheduler.py → executor.py · clock.py · explainer │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │ Razorpay Test Mode (keys server-side)
┌────────────────────────────────▼───────────────────────────────────────┐
│ POSTGRES 16  (docker compose)                                          │
│ customers · orders · payment_attempts · webhook_events                 │
│ recovery_actions · recovery_outcomes                                   │
└────────────────────────────────────────────────────────────────────────┘
```

Key design decisions (enforced in code):

- **One internal order per business order.** A retry creates a *new Razorpay
  session*, never a new internal order; retry attempts accumulate on the same
  `Order` row (`executor.py`).
- **Decisions are not interventions.** `RecoveryAction.intervention_ref` is set
  only by the executor after it creates the actual artifact (order id, link id,
  message ref), never by the pipeline.
- **Evidence-based attribution.** Recovery credit requires a verified reference;
  a self-resolving payment is `native_checkout`, not AI.
- **Observability.** `reasoning` records every scored candidate with `[blocked]`
  markers; each action is stamped with the model artifact that produced it
  (`gb`/`lr`) at decision time (`mode`/`model` from `/diagnostics/model`).

---

## ML Approach

| Artifact | Model | Role |
|---|---|---|
| `backend/data/model.pkl` | `HistGradientBoostingClassifier` | **Primary default.** Learns action × context interactions natively on ordinal-encoded categoricals |
| `backend/data/model_lr.pkl` | `LogisticRegression` (one-hot) | **Baseline / comparison.** Flat coefficients can't model those interactions |

Selector: `DEMO_MODEL=lr` loads the LR artifact (no retraining, GB bytes
untouched); anything else loads/trains GB — the default startup path
`train_and_load()` is GB-bound (`predictor.py`).

**Features (13).** 10 numeric — `historical_success_rate`,
`historical_failure_count`, `consecutive_failures`, `total_orders`,
`customer_age_days`, `previous_recovery_success`, `attempt_number`,
`hour_of_day`, `day_of_week`, `delay_hours`; plus 4 categorical —
`root_cause`, `payment_method`, `amount_bucket`, `action_type`. Context is built
from the order, the latest failed attempt, and the simulation clock
(`pipeline.py:_build_context`).

**Scoring.** One row per candidate action → `predict_proba(X)[:, 1]` = the
probability that the order is recovered if this action is taken.

---

## Recovery Actions

| Action | What happens | When permitted |
|---|---|---|
| `RETRY_LATER` (1h / 6h / 24h) | New Razorpay checkout session, same amount; no autonomous charge | temporary_failure, bank_unavailable, processor_timeout, unknown |
| `ALTERNATIVE_PAYMENT` | Razorpay Payment Link the customer can pay through any method | all causes except when retry-only semantics apply |
| `CUSTOMER_MESSAGE` | Records a recovery message to the customer (demo); production sends email/SMS | temporary_failure, bank_unavailable, insufficient_funds, card_expired, invalid_payment_method, repeated_failure, unknown |
| `STOP` | Halts recovery; never overwrites a payment that already succeeded | no permitted action clears the 0.40 floor, or max attempts (3) reached |

The **highest-scoring permitted** action wins; anything below `0.40` confidence
produces a `STOP` (policy), with the near-miss score recorded in the reasoning.

---

## Payment Attribution, Safety & Idempotency

- **Webhooks:** HMAC-SHA256 verified against the server-side secret; every event
  is PK-deduplicated on `razorpay_event_id`; raw payloads persisted before
  processing; processing failures roll back and re-persist the event.
- **Attribution** is assigned only with evidence:
  `AI_ACTION` (verified payment-link capture) · `ai_retry` (payment order-id
  equals the retry session) · `ai_message` (message ref within 72h) ·
  `native_checkout` (everything else).
- **Duplicate execution guard:** the scheduler uses an optimistic lock
  (`scheduled → executing` inside the same transaction) so one action executes
  exactly once even if ticks overlap.
- **STOP safety:** `_execute_stop` re-fetches the order and returns `stale`
  instead of halting if a real payment already landed.
- **Amount guard:** `_matches_amount_currency` + payment-link reference checks
  prevent attributing unrelated payments (`webhooks.py`).
- **Concurrency:** `Order.version_id` is incremented on every status transition.
- **LLM is advisory only:** the explainer generates post-decision narratives and
  falls back to deterministic templates; it can never change the selected action.

Amounts are stored in **paise** end-to-end (DB, recovery, evaluator). The order
API accepts **rupees** and converts to paise at the boundary
(`app/api/orders.py`); the UI displays ₹ with Indian formatting.

---

## Simulation Clock + Scheduler

- `simulated_now = real UTC now + in-memory offset` (`clock.py`). All "now"
  decisions (feature `hour_of_day`/`day_of_week`, `scheduled_for`) use the
  simulation clock; `executed_at` records real UTC for audit. There is no
  environment variable for the offset — it resets on restart.
- Admin controls: `POST /admin/clock/advance?hours=&minutes=`,
  `POST /admin/clock/reset`, `GET /admin/clock`, `POST /admin/scheduler/run`.
- The scheduler (`scheduler.py`) polls every 30s for `status="scheduled" AND
  scheduled_for <= simulated_now`, executes due actions, and commits per-action
  outcomes (`executed` / `failed` / `stale` / `halted`).

---

## Synthetic Pipeline (train / validation / held-out)

The evaluator is **isolated by design** from the live system and from the data
generator (`simulator/`):

- `customer_generator.py` → 4 customer segments by observable features
  (reliable / occasional / struggling / new).
- `behavior_model.py` → the **hidden** ground-truth recovery probability per
  root cause × action × delay (with segment/flow/amount/time modifiers). It is
  **never imported** by the pipeline, predictor, or any training code (isolation
  rule), and the segment column is never written to a CSV.
- `dataset_builder.py` → 87,500 outcome rows:
  `train.csv` 50,000 · `validation.csv` 12,500 · `held_out.csv` 25,000
  (held-out is **sealed**).
- `evaluator.py` runs both strategies on the sealed `held_out.csv` with equal
  attempts (2 interventions each, both 2-vs-2 — a structural fairness fix):
  - **Static baseline:** `RETRY_LATER_1h → RETRY_LATER_24h → stop`
  - **AI strategy:** ML picks the best permitted action per failure (offline
    threshold tuned on validation only, `MIN_CONFIDENCE = 0.15`), then reassesses.
  - Costs: `RETRY_LATER ₹1` · `ALTERNATIVE_PAYMENT ₹3` · `CUSTOMER_MESSAGE ₹0.50` ·
    `STOP ₹0`; net = gross recovered − action cost.

---

## Final Held-Out Results — *synthetic / offline, not production*

Results are frozen in `backend/data/evaluation_results.json` and
`backend/data/lr_snapshot.json`; they describe the **synthetic** held-out dataset
only and are **not** a claim about production performance.

| Model | Evaluator | AI recovery | Baseline | Uplift | ROC-AUC |
|---|---|---|---|---|---|
| Logistic Regression | Unfair (1 vs 2) | 53.96% | 55.78% | −1.82 pts | 0.743 |
| Logistic Regression | Fair (2 vs 2) | 64.02% | 55.78% | **+8.24 pts** | 0.743 |
| **Gradient Boosting** | **Fair (2 vs 2)** | **80.44%** | **55.78%** | **+24.66 pts** | **0.811** |

GB net revenue (held-out): AI **₹15,23,810.50** vs baseline **₹10,50,399** →
incremental net **₹4,73,411.50** (+45.07% relative). AI action mix: 3,595
alternative payments · 2,055 retries · 1,149 messages — with only **621
unnecessary retries vs 5,387** for the baseline.

> The live system deliberately runs a **conservative `0.40` confidence floor**
> (vs the offline evaluator's validation-tuned `0.15`) because live STOP events
> should be rare and auditable.

---

## Tech Stack

- **Backend:** Python 3.11 · FastAPI · SQLAlchemy 2 · PostgreSQL 16 (Docker) ·
  APScheduler · scikit-learn (`HistGradientBoostingClassifier`,
  `LogisticRegression`) + pandas · razorpay SDK · Groq (LLM explanations only,
  deterministic fallback)
- **Frontend:** React 19 · React Router 7 · Vite 8 · oxlint
- **Ops:** Docker Compose (Postgres only) · manual SQL migrations in
  `backend/migrations/` · `version_id` optimistic locking

---

## Repository Structure

```
.
├─ docker-compose.yml            # Postgres 16 (db: recovery)
├─ docs/ARCHITECTURE.md          # full end-to-end trace (file:line)
├─ backend/
│  ├─ app/
│  │  ├─ main.py                 # FastAPI app, lifespan: model load + scheduler
│  │  ├─ config.py               # env-config (keys, DEMO_MODEL, demo flags)
│  │  ├─ database.py             # SQLAlchemy engine/session
│  │  ├─ clock.py                # simulation clock
│  │  ├─ scheduler.py            # APScheduler poll + optimistic lock
│  │  ├─ api/                    # orders · webhooks · admin · dashboard
│  │  ├─ models/models.py        # customers, orders, payment_attempts, ...
│  │  ├─ schemas/order.py        # rupee→paise boundary contract
│  │  └─ recovery/
│  │     ├─ root_cause.py        # error → root-cause classification
│  │     ├─ pipeline.py          # guard → context → score → policy → action
│  │     ├─ predictor.py         # GB/LR models, 5-candidate scoring
│  │     ├─ executor.py          # Razorpay interventions / STOP
│  │     └─ explainer.py         # LLM explanation (post-decision)
│  ├─ simulator/                 # customer_generator · behavior_model ·
│  │                             # dataset_builder · evaluator (ISOLATED)
│  ├─ data/                      # sealed: train/validation/held_out.csv,
│  │                             # model.pkl (GB), model_lr.pkl (LR),
│  │                             # evaluation_results.json, lr_snapshot.json
│  ├─ migrations/                # manual SQL schema migrations
│  ├─ tests/                     # regression tests (plain-Python, no pytest dep)
│  ├─ requirements.txt
│  └─ seed_demo_customers.py     # 3 demo profiles for differentiated decisions
└─ frontend/
   ├─ src/pages/                 # Checkout · Overview · Orders · OrderDetail · Analytics
   ├─ src/modelInfo.js           # model identity from /diagnostics/model
   └─ package.json
```

---

## Setup & Run

Prerequisites: Docker, Python 3.11+, Node 18+.

```bash
# 1. Database
docker compose up -d          # Postgres 16 on :5432 (user/pass razorpay, db recovery)

# 2. Backend
cd backend
python -m venv .venv && .venv\Scripts\activate   # (Windows); use bin/activate on macOS/Linux
pip install -r requirements.txt
# create backend/.env:
#   RAZORPAY_KEY_ID=...            # Razorpay Test Mode keys
#   RAZORPAY_KEY_SECRET=...
#   DATABASE_URL=postgresql+psycopg2://razorpay:razorpay@localhost:5432/recovery
#   GROQ_API_KEY=...               # optional — explanations only
#   RECOVERY_DEMO_LOAD_ONLY=1      # recommended: load the sealed GB artifact, no retrain
#   DEMO_CUSTOMER_HISTORY=true     # demo-only: derive customer history (default false)
uvicorn app.main:app --reload     # API on :8000

# 3. Frontend
cd ../frontend
npm install
npm run dev                       # UI on :5173
```

Open **http://localhost:5173**. The dashboard reads the loaded artifact identity
from `GET /diagnostics/model` and shows which model your decisions came from.

---

## Demo Flow

1. On the **Checkout** page, enter an amount (e.g. `2000` → ₹2,000) and create a
   test payment.
2. In the Razorpay modal use a test card:
   - Success: `4111 1111 1111 1111` (future expiry, CVV `123`)
   - Decline: `4000 0000 0000 0002` (insufficient funds)
3. A decline fires the webhook → the engine classifies the root cause, scores
   the candidates, and schedules the best permitted action (or STOP).
4. **Differentiated decisions (optional):** seed three clearly distinct customer
   histories, then set `DEMO_CUSTOMER_HISTORY=true` in `backend/.env` and restart:

   ```bash
   cd backend && python seed_demo_customers.py
   ```

   With a high-ticket `bank_unavailable` failure the profiles land on different
   sides of the 0.40 floor — a reliable customer gets `RETRY_LATER`, a new one
   gets `STOP`. Remove with `python seed_demo_customers.py --unseed`.
5. **Fast-forward time** to watch the scheduler act: `POST
   /admin/clock/advance?hours=6` (needs the API, e.g.
   `curl -X POST "http://localhost:8000/admin/clock/advance?hours=6"`), then
   refresh a recovering order — the retry session / link appears with a **Retry**
   action in the order timeline.
6. **Compare models:** set `DEMO_MODEL=lr` and restart to run the LR baseline
   (the GB artifact is untouched); `GET /diagnostics/model` always reports which
   artifact is live, and every action is stamped with the model that produced it.

---

## Limitations & Future Improvements

- **Synthetic evaluation only.** Results in this repo come from simulated data
  with a hidden behavioral model; they are indicative, not production numbers.
  The next step is a holdout-style A/B live pilot before trusting real money.
- **Deterministic root-cause mapping.** Classification uses structured error
  signals; free-text parsing is heuristic and `unknown` is a catch-all.
- **No autonomous debit.** `RETRY_LATER` prepares a new checkout session for the
  customer to complete; it does not silently re-charge. This is intentional and
  honest, but reduces touchless-recovery upside.
- **Demo `CUSTOMER_MESSAGE`** records the message instead of sending it; a
  provider integration (email/SMS) is required for production.
- **Single-region, single-process scheduler.** Optimistic locking prevents
  duplicate execution within the app; horizontal scaling would need a
  distributed lock on the same semantics.
- **`previous_recovery_success` is currently a constant 0.** Wiring real
  recovery-outcome history back into the feature store is planned.
- **Manual schema migrations.** Moving to a versioned migration tool would help
  team environments.