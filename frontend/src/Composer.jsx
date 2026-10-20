import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, uid } from './api'
import { ErrorBox, Field, Select } from './components'

const CHANNELS = ['Nota', 'Correo', 'WhatsApp']
const LEGACY_DRAFT_CHANNELS = ['Llamada', 'Visita']
const TEMPLATES = {
  'Resumen de interacción': [
    'Solicitud del cliente',
    'Persona consultada',
    'Acuerdo',
    'Fecha acordada',
    'Próximo paso',
  ],
  'Seguimiento de recogida': [
    'Medio de contacto',
    'Respuesta',
    'Acuerdo de recogida',
    'Próximo seguimiento',
  ],
  'Aprobación o cambios': [
    'Cotización o arte revisado',
    'Decisión',
    'Cambios solicitados',
    'Próximo paso',
  ],
  'Incidencia interna': ['Situación', 'Gestión realizada', 'Resultado o pendiente'],
  'Anulación excepcional': [
    'Solicitud o situación',
    'Etapa alcanzada',
    'Trabajo realizado',
    'Acuerdo',
    'Pendientes',
  ],
}

export default function Composer({ record, user }) {
  const qc = useQueryClient()
  const emailDraft = record.drafts.find((draft) => draft.channel === 'Correo')
  const threadChoices = [...new Map((record.email_messages || []).filter((message) => message.thread_root).map((message) => [message.thread_root, message])).entries()]
  const defaultThread = threadChoices.at(-1)?.[0] || ''
  const threadOptions = threadChoices.map(([root, message]) => ({ value: root, label: `${message.subject || '(sin asunto)'} · ${message.sender || (message.to || []).join(', ')}` }))
  const [channel, setChannel] = useState('Nota')
  const [emailTo, setEmailTo] = useState(emailDraft?.email_to || record.client?.emails?.[0] || '')
  const [emailSubject, setEmailSubject] = useState(emailDraft?.email_subject || '')
  const [emailAttachmentIds, setEmailAttachmentIds] = useState(emailDraft?.email_attachment_ids || [])
  const [threadRoot, setThreadRoot] = useState(emailDraft?.email_thread_root || defaultThread)
  const [outgoingId, setOutgoingId] = useState(null)
  const [sentSnapshot, setSentSnapshot] = useState(null)
  const emailMeta = useRef({ email_to: emailDraft?.email_to || record.client?.emails?.[0] || '', email_subject: emailDraft?.email_subject || '', email_attachment_ids: emailDraft?.email_attachment_ids || [], email_thread_root: emailDraft?.email_thread_root || defaultThread })
  const [drafts, setDrafts] = useState(() =>
    Object.fromEntries(
      [...CHANNELS, ...LEGACY_DRAFT_CHANNELS].map((name) => {
        const server = record.drafts.find((draft) => draft.channel === name)
        const local = localStorage.getItem(`comgraf.draft.${user.id}.${record.id}.${name}`)
        return [name, local ?? server?.body ?? '']
      }),
    ),
  )
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState('')
  const [direction, setDirection] = useState('Saliente')
  const [template, setTemplate] = useState('')
  const [templateOpen, setTemplateOpen] = useState(false)
  const [templateValues, setTemplateValues] = useState({})
  const mailStatus = useQuery({ queryKey: ['mail-status'], queryFn: () => api('/mail/status'), enabled: channel === 'Correo', staleTime: 30000 })
  const outbox = useQuery({
    queryKey: ['mail-outbox', outgoingId],
    queryFn: () => api(`/mail/outbox/${outgoingId}`),
    enabled: Boolean(outgoingId),
    refetchInterval: (query) => ['Pendiente', 'Enviando'].includes(query.state.data?.status) ? 2000 : false,
  })
  const versions = useRef(
    Object.fromEntries(record.drafts.map((draft) => [draft.channel, draft.version])),
  )
  const key = useRef(uid())
  const acceptedHandled = useRef(null)
  const timer = useRef()
  const locked = useRef(false)
  const queue = useRef(Promise.resolve())
  const latest = useRef(drafts)
  const storageKey = (name) => `comgraf.draft.${user.id}.${record.id}.${name}`
  const emailSendStorageKey = `comgraf.email-send.${user.id}.${record.id}`

  useEffect(() => () => clearTimeout(timer.current), [])

  function persist(name, body) {
    queue.current = queue.current
      .catch(() => {})
      .then(async () => {
        const result = await api(`/cases/${record.id}/drafts/${name}`, {
          method: 'PUT',
          body: { body, version: versions.current[name] || 0, ...(name === 'Correo' ? emailMeta.current : {}) },
        })
        versions.current[name] = result.version
        setSaved('Borrador guardado')
      })
      .catch((cause) => {
        setError(cause)
        setSaved('Borrador conservado en este navegador')
      })
    return queue.current
  }

  function edit(text) {
    setDrafts((current) => ({ ...current, [channel]: text }))
    latest.current = { ...latest.current, [channel]: text }
    localStorage.setItem(storageKey(channel), text)
    if (channel === 'Correo') localStorage.removeItem(emailSendStorageKey)
    setSaved('Guardando borrador…')
    key.current = uid()
    clearTimeout(timer.current)
    timer.current = setTimeout(() => persist(channel, text), 650)
  }

  function editEmailMeta(field, value) {
    const next = { ...emailMeta.current, [field]: value }
    emailMeta.current = next
    localStorage.removeItem(emailSendStorageKey)
    if (field === 'email_to') setEmailTo(value)
    if (field === 'email_subject') setEmailSubject(value)
    if (field === 'email_attachment_ids') setEmailAttachmentIds(value)
    if (field === 'email_thread_root') setThreadRoot(value)
    setSaved('Guardando borrador…')
    clearTimeout(timer.current)
    timer.current = setTimeout(() => persist('Correo', latest.current.Correo), 650)
  }

  function switchChannel(next) {
    clearTimeout(timer.current)
    persist(channel, latest.current[channel])
    setChannel(next)
    setError(null)
    setSaved('')
    key.current = uid()
  }

  function addTemplate() {
    const text = (TEMPLATES[template] || [])
      .map((label) => [label, templateValues[label]?.trim()].filter(Boolean).join(': '))
      .filter(Boolean)
      .join('\n')
    if (!text) return
    const current = drafts.Nota.trim()
    const next = current ? `${current}\n\n${text}` : text
    const previousChannel = channel
    setChannel('Nota')
    setTemplateOpen(false)
    setTemplate('')
    setTemplateValues({})
    setDrafts((value) => ({ ...value, Nota: next }))
    latest.current = { ...latest.current, Nota: next }
    localStorage.setItem(storageKey('Nota'), next)
    setSaved('Borrador guardado localmente')
    if (previousChannel !== 'Nota') persist(previousChannel, latest.current[previousChannel])
    clearTimeout(timer.current)
    timer.current = setTimeout(() => persist('Nota', next), 650)
  }

  async function send(event) {
    event.preventDefault()
    if (locked.current) return
    locked.current = true
    setBusy(true)
    setError(null)
    clearTimeout(timer.current)
    try {
      await queue.current
      const body = drafts[channel].trim()
      if (!body) throw new Error('Escribe una nota o registra el contenido de la interacción.')
      if (channel === 'Correo') {
        if (!mailStatus.data?.smtp_ready) throw new Error('El envío SMTP del correo de la empresa no está disponible.')
        const recipients = emailTo.split(/[;,]/).map((value) => value.trim()).filter(Boolean)
        if (!recipients.length || !emailSubject.trim()) throw new Error('Indica destinatario y asunto antes de enviar.')
        await persist('Correo', body)
        const sendKey = localStorage.getItem(emailSendStorageKey) || uid()
        localStorage.setItem(emailSendStorageKey, sendKey)
        const queued = await api(`/cases/${record.id}/email/send`, {
          method: 'POST',
          body: { to: recipients, subject: emailSubject.trim(), body, attachment_ids: emailAttachmentIds, thread_root: threadRoot || null, send_key: sendKey },
        })
        setOutgoingId(queued.id)
        setSentSnapshot({ id: queued.id, body, recipients: emailTo, subject: emailSubject, attachments: emailAttachmentIds, thread: threadRoot })
        setSaved('Correo guardado en la bandeja de salida…')
        key.current = uid()
        return
      }
      const result = await api(`/cases/${record.id}/events`, {
        method: 'POST',
        body: {
          version: record.version,
          key: key.current,
          kind: channel === 'Nota' ? 'Nota interna' : 'Interacción manual',
          channel,
          body,
          direction: channel === 'Nota' ? 'Interna' : direction,
        },
      })
      qc.setQueryData(['case', String(record.id)], result)
      setDrafts((current) => ({ ...current, [channel]: '' }))
      latest.current = { ...latest.current, [channel]: '' }
      localStorage.removeItem(storageKey(channel))
      await persist(channel, '')
      key.current = uid()
      await qc.invalidateQueries({ queryKey: ['counts'] })
    } catch (cause) {
      setError(cause)
      if (cause.status === 409) qc.invalidateQueries({ queryKey: ['case', String(record.id)] })
    } finally {
      locked.current = false
      setBusy(false)
    }
  }

  useEffect(() => {
    if (outbox.data?.status !== 'Aceptado por Yahoo' || sentSnapshot?.id !== outbox.data.id || acceptedHandled.current === outbox.data.id) return
    acceptedHandled.current = outbox.data.id
    qc.invalidateQueries({ queryKey: ['case', String(record.id)] })
    qc.invalidateQueries({ queryKey: ['counts'] })
    qc.invalidateQueries({ queryKey: ['cases'] })
  }, [outbox.data?.status, outbox.data?.id, sentSnapshot, record.id, qc])

  function startAnotherEmail() {
    if (!sentSnapshot) return
    const unchanged = drafts.Correo === sentSnapshot.body && emailTo === sentSnapshot.recipients && emailSubject === sentSnapshot.subject && JSON.stringify(emailAttachmentIds) === JSON.stringify(sentSnapshot.attachments) && threadRoot === sentSnapshot.thread
    if (!unchanged) return
    setDrafts((current) => ({ ...current, Correo: '' }))
    latest.current = { ...latest.current, Correo: '' }
    localStorage.removeItem(storageKey('Correo'))
    emailMeta.current = { ...emailMeta.current, email_attachment_ids: [] }
    setEmailAttachmentIds([])
    localStorage.removeItem(emailSendStorageKey)
    persist('Correo', '')
    setSaved('El correo anterior fue aceptado por Yahoo. Borrador listo para un mensaje nuevo.')
  }

  async function retryEmail() {
    if (!outbox.data) return
    const uncertain = outbox.data.status === 'Resultado incierto'
    if (uncertain && !window.confirm('Yahoo pudo haber aceptado este mensaje. Revisa Enviados y el buzón antes de confirmar el reenvío. ¿Querés crear un nuevo envío?')) return
    setBusy(true)
    setError(null)
    try {
      const retry = await api(`/mail/outbox/${outbox.data.id}/retry`, { method: 'POST', body: { confirm_uncertain: uncertain } })
      setOutgoingId(retry.id)
      setSentSnapshot({ id: retry.id, body: retry.body_text, recipients: retry.to.join(', '), subject: retry.subject, attachments: retry.attachment_ids || [], thread: retry.thread_root || '' })
      setSaved('Reintento guardado en la bandeja de salida…')
    } catch (cause) { setError(cause) }
    finally { setBusy(false) }
  }

  return (
    <form className="composer" onSubmit={send}>
      <div className="tabs" role="tablist" aria-label="Canal de comunicación">
        {CHANNELS.map((name) => (
          <button
            role="tab"
            aria-selected={channel === name}
            key={name}
            type="button"
            disabled={busy}
            className={channel === name ? 'active' : ''}
            onClick={() => switchChannel(name)}
          >
            {name === 'Nota' ? 'Notas' : name}
          </button>
        ))}
      </div>
      {channel === 'WhatsApp' && (
        <div className="channel-status">
          WhatsApp no configurado · Guarda un borrador o registra una conversación realizada fuera
          de Comgraf. No se enviará desde aquí.
        </div>
      )}
      {channel === 'Correo' && <div className="email-compose-fields">
        {!mailStatus.isPending && !mailStatus.data?.smtp_ready && <div className="notice">El envío del correo de la empresa no está disponible. Un administrador puede revisarlo en <Link to="/settings">Configuración → Correo de la empresa</Link>.</div>}
        {mailStatus.data?.smtp_ready && <div className="notice">Enviando desde {mailStatus.data.email}. Yahoo acepta el mensaje antes de que aparezca como enviado; esto no confirma entrega ni lectura.{mailStatus.data.configured ? '' : ' La recepción IMAP presenta un problema.'}</div>}
        <Field label="Destinatario" type="text" autoComplete="email" placeholder="cliente@empresa.com" required disabled={busy} value={emailTo} onChange={(event) => editEmailMeta('email_to', event.target.value)}/>
        <Field label="Asunto" value={emailSubject} required disabled={busy} onChange={(event) => editEmailMeta('email_subject', event.target.value)}/>
        {threadOptions.length > 0 && <Select label="Hilo de correo" options={threadOptions} empty="Iniciar un hilo nuevo" disabled={busy} value={threadRoot} onChange={(event) => editEmailMeta('email_thread_root', event.target.value)}/>}
        {record.attachments?.length > 0 && <fieldset className="email-attachment-picker"><legend>Adjuntar archivos de esta solicitud</legend>{record.attachments.map((attachment) => <label className="check-label" key={attachment.id}><input type="checkbox" disabled={busy} checked={emailAttachmentIds.includes(attachment.id)} onChange={(event) => editEmailMeta('email_attachment_ids', event.target.checked ? [...emailAttachmentIds, attachment.id] : emailAttachmentIds.filter((id) => id !== attachment.id))}/>{attachment.name} · {(attachment.size / 1024 / 1024).toFixed(1)} MB</label>)}</fieldset>}
      </div>}
      {channel === 'Nota' && (
        <div className="template-tools">
          <button
            type="button"
            className="text-button"
            onClick={() => setTemplateOpen((value) => !value)}
          >
            Usar plantilla
          </button>
          {templateOpen && (
            <div className="notice">
              <Select
                label="Plantilla"
                options={Object.keys(TEMPLATES)}
                empty="Seleccionar plantilla"
                value={template}
                onChange={(event) => setTemplate(event.target.value)}
              />
              {template && (
                <div className="form-grid">
                  {TEMPLATES[template].map((label) => (
                    <Field
                      key={label}
                      label={label}
                      value={templateValues[label] || ''}
                      onChange={(event) =>
                        setTemplateValues((values) => ({ ...values, [label]: event.target.value }))
                      }
                    />
                  ))}
                </div>
              )}
              <button
                type="button"
                disabled={!template || !Object.values(templateValues).some((value) => value.trim())}
                onClick={addTemplate}
              >
                Añadir a la nota
              </button>
            </div>
          )}
        </div>
      )}
      <textarea
        aria-label={channel === 'Nota' ? 'Nota interna' : 'Registro de interacción'}
        placeholder={
          channel === 'Nota'
            ? 'Escribe una nota para el equipo…'
            : channel === 'Correo' ? 'Escribe el correo para el cliente…' : 'Registra lo que se conversó con el cliente…'
        }
        rows={4}
        required
        value={drafts[channel]}
        onChange={(event) => edit(event.target.value)}
        disabled={busy}
      />
      <div className="composer-footer">
        <small role="status">
          {saved ||
            (channel === 'Nota' ? 'Solo visible para el equipo' : 'Registro manual; no se envía')}
        </small>
        {channel === 'WhatsApp' && (
          <Select
            label="Dirección registrada"
            options={['Saliente', 'Entrante']}
            value={direction}
            onChange={(event) => {
              setDirection(event.target.value)
              key.current = uid()
            }}
          />
        )}
        {channel === 'Correo' ? <>
          <button type="button" disabled={busy} onClick={() => persist('Correo', drafts.Correo)}>Guardar borrador</button>
          <button className="primary" disabled={busy || !drafts.Correo.trim() || !mailStatus.data?.smtp_ready || !emailTo.trim() || !emailSubject.trim()}>{busy ? 'Preparando envío…' : 'Enviar correo'}</button>
        </> : <button className="primary" disabled={busy || !drafts[channel].trim()}>{busy ? 'Guardando…' : channel === 'Nota' ? 'Guardar nota' : 'Registrar interacción manual'}</button>}
      </div>
      {outbox.data && <div className={`mail-send-result ${outbox.data.status === 'Error' || outbox.data.status === 'Resultado incierto' ? 'warning' : ''}`} role="status">
        <strong>{outbox.data.status}</strong>
        {outbox.data.status === 'Aceptado por Yahoo' && <p>Yahoo aceptó el mensaje. No hay evidencia de que el destinatario lo haya recibido o leído. Copia en Enviados: {outbox.data.sent_copy_status || 'pendiente'}.</p>}
        {outbox.data.status === 'Aceptado por Yahoo' && <button type="button" onClick={startAnotherEmail}>Iniciar otro correo</button>}
        {outbox.data.status === 'Resultado incierto' && <p>La conexión se interrumpió durante el envío. Revisa la carpeta Enviados y el buzón antes de decidir reenviar.</p>}
        {outbox.data.status === 'Error' && <p>No se confirmó el envío. El borrador y sus adjuntos permanecen guardados.</p>}
        {outbox.data.status === 'Pausado' && <p>El envío quedó pausado al desconectar o cambiar de cuenta. Se debe reconectar la misma cuenta antes de reintentar.</p>}
        {['Error', 'Resultado incierto', 'Pausado'].includes(outbox.data.status) && <button type="button" onClick={retryEmail} disabled={busy}>Revisar y crear reintento</button>}
      </div>}
      <ErrorBox error={error} />
    </form>
  )
}
