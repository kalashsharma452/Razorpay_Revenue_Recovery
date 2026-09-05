import { useState } from 'react'
import { API } from '../utils'

export default function Checkout() {
  const [amount, setAmount] = useState(10000)
  const [customerId, setCustomerId] = useState('cust_demo_001')
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(false)

  async function handlePay() {
    setLoading(true)
    setStatus(null)
    try {
      const res = await fetch(`${API}/orders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ customer_id: customerId, amount, currency: 'INR' }),
      })
      if (!res.ok) throw new Error(await res.text())
      const order = await res.json()

      const options = {
        key: order.key_id,
        amount: order.amount,
        currency: order.currency,
        order_id: order.razorpay_order_id,
        name: 'Revenue Recovery Demo',
        description: 'Test payment',
        handler(response) {
          setStatus({ type: 'success', message: `Payment captured: ${response.razorpay_payment_id}` })
        },
        modal: {
          ondismiss() {
            setStatus({ type: 'info', message: 'Checkout closed without payment.' })
          },
        },
        prefill: { name: 'Test User', email: 'test@demo.com' },
        theme: { color: '#2563eb' },
      }

      const rzp = new window.Razorpay(options)
      rzp.on('payment.failed', (resp) => {
        setStatus({ type: 'error', message: `Payment failed: ${resp.error.description} (${resp.error.code})` })
      })
      rzp.open()
    } catch (err) {
      setStatus({ type: 'error', message: err.message })
    } finally {
      setLoading(false)
    }
  }

  const colors = { success: '#16a34a', error: '#dc2626', info: '#2563eb' }

  return (
    <div style={{ maxWidth: 420, margin: '80px auto', padding: '0 16px' }}>
      <h2 style={{ marginBottom: 8, fontSize: 20, fontWeight: 700 }}>Test Checkout</h2>
      <p style={{ marginBottom: 24, color: '#6b7280', fontSize: 14 }}>
        Trigger a test payment. Use Razorpay test cards to simulate failures and watch the recovery engine respond.
      </p>

      <label style={labelStyle}>
        Customer ID
        <input value={customerId} onChange={e => setCustomerId(e.target.value)} style={inputStyle} />
      </label>

      <label style={labelStyle}>
        Amount (paise) — ₹{(amount / 100).toFixed(2)}
        <input type="number" value={amount} min={100} step={100}
          onChange={e => setAmount(Number(e.target.value))} style={inputStyle} />
      </label>

      <button onClick={handlePay} disabled={loading} style={btnStyle}>
        {loading ? 'Creating order…' : 'Pay with Razorpay'}
      </button>

      {status && (
        <p style={{ marginTop: 20, color: colors[status.type], fontSize: 14 }}>{status.message}</p>
      )}

      <div style={{ marginTop: 32, padding: 16, background: '#f9fafb', borderRadius: 8, border: '1px solid #e5e7eb' }}>
        <p style={{ fontSize: 12, fontWeight: 600, color: '#6b7280', marginBottom: 8 }}>TEST CARDS</p>
        <p style={{ fontSize: 12, color: '#374151', marginBottom: 4 }}>Success: <code>4111 1111 1111 1111</code></p>
        <p style={{ fontSize: 12, color: '#374151', marginBottom: 4 }}>Failure (insufficient funds): <code>4000 0000 0000 0002</code></p>
        <p style={{ fontSize: 12, color: '#374151' }}>Any future expiry, CVV 123</p>
      </div>
    </div>
  )
}

const labelStyle = { display: 'block', marginBottom: 16, fontSize: 14, fontWeight: 500, color: '#374151' }
const inputStyle = {
  display: 'block', width: '100%', marginTop: 4,
  padding: '8px 10px', fontSize: 14, boxSizing: 'border-box',
  border: '1px solid #d1d5db', borderRadius: 6,
}
const btnStyle = {
  width: '100%', padding: '10px 0', fontSize: 15,
  background: '#2563eb', color: '#fff', border: 'none',
  borderRadius: 6, cursor: 'pointer', fontWeight: 600,
}
