import { Check } from 'lucide-react'
import { Badge } from './components'
import { money } from './api'
import {
  PRODUCTION_STAGES,
  PRODUCTION_STAGE_LABELS,
  PRODUCTION_STAGE,
  PRODUCTION_NEXT_STAGE,
} from './domain'

const reason = { name: 'reason', label: 'Motivo del reclamo', type: 'textarea', required: true }

export default function ProductionStages({ record, canManage, onSchedule, onDelivery, operation }) {
  const production = record.production
  const currentIndex = PRODUCTION_STAGES.indexOf(production.stage)
  const next = PRODUCTION_NEXT_STAGE[production.stage]
  const previous = currentIndex > 0 ? PRODUCTION_STAGES.slice(0, currentIndex) : []
  const visited = new Set([production.stage])
  for (const event of record.events || []) {
    if (event.data?.production_id !== production.id && !(record.production_count === 1 && !event.data?.production_id)) continue
    if (event.data?.action === 'converted' && event.data.stage) visited.add(event.data.stage)
    if (event.data?.action === 'stage' || event.data?.action === 'stage_classified') {
      if (event.data.from) visited.add(event.data.from)
      if (event.data.to || event.data.stage) visited.add(event.data.to || event.data.stage)
    }
  }

  function classify() {
    operation(
      'production/stage',
      'Clasificar etapa histórica',
      [
        {
          name: 'stage',
          label: 'Etapa física actual',
          options: PRODUCTION_STAGES.map((value) => ({
            value,
            label: PRODUCTION_STAGE_LABELS[value],
          })),
          required: true,
        },
      ],
      { stage: PRODUCTION_STAGE.NEW },
    )
  }

  return (
    <section className="production-path">
      <ol aria-label="Etapas de producción">
        {PRODUCTION_STAGES.map((stage, index) => (
          <li
            key={stage}
            className={
              stage === production.stage
                ? 'current'
                : visited.has(stage)
                  ? 'done'
                  : ''
            }
          >
            <span>
              {visited.has(stage) && stage !== production.stage ? <Check size={13} /> : index + 1}
            </span>
            {PRODUCTION_STAGE_LABELS[stage]}
          </li>
        ))}
      </ol>

      {production.classification_required && (
        <div className="notice">
          La etapa histórica «{production.legacy_stage}» no permite inferir la etapa física actual.
          Clasifícala explícitamente para continuar.
        </div>
      )}

      {production.cancelled_at ? (
        <div className={production.cancellation_review_pending ? 'notice' : 'error'}>
          Producción anulada.
          {production.cancellation_review_pending
            ? ' Resolución económica pendiente.'
            : record.balance === undefined
              ? ' Consulta el saldo con un usuario financiero.'
              : record.balance < 0
                ? ` Pendiente de devolución: ${money(-Number(record.balance), production.currency)}`
                : record.balance > 0
                  ? ` Saldo por cobrar: ${money(record.balance, production.currency)}`
                  : ' Saldo liquidado.'}
        </div>
      ) : (
        <div className="production-controls">
          <span>
            {production.blocked_since && (
              <Badge tone="red">Bloqueado: {production.block_reason}</Badge>
            )}
            {production.classification_required ? (
              canManage && <button onClick={classify}>Clasificar etapa</button>
            ) : (
              <>
                <strong>
                  {production.stage_label}
                  {record.delivery_status === 'Completa' ? ' · Entregado' : ''}
                </strong>
                {production.active_elapsed_seconds != null && (
                  <small>
                    {Math.floor(production.active_elapsed_seconds / 3600)} h en etapa activa
                  </small>
                )}
              </>
            )}
          </span>
          {canManage &&
            !production.classification_required &&
            record.delivery_status !== 'Completa' && (
              <div className="actions">
                <button onClick={onSchedule}>Programar</button>
                {production.stage === PRODUCTION_STAGE.READY ? (
                  <>
                    <button
                      className="primary"
                      onClick={onDelivery}
                      disabled={Boolean(production.blocked_since)}
                      title={
                        production.blocked_since
                          ? 'Resuelve el bloqueo antes de registrar entrega'
                          : undefined
                      }
                    >
                      Registrar entrega
                    </button>
                    {previous.length > 0 && (
                      <button
                        disabled={record.delivery_status !== 'Pendiente'}
                        title={
                          record.delivery_status !== 'Pendiente'
                            ? 'No se puede retroceder tras registrar entregas'
                            : undefined
                        }
                        onClick={() =>
                          operation(
                            'production/stage',
                            'Registrar reclamo',
                            [
                              {
                                name: 'stage',
                                label: 'Regresar a',
                                options: previous.map((value) => ({
                                  value,
                                  label: PRODUCTION_STAGE_LABELS[value],
                                })),
                                required: true,
                              },
                              reason,
                            ],
                            { stage: previous.at(-1) },
                            undefined,
                            'Registrar reclamo',
                          )
                        }
                      >
                        Registrar reclamo
                      </button>
                    )}
                  </>
                ) : (
                  <>
                    <button
                      disabled={Boolean(production.blocked_since) || !next}
                      title={
                        production.blocked_since ? 'Resuelve el bloqueo para avanzar' : undefined
                      }
                      onClick={() =>
                        operation(
                          'production/stage',
                          `Avanzar a ${PRODUCTION_STAGE_LABELS[next]}`,
                          [],
                          { stage: next },
                          undefined,
                          `Avanzar a ${PRODUCTION_STAGE_LABELS[next]}`,
                        )
                      }
                    >
                      {next ? `Avanzar a ${PRODUCTION_STAGE_LABELS[next]}` : 'Etapa final'}
                    </button>
                    {previous.length > 0 && (
                      <button
                        onClick={() =>
                          operation(
                            'production/stage',
                            'Registrar reclamo',
                            [
                              {
                                name: 'stage',
                                label: 'Regresar a',
                                options: previous.map((value) => ({
                                  value,
                                  label: PRODUCTION_STAGE_LABELS[value],
                                })),
                                required: true,
                              },
                              reason,
                            ],
                            { stage: previous.at(-1) },
                            undefined,
                            'Registrar reclamo',
                          )
                        }
                      >
                        Registrar reclamo
                      </button>
                    )}
                  </>
                )}
                <button
                  onClick={() =>
                    operation(
                      `production/${production.blocked_since ? 'unblock' : 'block'}`,
                      production.blocked_since ? 'Resolver bloqueo' : 'Bloquear trabajo',
                      [{ name: 'reason', label: 'Motivo', type: 'textarea', required: true }],
                    )
                  }
                >
                  {production.blocked_since ? 'Resolver bloqueo' : 'Bloquear'}
                </button>
              </div>
            )}
        </div>
      )}
    </section>
  )
}
