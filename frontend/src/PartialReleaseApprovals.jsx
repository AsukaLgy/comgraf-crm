import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, date, uid } from './api'
import { Badge, Empty, ErrorBox, Field, FormDialog, Loading, Panel } from './components'

export default function PartialReleaseApprovals() {
  const qc = useQueryClient()
  const [decision, setDecision] = useState(null)
  const [message, setMessage] = useState('')
  const query = useQuery({ queryKey: ['partial-release-requests'], queryFn: () => api('/scanner/exceptions') })

  async function submit(values) {
    const result = await api(`/scanner/exceptions/${decision.request.id}/decision`, {
      method: 'POST', body: { approve: decision.approve, comment: values.comment, request_key: uid() },
    })
    setDecision(null)
    setMessage(result.message)
    await qc.invalidateQueries({ queryKey: ['partial-release-requests'] })
    await qc.invalidateQueries()
  }

  return <>
    <div className="page-heading"><div><div className="eyebrow">CONTROL DE ENCUADERNACIÓN</div><h1>Liberaciones parciales</h1><p>Revisa la orden concreta, las órdenes pendientes y el motivo antes de decidir.</p></div></div>
    {message && <p className="notice" role="status">{message}</p>}
    <ErrorBox error={query.error}/>
    {query.isPending ? <Loading/> : query.data?.length ? query.data.map((request) => <Panel key={request.id} title={`${request.order_number} · Solicitud ${request.case_number}`} action={<Badge tone="amber">Pendiente</Badge>}>
      <h3>{request.subject}</h3>
      <p><strong>Solicitó:</strong> {request.requester} · {date(request.created_at)}</p>
      <p><strong>Motivo:</strong> {request.request_comment}</p>
      <p><strong>Etapa actual:</strong> {request.stage_label} · Ciclo {request.production_cycle}</p>
      {request.pending_orders.length > 0 && <><strong>Órdenes pendientes del grupo</strong><ul className="scanner-missing">{request.pending_orders.map((order) => <li key={order.production_id}>{order.order_number} · {order.stage_label} · {order.reason}</li>)}</ul></>}
      <div className="actions"><button onClick={() => setDecision({ request, approve: false })}>Rechazar</button><button className="primary" onClick={() => setDecision({ request, approve: true })}>Aprobar liberación de esta orden</button></div>
    </Panel>) : <Panel title="Sin solicitudes pendientes"><Empty>Las excepciones de liberación parcial que solicite el escáner aparecerán aquí.</Empty></Panel>}
    {decision && <FormDialog title={`${decision.approve ? 'Autorizar' : 'Rechazar'} ${decision.request.order_number}`} initial={{ comment: '' }} onClose={() => setDecision(null)} onSave={submit} render={(values, set) => <>
      <p>{decision.request.order_number} · Solicitud {decision.request.case_number}</p>
      <p><strong>Motivo del solicitante:</strong> {decision.request.request_comment}</p>
      <Field label={decision.approve ? 'Justificación obligatoria de aprobación' : 'Motivo del rechazo'} type="textarea" required maxLength={1000} value={values.comment} onChange={(event) => set('comment', event.target.value)}/>
    </>}/>}
  </>
}
