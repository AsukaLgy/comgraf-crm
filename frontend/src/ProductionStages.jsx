import { Check } from 'lucide-react'
import { Badge } from './components'
import {
  PRODUCTION_STAGES,
  PRODUCTION_STAGE_LABELS,
  PRODUCTION_STAGE,
  PRODUCTION_NEXT_STAGE,
} from './domain'

const reason = { name: 'reason', label: 'Motivo', type: 'textarea', required: true }
export default function ProductionStages({ record, canManage, onSchedule, operation }) {
  const production = record.production
  const currentIndex = PRODUCTION_STAGES.indexOf(production.stage)
  return (
    <section className="production-path">
      <ol aria-label="Etapas de producción">
        {PRODUCTION_STAGES.map((stage, index) => (
          <li
            key={stage}
            className={
              stage === production.stage
                ? 'current'
                : currentIndex >= 0 && index < currentIndex
                  ? 'done'
                  : ''
            }
          >
            <span>
              {currentIndex >= 0 && index < currentIndex ? <Check size={13} /> : index + 1}
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
        <div className="error">
          Producción anulada · Los pagos y entregas permanecen registrados.
        </div>
      ) : (
        <div className="production-controls">
          <span>
            {production.blocked_since && (
              <Badge tone="red">Bloqueado: {production.block_reason}</Badge>
            )}
            {production.classification_required && canManage && (
              <button
                onClick={() =>
                  operation(
                    'production/stage',
                    'Clasificar etapa actual',
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
              >
                Clasificar etapa
              </button>
            )}
            {!production.classification_required && (
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
                <button
                  onClick={() =>
                    operation(
                      'production/stage',
                      'Cambiar etapa',
                      [
                        {
                          name: 'stage',
                          label: 'Nueva etapa',
                          options: PRODUCTION_STAGES.map((value) => ({
                            value,
                            label: PRODUCTION_STAGE_LABELS[value],
                          })),
                        },
                        { ...reason, required: false },
                      ],
                      { stage: PRODUCTION_NEXT_STAGE[production.stage] || production.stage },
                    )
                  }
                >
                  Cambiar etapa
                </button>
                <button
                  onClick={() =>
                    operation(
                      `production/${production.blocked_since ? 'unblock' : 'block'}`,
                      production.blocked_since ? 'Resolver bloqueo' : 'Bloquear trabajo',
                      [reason],
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
