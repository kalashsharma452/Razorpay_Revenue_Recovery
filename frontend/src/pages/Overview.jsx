import { useEffect, useState } from 'react'
import { Card, Stat, Loading, Err } from '../components/UI'
import { apiFetch, fmt } from '../utils'
import { useModelInfo, modelName, modelTag } from '../modelInfo'

export default function Overview() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const modelInfo = useModelInfo()

  useEffect(() => {
    apiFetch('/dashboard/overview').then(setData).catch(e => setErr(e.message))
  }, [])

  if (err) return <Err msg={err} />
  if (!data) return <Loading />

  const live = data.live
  const ev = data.evaluation
  const ai = ev?.ai_strategy
  const base = ev?.static_baseline
  const uplift = ev?.uplift

  return (
    <div style={page}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h2 style={{ ...h2, marginBottom: 0 }}>Overview</h2>
        {modelInfo && (
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '4px 11px', borderRadius: 99,
            fontSize: 11, fontWeight: 600, letterSpacing: '0.01em',
            color: modelInfo.model === 'lr' ? '#92400e' : '#374151',
            background: modelInfo.model === 'lr' ? '#fef3c7' : '#f3f4f6',
            border: '1px solid #d1d5db55',
          }}>
            Model: {modelName(modelInfo)} — {modelTag(modelInfo)}
          </span>
        )}
      </div>

      {/* ── Sealed evaluation banner ── */}
      {ai && (
        <Card style={{ background: 'linear-gradient(135deg,#1d4ed8,#4f46e5)', border: 'none', marginBottom: 28 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
            <span style={sealBadge}>SEALED</span>
            <p style={{ color: '#bfdbfe', fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Held-Out Evaluation — 5,000 Transactions · Not used in training or tuning
            </p>
          </div>
          <div style={grid4}>
            <Stat label="AI Recovery Rate — Held-Out" value={fmt.pct(ai.recovery_rate * 100)} color="#fff" labelColor="#93c5fd" />
            <Stat label="Static Baseline Recovery Rate" value={fmt.pct(base.recovery_rate * 100)} color="#bfdbfe" labelColor="#93c5fd" />
            <Stat label="Recovery Rate Uplift" value={fmt.pctPts(uplift.recovery_rate_lift_points)} color="#86efac" labelColor="#93c5fd" />
            <Stat label="AI Net Recovered — Held-Out" value={fmt.inr(ai.net_recovered_inr)} color="#fff" labelColor="#93c5fd" />
          </div>
          <div style={{ ...grid4, marginTop: 20, paddingTop: 16, borderTop: '1px solid #3b82f633' }}>
            <Stat label="Incremental Net Revenue" value={fmt.inr(uplift.incremental_net_recovered_inr)} color="#86efac" labelColor="#93c5fd" />
            <Stat label="Net Revenue Uplift" value={`+${uplift.net_uplift_pct}%`} color="#86efac" labelColor="#93c5fd" />
            <Stat label="Unnecessary Retries — AI" value={fmt.num(ai.unnecessary_retries)} sub={`vs ${fmt.num(base.unnecessary_retries)} baseline`} color="#fde68a" labelColor="#93c5fd" />
            <Stat label="GB Model ROC-AUC" value="0.811" sub="vs Logistic Regression 0.743" color="#bfdbfe" labelColor="#93c5fd" />
          </div>
        </Card>
      )}

      {/* ── Live system ── */}
      <p style={sectionLabel}>Live System</p>
      <div style={grid4}>
        <Card><Stat label="Total Orders" value={fmt.num(live.total_orders)} /></Card>
        <Card><Stat label="Paid" value={fmt.num(live.paid)} color="#16a34a" /></Card>
        <Card><Stat label="Failed" value={fmt.num(live.failed)} color="#dc2626" /></Card>
        <Card><Stat label="In Recovery" value={fmt.num(live.in_recovery)} color="#d97706" /></Card>
      </div>
      <div style={{ ...grid4, marginTop: 12 }}>
        <Card><Stat label="Live Recovery Actions" value={fmt.num(live.executed_recovery_actions)} sub={`of ${fmt.num(live.total_recovery_actions)} scheduled`} /></Card>
        <Card><Stat label="Live Recovered Outcomes" value={fmt.num(live.recovered_outcomes)} /></Card>
        <Card><Stat label="Revenue Recovered (Live)" value={fmt.inr(live.recovered_revenue_inr)} /></Card>
        <Card><Stat label="Halted" value={fmt.num(live.halted)} color="#6b7280" /></Card>
      </div>

      {/* ── Evaluation action distribution ── */}
      {ai && (
        <>
          <p style={{ ...sectionLabel, marginTop: 28 }}>AI Action Distribution — Held-Out Evaluation</p>
          <Card>
            <div style={{ display: 'flex', gap: 40, flexWrap: 'wrap' }}>
              {Object.entries(ai.action_counts).map(([action, count]) => (
                <div key={action} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <span style={{ fontSize: 11, color: '#6b7280', fontWeight: 600, textTransform: 'uppercase' }}>
                    {action.replace(/_/g, ' ')}
                  </span>
                  <span style={{ fontSize: 22, fontWeight: 700 }}>{fmt.num(count)}</span>
                  <span style={{ fontSize: 12, color: '#6b7280' }}>
                    {fmt.pct(count / ai.recovery_actions * 100)} of actions
                  </span>
                </div>
              ))}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ fontSize: 11, color: '#6b7280', fontWeight: 600, textTransform: 'uppercase' }}>Stopped</span>
                <span style={{ fontSize: 22, fontWeight: 700 }}>{fmt.num(ai.stopped)}</span>
                <span style={{ fontSize: 12, color: '#6b7280' }}>policy halted</span>
              </div>
            </div>
          </Card>
        </>
      )}
    </div>
  )
}

const page = { padding: '28px 32px', maxWidth: 1100, margin: '0 auto' }
const h2 = { fontSize: 20, fontWeight: 700, marginBottom: 20, color: '#111827' }
const grid4 = { display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12 }
const sectionLabel = { fontSize: 11, fontWeight: 600, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 }
const sealBadge = {
  display: 'inline-block', padding: '2px 8px', borderRadius: 4,
  fontSize: 10, fontWeight: 800, letterSpacing: '0.08em',
  background: '#fde68a', color: '#92400e',
}
