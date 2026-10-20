import { useState } from 'react'
import { Link, useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Plus, Paperclip } from 'lucide-react'
import { api, can, date, money, quantity, toUTC, timezone, localInput, uid } from './api'
import { Badge, ErrorBox, Loading, Panel, Operation, FormDialog, Field, Empty } from './components'
import ProductForm, { Specifications } from './ProductForm'
import CaseAttention from './CaseAttention'
import CaseActivity from './CaseActivity'
import CasePrintSheet from './CasePrintSheet'
import OrderQRCode from './OrderQRCode'
import DeliveryFinance from './DeliveryFinance'
import ProductionStages from './ProductionStages'
import { HNL_CURRENCY } from './domain'
const reason = { name: 'reason', label: 'Motivo', type: 'textarea', required: true }

export default function CasePage({ user, print = false }) {
  const { id } = useParams(),
    nav = useNavigate(),
    [searchParams, setSearchParams] = useSearchParams(),
    selectedProductionId = searchParams.get('production_id') || '',
    qc = useQueryClient(),
    [modal, setModal] = useState(null),
    [section, setSection] = useState('Actividad'),
    [attentionError, setAttentionError] = useState(null),
    [attentionBusy, setAttentionBusy] = useState(false)
  const query = useQuery({ queryKey: ['case', id, selectedProductionId], queryFn: () => api('/cases/' + id + (selectedProductionId ? `?production_id=${selectedProductionId}` : '')), refetchInterval: print ? false : 15000, refetchOnWindowFocus: !print }),
    people = useQuery({ queryKey: ['users'], queryFn: () => api('/users') })
  if (query.isPending) return <Loading />
  if (query.error) return <ErrorBox error={query.error} />
  const c = query.data,
    p = c.production,
    users = people.data || [],
    close = () => setModal(null)
  const attention = can(user, 'attention') && (Number(c.owner_id) === Number(user.id) || can(user, 'assign')),
    canTake = can(user, 'attention') && !c.owner_id && c.lifecycle !== 'Completado',
    production = can(user, 'production'),
    finance = can(user, 'finance')
  const activeAttention = attention && c.lifecycle !== 'Completado'
  const completionBlockers = c.completion_blockers || []
  async function changeLifecycle(action) {
    const result = await api(`/cases/${c.id}/${action}`, {
      method: 'POST',
      body: { version: c.version, key: uid() },
    })
    qc.setQueryData(['case', String(c.id), selectedProductionId], result)
    await qc.invalidateQueries()
  }
  const person = (id) => users.find((u) => u.id === id)?.name || 'Sin asignar'
  const personOptions = (permission) =>
    users.filter((u) => u.active && can(u, permission)).map((u) => ({ value: u.id, label: u.name }))
  const operation = (
    path,
    title,
    fields = [],
    initial = {},
    transform,
    submit,
    method = 'POST',
    children,
    renderSummary,
  ) =>
    setModal({
      type: 'operation',
      path,
      title,
      fields,
      initial,
      transform,
      submit,
      method,
      children,
      renderSummary,
    })
  const assign = () =>
    operation(
      'assign',
      'Asignar solicitud',
      [
        {
          name: 'owner_id',
          label: 'Responsable principal',
          options: personOptions('attention'),
          empty: 'Sin asignar',
        },
      ],
      { owner_id: c.owner_id || '' },
      (v) => ({ ...v, owner_id: v.owner_id ? Number(v.owner_id) : null }),
    )
  async function toggleAttention() {
    if (attentionBusy) return
    setAttentionBusy(true)
    setAttentionError(null)
    try {
      const result = await api('/cases/' + c.id + '/attention', {
        method: 'POST',
        body: {
          status: c.status === 'Cerrado' ? 'Abierto' : 'Cerrado',
          version: c.version,
          key: uid(),
        },
      })
      qc.setQueryData(['case', String(c.id), selectedProductionId], result)
      await qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'case' })
    } catch (error) {
      setAttentionError(error)
      if (error.status === 409) await query.refetch()
    } finally {
      setAttentionBusy(false)
    }
  }
  function schedule() {
    operation(
      'production/schedule',
      'Programar producción y compromiso',
      [
        {
          name: 'scheduled_at',
          label: `Inicio programado (${timezone})`,
          type: 'datetime-local',
          required: true,
        },
        {
          name: 'committed_date',
          label: 'Fecha comprometida de entrega',
          type: 'date',
          required: true,
        },
      ],
      { committed_date: p.committed_date || '' },
      (v) => ({ ...v, scheduled_at: toUTC(v.scheduled_at) }),
    )
  }
  function delivery() {
    operation(
      'deliveries',
      'Registrar entrega',
      [
        {
          name: 'item_id',
          label: 'Partida',
          options: c.items.map((i) => ({
            value: i.id,
            label: `${i.product} · ${quantity(i.quantity)} ${i.unit}`,
          })),
          required: true,
          empty: 'Seleccionar partida',
        },
        {
          name: 'quantity',
          label: 'Cantidad entregada',
          type: 'number',
          step: '0.001',
          min: '0.001',
          required: true,
        },
        {
          name: 'delivered_at',
          label: `Fecha y hora (${timezone})`,
          type: 'datetime-local',
          required: true,
        },
        { name: 'receiver', label: 'Recibido por', required: true },
        { name: 'method', label: 'Modalidad', required: true },
        {
          name: 'evidence',
          label: 'Evidencia / referencia del recibo',
          type: 'textarea',
          required: true,
        },
        ...(finance && p.currency === HNL_CURRENCY
          ? [
              { name: 'payment_now', label: 'Registrar pago con esta entrega', type: 'checkbox' },
              {
                name: 'payment_amount',
                label: `Abono o liquidación (${HNL_CURRENCY}) · saldo actual ${money(c.balance, p.currency)}`,
                type: 'number',
                min: '0.01',
                step: '0.01',
                required: true,
                when: (v) => v.payment_now,
              },
              {
                name: 'payment_reference',
                label: 'Referencia del pago',
                required: true,
                when: (v) => v.payment_now,
              },
            ]
          : []),
      ],
      {
        method: p.delivery_method,
        payment_now: false,
        payment_amount: c.balance > 0 ? c.balance : '',
      },
      (v) => {
        const { payment_now, ...delivery } = v
        return {
          ...delivery,
          item_id: Number(v.item_id),
          delivered_at: toUTC(v.delivered_at),
          payment_amount: payment_now ? v.payment_amount : null,
          payment_reference: payment_now ? v.payment_reference : null,
        }
      },
    )
  }
  if (print) return <CasePrintSheet id={id} record={c} person={person} />
  return (
    <>
      <button className="back-link" onClick={() => nav(-1)}>
        <ArrowLeft size={16} />
        Volver a la bandeja
      </button>
      <CaseAttention
        record={c}
        person={person}
        canAttend={activeAttention}
        canTake={canTake}
        canAssign={can(user, 'assign') && c.lifecycle !== 'Completado'}
        onTake={() => operation('take', 'Tomar solicitud', [], {}, undefined, 'Tomar solicitud')}
        onAssign={assign}
        onConvert={() => setModal({ type: 'convert' })}
        onToggle={toggleAttention}
        attentionError={attentionError}
        attentionBusy={attentionBusy}
        onRefresh={() => query.refetch()}
        onEdit={() =>
          operation(
            'details',
            'Editar datos de la solicitud',
            [
              {
                name: 'priority',
                label: 'Prioridad',
                options: ['Baja', 'Normal', 'Alta', 'Urgente'],
                required: true,
              },
              { name: 'next_action', label: 'Próxima acción', required: false },
            ],
            { priority: c.priority, next_action: c.next_action },
            (v) => ({ priority: v.priority, next_action: v.next_action }),
          )
        }
        canComplete={completionBlockers.length === 0}
        completionBusy={attentionBusy}
        canReactivate={attention}
        onComplete={() => setModal({ type: 'complete' })}
        onReactivate={async () => {
          if (attentionBusy) return
          setAttentionBusy(true)
          setAttentionError(null)
          try {
            await changeLifecycle('reactivate')
          } catch (error) {
            setAttentionError(error)
            if (error.status === 409) await query.refetch()
          } finally {
            setAttentionBusy(false)
          }
        }}
      />
      {p && (
        <ProductionStages
          record={c}
          canManage={production && c.lifecycle !== 'Completado'}
          onSchedule={schedule}
          onDelivery={delivery}
          operation={operation}
        />
      )}
      <div className="case-grid">
        <div className="case-main">
          <div className="tabs section-tabs">
            {[
              'Actividad',
              'Cotizaciones',
              ...(c.production_count ? ['Producción', 'Entregas y cobros'] : []),
              'Archivos',
            ].map((s) => (
              <button
                className={section === s ? 'active' : ''}
                key={s}
                onClick={() => setSection(s)}
              >
                {s}
                {s === 'Archivos' && <span>{c.attachments.length}</span>}
              </button>
            ))}
          </div>
          {section === 'Actividad' && (
            <CaseActivity
              record={c}
              attention={activeAttention}
              user={user}
              person={person}
              onReference={(reference) => {
                setSection(reference === 'production' ? 'Producción' : 'Cotizaciones')
                window.setTimeout(
                  () =>
                    document
                      .getElementById(reference)
                      ?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
                  0,
                )
              }}
            />
          )}
          {section === 'Cotizaciones' && (
            <Panel
              title="Cotizaciones y versiones"
              action={
                activeAttention && (
                  <button onClick={() => setModal({ type: 'quote' })}>
                    <Plus size={15} />
                    Nueva versión
                  </button>
                )
              }
            >
              {c.quotes.length ? (
                c.quotes.map((q) => (
                  <article className="quote" id={`quote-${q.id}`} key={q.id}>
                    <div className="section-line">
                      <h3>
                        Versión {q.version}{' '}
                        <Badge tone={q.status === 'Aceptada' ? 'teal' : ''}>{q.status}</Badge>
                      </h3>
                      <strong>{money(q.total, q.currency)}</strong>
                    </div>
                    <p className="muted">
                      Vigencia: {date(q.valid_until)} · {q.items.length} partidas
                    </p>
                    {q.items.map((i, n) => (
                      <details key={n}>
                        <summary>
                          {i.product} · {quantity(i.quantity)} {i.unit} ·{' '}
                          {money(i.unit_price, q.currency)} / unidad
                        </summary>
                        <Specifications specs={i.specs} />
                      </details>
                    ))}
                    {q.decision_evidence && <p>{q.decision_evidence}</p>}
                    <div className="actions">
                      {activeAttention && (
                        <button onClick={() => setModal({ type: 'quote', source: q })}>
                          Revisar en nueva versión
                        </button>
                      )}
                      {activeAttention && q.status === 'Pendiente' && (
                        <button
                          onClick={() =>
                            operation(
                              `quotes/${q.id}/decision`,
                              'Registrar decisión de cotización',
                              [
                                {
                                  name: 'status',
                                  label: 'Decisión',
                                  options: ['Aceptada', 'Rechazada'],
                                },
                                {
                                  name: 'evidence',
                                  label: 'Evidencia / comunicación del cliente',
                                  type: 'textarea',
                                  required: true,
                                },
                              ],
                              { status: 'Aceptada' },
                            )
                          }
                        >
                          Registrar decisión
                        </button>
                      )}
                      {activeAttention && q.status === 'Aceptada' && q.currency === 'HNL' && !q.production_id && (
                        <button
                          className="primary"
                          onClick={() => setModal({ type: 'convert', source: q })}
                        >
                          Generar orden con esta versión
                        </button>
                      )}
                      {q.production_id && <Link className="button" to={`/cases/${c.id}?production_id=${q.production_id}`}>Ver {q.order_number}</Link>}
                    </div>
                  </article>
                ))
              ) : (
                <Empty>
                  Aún no hay cotizaciones. Puedes cotizar en lempiras sin iniciar fabricación.
                </Empty>
              )}
            </Panel>
          )}
          {section === 'Producción' && c.production_count > 0 && (
            <Panel title={`Órdenes de producción · ${c.production_count} · ${c.production_pending_count} pendientes`} action={activeAttention && <button className="primary" onClick={() => setModal({ type: 'convert' })}><Plus size={15}/>Agregar orden de producción</button>}>
              <div className="order-selector" role="list" aria-label="Órdenes de producción">
                {c.productions.map((order) => <Link role="listitem" key={order.id} className={String(order.id)===String(p?.id)?'order-card active':'order-card'} to={`/cases/${c.id}?production_id=${order.id}`}>
                  <strong>{order.order_number}</strong><span>{order.stage_label || 'Pendiente de clasificación'}</span><span>{order.delivery_status==='Completa'?'Entregada':`Entrega ${order.delivery_status}`}</span><span>{order.committed_date?`Compromiso ${date(order.committed_date)}`:'Sin fecha comprometida'}</span>
                </Link>)}
              </div>
            </Panel>
          )}
          {section === 'Producción' && p && (
            <Panel title="Especificaciones de producción">
              <div id="production" />
              <div className="notice">
                Etapa actual: {p.stage_label || 'Pendiente de clasificación'}. Las especificaciones
                capturadas al generar se conservan como referencia histórica.
              </div>
              {p.stage === 'WORKSHOP' && (
                <div className={p.binding_confirmed_at && p.binding_confirmed_cycle === p.workflow_cycle ? 'notice' : 'scanner-pending'}>
                  {p.binding_confirmed_at && p.binding_confirmed_cycle === p.workflow_cycle
                    ? `Encuadernación confirmada por ${person(p.binding_confirmed_by_id)} el ${date(p.binding_confirmed_at)}. La liberación depende del grupo de órdenes.`
                    : 'Pendiente de confirmar encuadernación desde el escáner.'}
                </div>
              )}
              {c.items.map((i) => (
                <article className="quote" key={i.id}>
                  <div className="section-line">
                    <h3>{i.product}</h3>
                  </div>
                  <p>
                    {quantity(i.quantity)} {i.unit}
                    {i.unit_price !== undefined &&
                      ' · ' + money(i.unit_price, p.currency) + ' / unidad'}
                  </p>
                  <Specifications specs={i.specs} />
                </article>
              ))}
              <h3>Instrucciones</h3>
              <p className="prewrap">{p.instructions || 'Sin instrucciones adicionales'}</p>
              <OrderQRCode production={p} caseNumber={c.number} description={p.instructions || c.subject} client={c.client.name} compact />
              {can(user, 'production') &&
                c.lifecycle !== 'Completado' &&
                !p.cancelled_at &&
                c.delivery_status !== 'Completa' && (
                  <button
                    className="danger"
                    onClick={() =>
                      operation(
                        'production/cancel',
                        'Anular trabajo',
                        [
                          { ...reason, label: 'Motivo de anulación' },
                          ...(finance && can(user, 'adjust')
                            ? [
                                {
                                  name: 'treatment',
                                  label: 'Tratamiento económico',
                                  options: [
                                    { value: 'SIN_CARGO', label: 'Sin cargo por cancelación' },
                                    {
                                      value: 'IMPORTE_FINAL',
                                      label: 'Con importe final acordado a cobrar',
                                    },
                                  ],
                                  empty: 'Seleccionar tratamiento',
                                  required: true,
                                },
                                {
                                  name: 'final_amount',
                                  label: 'Importe final acordado (HNL)',
                                  type: 'number',
                                  min: '0',
                                  step: '0.01',
                                  required: (v) => v.treatment === 'IMPORTE_FINAL',
                                  when: (v) => v.treatment === 'IMPORTE_FINAL',
                                },
                              ]
                            : []),
                        ],
                        {},
                        (v) => ({
                          reason: v.reason,
                          ...(v.treatment ? { treatment: v.treatment } : {}),
                          ...(v.treatment === 'IMPORTE_FINAL'
                            ? { final_amount: v.final_amount }
                            : {}),
                        }),
                        undefined,
                        undefined,
                        <CancellationSummary record={c} />,
                        (values) => <CancellationPreview record={c} values={values} />,
                      )
                    }
                  >
                    Anular trabajo
                  </button>
                )}
              {p.cancelled_at &&
                p.cancellation_review_pending &&
                finance &&
                can(user, 'adjust') && (
                  <button
                    className="primary"
                    onClick={() =>
                      operation(
                        'production/cancel/resolve',
                        'Resolver situación económica de la anulación',
                        [
                          { ...reason, label: 'Nota de resolución' },
                          {
                            name: 'treatment',
                            label: 'Tratamiento económico',
                            options: [
                              { value: 'SIN_CARGO', label: 'Sin cargo por cancelación' },
                              {
                                value: 'IMPORTE_FINAL',
                                label: 'Con importe final acordado a cobrar',
                              },
                            ],
                            empty: 'Seleccionar tratamiento',
                            required: true,
                          },
                          {
                            name: 'final_amount',
                            label: 'Importe final acordado (HNL)',
                            type: 'number',
                            min: '0',
                            step: '0.01',
                            required: (v) => v.treatment === 'IMPORTE_FINAL',
                            when: (v) => v.treatment === 'IMPORTE_FINAL',
                          },
                        ],
                        {},
                        (v) => ({
                          reason: v.reason,
                          treatment: v.treatment,
                          ...(v.treatment === 'IMPORTE_FINAL'
                            ? { final_amount: v.final_amount }
                            : {}),
                        }),
                        undefined,
                        undefined,
                        <CancellationSummary record={c} />,
                        (values) => <CancellationPreview record={c} values={values} />,
                      )
                    }
                  >
                    Resolver situación económica
                  </button>
                )}
            </Panel>
          )}
          {section === 'Entregas y cobros' && p && (
            <DeliveryFinance
              record={c}
              user={user}
              finance={finance && c.lifecycle !== 'Completado'}
              onOperation={operation}
            />
          )}
          {section === 'Archivos' && (
            <Panel
              title="Archivos de la solicitud"
              action={
                (activeAttention || (production && p && c.lifecycle !== 'Completado')) && (
                  <button onClick={() => setModal({ type: 'upload' })}>
                    <Paperclip size={15} />
                    Adjuntar archivo
                  </button>
                )
              }
            >
              <p className="muted">
                PDF, PNG, JPG o TXT · hasta 20 MB por archivo. Los artes se vinculan a una versión.
              </p>
              {c.attachments.map((a) => (
                <div className="attachment" key={a.id}>
                  <Paperclip size={18} />
                  <div>
                    <a href={'/api/attachments/' + a.id}>{a.name}</a>
                    <small>
                      Versión del arte: {a.art_version || 'Sin versión'} ·{' '}
                      {(a.size / 1024).toFixed(1)} KB · {date(a.created_at)}
                    </small>
                  </div>
                </div>
              ))}
              {!c.attachments.length && <Empty>No hay archivos adjuntos.</Empty>}
            </Panel>
          )}
        </div>
        <aside className="case-aside">
          <Panel title="Cliente">
            <div className="client-avatar">{c.client.name.slice(0, 2).toUpperCase()}</div>
            <h3>
              {can(user, 'clients') ? (
                <Link to={'/clients/' + c.client.id}>{c.client.name}</Link>
              ) : (
                c.client.name
              )}
            </h3>
            <p className="muted">
              {c.client.company || 'Cliente particular'}
              {c.client.provisional ? ' · Provisional' : ''}
            </p>
            {c.client.rtn && <p>RTN {c.client.rtn}</p>}
            {c.client.phones.map((p) => (
              <p key={p}>{p}</p>
            ))}
            {c.client.emails.map((e) => (
              <p key={e}>{e}</p>
            ))}
            {c.client.addresses.map((a) => (
              <p key={a}>{a}</p>
            ))}
            {c.description && (
              <>
                <hr />
                <p className="prewrap">{c.description}</p>
              </>
            )}
          </Panel>

          <Panel
            title="Próxima acción"
            action={
              activeAttention && (
                <button
                  className="icon-button"
                  aria-label="Programar seguimiento"
                  onClick={() =>
                    operation(
                      'tasks',
                      'Programar seguimiento',
                      [
                        { name: 'title', label: 'Acción a realizar', required: true },
                        {
                          name: 'owner_id',
                          label: 'Responsable',
                          options: personOptions('attention'),
                          required: true,
                        },
                        {
                          name: 'due_at',
                          label: `Fecha y hora (${timezone})`,
                          type: 'datetime-local',
                          required: true,
                        },
                      ],
                      { owner_id: user.id },
                      (v) => ({ ...v, owner_id: Number(v.owner_id), due_at: toUTC(v.due_at) }),
                    )
                  }
                >
                  <Plus size={16} />
                </button>
              )
            }
          >
            <p>{c.next_action || 'Sin próxima acción definida'}</p>
            {c.tasks.map((t) => (
              <div className="task" key={t.id}>
                <strong>{t.title}</strong>
                <small>
                  {date(t.due_at)} · {person(t.owner_id)}
                </small>
                {t.done_at ? (
                  <Badge tone="teal">Completado</Badge>
                ) : (
                  <>
                    {new Date(t.due_at) < new Date() && <Badge tone="red">Vencido</Badge>}
                    {activeAttention && (
                      <>
                        <button
                          onClick={() =>
                            operation(
                              `tasks/${t.id}/complete`,
                              t.category === 'PICKUP'
                                ? 'Registrar contacto de recogida'
                                : 'Completar seguimiento',
                              t.category === 'PICKUP'
                                ? [
                                    {
                                      name: 'note',
                                      label: 'Resumen del contacto',
                                      type: 'textarea',
                                    },
                                    {
                                      name: 'schedule_next_pickup',
                                      label:
                                        'Programar otro seguimiento en siete días si no se recoge',
                                      type: 'checkbox',
                                    },
                                  ]
                                : [],
                              { schedule_next_pickup: false },
                              (values) => ({
                                note: values.note || '',
                                schedule_next_pickup: Boolean(values.schedule_next_pickup),
                              }),
                              'Guardar contacto',
                            )
                          }
                        >
                          {t.category === 'PICKUP' ? 'Registrar contacto' : 'Completar'}
                        </button>
                        <button
                          onClick={() =>
                            operation(
                              `tasks/${t.id}`,
                              'Reprogramar seguimiento',
                              [
                                {
                                  name: 'due_at',
                                  label: `Nueva fecha y hora (${timezone})`,
                                  type: 'datetime-local',
                                  required: true,
                                },
                              ],
                              { due_at: localInput(t.due_at) },
                              (v) => ({ due_at: toUTC(v.due_at) }),
                              undefined,
                              'PUT',
                            )
                          }
                        >
                          Reprogramar
                        </button>
                      </>
                    )}
                  </>
                )}
              </div>
            ))}
          </Panel>
          {p && (
            <Panel title="Compromisos">
              <dl className="key-values">
                <dt>Fecha solicitada</dt>
                <dd>{date(p.requested_date)}</dd>
                <dt>Compromiso original</dt>
                <dd>{date(p.original_committed_date)}</dd>
                <dt>Compromiso actual</dt>
                <dd>{date(p.committed_date)}</dd>
                <dt>Programación</dt>
                <dd>{date(p.scheduled_at)}</dd>
                <dt>Entrega</dt>
                <dd>{p.delivery_method}</dd>
              </dl>
            </Panel>
          )}
          {c.status === 'Cerrado' && (
            <Panel title="Cierre de atención">
              <p>{c.close_reason}</p>
              <p>{c.close_summary}</p>
              <small>{date(c.closed_at)}</small>
            </Panel>
          )}
        </aside>
      </div>
      {modal?.type === 'operation' && <Operation {...modal} record={c} onClose={close} />}{' '}
      {modal?.type === 'complete' && (
        <FormDialog
          title="Completar solicitud"
          initial={{}}
          submit="Completar solicitud"
          canSubmit={completionBlockers.length === 0}
          onClose={close}
          onSave={() => changeLifecycle('complete')}
          render={() => (
            <div className="notice">
              <p>
                La solicitud dejará las bandejas activas y seguirá disponible en el historial de solicitudes y
                perfil del cliente.
              </p>
              {completionBlockers.length > 0 ? (
                <>
                  <strong>Resuelve estos pendientes antes de completar:</strong>
                  <ul>
                    {completionBlockers.map((item, index) => (
                      <li key={index}>{item}</li>
                    ))}
                  </ul>
                </>
              ) : (
                <p>Completar no modifica producción, entregas ni pagos.</p>
              )}
            </div>
          )}
        />
      )}
      {(modal?.type === 'quote' || modal?.type === 'convert') && (
        <ProductForm
          record={c}
          user={user}
          quote={modal.type === 'quote'}
          source={modal.source}
          onClose={close}
          onCreated={(order) => {setSearchParams({ production_id: String(order.id) });setSection('Producción')}}
        />
      )}
      {modal?.type === 'upload' && (
        <FormDialog
          title="Adjuntar archivo"
          initial={{ file: null, art_version: '' }}
          onClose={close}
          onSave={async (v) => {
            const form = new FormData()
            form.append('file', v.file)
            form.append('art_version', v.art_version)
            await api(`/cases/${id}/attachments`, { method: 'POST', body: form })
            await qc.invalidateQueries({ queryKey: ['case', id] })
          }}
          render={(v, set) => (
            <>
              <Field
                label="Archivo"
                required
                type="file"
                accept=".pdf,.png,.jpg,.jpeg,.txt"
                onChange={(e) => set('file', e.target.files[0])}
              />
              <Field
                label="Versión del arte"
                placeholder="Ej. v1"
                value={v.art_version}
                onChange={(e) => set('art_version', e.target.value)}
              />
            </>
          )}
        />
      )}
    </>
  )
}

function CancellationSummary({ record }) {
  const ledger = record.ledger || []
  const payments = ledger
    .filter((entry) => ['Anticipo', 'Abono', 'Reversión de pago'].includes(entry.kind))
    .reduce((sum, entry) => sum - Number(entry.amount), 0)
  const refunds = ledger
    .filter((entry) => entry.kind === 'Devolución')
    .reduce((sum, entry) => sum + Number(entry.amount), 0)
  const items = record.items || []
  return (
    <div className="notice">
      <strong>Resumen antes de anular</strong>
      <p>Etapa: {record.production.stage_label || 'Pendiente de clasificación'}</p>
      {items.map((item) => {
        const delivered = (record.deliveries || [])
          .filter((delivery) => delivery.item_id === item.id)
          .reduce((sum, delivery) => sum + Number(delivery.quantity), 0)
        return (
          <p key={item.id}>
            {item.product}: {quantity(delivered)} de {quantity(item.quantity)} {item.unit}{' '}
            entregados
          </p>
        )
      })}
      {record.balance !== undefined ? (
        <>
          <p>Pagos netos recibidos: {money(payments, record.production.currency)}</p>
          <p>Devoluciones ya registradas: {money(refunds, record.production.currency)}</p>
          <p>Saldo financiero actual: {money(record.balance, record.production.currency)}</p>
        </>
      ) : (
        <p>El resumen económico está restringido a usuarios con permisos financieros.</p>
      )}
      {record.production.currency === HNL_CURRENCY && (
        <p>El importe final acordado es el total definitivo, no un cargo adicional.</p>
      )}
    </div>
  )
}

function CancellationPreview({ record, values }) {
  if (record.balance === undefined || !values.treatment) return null
  const chargeKinds = ['Obligación', 'Ajuste', 'Ajuste de anulación']
  const currentCharges = (record.ledger || [])
    .filter((entry) => chargeKinds.includes(entry.kind))
    .reduce((sum, entry) => sum + Number(entry.amount), 0)
  const finalAmount = values.treatment === 'SIN_CARGO' ? 0 : Number(values.final_amount)
  if (!Number.isFinite(finalAmount)) return null
  const adjustment = finalAmount - currentCharges
  const resultingBalance = Number(record.balance) + adjustment
  return (
    <div className="notice" aria-live="polite">
      <strong>Vista previa del resultado</strong>
      <p>Cargo neto definitivo: {money(finalAmount, record.production.currency)}</p>
      <p>Ajuste de anulación: {money(adjustment, record.production.currency)}</p>
      {resultingBalance > 0 ? (
        <p>Saldo por cobrar: {money(resultingBalance, record.production.currency)}</p>
      ) : resultingBalance < 0 ? (
        <p>Pendiente de devolución: {money(-resultingBalance, record.production.currency)}</p>
      ) : (
        <p>
          Saldo resultante: {money(0, record.production.currency)} · sin cobro ni devolución
          pendientes.
        </p>
      )}
      <small>
        La vista previa no registra una devolución. El servidor vuelve a calcular el ajuste al
        guardar.
      </small>
    </div>
  )
}
