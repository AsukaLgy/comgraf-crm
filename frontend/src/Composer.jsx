import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
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
  const [channel, setChannel] = useState('Nota')
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
  const versions = useRef(
    Object.fromEntries(record.drafts.map((draft) => [draft.channel, draft.version])),
  )
  const key = useRef(uid())
  const timer = useRef()
  const locked = useRef(false)
  const queue = useRef(Promise.resolve())
  const latest = useRef(drafts)
  const storageKey = (name) => `comgraf.draft.${user.id}.${record.id}.${name}`

  useEffect(() => () => clearTimeout(timer.current), [])

  function persist(name, body) {
    queue.current = queue.current
      .catch(() => {})
      .then(async () => {
        const result = await api(`/cases/${record.id}/drafts/${name}`, {
          method: 'PUT',
          body: { body, version: versions.current[name] || 0 },
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
    setSaved('Guardando borrador…')
    key.current = uid()
    clearTimeout(timer.current)
    timer.current = setTimeout(() => persist(channel, text), 650)
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
      {['Correo', 'WhatsApp'].includes(channel) && (
        <div className="channel-status">
          {channel} no configurado · Guarda un borrador o registra una conversación realizada fuera
          de Comgraf. No se enviará desde aquí.
        </div>
      )}
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
            : 'Registra lo que se conversó con el cliente…'
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
        {channel !== 'Nota' && (
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
        <button className="primary" disabled={busy || !drafts[channel].trim()}>
          {busy
            ? 'Guardando…'
            : channel === 'Nota'
              ? 'Guardar nota'
              : 'Registrar interacción manual'}
        </button>
      </div>
      <ErrorBox error={error} />
    </form>
  )
}
