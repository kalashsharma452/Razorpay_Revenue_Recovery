export const API = 'http://localhost:8000'

export const fmt = {
  inr: (v) => `₹${Number(v).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`,
  pct: (v) => `${Number(v).toFixed(2)}%`,
  pctPts: (v) => `${v > 0 ? '+' : ''}${Number(v).toFixed(2)} pts`,
  dt: (iso) => iso ? new Date(iso).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' }) : '—',
  num: (v) => Number(v).toLocaleString('en-IN'),
}

export const STATUS_COLOR = {
  paid: '#16a34a',
  failed: '#dc2626',
  recovery_in_progress: '#d97706',
  recovered: '#16a34a',
  unrecoverable_halt: '#6b7280',
  created: '#2563eb',
  attempted: '#7c3aed',
  abandoned: '#6b7280',
}

export const ACTION_COLOR = {
  RETRY_LATER: '#2563eb',
  ALTERNATIVE_PAYMENT: '#7c3aed',
  CUSTOMER_MESSAGE: '#0891b2',
  STOP: '#6b7280',
}

export async function apiFetch(path) {
  const res = await fetch(`${API}${path}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}
