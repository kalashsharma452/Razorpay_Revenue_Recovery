import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Badge, Table, Loading, Err } from '../components/UI'
import { apiFetch, fmt, STATUS_COLOR } from '../utils'

export default function Orders() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const navigate = useNavigate()

  useEffect(() => {
    apiFetch('/dashboard/orders?limit=100').then(setData).catch(e => setErr(e.message))
  }, [])

  if (err) return <Err msg={err} />
  if (!data) return <Loading />

  const cols = [
    { key: 'id', label: 'ID', render: r => <span style={{ fontFamily: 'monospace', color: '#6b7280' }}>#{r.id}</span> },
    { key: 'razorpay_order_id', label: 'Razorpay Order', render: r => <span style={{ fontFamily: 'monospace', fontSize: 11 }}>{r.razorpay_order_id}</span> },
    { key: 'customer_id', label: 'Customer' },
    { key: 'amount_inr', label: 'Amount', render: r => <strong>{fmt.inr(r.amount_inr)}</strong> },
    {
      key: 'status', label: 'Status',
      render: r => <Badge label={r.status.replace(/_/g, ' ')} color={STATUS_COLOR[r.status]} />
    },
    { key: 'attempt_count', label: 'Attempts', render: r => `${r.attempt_count} (${r.failed_attempts} failed)` },
    {
      key: 'recovered', label: 'Recovery',
      render: r => {
        if (r.recovered) {
          const src = r.recovery_source || ''
          const isAi = src && src !== 'native_checkout'
          return <Badge
            label={isAi ? `AI · ${src.toUpperCase().replace(/_/g,' ')}` : 'NATIVE CHECKOUT'}
            color={isAi ? '#7c3aed' : '#6b7280'}
          />
        }
        if (r.recovery_action_count > 0)
          return <Badge label={`${r.recovery_action_count} action${r.recovery_action_count > 1 ? 's' : ''} · in progress`} color="#d97706" />
        return <span style={{ color: '#9ca3af', fontSize: 12 }}>—</span>
      }
    },
    { key: 'created_at', label: 'Created', render: r => <span style={{ fontSize: 12, color: '#6b7280' }}>{fmt.dt(r.created_at)}</span> },
  ]

  return (
    <div style={page}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h2 style={h2}>Orders <span style={{ fontSize: 14, color: '#6b7280', fontWeight: 400 }}>({data.total} total)</span></h2>
      </div>
      <Card style={{ padding: 0 }}>
        <Table
          cols={cols}
          rows={data.items}
          onRow={r => navigate(`/dashboard/orders/${r.id}`)}
        />
      </Card>
    </div>
  )
}

const page = { padding: '28px 32px', maxWidth: 1200, margin: '0 auto' }
const h2 = { fontSize: 20, fontWeight: 700, color: '#111827' }
