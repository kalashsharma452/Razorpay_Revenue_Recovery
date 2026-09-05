export function Card({ children, style }) {
  return <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 10, padding: 20, ...style }}>{children}</div>
}

export function Stat({ label, value, sub, color, labelColor }) {
  const muted = labelColor || '#6b7280'
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 12, color: muted, fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</span>
      <span style={{ fontSize: 26, fontWeight: 700, color: color || '#111827', lineHeight: 1 }}>{value}</span>
      {sub && <span style={{ fontSize: 12, color: muted }}>{sub}</span>}
    </div>
  )
}

export function Badge({ label, color }) {
  const bg = { '#16a34a': '#dcfce7', '#dc2626': '#fee2e2', '#d97706': '#fef3c7', '#2563eb': '#dbeafe', '#7c3aed': '#ede9fe', '#6b7280': '#f3f4f6', '#0891b2': '#cffafe' }
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: 99,
      fontSize: 11, fontWeight: 600, color: color || '#374151',
      background: bg[color] || '#f3f4f6', border: `1px solid ${color || '#d1d5db'}22`,
    }}>{label}</span>
  )
}

export function Loading() {
  return <div style={{ padding: 40, textAlign: 'center', color: '#9ca3af' }}>Loading…</div>
}

export function Err({ msg }) {
  return <div style={{ padding: 20, color: '#dc2626', background: '#fee2e2', borderRadius: 8 }}>{msg}</div>
}

export function Table({ cols, rows, onRow }) {
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: '2px solid #e5e7eb' }}>
            {cols.map(c => <th key={c.key} style={{ padding: '8px 12px', textAlign: 'left', color: '#6b7280', fontWeight: 600, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em', whiteSpace: 'nowrap' }}>{c.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}
              onClick={() => onRow && onRow(row)}
              style={{ borderBottom: '1px solid #f3f4f6', cursor: onRow ? 'pointer' : 'default', transition: 'background 0.1s' }}
              onMouseEnter={e => { if (onRow) e.currentTarget.style.background = '#f9fafb' }}
              onMouseLeave={e => { e.currentTarget.style.background = '' }}
            >
              {cols.map(c => <td key={c.key} style={{ padding: '10px 12px', verticalAlign: 'middle' }}>{c.render ? c.render(row) : row[c.key]}</td>)}
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={cols.length} style={{ padding: 32, textAlign: 'center', color: '#9ca3af' }}>No records</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
