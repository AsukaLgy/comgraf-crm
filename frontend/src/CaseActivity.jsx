import { Clock, MessageSquare } from 'lucide-react'
import { Link } from 'react-router-dom'
import { date } from './api'
import { Badge, Empty, Panel } from './components'
import Composer from './Composer'

export default function CaseActivity({ record, attention, user, person, onReference }) {
  return (
    <Panel title="Bitácora y comunicación" action={<Badge>{record.events.length} eventos</Badge>}>
      {attention ? (
        <Composer key={record.id} record={record} user={user} />
      ) : (
        <div className="notice">
          Etapa actual: {record.production?.stage_label || 'sin producción'}. Las especificaciones y
          conversaciones se conservan en este expediente.
        </div>
      )}
      {record.events.length ? (
        <ol className="timeline">
          {[...record.events].reverse().map((event) => (
            <li key={event.id} className={event.kind === 'Sistema' ? 'system' : ''}>
              <span className="timeline-icon">
                {event.kind === 'Sistema' ? <Clock size={14} /> : <MessageSquare size={14} />}
              </span>
              <div>
                <div className="event-meta">
                  <strong>{event.kind}</strong>
                  <span>{event.channel !== 'Sistema' && event.channel}</span>
                  <span>{person(event.actor_id)}</span>
                  <time>{date(event.created_at)}</time>
                </div>
                <p>{event.body}</p>
                {event.data?.action === 'converted' && (
                  <p className="event-references">
                    <Link to="#production" onClick={() => onReference?.('production')}>
                      Orden {event.data.order_number || `OP-${String(record.id).padStart(5, '0')}`}
                    </Link>
                    {event.data.quote_id &&
                    (record.quotes || []).some((q) => q.id === event.data.quote_id) ? (
                      <>
                        {' '}
                        ·{' '}
                        <Link
                          to={`#quote-${event.data.quote_id}`}
                          onClick={() => onReference?.(`quote-${event.data.quote_id}`)}
                        >
                          Cotización {event.data.quote_number}, versión {event.data.quote_version}
                        </Link>
                      </>
                    ) : event.data.quote_id ? (
                      <small> · Cotización no disponible o sin permiso</small>
                    ) : null}
                  </p>
                )}
                {event.data.direction && <small>{event.data.direction} · Registro manual</small>}
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <Empty />
      )}
    </Panel>
  )
}
