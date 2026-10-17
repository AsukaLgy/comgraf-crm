import { Link } from 'react-router-dom'
import { Plus, Printer, UserRound } from 'lucide-react'
import { date } from './api'
import { Badge, ErrorBox } from './components'

export default function CaseAttention({
  record,
  person,
  canAttend,
  canTake,
  canAssign,
  onTake,
  onAssign,
  onConvert,
  onToggle,
  attentionError,
  attentionBusy,
  onRefresh,
  onEdit,
}) {
  return (
    <>
      <section className="case-heading">
        <div>
          <div className="eyebrow">
            EXPEDIENTE {record.number}{' '}
            <Badge tone={record.production ? 'teal' : ''}>{record.mode}</Badge>
          </div>
          <h1>{record.subject}</h1>
          <div className="case-meta">
            <span>{record.client.name}</span>
            <span>· Responsable: {person(record.owner_id)}</span>
            <span>· {record.kind}</span>
            <span>· {record.channel}</span>
            <span>· {date(record.created_at)}</span>
          </div>
        </div>
        <div className="actions">
          {!record.owner_id && canTake && (
            <button className="primary" onClick={onTake}>
              <UserRound size={15} />
              Tomar caso
            </button>
          )}
          {canAssign && <button onClick={onAssign}>Asignar caso</button>}
          {canAttend && !record.production && (
            <button className="primary" onClick={onConvert}>
              <Plus size={16} />
              Generar orden de producción
            </button>
          )}
          {canAttend && (
            <button onClick={onToggle} disabled={attentionBusy}>
              {record.status === 'Cerrado' ? 'Abrir atención' : 'Cerrar atención'}
            </button>
          )}
          {record.production && (
            <Link className="button" to={`/cases/${record.id}/print`}>
              <Printer size={16} />
              Ficha de taller
            </Link>
          )}
        </div>
      </section>
      <div className="status-strip">
        <Badge>{record.status}</Badge>
        <Badge tone={record.priority === 'Urgente' ? 'red' : ''}>{record.priority}</Badge>
        <span>
          Atención: <strong>{person(record.owner_id)}</strong>
        </span>
        {record.production && (
          <>
            <span>
              Entrega:{' '}
              <strong>
                {record.delivery_status === 'Completa' ? 'Entregado' : record.delivery_status}
              </strong>
            </span>
            {record.payment_status && (
              <span>
                Pago:{' '}
                <Badge
                  tone={
                    record.payment_status === 'Vencido'
                      ? 'red'
                      : record.payment_status === 'Pagado'
                        ? 'teal'
                        : ''
                  }
                >
                  {record.payment_status}
                </Badge>
              </span>
            )}
          </>
        )}
        {canAttend && <button onClick={onEdit}>Editar datos del caso</button>}
        {record.related_case_id && (
          <Link to={`/cases/${record.related_case_id}`}>Caso relacionado</Link>
        )}
        <span className="muted">Revisión {record.version}</span>
        <button className="text-button" onClick={onRefresh}>
          Actualizar
        </button>
      </div>
      {canAttend && (
        <p className="muted">
          Cerrar atención retira el caso de pendientes inmediatos; el trabajo y sus cobros
          continúan.
        </p>
      )}
      <ErrorBox error={attentionError} />
    </>
  )
}
