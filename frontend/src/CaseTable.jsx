import { Link } from 'react-router-dom'
import { date, money } from './api'
import { Badge, Empty } from './components'
import { PRODUCTION_STAGE_LABELS } from './domain'

// Shared read-only row projection for operational pools and customer history.
export default function CaseTable({ items, remember }) {
  if (!items.length) return <Empty />

  return (
    <div className="table-wrap">
      <table className="case-table">
        <thead>
          <tr>
            <th>Expediente / asunto</th>
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
                {record.requires_action && <span className="action-dot" title="Requiere acción" />}
              </td>
              <td>{record.client.name}</td>
              <td>{record.owner_name || <span className="muted">Sin asignar</span>}</td>
              <td>
                <Badge>{record.status}</Badge>
              </td>
              <td>
                {record.production ? (
                  <>
                    <Badge tone={productionTone(record.production)}>
                      {productionLabel(record.production)}
                    </Badge>
                    <small>Entrega {record.delivery_status.toLowerCase()}</small>
                  </>
                ) : (
                  <span className="muted">Atención</span>
                )}
              </td>
              <td>
                {record.production?.committed_date ? date(record.production.committed_date) : '—'}
                <small>{record.next_action}</small>
              </td>
              <td>
                <Badge tone={priorityTone(record.priority)}>{record.priority}</Badge>
              </td>
              <td>
                {record.balance === undefined
                  ? '—'
                  : money(record.balance, record.production?.currency)}
                {record.payment_status === 'Vencido' && <small className="text-red">Vencido</small>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function productionTone(production) {
  if (production.cancelled_at) return 'red'
  if (production.blocked_since) return 'amber'
  return 'teal'
}

function productionLabel(production) {
  if (production.cancelled_at) return 'Cancelado'
  if (production.blocked_since) return 'Bloqueado'
  if (production.result_label) return production.result_label
  return (
    production.stage_label ||
    PRODUCTION_STAGE_LABELS[production.stage] ||
    'Pendiente de clasificación'
  )
}

function priorityTone(priority) {
  if (priority === 'Urgente') return 'red'
  if (priority === 'Alta') return 'amber'
  return ''
}
