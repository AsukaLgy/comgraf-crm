import { cloneElement, useEffect, useRef, useState } from 'react'
import { X, AlertCircle, LoaderCircle, Inbox } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { api, uid } from './api'

export function Badge({ children, tone = '' }) {
  return <span className={'badge ' + tone}>{children}</span>
}
export function ErrorBox({ error }) {
  return error ? (
    <div role="alert" className="error">
      <AlertCircle size={16} />
      <span>{error.message || error}</span>
    </div>
  ) : null
}
export function Loading() {
  return (
    <div className="empty" role="status">
      <LoaderCircle className="spin" />
      Cargando…
    </div>
  )
}
export function Empty({ children = 'No hay registros en esta vista.' }) {
  return (
    <div className="empty">
      <Inbox size={30} />
      <p>{children}</p>
    </div>
  )
}
export function Field({ label, children, ...props }) {
  return (
    <label className={'field ' + (props.className || '')}>
      <span>{label}</span>
      {children ? (
        cloneElement(children, { 'aria-label': label })
      ) : (
        <input aria-label={label} {...props} />
      )}
    </label>
  )
}
export function Select({ label, options, empty, ...props }) {
  return (
    <Field label={label}>
      <select {...props}>
        {empty !== undefined && <option value="">{empty}</option>}
        {options.map((x) => (
          <option
            key={typeof x === 'object' ? x.value : x}
            value={typeof x === 'object' ? x.value : x}
          >
            {typeof x === 'object' ? x.label : x}
          </option>
        ))}
      </select>
    </Field>
  )
}
export function Panel({ title, action, children, className = '' }) {
  return (
    <section className={'panel ' + className}>
      <div className="panel-title">
        <h2>{title}</h2>
        {action}
      </div>
      <div className="panel-body">{children}</div>
    </section>
  )
}
export function Modal({ title, onClose, children, wide = false, busy = false }) {
  const ref = useRef()
  useEffect(() => {
    const prior = document.activeElement
    ref.current.showModal()
    return () => prior?.focus()
  }, [])
  return (
    <dialog
      ref={ref}
      className={wide ? 'wide' : ''}
      onCancel={(e) => {
        e.preventDefault()
        if (!busy) onClose()
      }}
    >
      <header>
        <h2>{title}</h2>
        <button
          type="button"
          className="icon-button"
          aria-label="Cerrar diálogo"
          onClick={onClose}
          disabled={busy}
        >
          <X size={20} />
        </button>
      </header>
      {children}
    </dialog>
  )
}
export function FormDialog({ title, initial, onClose, onSave, render, wide, submit = 'Guardar' }) {
  const [values, setValues] = useState(initial),
    [error, setError] = useState(null),
    [busy, setBusy] = useState(false)
  const locked = useRef(false)
  const change = (key, value) => {
    setValues((v) => ({ ...v, [key]: value }))
    setError(null)
  }
  async function save(e) {
    e.preventDefault()
    if (locked.current) return
    locked.current = true
    setBusy(true)
    setError(null)
    try {
      await onSave(values)
      onClose()
    } catch (err) {
      setError(err)
    } finally {
      locked.current = false
      setBusy(false)
    }
  }
  return (
    <Modal title={title} onClose={onClose} wide={wide} busy={busy}>
      <form onSubmit={save}>
        <div className="dialog-body">
          {render(values, change)}
          <ErrorBox error={error} />
        </div>
        <footer>
          <button type="button" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
          <button className="primary" disabled={busy}>
            {busy ? 'Guardando…' : submit}
          </button>
        </footer>
      </form>
    </Modal>
  )
}
export function Operation({
  record,
  path,
  title,
  method = 'POST',
  fields = [],
  initial = {},
  children,
  onClose,
  transform = (v) => v,
  submit = 'Guardar',
}) {
  const qc = useQueryClient(),
    key = useRef(uid()),
    lastPayload = useRef(null)
  const defaults = Object.fromEntries(
    fields.map((f) => [f.name, f.value ?? (f.type === 'checkbox' ? false : '')]),
  )
  async function save(values) {
    const body = transform(values),
      signature = JSON.stringify(body)
    if (lastPayload.current !== null && signature !== lastPayload.current) key.current = uid()
    lastPayload.current = signature
    try {
      const result = await api(`/cases/${record.id}/${path}`, {
        method,
        body: { ...body, version: record.version, key: key.current },
      })
      qc.setQueryData(['case', String(record.id)], result)
      await qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'case' })
    } catch (error) {
      if (error.status === 409)
        await qc.invalidateQueries({ queryKey: ['case', String(record.id)] })
      throw error
    }
  }
  return (
    <FormDialog
      title={title}
      initial={{ ...defaults, ...initial }}
      onClose={onClose}
      onSave={save}
      submit={submit}
      render={(v, set) => (
        <>
          {children}
          <div className="form-grid">
            {fields
              .filter((f) => !f.when || f.when(v))
              .map((f) =>
                f.options ? (
                  <Select
                    key={f.name}
                    label={f.label}
                    options={f.options}
                    empty={f.empty}
                    value={v[f.name]}
                    required={f.required}
                    onChange={(e) => set(f.name, e.target.value)}
                  />
                ) : f.type === 'textarea' ? (
                  <Field key={f.name} label={f.label} className="full">
                    <textarea
                      rows={3}
                      required={f.required}
                      value={v[f.name]}
                      onChange={(e) => set(f.name, e.target.value)}
                    />
                  </Field>
                ) : (
                  <Field
                    key={f.name}
                    label={f.label}
                    type={f.type || 'text'}
                    min={f.min}
                    step={f.step}
                    required={typeof f.required === 'function' ? f.required(v) : f.required}
                    value={f.type === 'checkbox' ? undefined : v[f.name]}
                    checked={f.type === 'checkbox' ? v[f.name] : undefined}
                    onChange={(e) =>
                      set(f.name, f.type === 'checkbox' ? e.target.checked : e.target.value)
                    }
                  />
                ),
              )}
          </div>
        </>
      )}
    />
  )
}
