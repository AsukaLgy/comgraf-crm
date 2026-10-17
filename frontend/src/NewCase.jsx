import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, can } from './api'
import { ErrorBox, Field, FormDialog, Select } from './components'
import { CASE_PRIORITIES } from './domain'

const CASE_KINDS = ['Consulta', 'Cotización', 'Solicitud', 'Reclamo', 'Seguimiento']
const ORIGIN_CHANNELS = ['Presencial', 'Llamada', 'Correo', 'WhatsApp', 'Otro']

export default function NewCase({ onClose, clientId, client: initialClient, relatedId, user }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const prefill = initialClient || null

  function selectClient(setValue, client) {
    setValue('client_id', client.id)
    setValue('contact_id', '')
    queryClient.invalidateQueries({ queryKey: ['clients-search'] })
  }

  return (
    <FormDialog
      title="Nuevo caso de atención"
      onClose={onClose}
      initial={{
        client_id: clientId || '',
        contact_id: '',
        related_case_id: relatedId || '',
        subject: '',
        description: '',
        kind: 'Consulta',
        channel: 'Presencial',
        priority: 'Normal',
      }}
      onSave={async (values) => {
        if (!values.client_id) throw new Error('Selecciona o crea un cliente antes de guardar.')

        const record = await api('/cases', {
          method: 'POST',
          body: {
            subject: values.subject,
            description: values.description,
            kind: values.kind,
            channel: values.channel,
            priority: values.priority,
            client_id: Number(values.client_id),
            contact_id: values.contact_id ? Number(values.contact_id) : null,
            related_case_id: values.related_case_id ? Number(values.related_case_id) : null,
          },
        })

        queryClient.invalidateQueries()
        navigate(`/cases/${record.id}`)
      }}
      render={(values, setValue) => (
        <>
          <div className="form-grid">
            <ClientAutocomplete
              user={user}
              selectedId={values.client_id}
              initialClient={prefill}
              onSelect={(client) => selectClient(setValue, client)}
              onClear={() => {
                setValue('client_id', '')
                setValue('contact_id', '')
              }}
            />
            <ContactAutocomplete
              key={values.client_id || 'none'}
              user={user}
              clientId={values.client_id}
              value={values.contact_id}
              onChange={(value) => setValue('contact_id', value)}
            />
            <Field
              label="Asunto"
              className="full"
              required
              maxLength={240}
              value={values.subject}
              onChange={(event) => setValue('subject', event.target.value)}
            />
            <Select
              label="Tipo de atención"
              options={CASE_KINDS}
              value={values.kind}
              onChange={(event) => setValue('kind', event.target.value)}
            />
            <Select
              label="Canal de origen"
              options={ORIGIN_CHANNELS}
              value={values.channel}
              onChange={(event) => setValue('channel', event.target.value)}
            />
            <Select
              label="Prioridad"
              options={CASE_PRIORITIES}
              value={values.priority}
              onChange={(event) => setValue('priority', event.target.value)}
            />
            <Field
              label="Caso relacionado (ID opcional)"
              type="number"
              min="1"
              value={values.related_case_id}
              onChange={(event) => setValue('related_case_id', event.target.value)}
            />
            <Field label="Descripción" className="full">
              <textarea
                rows={4}
                value={values.description}
                onChange={(event) => setValue('description', event.target.value)}
              />
            </Field>
          </div>
          <p className="muted">
            El caso entrará al pool sin asignar. Puede cerrarse sin producir ningún trabajo.
          </p>
        </>
      )}
    />
  )
}

function ClientAutocomplete({ user, selectedId, initialClient, onSelect, onClear }) {
  const [text, setText] = useState(
    initialClient?.id === Number(selectedId) ? initialClient.name : '',
  )
  const [term, setTerm] = useState('')
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const [createOpen, setCreateOpen] = useState(false)
  const [draft, setDraft] = useState({ name: '', company: '', phone: '', email: '' })
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState(null)
  const createLock = useRef(false)
  const queryClient = useQueryClient()
  const search = useQuery({
    queryKey: ['new-case-client-search', term],
    queryFn: () => api(`/clients?q=${encodeURIComponent(term)}`),
    enabled: term.length > 0,
  })

  useEffect(() => {
    if (!selectedId && text.trim()) {
      const timer = setTimeout(() => setTerm(text.trim()), 220)
      return () => clearTimeout(timer)
    }
  }, [text, selectedId])

  const results = term === text.trim() ? search.data || [] : []

  async function createClient() {
    if (createLock.current) return
    createLock.current = true
    setCreating(true)
    setCreateError(null)

    try {
      const client = await api('/clients', {
        method: 'POST',
        body: {
          name: draft.name.trim(),
          company: draft.company.trim(),
          phones: draft.phone.trim() ? [draft.phone.trim()] : [],
          emails: draft.email.trim() ? [draft.email.trim()] : [],
          addresses: [],
          observations: '',
          provisional: true,
        },
      })
      onSelect(client)
      setText(client.name)
      setCreateOpen(false)
      await queryClient.invalidateQueries({ queryKey: ['new-case-client-search'] })
    } catch (error) {
      setCreateError(error)
    } finally {
      createLock.current = false
      setCreating(false)
    }
  }

  function selectResult(client) {
    onSelect(client)
    setText(client.name)
    setOpen(false)
  }

  function onKeyDown(event) {
    if (event.key === 'Escape') {
      setOpen(false)
      return
    }
    if (event.key === 'ArrowDown' && results.length) {
      event.preventDefault()
      setOpen(true)
      setActiveIndex((index) => Math.min(index + 1, results.length - 1))
      return
    }
    if (event.key === 'ArrowUp' && results.length) {
      event.preventDefault()
      setActiveIndex((index) => Math.max(index - 1, 0))
      return
    }
    if (event.key === 'Enter' && open && results[activeIndex]) {
      event.preventDefault()
      selectResult(results[activeIndex])
    }
  }

  return (
    <div className="field autocomplete">
      <label htmlFor="case-client">Cliente</label>
      <input
        id="case-client"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls="client-suggestions"
        autoComplete="off"
        value={text}
        placeholder="Buscar nombre, empresa, RTN, teléfono o correo"
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        onChange={(event) => {
          setText(event.target.value)
          onClear()
          setOpen(true)
          setActiveIndex(0)
        }}
      />
      <ErrorBox error={search.error || createError} />
      {open && (
        <div className="suggestion-list" id="client-suggestions" role="listbox">
          {selectedId && <p role="status">Cliente seleccionado</p>}
          {!selectedId && text.trim() && search.isFetching && (
            <p role="status">Buscando clientes…</p>
          )}
          {!selectedId &&
            term === text.trim() &&
            text.trim() &&
            !search.isFetching &&
            !results.length && <p role="status">Sin coincidencias.</p>}
          {!selectedId &&
            results.map((client, index) => (
              <button
                type="button"
                role="option"
                aria-selected={index === activeIndex}
                key={client.id}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => selectResult(client)}
              >
                <strong>{client.name}</strong>
                <small>{clientDetails(client)}</small>
              </button>
            ))}
          {!selectedId && can(user, 'clients') && text.trim() && (
            <button
              type="button"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                setDraft((current) => ({ ...current, name: text.trim() }))
                setCreateOpen(true)
                setOpen(false)
              }}
            >
              ＋ Crear cliente “{text.trim()}”
            </button>
          )}
        </div>
      )}
      {createOpen && (
        <div className="inline-create">
          <Field
            label="Nombre"
            required
            value={draft.name}
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
          />
          <Field
            label="Empresa (opcional)"
            value={draft.company}
            onChange={(event) => setDraft({ ...draft, company: event.target.value })}
          />
          <Field
            label="Teléfono (opcional)"
            value={draft.phone}
            onChange={(event) => setDraft({ ...draft, phone: event.target.value })}
          />
          <Field
            label="Correo (opcional)"
            value={draft.email}
            onChange={(event) => setDraft({ ...draft, email: event.target.value })}
          />
          <button type="button" disabled={creating || !draft.name.trim()} onClick={createClient}>
            {creating ? 'Creando…' : 'Crear y seleccionar cliente'}
          </button>
          <button type="button" onClick={() => setCreateOpen(false)}>
            Cancelar
          </button>
        </div>
      )}
    </div>
  )
}

function ContactAutocomplete({ user, clientId, value, onChange }) {
  const [text, setText] = useState('')
  const [term, setTerm] = useState('')
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const [createOpen, setCreateOpen] = useState(false)
  const [name, setName] = useState('')
  const [createError, setCreateError] = useState(null)
  const [creating, setCreating] = useState(false)
  const createLock = useRef(false)
  const contacts = useQuery({
    queryKey: ['client-contacts', clientId],
    queryFn: () => api(`/clients/${clientId}/contacts`),
    enabled: !!clientId,
  })

  useEffect(() => {
    const timer = setTimeout(() => setTerm(text.trim().toLocaleLowerCase()), 180)
    return () => clearTimeout(timer)
  }, [text])

  const results = (contacts.data || []).filter((contact) =>
    `${contact.name} ${contact.phone} ${contact.email}`.toLocaleLowerCase().includes(term),
  )
  const selectedContact = contacts.data?.find((contact) => String(contact.id) === String(value))

  function selectContact(contact) {
    onChange(String(contact.id))
    setText(contact.name)
    setOpen(false)
  }

  function onKeyDown(event) {
    if (event.key === 'Escape') {
      setOpen(false)
      return
    }
    if (event.key === 'ArrowDown' && results.length) {
      event.preventDefault()
      setActiveIndex((index) => Math.min(index + 1, results.length - 1))
      return
    }
    if (event.key === 'ArrowUp' && results.length) {
      event.preventDefault()
      setActiveIndex((index) => Math.max(index - 1, 0))
      return
    }
    if (event.key === 'Enter' && open && results[activeIndex]) {
      event.preventDefault()
      selectContact(results[activeIndex])
    }
  }

  async function createContact() {
    if (createLock.current || !name.trim()) return
    createLock.current = true
    setCreating(true)
    setCreateError(null)
    try {
      const contact = await api(`/clients/${clientId}/contacts`, {
        method: 'POST',
        body: { name: name.trim() },
      })
      selectContact(contact)
      setCreateOpen(false)
      await contacts.refetch()
    } catch (error) {
      setCreateError(error)
    } finally {
      createLock.current = false
      setCreating(false)
    }
  }

  return (
    <div className="field autocomplete">
      <label htmlFor="case-contact">Contacto (opcional)</label>
      <input
        id="case-contact"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        autoComplete="off"
        disabled={!clientId}
        value={text || selectedContact?.name || ''}
        placeholder={clientId ? 'Buscar contacto…' : 'Selecciona un cliente primero'}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        onChange={(event) => {
          setText(event.target.value)
          onChange('')
          setOpen(true)
          setActiveIndex(0)
        }}
      />
      {value && (
        <button
          type="button"
          className="text-button"
          onClick={() => {
            setText('')
            onChange('')
          }}
        >
          Quitar contacto
        </button>
      )}
      <ErrorBox error={contacts.error || createError} />
      {open && clientId && (
        <div className="suggestion-list" role="listbox">
          <button
            type="button"
            role="option"
            onClick={() => {
              setText('')
              onChange('')
              setOpen(false)
            }}
          >
            Sin contacto específico
          </button>
          {contacts.isPending ? (
            <p role="status">Cargando contactos…</p>
          ) : (
            results.map((contact, index) => (
              <button
                type="button"
                role="option"
                aria-selected={index === activeIndex}
                key={contact.id}
                onClick={() => selectContact(contact)}
              >
                <strong>{contact.name}</strong>
                <small>
                  {[contact.position, contact.phone, contact.email].filter(Boolean).join(' · ')}
                </small>
              </button>
            ))
          )}
          {!results.length && !contacts.isPending && (
            <p role="status">No hay contactos coincidentes.</p>
          )}
          {can(user, 'clients') && (
            <button
              type="button"
              onClick={() => {
                setName(text.trim())
                setCreateOpen(true)
                setOpen(false)
              }}
            >
              ＋ Crear contacto
            </button>
          )}
        </div>
      )}
      {createOpen && (
        <div className="inline-create">
          <Field
            label="Nombre del contacto"
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <button type="button" disabled={creating || !name.trim()} onClick={createContact}>
            {creating ? 'Creando…' : 'Crear y seleccionar'}
          </button>
          <button type="button" onClick={() => setCreateOpen(false)}>
            Cancelar
          </button>
        </div>
      )}
    </div>
  )
}

function clientDetails(client) {
  return [
    client.company,
    client.rtn && `RTN ${client.rtn}`,
    client.phones?.join(', '),
    client.emails?.join(', '),
  ]
    .filter(Boolean)
    .join(' · ')
}
