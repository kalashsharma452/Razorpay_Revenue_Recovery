import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Nav from './components/Nav'
import Checkout from './pages/Checkout'
import Overview from './pages/Overview'
import Orders from './pages/Orders'
import OrderDetail from './pages/OrderDetail'
import Analytics from './pages/Analytics'

export default function App() {
  return (
    <BrowserRouter>
      <div style={{ minHeight: '100vh', background: '#f9fafb', fontFamily: 'system-ui, -apple-system, sans-serif' }}>
        <Nav />
        <Routes>
          <Route path="/" element={<Checkout />} />
          <Route path="/dashboard" element={<Overview />} />
          <Route path="/dashboard/orders" element={<Orders />} />
          <Route path="/dashboard/orders/:id" element={<OrderDetail />} />
          <Route path="/dashboard/analytics" element={<Analytics />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}
