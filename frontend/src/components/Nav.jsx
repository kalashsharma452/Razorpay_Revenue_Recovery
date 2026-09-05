import { NavLink } from 'react-router-dom'

const links = [
  { to: '/', label: 'Checkout' },
  { to: '/dashboard', label: 'Overview' },
  { to: '/dashboard/orders', label: 'Orders' },
  { to: '/dashboard/analytics', label: 'AI Analytics' },
]

export default function Nav() {
  return (
    <nav style={nav}>
      <span style={brand}>Revenue Recovery</span>
      <div style={{ display: 'flex', gap: 4 }}>
        {links.map(l => (
          <NavLink key={l.to} to={l.to} end={l.to === '/'} style={({ isActive }) => ({
            ...link, background: isActive ? '#1e40af' : 'transparent',
          })}>
            {l.label}
          </NavLink>
        ))}
      </div>
    </nav>
  )
}

const nav = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  padding: '0 24px', height: 52, background: '#1d4ed8',
  position: 'sticky', top: 0, zIndex: 100,
}
const brand = { color: '#fff', fontWeight: 700, fontSize: 15, letterSpacing: '-0.3px' }
const link = {
  color: '#bfdbfe', textDecoration: 'none', padding: '6px 12px',
  borderRadius: 6, fontSize: 14, fontWeight: 500,
}
