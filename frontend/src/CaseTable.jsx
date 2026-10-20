import { Link } from 'react-router-dom'
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, can, date, money, uid } from './api'
import { Badge, Empty, ErrorBox } from './components'

// Shared read-only row projection for operational pools and customer history.
export default function CaseTable({ items, remember, user }) {
  if (!items.length) return <Empty />

  return (
    <div
      className={`table-wrap${remember ? ' inbox-list-scroll' : ''}`}
      tabIndex={remember ? 0 : undefined}
      role={remember ? 'region' : undefined}
      aria-label={remember ? 'Lista desplazable de solicitudes' : undefined}
    >
      <table className="case-table">
        <thead>
          <tr>
            <th>Solicitud / asunto</th>
            <th>Cliente</th>
            <th>Responsable</th>
            <th>Atención</th>
            <th>Producción / entrega</th>
            <th>Compromiso</th>
            <th>Prioridad</th>
            <th>Saldo</th>
          </tr>
        </thead>
        <tbody>
          {items.map((record) => (
            <tr key={record.id}>
              <td>
                <Link onClick={remember} to={`/cases/${record.id}`}>
                  <small className="case-number">{record.number}</small>
                  <strong>{record.subject}</strong>
                </Link>
                {record.lifecycle === 'Completado' && (
                  <small>
                    <Badge tone="teal">Completado</Badge> {date(record.completed_at)}
                  </small>
                )}
                {record.requires_action && <span className="action-dot" title="Requiere acción" />}
                {!record.owner_id && can(user, 'attention') && <TakeCaseButton record={record} />}
              </td>
              <td>{record.client.name}</td>
              <td>{record.owner_name || <span className="muted">Sin asignar</span>}</td>
              <td>
                <Badge>{record.status}</Badge>
              </td>
              <td>
                {record.productions?.length ? record.productions.map((order) => <div className="order-table-row" key={order.id}>
                  <Link to={`/cases/${record.id}?production_id=${order.id}`} onClick={remember}>{order.order_number}</Link>
                  <Badge tone={order.cancelled_at?'red':order.blocked_since?'amber':order.delivery_status==='Completa'?'teal':order.stage==='READY'?'amber':''}>{order.cancelled_at?'Anulada':order.blocked_since?'Bloqueada':order.delivery_status==='Completa'?'Entregada':order.stage_label}</Badge>
                  <small>Entrega {order.delivery_status.toLowerCase()}</small>
                </div>) : (
                  <span className="muted">Atención</span>
                )}
              </td>
              <td>
                {record.productions?.length ? record.productions.filter(o=>o.committed_date).map(o=><small className="order-date" key={o.id}><Link to={`/cases/${record.id}?production_id=${o.id}`} onClick={remember}>{o.order_number}</Link> · {date(o.committed_date)}</small>) : '—'}
                <small>{record.next_action}</small>
              </td>
              <td>
                <Badge tone={priorityTone(record.priority)}>{record.priority}</Badge>
              </td>
              <td>
                {record.balance === undefined
                  ? '—'
                  : record.balance === null ? 'Saldo por moneda' : money(record.balance, record.production?.currency)}
                {record.production?.cancellation_review_pending && (
                  <small className="text-red">Resolución de anulación pendiente</small>
                )}
                {record.production?.stage === 'READY' && record.pickup_payment_status && (
                  <small>
                    {record.pickup_payment_status} · recibido{' '}
                    {money(record.received_amount, record.production.currency)}
                  </small>
                )}
                {record.balance < 0 && <small>Pendiente de devolución</small>}
                {record.payment_status === 'Vencido' && <small className="text-red">Vencido</small>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TakeCaseButton({ record }) {
  const qc = useQueryClient()
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  async function take() {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await api(`/cases/${record.id}/take`, {
        method: 'POST',
        body: { version: record.version, key: uid() },
      })
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['cases'] }),
        qc.invalidateQueries({ queryKey: ['counts'] }),
        qc.invalidateQueries({ queryKey: ['personal-counts'] }),
      ])
    } catch (cause) {
      setError(cause)
      if (cause.status === 409) {
        await Promise.all([
          qc.invalidateQueries({ queryKey: ['cases'] }),
          qc.invalidateQueries({ queryKey: ['counts'] }),
          qc.invalidateQueries({ queryKey: ['personal-counts'] }),
        ])
      }
    } finally {
      setBusy(false)
    }
  }
  return (
    <span className="case-row-take">
      <button onClick={take} disabled={busy}>
        {busy ? 'Tomando…' : 'Tomar solicitud'}
      </button>
      {error && <ErrorBox error={error} />}
    </span>
  )
}

function priorityTone(priority) {
  if (priority === 'Urgente') return 'red'
  if (priority === 'Alta') return 'amber'
  return ''
}
