import { Plus } from 'lucide-react'
import { can, date, localDate, money, quantity } from './api'
import { Badge, Empty, Panel } from './components'
import { HNL_CURRENCY, PRODUCTION_STAGE } from './domain'

export default function DeliveryFinance({
  record,
  user,
  finance,
  canProduce,
  onOperation,
  onDelivery,
}) {
  const production = record.production
  const canDeliver =
    canProduce &&
    production.stage === PRODUCTION_STAGE.READY &&
    record.delivery_status !== 'Completa' &&
    !production.blocked_since &&
    !production.cancelled_at

  return (
    <Panel
      title="Entregas y cobros"
      action={
        <div className="actions">
          {canDeliver && (
            <button onClick={onDelivery}>
              <Plus size={15} />
              Registrar entrega
            </button>
          )}
          {finance && !production.cancelled_at && production.currency === HNL_CURRENCY && (
            <button
              className="primary"
              onClick={() =>
                onOperation(
                  'payments',
                  'Registrar abono',
                  [
                    { name: 'kind', label: 'Tipo', options: ['Anticipo', 'Abono'] },
                    {
                      name: 'amount',
                      label: `Importe (${HNL_CURRENCY})`,
                      type: 'number',
                      min: '0.01',
                      step: '0.01',
                      required: true,
                    },
                    { name: 'reference', label: 'Referencia / comprobante', required: true },
                  ],
                  { kind: 'Abono' },
                )
              }
            >
              Registrar abono
            </button>
          )}
          {can(user, 'adjust') &&
            !production.cancelled_at &&
            production.currency === HNL_CURRENCY && (
              <button
                onClick={() =>
                  onOperation(
                    'adjustments',
                    'Ajuste autorizado',
                    [
                      {
                        name: 'amount',
                        label: `Importe (${HNL_CURRENCY}): positivo aumenta saldo, negativo lo reduce`,
                        type: 'number',
                        step: '0.01',
                        required: true,
                      },
                      {
                        name: 'due_date',
                        label: 'Vencimiento del ajuste',
                        type: 'date',
                        required: true,
                      },
                      { name: 'reason', label: 'Motivo', type: 'textarea', required: true },
                    ],
                    { due_date: localDate() },
                  )
                }
              >
                Ajuste
              </button>
            )}
        </div>
      }
    >
      {finance ? (
        <div className="finance-summary">
          <div>
            <small>Precio de la orden</small>
            <strong>{money(production.price, production.currency)}</strong>
          </div>
          <div>
            <small>Saldo pendiente</small>
            <strong>{money(record.balance, production.currency)}</strong>
          </div>
          <div>
            <small>Saldo vencido</small>
            <strong>{money(record.overdue_balance, production.currency)}</strong>
          </div>
          <div>
            <small>Situación de pago</small>
            <Badge
              tone={
                record.payment_status === 'Vencido'
                  ? 'red'
                  : record.payment_status === 'Pagado'
                    ? 'teal'
                    : ''
              }
            >
              {record.payment_status || 'Sin saldo registrado'}
            </Badge>
          </div>
        </div>
      ) : (
        <p className="notice">Importes restringidos al rol de Cobranza.</p>
      )}
      {finance && production.currency !== HNL_CURRENCY && (
        <p className="notice">
          Orden histórica en USD. Se conserva su moneda original y no se admiten nuevos pagos HNL.
        </p>
      )}
      <h3>Entregas</h3>
      {record.deliveries?.length ? (
        <div className="table-wrap">
          <table className="case-table">
            <thead>
              <tr>
                <th>Fecha</th>
                <th>Partida</th>
                <th>Cantidad</th>
                <th>Recibe</th>
                <th>Modalidad</th>
                <th>Evidencia</th>
              </tr>
            </thead>
            <tbody>
              {record.deliveries.map((delivery) => (
                <tr key={delivery.id}>
                  <td>{date(delivery.delivered_at)}</td>
                  <td>
                    {record.items.find((item) => item.id === delivery.item_id)?.product ||
                      'Partida'}
                  </td>
                  <td>{quantity(delivery.quantity)}</td>
                  <td>{delivery.receiver}</td>
                  <td>{delivery.method}</td>
                  <td>{delivery.evidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>Aún no hay entregas registradas.</Empty>
      )}
      <h3>Movimientos de saldo</h3>
      {record.ledger?.length ? (
        <div className="table-wrap">
          <table className="case-table">
            <thead>
              <tr>
                <th>Fecha</th>
                <th>Tipo</th>
                <th>Importe</th>
                <th>Vencimiento</th>
                <th>Referencia / motivo</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {record.ledger.map((entry) => (
                <tr key={entry.id}>
                  <td>{date(entry.created_at)}</td>
                  <td>{entry.kind}</td>
                  <td>{money(entry.amount, production.currency)}</td>
                  <td>{date(entry.due_date)}</td>
                  <td>{entry.reason || entry.reference}</td>
                  <td>
                    {can(user, 'adjust') &&
                      ['Anticipo', 'Abono'].includes(entry.kind) &&
                      !record.ledger.some((other) => other.reverses_id === entry.id) && (
                        <button
                          onClick={() =>
                            onOperation(
                              `payments/${entry.id}/reverse`,
                              'Revertir pago registrado',
                              [
                                {
                                  name: 'reason',
                                  label: 'Motivo de reversión',
                                  type: 'textarea',
                                  required: true,
                                },
                              ],
                            )
                          }
                        >
                          Revertir con trazabilidad
                        </button>
                      )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>No hay abonos ni ajustes registrados.</Empty>
      )}
      <p className="muted">
        Control operativo de cobros; no sustituye un sistema contable o fiscal.
      </p>
    </Panel>
  )
}
