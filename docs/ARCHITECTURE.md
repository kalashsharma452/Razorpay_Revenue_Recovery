# Revenue Recovery Intelligence Engine — Architecture

Complete end-to-end trace of the live system and the evaluation harness.
Every hop references the exact source file (`path:line`) it runs in.

> Live flow and synthetic evaluation are intentionally separate systems
> (RULES.md #24–#27, MEMORY.md "Locked Decisions").
> **The held-out evaluation is FINALIZED and must not be re-run, re-tuned,
> or modified** (MEMORY.md).

---

## 1. System Overview (live)

```text
┌────────────────────────────────────────────────────────────────────────────┐
│  FRONTEND  (React + react-router-dom, Vite)                                │
│   /                     Checkout (create order → Razorpay Checkout)         │
│   /dashboard            Overview (live stats + sealed evaluation)           │
│   /dashboard/orders     Orders list                                         │
│   /dashboard/orders/:id Order detail + full event timeline + Retry button   │
│   /dashboard/analytics  AI analytics + action distribution                   │
└───────────────────────────────────┬────────────────────────────────────────┘
                                    │  REST (localhost:8000, CORS :5173)
┌───────────────────────────────────▼────────────────────────────────────────┐
│  BACKEND  (FastAPI, Python 3.11)                                           │
│   /orders          create / status / retry-session                 orders.py │
│   /webhooks        payment.failed / payment.captured / payment_link.paid    │
│   /admin           clock advance/reset, scheduler run, model status  admin.py│
│   /dashboard        overview / orders / orders/:id / analytics   dashboard.py│
│   /diagnostics/model   loaded artifact identity (path, sha256, DEMO_MODEL)  │
│   /health          liveness                                                  │
│   ── recovery engine ──                                                      │
│     root_cause.py  classify()        failure → root-cause category           │
│     pipeline.py    run()             guard → context → score → policy → action│
│     predictor.py   score()           GB|LR model, 5 candidate actions        │
│     executor.py    execute()         Razorpay intervention / STOP            │
│     scheduler.py   tick()            APScheduler 30s poll + optimistic lock  │
│     clock.py       simulated_now     real_now + in-memory offset             │
│     explainer.py   explain()         Groq LLM w/ deterministic fallback      │
└───────────────────────────────────┬────────────────────────────────────────┘
                                    │  Razorpay Test Mode APIs (server-side keys)
┌───────────────────────────────────▼────────────────────────────────────────┐
│  RAZORPAY TEST MODE                                                         │
│   order.create / payment_link.create / Checkout / webhooks                  │
└───────────────────────────────────┬────────────────────────────────────────┘
                                    │  SQLAlchemy
┌───────────────────────────────────▼────────────────────────────────────────┐
│  POSTGRESQL (docker compose, razorpay-db-1, :5432)                          │
│   customers · orders · payment_attempts · webhook_events ·                  │
│   recovery_actions · recovery_outcomes                                      │
└────────────────────────────────────────────────────────────────────────────┘
```

Config (`.env` → `app/config.py`): `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`,
`DATABASE_URL`, `GROQ_API_KEY` (optional), `RECOVERY_DEMO_LOAD_ONLY`,
`DEMO_MODEL` (`gb` default | `lr`), `DEMO_CUSTOMER_HISTORY` (false default).

---

## 2. End-to-End Trace

### 2.1 Order creation → checkout (`app/api/orders.py`)

1. `POST /orders` (`orders.py:15`) auto-creates a guest `Customer` if unknown,
   then calls `rzp.order.create(...)` (`orders.py:23`) and persists an internal
   `Order` row with `status="created"` (`orders.py:29`).
2. Frontend opens Razorpay Checkout with the returned `razorpay_order_id` +
   `key_id`. The customer pays (Test Mode).

### 2.2 Webhook ingestion (`app/api/webhooks.py`)

Every Razorpay event lands on `POST /webhooks` (`webhooks.py:327`):

```
body → HMAC-SHA256 verify vs RAZORPAY_KEY_SECRET        (webhooks.py:18,335)
     → PK-dedup on event id (WebhookEvent)              (webhooks.py:343)
     → persist raw payload (processed=False)            (webhooks.py:347)
     → route by event type                              (webhooks.py:378)
        payment.failed      → _handle_failed            returns order_id
        payment.captured    → _handle_captured
        payment_link.paid   → _handle_payment_link_paid
     → commit, respond 200 → recovery runs in BackgroundTasks
                             AFTER the response          (webhooks.py:361-362)
```

- Signature verification and event dedup happen **synchronously** (Rules #18, #19).
- ML inference is **never** synchronous in webhook ingestion (Rule #20): the
  recovery pipeline is dispatched as a FastAPI `BackgroundTasks` call
  (`webhooks.py:471` `_run_recovery`).
- On processing error the txn rolls back and the event is re-persisted as
  unprocessed (`webhooks.py:364-372`) so it cannot be silently lost.

### 2.3 Failure handling + DB state (`_handle_failed`, `webhooks.py:449`)

1. `_find_order_for_payment` (`webhooks.py:74`) resolves the internal order via:
   `payment.order_id` equality **→** a `RETRY_LATER` action whose
   `intervention_ref == rzp_order_id` **→** payment notes `recovery_action_id` /
   `original_order_id`.
2. A new `PaymentAttempt` is recorded against the **same** internal order
   (attempt_number = count+1, `status="failed"`, error_code / error_description /
   error_source) (`webhooks.py:454`).
3. `Order.status = "failed"`, `version_id += 1` (optimistic lock) (`webhooks.py:466`).

>>>RETRY_LATER creates a new Razorpay order but never a new internal order
>>> (Rule #21). Ordinary retries stay inside one `Order`.

### 2.4 Root-cause detection (`app/recovery/root_cause.py`)

`pipeline.run` (`pipeline.py:88`) calls `classify(error_code, error_description,
error_source, attempt_number, payment_method)` → one canonical category:

| Root cause | Trigger (code precedence) |
|---|---|
| `repeated_failure` | `attempt_number >= 3` |
| `insufficient_funds` | desc "insufficient"/"low balance" or `INSUFFICIENT_FUNDS` |
| `card_expired` | desc "expired" or `CARD_EXPIRED` |
| `invalid_payment_method` | desc "invalid" + card/account |
| `bank_unavailable` | source ∈ {bank, issuer} |
| `temporary_failure` | source ∈ {gateway, acquirer, network} **or** code ∈ {BAD_REQUEST_ERROR, GATEWAY_ERROR, SERVER_ERROR} |
| `processor_timeout` | desc "timeout"/"timed out" |
| `unknown` | nothing else matched |

### 2.5 Feature / context vector (`_build_context`, `pipeline.py:164`)

Built from the order, latest failed attempt, root cause, and the **simulation
clock** (Section 2.8):

```
historical_success_rate  (captured/total attempts)
historical_failure_count (failed attempts)
consecutive_failures     (= attempt_number)
total_orders             (demo-history: COUNT(orders) for customer · else 1)
customer_age_days        (demo-history: days since Customer.created_at · else 30)
previous_recovery_success(= 0)
attempt_number, hour_of_day (simulated), day_of_week (simulated)
root_cause, payment_method, amount_bucket
        amount_bucket: <5,000 low · <50,000 medium · ≥50,000 high   (pipeline.py:193)
```

The predictor adds the action side `action_type` + `delay_hours` per candidate
(Section 3), giving 10 numeric + 4 categoricals.

**Demo-only customer history (default off):** with `DEMO_CUSTOMER_HISTORY=true`,
`_build_context` derives `total_orders` from the customer's `Order` count and
`customer_age_days` from `Customer.created_at`; when disabled it keeps the
literals `1`/`30` (production path byte-identical). Seed 3 distinct profiles
across the 0.40 floor: `python seed_demo_customers.py`; remove them with
`--unseed`. The seed touches only `customers`/`orders`/`payment_attempts` — no
attribution, outcomes, evaluator, or model changes.

### 2.6 ML scoring (`app/recovery/predictor.py`)

`predictor_module.get().score(context)` (`pipeline.py:100`) builds **5 rows** —
one per candidate action — and returns P(recovery) for each:

```text
RETRY_LATER_1h  RETRY_LATER_6h  RETRY_LATER_24h  ALTERNATIVE_PAYMENT_0h  CUSTOMER_MESSAGE_0h
```

- Model is the process singleton loaded at startup (Section 3).
- If no model is loaded, `pipeline.py:106` falls back to rule-based scores and
  records `decision_source="rules"`.

### 2.7 Policy / threshold (`pipeline.py`)

1. **Guard 1 — max attempts:** existing `RecoveryAction` count for the order
   `>= MAX_RECOVERY_ATTEMPTS (3)` → immediate `STOP`, `decision_source="policy"`,
   confidence 1.0 (`pipeline.py:68`).
2. **Guard 2 — permitted actions** (`_PERMITTED_ACTIONS`, `pipeline.py:33`):

```text
temporary_failure / bank_unavailable / unknown
        → {RETRY_LATER, ALTERNATIVE_PAYMENT, CUSTOMER_MESSAGE}
processor_timeout → {RETRY_LATER, ALTERNATIVE_PAYMENT}
insufficient_funds / card_expired / invalid_payment_method / repeated_failure
        → {ALTERNATIVE_PAYMENT, CUSTOMER_MESSAGE}
(unlisted → {RETRY_LATER, ALTERNATIVE_PAYMENT})
```

3. **Best action:** `_select_best` (`pipeline.py:201`) picks the highest-scoring
   **permitted** candidate from the 5 scored keys.
4. **Guard 3 — confidence:** if nothing cleared `MIN_CONFIDENCE = 0.40`
   (live), or no permitted action exists → `STOP` with `decision_source="policy"`
   (`pipeline.py:114`).
5. **Delay:** for `RETRY_LATER` the delay comes from the winning key
   (1h/6h/24h) or the root-cause map (`_RETRY_DELAY_HOURS`: temporary 1h, bank 6h,
   processor 1h, insufficient funds 24h, default 6h) (`pipeline.py:129`).
6. **Audit trail:** `reasoning` stores root cause + all scores with `[blocked]`
   markers (`pipeline.py:136`); `explanation` is generated by
   `explainer.explain` — **post-decision only** (`pipeline.py:142`).
7. A `RecoveryAction` row is created with `status="scheduled"`,
   `scheduled_for = simulated_now + delay`, `outcome="pending"`, and
   **`intervention_ref=None`** — a decision is not evidence of an intervention
   (`pipeline.py:247`). `Order.status → "recovery_in_progress"`, `version_id++`.

### 2.8 Scheduler + simulation clock (`app/scheduler.py`, `app/clock.py`)

```text
simulated_now = real_utc_now + _offset          (clock.py:9, in-memory per process)
```

- `scheduler.start()` (`main.py:27`) launches an APScheduler background job
  every 30s (`scheduler.py:101`).
- `tick(db)` (`scheduler.py:22`):
  1. selects actions with `status="scheduled" AND scheduled_for <= simulated_now`
     (naive UTC — DB stores naive UTC, clock strips tzinfo, `scheduler.py:33`),
  2. **optimistic lock**: re-fetch + re-check `status == "scheduled"`, then set
     `"executing"` and flush **before** any external work (`scheduler.py:47-54`),
  3. `executor.execute(action)` then commits `executed`/`failed`/`stale`
     (+ per-type outcome), setting `executed_at = real UTC` for audit
     (`scheduler.py:57-73`).
- Admin controls (`app/api/admin.py`):
  `POST /admin/clock/advance?hours=&minutes=` · `POST /admin/clock/reset` ·
  `GET /admin/clock` · `POST /admin/scheduler/run` (manual tick).
  There is **no env var** for the clock offset; it is process-local and resets on restart.

### 2.9 Executor → Razorpay intervention (`app/recovery/executor.py`)

`execute(action)` (`executor.py:26`) first **resets `intervention_ref=None`** so
no action inherits evidence from a prior one, then dispatches by type:

| Action | What the executor does | `intervention_ref` set to | Returns |
|---|---|---|---|
| `RETRY_LATER` | `rzp.order.create` (same amount, notes `recovery_action_id`/`original_order_id`/`retry_attempt=true`); records a new `PaymentAttempt` with `status="created"`, `razorpay_order_id=<new rzp order>`. **No autonomous charge** — it prepares a retry opportunity. | new `razorpay_order_id` | new order id, attempt number |
| `ALTERNATIVE_PAYMENT` | `rzp.payment_link.create` (`reference_id=recovery_action_{id}`, same notes; notify/reminder off) — customer pays via any method. | payment-link id | link id + short_url |
| `CUSTOMER_MESSAGE` | Demo: records message text (from `explanation`); production would email/SMS (`executor.py:167`). | `message_{action.id}` | message_ref |
| `STOP` | Re-fetches order; if already `paid` or a captured attempt exists → `stale` (never overwrite a real payment); else `order.status = "unrecoverable_halt"`. `intervention_ref` stays `None`. | *(stays None)* | executed/stale |

The frontend re-opens checkout for a retry via `GET /orders/{id}/retry-session`
(`orders.py:64`) which returns the latest executed `RETRY_LATER` action's
`intervention_ref` (or the original rzp order).

### 2.10 Success webhook → attribution → RecoveryOutcome

**`payment.captured`** (`_handle_captured`, `webhooks.py:422`):
resolve order → upsert captured `PaymentAttempt` (deduped by
`razorpay_payment_id`, `webhooks.py:109`) → `order.status="paid"`, `version_id++`.
Only if the order was in recovery / had a prior failure does attribution run.

**`_attribute_recovery`** (`webhooks.py:240`) — evidence-based, never
presumed:

```text
payment has recovery_action_id notes AND passes _is_verified_alternative_payment_capture
        ⇒ source = AI_ACTION                                   (verified link capture)
else latest executed action with intervention_ref:
  RETRY_LATER        payment.order_id == intervention_ref  ⇒ ai_retry      else native_checkout
  ALTERNATIVE_PAYMENT (handled on payment_link.paid path)  ⇒ native_checkout (no dual-count)
  CUSTOMER_MESSAGE   message_ ref AND captured within 72h  ⇒ ai_message     else native_checkout
  anything else ⇒ native_checkout
```

**`payment_link.paid`** (`webhooks.py:392`): finds the `ALTERNATIVE_PAYMENT`
action whose `intervention_ref == link_id`, then `_is_verified_payment_link_action`
(amount+currency match, `reference_id == recovery_action_{id}`, notes
`recovery_action_id`/`original_order_id`, statuses) before attributing `AI_ACTION`
— preventing needle-attribution of unrelated payments.

**`_record_recovery_outcome`** (`webhooks.py:279`): one `RecoveryOutcome` per
order (`recovered=True` upserted, single row), sets `action.outcome="recovered"`,
`amount_recovered=order.amount`, source, `razorpay_payment_id`, and an LLM
outcome narrative from `explain_outcome` (deterministic fallback) (`webhooks.py:279-324`).

```text
recovery_source ∈ { AI_ACTION, ai_retry, ai_message, native_checkout }
```

A payment that succeeds on its own after a failed attempt — with no verified
intervention — is recorded as `native_checkout`, **not** AI (MEMORY.md "Safety").

### 2.11 Dashboard / analytics (`app/api/dashboard.py`)

- `GET /dashboard/overview` — live order counts by status, executed actions,
  recovered outcomes + revenue, plus **sealed** `evaluation_results.json`.
- `GET /dashboard/orders` — list with attempt/failure counts, recovered flag, source.
- `GET /dashboard/orders/:id` — attempts, actions (ML scores/blocked parsed from
  `reasoning` via regex), outcomes, unified chronological timeline.
- `GET /dashboard/analytics` — executed action distribution, recovery rate by
  action type (`outcome=="recovered"`), recovery-by-source breakdown, evaluation.
- `GET /diagnostics/model` — the exact artifact identity in-process: `model`
  (`gb`|`lr`), `model_path`, `sha256`, `size_bytes`, `mtime_utc` (`main.py:53`).

---

## 3. ML Models — LR vs GB Roles

### Artifacts & selector

| File | Model | Role |
|---|---|---|
| `data/model.pkl` | `HistGradientBoostingClassifier` (GB) | **Default live model** (ROC-AUC 0.811) |
| `data/model_lr.pkl` | `LogisticRegression` (LR) | Baseline / comparison-demo model (ROC-AUC 0.743) |

Live artifact selection (`predictor.py:65`):

```text
DEMO_MODEL env  →  "lr"  ⇒ model_lr.pkl
                  (anything else / unset) ⇒ model.pkl (GB — default, unchanged)
```

Startup logic (`main.py:17-30`):

```text
RECOVERY_DEMO_LOAD_ONLY=1         → load existing artifact (no training)
else DEMO_MODEL=lr                → load LR artifact (no training; GB file untouched)
else (default)                    → train_and_load()  → trains GB, saves model.pkl
app.state.model_diagnostics       → {mode, model, sha256, path, ...}
```

The selector is **fully reversible**: flip `DEMO_MODEL` and restart.
`train_and_load` stays GB-bound (`predictor.py:210`) — LR never overwrites the
GB artifact. Setting `DEMO_MODEL=lr` skips retraining so `model.pkl` bytes are
preserved.

### Scoring pipeline (`predictor.py:29-63, 169-188`)

```text
context (13 features) + candidate action_type + delay_hours
  → 10 numeric (StandardScaler / passthrough) + 4 categoricals
  → predict_proba(X)[:, 1]  (P(recovery)) for all 5 candidate rows
```

- **GB** (`_build_gb_pipeline`): ordinal-encoded categoricals passed as
  `categorical_features`; natively learns action×context interactions
  (e.g. `bank_unavailable × RETRY_LATER_6h`).
- **LR** (`_build_lr_pipeline`): one-hot + logistic regression; flat coefficients
  cannot capture those interactions and systematically over-score
  `ALTERNATIVE_PAYMENT` (that is the "comparison story").
- Known flip on the identical medium-ticket bank-unavailable vector:
  **LR → ALTERNATIVE_PAYMENT_0h 0.573**, **GB → RETRY_LATER_6h 0.423**.

### Model lifecycle

`train()` (`predictor.py:126`) fits on `train.csv`, validates on
`validation.csv`, logs ROC-AUC/log-loss, pickles to disk. The module singleton
`load()/get()` (`predictor.py:196-203`) is what the pipeline calls; the evaluator
loads a **fresh** instance via `predictor_module.load(str(MODEL_PATH))`, so the
sealed evaluation always uses the GB artifact explicitly.

---

## 4. Action Decision Matrix

```text
                       RETRY_LATER (1/6/24h)   ALTERNATIVE_PAYMENT   CUSTOMER_MESSAGE
temporary_failure      ✔  (1h)                 ✔                     ✔
bank_unavailable       ✔  (6h)                 ✔                     ✔
processor_timeout      ✔  (1h)                 ✔                     ✘
insufficient_funds     ✘                      ✔                     ✔
card_expired           ✘                      ✔                     ✔
invalid_payment_method ✘                      ✔                     ✔
repeated_failure       ✘                      ✔                     ✔
unknown                ✔ (6h)                 ✔                     ✔

Selection: highest-scoring PERMITTED action; if < 0.40 → STOP (policy).
STOP: also forced after MAX_RECOVERY_ATTEMPTS (3) actions per order.
```

Evaluation harness uses the same permission table but `MIN_CONFIDENCE=0.15`
(`simulator/evaluator.py:39`), which was tuned on validation only, never held-out.
0.40 is the conservative live deployment safety floor; 0.15 is the
validation-tuned offline evaluator threshold used to estimate the revenue-optimal
policy on sealed held-out data.

---

## 5. Database Schema & Relationships

```text
customers ──1────N── orders ──1────N── payment_attempts
   id  PK                id  PK              id PK (ser)
                         razorpay_order_id UNIQ   order_id FK
                         customer_id FK             razorpay_order_id (retry session)
                         amount (paise)             razorpay_payment_id UNIQ
                         status                     attempt_number
                         version_id (optimistic)    payment_method
                                                   status created|captured|failed
                                                   error_code/description/source
           │                  │
           │ 1                │ 1
           │                  │
           ▼                  ▼
   recovery_actions     recovery_outcomes
   id PK                 id PK (ser)
   order_id FK           order_id FK
   action_type           recovery_action_id FK (nullable)
   status                recovered BOOL
   reasoning/explanation recovered_amount (paise)
   decision_source       recovery_source
   confidence            razorpay_payment_id
   scheduled_for (sim.now) explanation (LLM)
   executed_at (real UTC)
   execution_cost / incentive_cost
   intervention_ref (set by executor only)

webhook_events  (standalone)
   razorpay_event_id PK  · event_type · raw_payload · processed · processed_at
```

Data (`app/models/models.py`):
- `Order.status`: `created | attempted | paid | failed | abandoned |
  recovery_in_progress | recovered | unrecoverable_halt`
- `RecoveryAction.status`: `scheduled | executing | executed | failed`;
  `outcome`: `pending | recovered | halted | stale | failed | executed`
- One internal `Order` never splits into unrelated orders for ordinary retries.

---

## 6. Baseline + Held-Out Evaluation Flow

### 6.1 Synthetic data generation (`simulator/`)

```text
customer_generator  → 4 segments by observable features
                      reliable (0.40) / occasional (0.30) / struggling (0.20) / new (0.10)

behavior_model      → HIDDEN ground truth  (recovery_probability per
                      root_cause × action × delay, then segment / flow / amount /
                      time-of-day / attempt modifiers) — NEVER imported by
                      pipeline, predictor, or any training code (ISOLATION RULE).
                      The hidden behavioral segment influences ONLY the
                      ground-truth outcomes it generates.

dataset_builder     → for each customer × 5 failures × 5 candidate actions,
                      sample_outcome(recovered ∈ {0,1}) from the hidden prob
                      → 87,500 rows total:
                          train.csv      50,000 rows
                          validation.csv 12,500 rows
                          held_out.csv   25,000 rows — held-out is SEALED

The predictor and training pipeline receive ONLY observable customer/payment
features (historical_success_rate, historical_failure_count, total_orders,
customer_age_days, previous_recovery_success, root_cause, payment_method,
attempt_number, amount_bucket, hour_of_day, day_of_week, action combination).
The hidden segment is never written to any CSV and never reaches the model.
```

### 6.2 Sealed evaluator (`simulator/evaluator.py`)

```text
held_out.csv
   │  group rows by CONTEXT_COLUMNS → one transaction + outcome per action
   ├── STATIC BASELINE   RETRY_LATER_1h → RETRY_LATER_24h → stop
   └── AI STRATEGY       model.score → pick best permitted ≥0.15
                         (exclude already-tried action), up to MAX_INTERVENTIONS=2

costs: RETRY_LATER 1.00 · ALTERNATIVE_PAYMENT 3.00 · CUSTOMER_MESSAGE 0.50 · STOP 0
net = gross_recovered − action_cost     → uplift (recovery-rate pts, incremental net INR)
```

Both strategies receive **equal** attempts (fairness fix — LR under an unfair
1-vs-2 evaluator scored −1.82 pts). Isolated from `behavior_model.py`; reads only
sealed rows + binary outcomes. Outputs → `data/evaluation_results.json` (SEALED).

### 6.3 Finalized numbers (MEMORY.md / `evaluation_results.json`)

| Model | Evaluator | AI recovery | Baseline | Uplift | ROC-AUC |
|---|---|---|---|---|---|
| Logistic Regression | Unfair (1 vs 2) | 53.96% | 55.78% | −1.82 pts | 0.743 |
| Logistic Regression | Fair (2 vs 2) | 64.02% | 55.78% | +8.24 pts | 0.743 |
| **Gradient Boosting** | **Fair (2 vs 2)** | **80.44%** | **55.78%** | **+24.66 pts** | **0.811** |

GB net revenue (sealed held-out): AI **₹15,23,810.50** vs static baseline **₹10,50,399**
→ incremental net revenue **₹4,73,411.50** (+45.07% relative net uplift) ·
AI action mix: ALT 3,595 · RETRY 2,055 · MSG 1,149 · unnecessary retries 621 vs 5,387.

---

## 7. Safety · Idempotency · Stopping Rules

| Rule | Where enforced | Implementation |
|---|---|---|
| Max retries | `pipeline.py:68` | ≥ `MAX_RECOVERY_ATTEMPTS (3)` actions → `STOP`(policy) |
| Confidence floor | `pipeline.py:114` | no permitted action ≥ 0.40 → `STOP` |
| No repeated/duplicate action execution | `scheduler.py:47-54` | optimistic lock `scheduled→executing` inside the tick txn |
| STOP never clobbers a real payment | `executor.py:149-160` | `_execute_stop` re-fetches; `paid` or captured attempt → `stale` |
| STOP keeps no intervention ref | `executor.py:33,142` | refs reset to `None` before every execution |
| Webhook authenticity | `webhooks.py:18,335` | HMAC-SHA256 vs `RAZORPAY_KEY_SECRET`; secrets server-side only |
| Webhook idempotency | `webhooks.py:343` | PK `razorpay_event_id` dedup; captured attempts upserted by payment id |
| Attribution is evidence-based | `webhooks.py:240-276` | verified notes/refs only; otherwise `native_checkout`; message-attribution within 72h |
| Single outcome per order | `webhooks.py:287` | `recovered=True` upserted to one row |
| No LLM financial authority | `explainer.py` | LLM explains **after** deterministic selection; template fallback; failure non-blocking |
| Traceability / audit | `pipeline.py:136`, `executor` refs, `models` | reasoning scores, `intervention_ref`, `decision_source`, `executed_at`(real), `version_id` |
| Races on order state | `models.py:27` + executors | `version_id` optimistic locking on every status transition |
| Simulation-clock discipline | `pipeline.py`, `scheduler.py` | all "now" decisions via `get_simulated_now()`; `executed_at` uses real UTC for audit |
| Live vs evaluation separation | Section 6 isolation | evaluator never imports hidden model; held-out sealed; final metrics fixed |

### Runtime failure handling

- **Groq down / no key** → deterministic template `explain()` returns; pipeline unaffected.
- **Razorpay API failure in executor** → action `status="failed"`, `outcome="failed"`
  (`scheduler.py:60-62`); no phantom intervention ref is left behind.
- **Scheduler exception** → txn rollback, action marked failed (`scheduler.py:76-84`).
- **Model absent** → rule-based fallback scores, `decision_source="rules"`
  (`pipeline.py:105`).
- **No model loaded in `/admin/model/status`** → `{"loaded": false}`
  (`admin.py:44`); validation metrics only exist for the GB trained path.

---

## 8. Tech Stack

Python 3.11 · FastAPI · SQLAlchemy 2 · PostgreSQL (docker) · APScheduler ·
scikit-learn (HistGradientBoosting / LogisticRegression, pandas) · Groq (qwen,
explanations only) · razorpay SDK · React + Vite + react-router-dom ·
Docker Compose (Postgres only). No Kafka/K8s/microservices (RULES.md #24, #25).