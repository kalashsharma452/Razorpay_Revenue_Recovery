import { useEffect, useState } from 'react'
import { apiFetch } from './utils'

// Single source of truth for the loaded model: the backend /diagnostics/model
// response (build from app.state.model_diagnostics). Frontend only formats it.
let inflight = null

export function getModelInfo() {
  if (!inflight) {
    inflight = apiFetch('/diagnostics/model').catch((e) => {
      inflight = null
      throw e
    })
  }
  return inflight
}

export function modelName(info) {
  return info?.model === 'lr' ? 'Logistic Regression' : 'Gradient Boosting'
}

export function modelTag(info) {
  return info?.model === 'lr' ? 'Demo' : 'Primary'
}

// Label for a stored per-action model identity (a.model from /dashboard/orders/:id).
// NULL = legacy/rule-based decision; we show a generic label rather than guessing.
export function actionModelLabel(model) {
  if (model === 'lr') return 'ML Model (Logistic Regression)'
  if (model === 'gb') return 'ML Model (Gradient Boosting)'
  return 'ML Model'
}

export function useModelInfo() {
  const [info, setInfo] = useState(null)
  useEffect(() => {
    let mounted = true
    getModelInfo().then((d) => { if (mounted) setInfo(d) }).catch(() => {})
    return () => { mounted = false }
  }, [])
  return info
}