import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { Badge, Empty, ErrorBox, Loading, Panel, Select } from './components'

export default function MailInbox() {
  const qc = useQueryClient()
  const pending = useQuery({ queryKey: ['mail-pending'], queryFn: () => api('/mail/pending'), refetchInterval: 15000 })
  const overview = useQuery({ queryKey: ['mail-overview'], queryFn: () => api('/mail/overview'), refetchInterval: 15000 })
  const issues = useQuery({ queryKey: ['mail-issues'], queryFn: () => api('/mail/issues'), refetchInterval: 30000 })
  const [selection, setSelection] = useState({})
  const [busyId, setBusyId] = useState(null)
  const [error, setError] = useState(null)
  async function classify(message, action) {
    const choice = selection[message.id] || {}
    setBusyId(message.id)
    setError(null)
    try {
      await api(`/mail/pending/${message.id}/classify`, {
        method: 'POST', body: { action, case_id: Number(choice.case_id) || null, client_id: Number(choice.client_id) || null },
      })
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['mail-pending'] }),
        qc.invalidateQueries({ queryKey: ['mail-overview'] }),
        qc.invalidateQueries({ queryKey: ['counts'] }),
      ])
    } catch (cause) {
      setError(cause)
    } finally {
      setBusyId(null)
    }
  }
  return <>
    <div className="page-heading"><div><div className="eyebrow">COMUNICACIÓN ENTRANTE</div><h1>Correos por clasificar</h1><p>Los mensajes recibidos se guardan antes de asociarlos a una solicitud.</p></div><Badge>{overview.data?.unclassified ?? pending.data?.length ?? 0} por clasificar</Badge></div>
    <div className="metrics mail-overview">
      {[['Recibidos vinculados', overview.data?.received], ['Por clasificar', overview.data?.unclassified], ['Ignorados', overview.data?.ignored], ['Errores de recepción', overview.data?.errors]].map(([label, count]) => <div className="metric" key={label}><span>{label}</span><strong>{count ?? '—'}</strong></div>)}
    </div>
    <Panel title="Bandeja de clasificación">
      <p>Las respuestas a solicitudes completadas, referencias dudosas y remitentes ambiguos esperan aquí. Los errores de análisis se identifican para revisión y no detienen los mensajes siguientes.</p>
      <ErrorBox error={pending.error || overview.error || error}/>
      {pending.isPending ? <Loading/> : pending.data?.length ? pending.data.map((message) => <article className="mail-review" key={message.id}>
        <strong>{message.subject || '(sin asunto)'}</strong><p>{message.sender} · <Badge tone={message.status === 'Error de análisis' ? 'red' : ''}>{message.status === 'Error de análisis' ? 'Con error' : 'Por clasificar'}</Badge></p><pre>{message.body_text}</pre>
        {message.attachments?.map((attachment) => <a key={attachment.id} href={`/api/mail/attachments/${attachment.id}`}>{attachment.filename}</a>)}
        <div className="form-grid">
          {message.clients?.length > 0 && <Select label="Cliente" options={message.clients.map((client) => ({ value: client.id, label: `${client.name}${client.company ? ` · ${client.company}` : ''}` }))} value={selection[message.id]?.client_id || ''} onChange={(event) => setSelection((current) => ({ ...current, [message.id]: { ...current[message.id], client_id: event.target.value } }))}/>}
          {message.candidate_cases?.length > 0 && <Select label="Solicitud relacionada" empty="Seleccionar solicitud" options={message.candidate_cases.map((entry) => ({ value: entry.id, label: `${entry.number} · ${entry.subject} · ${entry.lifecycle}` }))} value={selection[message.id]?.case_id || message.candidate_case_id || ''} onChange={(event) => setSelection((current) => ({ ...current, [message.id]: { ...current[message.id], case_id: event.target.value } }))}/>}
        </div>
        <div className="form-actions">
          {message.status !== 'Error de análisis' && <>
            {message.clients?.length > 0 && <button disabled={busyId === message.id || !selection[message.id]?.client_id} onClick={() => classify(message, 'new')}>Crear solicitud para cliente</button>}
            {(message.candidate_cases?.length > 0 || message.candidate_case_id) && <>
              <button disabled={busyId === message.id} onClick={() => classify(message, 'link')}>Asociar a solicitud activa</button>
              <button disabled={busyId === message.id} onClick={() => classify(message, 'reactivate')}>Reactivar y asociar</button>
              <button disabled={busyId === message.id} onClick={() => classify(message, 'related')}>Crear solicitud relacionada</button>
            </>}
          </>}
          <button disabled={busyId === message.id} onClick={() => classify(message, 'ignore')}>Ignorar este correo</button>
        </div>
      </article>) : <Empty>No hay correos por clasificar.</Empty>}
    </Panel>
    {(issues.data?.length > 0 || issues.error) && <Panel title="Recepción pendiente de recuperar">
      <p>Estos UID quedaron registrados para reintento o revisión; el cursor no se reinicia y los mensajes posteriores continúan su procesamiento.</p>
      <ErrorBox error={issues.error}/>
      {issues.data?.map((item) => <div className="kpi-row" key={`${item.uidvalidity}-${item.uid}`}><strong>UID {item.uid}</strong><span>{item.state === 'retry' ? 'Error recuperable' : 'Revisión manual'} · {item.reason} · intentos {item.attempts}</span><time>{new Date(item.last_attempt_at).toLocaleString('es-HN')}</time></div>)}
    </Panel>}
  </>
}
