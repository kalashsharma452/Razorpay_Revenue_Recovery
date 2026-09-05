import { useEffect, useState } from 'react'
import { Card, Stat, Loading, Err, Badge } from '../components/UI'
import { apiFetch, fmt, ACTION_COLOR } from '../utils'

export default function Analytics() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    apiFetch('/dashboard/analytics').then(setData).catch(e => setErr(e.message))
  }, [])

  if (err) return <Err msg={err} />
  if (!data) return <Loading />

  const ev = data.evaluation
  const ai = ev?.ai_strategy
  const base = ev?.static_baseline
  const uplift = ev?.uplift
  const live = data.live

  return (
    <div style={page}>
      <h2 style={h2}>AI Analytics</h2>

      {/* AI vs Baseline comparison */}
      {ai && base && (
        <>
          <p style={sectionLabel}>Evaluation — AI vs Static Baseline (5,000 held-out transactions)</p>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 24 }}>
            <Card style={{ borderTop: '3px solid #1d4ed8' }}>
              <p style={{ fontWeight: 700, marginBottom: 16, color: '#1d4ed8' }}>AI Strategy (Gradient Boosting)</p>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <Stat label="Recovery Rate" value={fmt.pct(ai.recovery_rate * 100)} color="#16a34a" />
                <Stat label="Net Recovered" value={fmt.inr(ai.net_recovered_inr)} />
                <Stat label="Gross Recovered" value={fmt.inr(ai.gross_recovered_inr)} />
                <Stat label="Intervention Cost" value={fmt.inr(ai.action_cost_inr)} />
                <Stat label="Unnecessary Retries" value={fmt.num(ai.unnecessary_retries)} />
                <Stat label="Stopped" value={fmt.num(ai.stopped)} />
              </div>
            </Card>
            <Card style={{ borderTop: '3px solid #6b7280' }}>
              <p style={{ fontWeight: 700, marginBottom: 16, color: '#6b7280' }}>Static Baseline (Retry 1h → 24h)</p>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <Stat label="Recovery Rate" value={fmt.pct(base.recovery_rate * 100)} />
                <Stat label="Net Recovered" value={fmt.inr(base.net_recovered_inr)} />
                <Stat label="Gross Recovered" value={fmt.inr(base.gross_recovered_inr)} />
                <Stat label="Intervention Cost" value={fmt.inr(base.action_cost_inr)} />
                <Stat label="Unnecessary Retries" value={fmt.num(base.unnecessary_retries)} />
                <Stat label="Stopped" value={fmt.num(base.stopped)} />
              </div>
            </Card>
          </div>

          {/* Uplift summary */}
          <Card style={{ background: '#f0fdf4', border: '1px solid #bbf7d0', marginBottom: 24 }}>
            <p style={{ fontWeight: 700, marginBottom: 16, color: '#15803d' }}>Uplift</p>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16 }}>
              <Stat label="Recovery Rate Lift" value={fmt.pctPts(uplift.recovery_rate_lift_points)} color="#15803d" />
              <Stat label="Net Revenue Uplift" value={`+${uplift.net_uplift_pct}%`} color="#15803d" />
              <Stat label="Incremental Net Revenue" value={fmt.inr(uplift.incremental_net_recovered_inr)} color="#15803d" />
              <Stat label="Unnecessary Retries Saved" value={fmt.num(base.unnecessary_retries - ai.unnecessary_retries)} color="#15803d" />
            </div>
          </Card>

          {/* Action distribution */}
          <p style={sectionLabel}>AI Action Distribution (Evaluation)</p>
          <Card style={{ marginBottom: 24 }}>
            <div style={{ display: 'flex', gap: 0, flexWrap: 'wrap' }}>
              {Object.entries(ai.action_counts).map(([action, count]) => {
                const pct = count / ai.recovery_actions * 100
                return (
                  <div key={action} style={{ flex: 1, minWidth: 160, padding: '12px 16px', borderRight: '1px solid #f3f4f6' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                      <div style={{ width: 10, height: 10, borderRadius: '50%', background: ACTION_COLOR[action] || '#6b7280' }} />
                      <span style={{ fontSize: 12, fontWeight: 600, color: '#374151' }}>{action.replace(/_/g, ' ')}</span>
                    </div>
                    <div style={{ fontSize: 24, fontWeight: 700, color: '#111827' }}>{fmt.num(count)}</div>
                    <div style={{ fontSize: 12, color: '#6b7280', marginTop: 2 }}>{fmt.pct(pct)} of actions</div>
                    <div style={{ marginTop: 8, height: 4, background: '#f3f4f6', borderRadius: 2 }}>
                      <div style={{ height: '100%', width: `${pct}%`, background: ACTION_COLOR[action] || '#6b7280', borderRadius: 2 }} />
                    </div>
                  </div>
                )
              })}
            </div>
          </Card>

          {/* Model comparison */}
          <p style={sectionLabel}>Model Iteration</p>
          <Card style={{ marginBottom: 24 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #e5e7eb' }}>
                  {['Model', 'Evaluator', 'AI Recovery', 'Baseline', 'Uplift', 'ROC-AUC', 'Note'].map(h => (
                    <th key={h} style={{ padding: '8px 12px', textAlign: 'left', fontSize: 11, color: '#6b7280', fontWeight: 600, textTransform: 'uppercase' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[
                  { model: 'Logistic Regression', evaluator: 'Unfair (1 vs 2 attempts)', ai: '53.96%', base: '55.78%', uplift: '-1.82 pts', auc: '0.743', note: 'Cannot learn action×context interactions' },
                  { model: 'Logistic Regression', evaluator: 'Fair (2 vs 2 attempts)', ai: '64.02%', base: '55.78%', uplift: '+8.24 pts', auc: '0.743', note: 'Evaluator fix alone recovered 8 pts' },
                  { model: 'Gradient Boosting', evaluator: 'Fair (2 vs 2 attempts)', ai: '80.44%', base: '55.78%', uplift: '+24.66 pts', auc: '0.811', note: 'Final result — sealed held-out' },
                ].map((r, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid #f3f4f6', background: i === 2 ? '#f0fdf4' : '' }}>
                    <td style={{ padding: '10px 12px', fontWeight: i === 2 ? 700 : 400 }}>{r.model}</td>
                    <td style={{ padding: '10px 12px', fontSize: 12, color: '#6b7280' }}>{r.evaluator}</td>
                    <td style={{ padding: '10px 12px', color: i === 2 ? '#16a34a' : '#374151', fontWeight: i === 2 ? 700 : 400 }}>{r.ai}</td>
                    <td style={{ padding: '10px 12px' }}>{r.base}</td>
                    <td style={{ padding: '10px 12px', color: r.uplift.startsWith('+') ? '#16a34a' : '#dc2626', fontWeight: 600 }}>{r.uplift}</td>
                    <td style={{ padding: '10px 12px' }}>{r.auc}</td>
                    <td style={{ padding: '10px 12px', fontSize: 12, color: '#6b7280' }}>{r.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </>
      )}

      {/* Live DB analytics */}
      {Object.keys(live.action_distribution).length > 0 && (
        <>
          <p style={sectionLabel}>Live System — Action Distribution</p>
          <Card style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
              {Object.entries(live.action_distribution).map(([action, count]) => (
                <div key={action} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Badge label={action.replace(/_/g, ' ')} color={ACTION_COLOR[action]} />
                  <span style={{ fontWeight: 700 }}>{count}</span>
                </div>
              ))}
            </div>
          </Card>
          <p style={sectionLabel}>Live System — Recovery by Source</p>
          <Card>
            <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
              {Object.entries(live.recovery_by_source).map(([src, count]) => (
                <div key={src} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <span style={{ fontSize: 11, color: '#6b7280', fontWeight: 600 }}>{src}</span>
                  <span style={{ fontSize: 20, fontWeight: 700 }}>{count}</span>
                </div>
              ))}
              {Object.keys(live.recovery_by_source).length === 0 && <span style={{ color: '#9ca3af', fontSize: 13 }}>No recovered outcomes yet</span>}
            </div>
          </Card>
        </>
      )}
    </div>
  )
}

const page = { padding: '28px 32px', maxWidth: 1100, margin: '0 auto' }
const h2 = { fontSize: 20, fontWeight: 700, marginBottom: 20, color: '#111827' }
const sectionLabel = { fontSize: 11, fontWeight: 600, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 }
