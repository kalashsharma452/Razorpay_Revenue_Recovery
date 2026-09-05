import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Badge, Loading, Err } from '../components/UI'
import { API, apiFetch, fmt, STATUS_COLOR, ACTION_COLOR } from '../utils'
import { actionModelLabel } from '../modelInfo'

const SOURCE_LABEL = {
  AI_ACTION:        { label: 'AI ACTION',        color: '#7c3aed', bg: '#ede9fe' },
  ai_retry:         { label: 'AI RETRY',          color: '#2563eb', bg: '#dbeafe' },
  ai_message:       { label: 'AI MESSAGE',        color: '#0891b2', bg: '#cffafe' },
  native_checkout:  { label: 'NATIVE CHECKOUT',   color: '#6b7280', bg: '#f3f4f6' },
}

export default function OrderDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [order, setOrder] = useState(null)
  const [err, setErr] = useState(null)
  const [retrying, setRetrying] = useState(false)
  const [retryMsg, setRetryMsg] = useState(null)

  useEffect(() => {
    apiFetch(`/dashboard/orders/${id}`).then(setOrder).catch(e => setErr(e.message))
  }, [id])

  async function handleRetryPayment() {
    setRetrying(true)
    setRetryMsg(null)
    try {
      const res = await fetch(`${API}/orders/${id}/retry-session`)
      if (!res.ok) throw new Error(await res.text())
      const session = await res.json()
      const rzp = new window.Razorpay({
        key: session.key_id,
        amount: session.amount,
        currency: session.currency,
        order_id: session.razorpay_order_id,
        name: 'Revenue Recovery Demo',
        description: 'Retry payment',
        handler(response) {
          setRetryMsg({ type: 'success', text: `Payment captured: ${response.razorpay_payment_id}` })
          setTimeout(refreshOrder, 2000)
        },
        modal: {
          ondismiss() {
            setRetryMsg({ type: 'info', text: 'Checkout closed without payment.' })
          },
        },
        prefill: { name: 'Test User', email: 'test@demo.com' },
        theme: { color: '#2563eb' },
      })
      rzp.on('payment.failed', (resp) => {
        setRetryMsg({ type: 'error', text: `Payment failed: ${resp.error.description} (${resp.error.code})` })
        setTimeout(refreshOrder, 2000)
      })
      rzp.open()
    } catch (err) {
      setRetryMsg({ type: 'error', text: err.message })
    } finally {
      setRetrying(false)
    }
  }

  function refreshOrder() {
    apiFetch(`/dashboard/orders/${id}`).then(setOrder).catch(() => {})
  }

  if (err) return <div style={page}><Err msg={err} /></div>
  if (!order) return <Loading />

  const retryAction = [...order.recovery_actions]
    .reverse()
    .find(a => a.action_type === 'RETRY_LATER' && a.status === 'executed' && a.intervention_ref)
  const canRetry = !!retryAction && ['recovery_in_progress', 'failed'].includes(order.status)

  const outcome = order.recovery_outcomes[order.recovery_outcomes.length - 1]
  const isAiRecovered = outcome?.recovered && outcome?.recovery_source && outcome.recovery_source !== 'native_checkout'

  // Use the pre-sorted unified timeline from the backend.
  // Falls back to manual merge if backend doesn't provide it (backwards compat).
  const timeline = order.timeline || buildFallbackTimeline(order)

  return (
    <div style={page}>
      <button onClick={() => navigate('/dashboard/orders')} style={backBtn}>← Orders</button>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <h2 style={{ fontSize: 20, fontWeight: 700, color: '#111827', marginBottom: 4 }}>
            Order #{order.id} — {fmt.inr(order.amount_inr)}
          </h2>
          <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#6b7280' }}>{order.razorpay_order_id}</span>
          <span style={{ marginLeft: 12, fontSize: 12, color: '#6b7280' }}>Customer: {order.customer_id}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Badge label={order.status.replace(/_/g, ' ')} color={STATUS_COLOR[order.status]} />
          {canRetry && (
            <button onClick={handleRetryPayment} disabled={retrying} style={retryBtn}>
              {retrying ? 'Opening checkout…' : 'Retry Payment'}
            </button>
          )}
        </div>
      </div>

      {retryMsg && (
        <p style={{ marginBottom: 20, fontSize: 14, color: msgColors[retryMsg.type] }}>{retryMsg.text}</p>
      )}

      {/* Attribution banner */}
      {outcome?.recovered && (
        <div style={{
          marginBottom: 24, padding: '14px 18px', borderRadius: 8,
          background: isAiRecovered ? '#ede9fe' : '#f3f4f6',
          border: `1px solid ${isAiRecovered ? '#7c3aed44' : '#d1d5db'}`,
          display: 'flex', alignItems: 'center', gap: 16,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, fontWeight: 700, color: '#374151' }}>
            <span style={flowPill('#dc2626')}>FAILED</span>
            <span style={arrow}>→</span>
            {isAiRecovered
              ? <span style={flowPill('#7c3aed')}>{(SOURCE_LABEL[outcome.recovery_source] || SOURCE_LABEL.AI_ACTION).label}</span>
              : <span style={flowPill('#6b7280')}>NATIVE CHECKOUT</span>
            }
            <span style={arrow}>→</span>
            <span style={flowPill('#16a34a')}>PAYMENT CAPTURED</span>
          </div>
          <div style={{ marginLeft: 'auto', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2 }}>
            <span style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Recovery Source</span>
            <span style={{
              fontSize: 12, fontWeight: 700,
              color: (SOURCE_LABEL[outcome.recovery_source] || SOURCE_LABEL.native_checkout).color,
            }}>
              {outcome.recovery_source?.toUpperCase().replace(/_/g, ' ') || '—'}
            </span>
          </div>
        </div>
      )}

      {/* Chronological timeline */}
      <p style={sectionLabel}>Recovery Timeline</p>
      <div style={{ position: 'relative', paddingLeft: 28, marginBottom: 28 }}>
        <div style={{ position: 'absolute', left: 9, top: 8, bottom: 8, width: 2, background: '#e5e7eb' }} />

        {timeline.map((event, i) => {
          if (event.event_type === 'payment_attempt') return <AttemptItem key={`a-${i}`} a={event.data} />
          if (event.event_type === 'recovery_action') return <ActionItem key={`r-${i}`} a={event.data} />
          if (event.event_type === 'recovery_outcome') return <OutcomeItem key={`o-${i}`} o={event.data} />
          return null
        })}
      </div>
    </div>
  )
}

// Build a sorted timeline client-side if the backend doesn't provide one
function buildFallbackTimeline(order) {
  const events = []
  for (const a of order.payment_attempts)
    events.push({ event_type: 'payment_attempt', ts: a.created_at || '', data: a })
  for (const a of order.recovery_actions)
    events.push({ event_type: 'recovery_action', ts: a.created_at || '', data: a })
  for (const o of order.recovery_outcomes)
    events.push({ event_type: 'recovery_outcome', ts: o.created_at || '', data: o })
  return events.sort((a, b) => a.ts.localeCompare(b.ts))
}

function AttemptItem({ a }) {
  const dot = a.status === 'captured' ? '#16a34a' : a.status === 'failed' ? '#dc2626' : '#d97706'
  return (
    <TimelineItem
      dot={dot}
      title={`Payment Attempt #${a.attempt_number} — ${a.status.toUpperCase()}`}
      time={fmt.dt(a.created_at)}
    >
      <Row label="Payment ID" value={a.razorpay_payment_id || '—'} mono />
      {a.razorpay_order_id && <Row label="Razorpay Order" value={a.razorpay_order_id} mono />}
      <Row label="Method" value={a.payment_method || '—'} />
      {a.status === 'failed' && <>
        <Row label="Error Code" value={a.error_code || '—'} />
        <Row label="Description" value={a.error_description || '—'} />
        <Row label="Source" value={a.error_source || '—'} />
      </>}
    </TimelineItem>
  )
}

function ActionItem({ a }) {
  const dot = ACTION_COLOR[a.action_type] || '#6b7280'
  const badge = <Badge label={a.status} color={a.status === 'executed' ? '#16a34a' : a.status === 'failed' ? '#dc2626' : '#d97706'} />

  // For STOP, show a distinct label
  const title = a.action_type === 'STOP'
    ? 'AI Decision — STOP (policy halted recovery)'
    : `AI Decision — ${a.action_type.replace(/_/g, ' ')}`

  return (
    <TimelineItem dot={dot} title={title} time={fmt.dt(a.created_at)} badge={badge}>
      <AiDecisionPanel action={a} />
    </TimelineItem>
  )
}

function OutcomeItem({ o }) {
  return (
    <TimelineItem
      dot={o.recovered ? '#16a34a' : '#dc2626'}
      title={o.recovered ? `Recovered — ${fmt.inr(o.amount_recovered_inr)}` : 'Not Recovered'}
      time={fmt.dt(o.created_at)}
    >
      <Row label="Recovery Source" value={o.recovery_source?.toUpperCase().replace(/_/g, ' ') || '—'} />
      <Row label="Payment ID" value={o.razorpay_payment_id || '—'} mono />
      {o.explanation && (
        <div style={{ marginTop: 10, padding: '10px 12px', background: '#f0fdf4', borderRadius: 6, borderLeft: '3px solid #16a34a' }}>
          <p style={{ fontSize: 11, fontWeight: 600, color: '#16a34a', marginBottom: 4 }}>Outcome Explanation</p>
          <p style={{ fontSize: 13, color: '#14532d', lineHeight: 1.5 }}>{o.explanation}</p>
        </div>
      )}
    </TimelineItem>
  )
}

function AiDecisionPanel({ action: a }) {
  const hasScores = a.ml_scores && Object.keys(a.ml_scores).length > 0
  const selectedKey = a.action_type === 'RETRY_LATER'
    ? Object.keys(a.ml_scores || {}).find(k => k.startsWith('RETRY_LATER') && !a.ml_scores_blocked?.includes(k))
    : `${a.action_type}_0h`

  return (
    <div>
      {a.root_cause && <Row label="Root Cause" value={a.root_cause.replace(/_/g, ' ')} />}
      <Row label="Selected Action" value={a.action_type.replace(/_/g, ' ')} />
      <Row label="Decision Source" value={a.decision_source === 'ml' ? actionModelLabel(a.model) : a.decision_source || '—'} />
      <Row label="Confidence" value={a.confidence != null ? `${(a.confidence * 100).toFixed(1)}%` : '—'} />
      {a.scheduled_for && <Row label="Scheduled For" value={fmt.dt(a.scheduled_for)} />}
      {a.executed_at && <Row label="Executed At" value={fmt.dt(a.executed_at)} />}
      {a.intervention_ref && <Row label="Intervention Ref" value={a.intervention_ref} mono />}
      {a.outcome && a.outcome !== 'pending' && (
        <Row label="Outcome" value={a.outcome.toUpperCase()} />
      )}

      {/* Candidate ML scores */}
      {hasScores && (
        <div style={{ marginTop: 12, padding: '12px 14px', background: '#f8fafc', borderRadius: 6, border: '1px solid #e2e8f0' }}>
          <p style={{ fontSize: 11, fontWeight: 700, color: '#374151', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 }}>
            Candidate ML Scores — Policy selected highest permitted
          </p>
          {Object.entries(a.ml_scores).map(([key, score]) => {
            const isSelected = key === selectedKey || (a.action_type !== 'RETRY_LATER' && key === `${a.action_type}_0h`)
            const isBlocked = a.ml_scores_blocked?.includes(key)
            const barPct = Math.round(score * 100)
            return (
              <div key={key} style={{ marginBottom: 7 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 3 }}>
                  <span style={{
                    fontSize: 12, fontWeight: isSelected ? 700 : 400,
                    color: isBlocked ? '#9ca3af' : isSelected ? '#111827' : '#374151',
                    textDecoration: isBlocked ? 'line-through' : 'none',
                  }}>
                    {key}
                    {isSelected && <span style={{ marginLeft: 6, fontSize: 10, fontWeight: 700, color: '#7c3aed', background: '#ede9fe', padding: '1px 5px', borderRadius: 3 }}>SELECTED</span>}
                    {isBlocked && <span style={{ marginLeft: 6, fontSize: 10, color: '#9ca3af' }}>blocked by policy</span>}
                  </span>
                  <span style={{ fontSize: 12, fontWeight: 600, color: isBlocked ? '#9ca3af' : '#374151' }}>
                    {(score * 100).toFixed(1)}%
                  </span>
                </div>
                <div style={{ height: 4, background: '#e5e7eb', borderRadius: 2 }}>
                  <div style={{
                    height: '100%', width: `${barPct}%`, borderRadius: 2,
                    background: isBlocked ? '#d1d5db' : isSelected ? '#7c3aed' : '#93c5fd',
                  }} />
                </div>
              </div>
            )
          })}
          <p style={{ fontSize: 11, color: '#6b7280', marginTop: 8 }}>
            Policy enforces permitted actions per root cause. LLM explains the already-made decision — it does not select the action.
          </p>
        </div>
      )}

      {/* LLM explanation */}
      {a.explanation && (
        <div style={{ marginTop: 10, padding: '10px 12px', background: '#f0f9ff', borderRadius: 6, borderLeft: '3px solid #0891b2' }}>
          <p style={{ fontSize: 11, fontWeight: 600, color: '#0891b2', marginBottom: 4 }}>
            LLM Explanation <span style={{ fontWeight: 400, color: '#6b7280' }}>(generated after decision)</span>
          </p>
          <p style={{ fontSize: 13, color: '#1e3a5f', lineHeight: 1.5 }}>{a.explanation}</p>
        </div>
      )}

      {/* Technical audit trail — collapsed by default */}
      {a.reasoning && (
        <details style={{ marginTop: 8 }}>
          <summary style={{ fontSize: 11, color: '#6b7280', cursor: 'pointer', userSelect: 'none' }}>
            Technical audit trail
          </summary>
          <pre style={{ fontSize: 11, color: '#374151', background: '#f9fafb', padding: 10, borderRadius: 6, marginTop: 6, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {a.reasoning}
          </pre>
        </details>
      )}
    </div>
  )
}

function TimelineItem({ dot, title, time, badge, children }) {
  return (
    <div style={{ position: 'relative', marginBottom: 20, paddingLeft: 8 }}>
      <div style={{
        position: 'absolute', left: -19, top: 6,
        width: 12, height: 12, borderRadius: '50%',
        background: dot, border: '2px solid #fff', boxShadow: '0 0 0 2px ' + dot + '44',
      }} />
      <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 8, padding: '12px 16px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <span style={{ fontWeight: 600, fontSize: 13, color: '#111827' }}>{title}</span>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {badge}
            <span style={{ fontSize: 11, color: '#9ca3af' }}>{time}</span>
          </div>
        </div>
        {children}
      </div>
    </div>
  )
}

function Row({ label, value, mono }) {
  return (
    <div style={{ display: 'flex', gap: 8, marginBottom: 4 }}>
      <span style={{ fontSize: 11, color: '#6b7280', minWidth: 130, flexShrink: 0 }}>{label}</span>
      <span style={{ fontSize: 12, color: '#374151', fontFamily: mono ? 'monospace' : 'inherit', wordBreak: 'break-all' }}>
        {value}
      </span>
    </div>
  )
}

function flowPill(color) {
  const bg = { '#dc2626': '#fee2e2', '#7c3aed': '#ede9fe', '#16a34a': '#dcfce7', '#6b7280': '#f3f4f6' }
  return {
    padding: '3px 10px', borderRadius: 4, fontSize: 11, fontWeight: 700,
    color, background: bg[color] || '#f3f4f6', border: `1px solid ${color}33`,
  }
}

const arrow = { color: '#9ca3af', fontSize: 14 }
const retryBtn = {
  background: '#2563eb', color: '#fff', border: 'none', borderRadius: 6,
  padding: '8px 14px', fontSize: 13, fontWeight: 600, cursor: 'pointer',
}
const msgColors = { success: '#16a34a', error: '#dc2626', info: '#2563eb' }
const page = { padding: '28px 32px', maxWidth: 900, margin: '0 auto' }
const sectionLabel = { fontSize: 11, fontWeight: 600, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 12 }
const backBtn = { background: 'none', border: 'none', color: '#2563eb', cursor: 'pointer', fontSize: 13, padding: '0 0 16px', fontWeight: 500 }
