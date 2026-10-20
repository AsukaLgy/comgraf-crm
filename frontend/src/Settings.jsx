import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, can } from './api'
import { Badge, ErrorBox, Field, FormDialog, Loading, Panel, Select } from './components'

const ROLES = ['Administrador', 'Coordinador', 'Atención', 'Producción', 'Cobranza', 'Escáner de encuadernación']

export default function Settings({ user }) {
  const qc = useQueryClient()
  const [modal, setModal] = useState(null)
  const users = useQuery({ queryKey: ['users'], queryFn: () => api('/users'), enabled: can(user, '*') })
  const roles = useQuery({ queryKey: ['roles'], queryFn: () => api('/roles'), enabled: can(user, '*') })

  function updateUserRoles(value, set, role, checked) {
    if (role === 'Escáner de encuadernación' && checked) return set('roles', [role])
    if (value.roles.includes(role) && role === 'Escáner de encuadernación' && !checked) return set('roles', ['Atención'])
    if (value.roles.includes('Escáner de encuadernación')) return set('roles', [role])
    set('roles', checked ? [...value.roles, role] : value.roles.filter((item) => item !== role))
  }

  return <>
    <div className="page-heading"><div><div className="eyebrow">ADMINISTRACIÓN LOCAL</div><h1>Configuración</h1><p>Equipo, permisos y canales de comunicación.</p></div></div>
    <Panel title="Mi cuenta" action={<button onClick={() => setModal({ type: 'password' })}>Cambiar contraseña</button>}><p>{user.name} · {user.roles.join(', ')}</p></Panel>
    {can(user, '*') && <>
      <Panel title="Usuarios y roles" action={<button className="primary" onClick={() => setModal({ type: 'user' })}>Crear usuario</button>}>
        <ErrorBox error={users.error}/>
        {users.isPending ? <Loading/> : <div className="table-wrap"><table><thead><tr><th>Nombre</th><th>Usuario</th><th>Roles</th><th>Capacidad</th><th>Estado</th><th/></tr></thead><tbody>{users.data?.map((entry) => <tr key={entry.id}><td>{entry.name}</td><td>{entry.username}</td><td>{entry.roles.join(', ')}</td><td>{entry.capacity} solicitudes</td><td><Badge tone={entry.active ? 'teal' : ''}>{entry.active ? 'Activo' : 'Desactivado'}</Badge></td><td><button onClick={() => setModal({ type: 'user', user: entry })}>Editar</button></td></tr>)}</tbody></table></div>}
      </Panel>
      <Panel title="Permisos por rol"><p className="muted">El rol de escaneo es exclusivo. Sus cuentas solo pueden escanear, consultar el resultado y solicitar excepciones.</p><ErrorBox error={roles.error}/>{roles.data?.roles.map((role) => <div className="role-row" key={role.name}><strong>{role.name}</strong><span>{role.permissions.join(', ')}</span>{role.name !== 'Administrador' && role.name !== 'Escáner de encuadernación' && <button onClick={() => setModal({ type: 'role', role })}>Configurar</button>}</div>)}</Panel>
    </>}
    {can(user, '*') && <MailCompanyConfig user={user}/>}
    <Panel title="WhatsApp"><h3>WhatsApp <Badge>No configurado</Badge></h3><p>Se conservan los borradores y el registro manual. Esta conexión todavía no envía mensajes.</p></Panel>
    {(can(user, 'attention') || can(user, '*')) && <PendingEmailClassification user={user}/>}
    <Panel title="Instalación"><p>Zona horaria: <strong>{user.timezone}</strong></p><p>Datos compartidos en MySQL. Adjuntos almacenados en el servidor. Los respaldos deben incluir ambos.</p></Panel>

    {modal?.type === 'user' && <FormDialog title={modal.user ? 'Editar usuario' : 'Crear usuario'} initial={modal.user ? { name: modal.user.name, roles: modal.user.roles, active: modal.user.active, capacity: modal.user.capacity, password: '' } : { username: '', name: '', password: '', roles: ['Atención'], capacity: 20 }} onClose={() => setModal(null)} onSave={async (value) => { const body = { ...value, capacity: Number(value.capacity) }; if (modal.user && !body.password) body.password = null; await api('/users' + (modal.user ? '/' + modal.user.id : ''), { method: modal.user ? 'PUT' : 'POST', body }); qc.invalidateQueries({ queryKey: ['users'] }) }} render={(value, set) => <>
      <div className="form-grid">{!modal.user && <Field label="Usuario" required pattern="[a-zA-Z0-9_.-]+" minLength={3} value={value.username} onChange={(event) => set('username', event.target.value)}/>}<Field label="Nombre" required value={value.name} onChange={(event) => set('name', event.target.value)}/><Field label={modal.user ? 'Nueva contraseña (opcional)' : 'Contraseña inicial'} type="password" autoComplete="new-password" minLength={12} required={!modal.user} value={value.password} onChange={(event) => set('password', event.target.value)}/><Field label="Capacidad de solicitudes abiertas" type="number" min="1" max="500" required value={value.capacity} onChange={(event) => set('capacity', event.target.value)}/>{modal.user && <Field label="Cuenta activa" type="checkbox" checked={value.active} onChange={(event) => set('active', event.target.checked)}/>}</div>
      <fieldset><legend>Roles</legend>{ROLES.map((role) => <label className="check-label" key={role}><input type="checkbox" checked={value.roles.includes(role)} onChange={(event) => updateUserRoles(value, set, role, event.target.checked)}/>{role}</label>)}{value.roles.includes('Escáner de encuadernación') && <p className="muted">Esta cuenta no tendrá acceso a clientes, órdenes completas, cobros, entregas ni administración.</p>}</fieldset>
    </>}/>}
    {modal?.type === 'role' && <FormDialog title={'Permisos · ' + modal.role.name} initial={{ permissions: modal.role.permissions }} onClose={() => setModal(null)} onSave={async (value) => { await api('/roles/' + encodeURIComponent(modal.role.name), { method: 'PUT', body: value }); qc.invalidateQueries({ queryKey: ['roles'] }); qc.invalidateQueries({ queryKey: ['users'] }) }} render={(value, set) => <fieldset><legend>Capacidades del rol</legend>{roles.data.available.filter((permission) => permission !== '*').map((permission) => <label className="check-label" key={permission}><input type="checkbox" checked={value.permissions.includes(permission)} onChange={(event) => set('permissions', event.target.checked ? [...value.permissions, permission] : value.permissions.filter((item) => item !== permission))}/>{permission}</label>)}</fieldset>}/>}
    {modal?.type === 'password' && <FormDialog title="Cambiar contraseña" initial={{ current_password: '', new_password: '' }} onClose={() => setModal(null)} onSave={async (value) => { await api('/auth/password', { method: 'POST', body: value }); qc.clear(); location.assign('/') }} render={(value, set) => <><Field label="Contraseña actual" type="password" autoComplete="current-password" required value={value.current_password} onChange={(event) => set('current_password', event.target.value)}/><Field label="Nueva contraseña" type="password" autoComplete="new-password" minLength={12} required value={value.new_password} onChange={(event) => set('new_password', event.target.value)}/></>}/>}
  </>
}

function MailCompanyConfig({ user }) {
  const qc = useQueryClient()
  const mail = useQuery({ queryKey: ['mail-config'], queryFn: () => api('/mail/config'), refetchInterval: 30000 })
  const [email, setEmail] = useState(null)
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const emailValue = email ?? mail.data?.email ?? ''

  async function save(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const response = await api('/mail/config', {
        method: 'PUT',
        body: { email: emailValue.trim(), application_password: password || null },
      })
      setResult(response)
      setPassword('')
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['mail-config'] }),
        qc.invalidateQueries({ queryKey: ['mail-status'] }),
      ])
    } catch (cause) {
      setError(cause)
    } finally {
      setBusy(false)
    }
  }

  async function sendTest() {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      setResult({ test: await api('/mail/test', { method: 'POST', body: {} }) })
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['mail-config'] }),
        qc.invalidateQueries({ queryKey: ['mail-status'] }),
      ])
    } catch (cause) {
      setError(cause)
    } finally {
      setBusy(false)
    }
  }

  async function disconnect() {
    if (!window.confirm('¿Desconectar el buzón? Se detendrán la sincronización y los envíos pendientes; el historial se conservará.')) return
    setBusy(true)
    setError(null)
    try {
      await api('/mail/config', { method: 'DELETE' })
      setPassword('')
      setResult(null)
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['mail-config'] }),
        qc.invalidateQueries({ queryKey: ['mail-status'] }),
      ])
    } catch (cause) {
      setError(cause)
    } finally {
      setBusy(false)
    }
  }

  const state = mail.data
  const tested = result?.connection
  return <Panel title="Correo de la empresa" action={<Badge tone={state?.configured ? 'teal' : ''}>{state?.status_label || 'Sin comprobar'}</Badge>}>
    <p>Conecta el buzón Yahoo para recibir y enviar mensajes desde las solicitudes. La contraseña de aplicación se cifra en el servidor y no vuelve a mostrarse.</p>
    <form className="form-grid" onSubmit={save}>
      <Field label="Correo Yahoo" type="email" autoComplete="email" required value={emailValue} onChange={(event) => setEmail(event.target.value)}/>
      <div>
        <Field label="Contraseña de aplicación" type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)}/>
        <small className="muted">Usá una contraseña de aplicación generada en Yahoo para Comgraf. No ingresés tu contraseña habitual.</small>
        {state?.has_password && <small className="muted">Hay una contraseña guardada. Dejá este campo vacío para conservarla.</small>}
      </div>
      <div className="form-actions">
        <button className="primary" disabled={busy || !emailValue.trim() || (!password && !state?.has_password)}>{busy ? 'Comprobando…' : 'Guardar y comprobar conexión'}</button>
        <button type="button" disabled={busy || !state?.smtp_ready} onClick={sendTest}>Enviar prueba al mismo buzón</button>
        <button type="button" disabled={busy || !state?.configured} onClick={disconnect}>Desconectar</button>
      </div>
    </form>
    <ErrorBox error={error}/>
    {tested && <div className="notice" role="status">
      <strong>{result.saved ? 'Configuración guardada.' : 'No se reemplazó la configuración actual.'}</strong>
      <p>IMAP: {tested.imap.ok ? 'Conectado' : mailStatusLabel(tested.imap.status)} · SMTP: {tested.smtp.ok ? 'Conectado' : mailStatusLabel(tested.smtp.status)}</p>
      {!tested.imap.ok && tested.imap.operation && <p>Operación IMAP: {tested.imap.operation}{tested.imap.cause_type ? ` · ${tested.imap.cause_type}` : ''}</p>}
    </div>}
    {result?.test && <div className="notice" role="status">{result.test.sent ? `Prueba aceptada por Yahoo. Copia en Enviados: ${result.test.sent_copy_status}.` : `Falló SMTP: ${result.test.smtp_label}.`}</div>}
    {state?.smtp_ready && !state?.configured && <div className="notice">SMTP está disponible para enviar. La recepción IMAP presenta un problema; revisa su estado antes de depender de la bandeja de entrada.</div>}
    {(state?.configured || state?.has_password || state?.last_sync_at) && <div className="profile-grid">
      <p>IMAP: <strong>{state.imap_label}</strong></p><p>SMTP: <strong>{state.smtp_label}</strong></p>
      <p>Sincronizador: <strong>{state.worker_label || 'Estado desconocido'}</strong></p>
      {state.last_error_label && <p>Último diagnóstico: <strong>{state.last_error_label}</strong></p>}
      <p>Última sincronización correcta: <strong>{state.last_sync_at ? new Date(state.last_sync_at).toLocaleString('es-HN', { timeZone: user.timezone }) : 'Aún no realizada'}</strong></p>
      <p>Se consulta INBOX aproximadamente cada minuto. Los correos anteriores al momento de conexión no se importan.</p>
    </div>}
    {state?.worker_status === 'worker_stale' && <div className="notice warning" role="status">El buzón se puede autenticar, pero el sincronizador no registra una consulta correcta reciente. Revisa los registros del servicio Comgraf.</div>}
  </Panel>
}

function mailStatusLabel(code) {
  return ({
    authentication_error: 'Error de autenticación', connection_error: 'Error de conexión',
    dns_error: 'Error de DNS', timeout_error: 'Tiempo de conexión agotado',
    tls_error: 'Error de TLS/certificado', inbox_select_error: 'Error al abrir INBOX',
    inbox_query_error: 'Error al consultar INBOX', inbox_metadata_error: 'Error en metadatos de INBOX',
    message_fetch_error: 'Error al descargar mensaje', message_processing_error: 'Error al procesar mensaje',
    persistence_error: 'Error al guardar correo',
  })[code] || 'Error de conexión'
}

function PendingEmailClassification() {
  const qc = useQueryClient()
  const pending = useQuery({ queryKey: ['mail-pending'], queryFn: () => api('/mail/pending'), refetchInterval: 30000 })
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
      await qc.invalidateQueries({ queryKey: ['mail-pending'] })
      await qc.invalidateQueries({ queryKey: ['counts'] })
    } catch (cause) {
      setError(cause)
    } finally {
      setBusyId(null)
    }
  }
  return <Panel title="Correos por clasificar" action={<Badge>{pending.data?.length || 0}</Badge>}>
    <p>Los remitentes ambiguos, respuestas a solicitudes completadas y referencias dudosas esperan aquí antes de entrar a la bitácora.</p>
    <ErrorBox error={pending.error || error}/>
    {pending.data?.map((message) => <article className="mail-review" key={message.id}>
      <strong>{message.subject || '(sin asunto)'}</strong><p>{message.sender} · {message.status}</p><pre>{message.body_text}</pre>
      {message.attachments?.map((attachment) => <a key={attachment.id} href={`/api/mail/attachments/${attachment.id}`}>{attachment.filename}</a>)}
      <div className="form-grid">
        {message.clients?.length > 0 && <Select label="Cliente" options={message.clients.map((client) => ({ value: client.id, label: `${client.name}${client.company ? ` · ${client.company}` : ''}` }))} value={selection[message.id]?.client_id || ''} onChange={(event) => setSelection((current) => ({ ...current, [message.id]: { ...current[message.id], client_id: event.target.value } }))}/>}
        {message.candidate_cases?.length > 0 && <Select label="Solicitud relacionada" empty="Seleccionar solicitud" options={message.candidate_cases.map((entry) => ({ value: entry.id, label: `${entry.number} · ${entry.subject} · ${entry.lifecycle}` }))} value={selection[message.id]?.case_id || message.candidate_case_id || ''} onChange={(event) => setSelection((current) => ({ ...current, [message.id]: { ...current[message.id], case_id: event.target.value } }))}/>}
      </div>
      <div className="form-actions">
        <button disabled={busyId === message.id || !selection[message.id]?.client_id} onClick={() => classify(message, 'new')}>Crear solicitud para cliente</button>
        {(message.candidate_cases?.length > 0 || message.candidate_case_id) && <>
          <button disabled={busyId === message.id} onClick={() => classify(message, 'link')}>Asociar a solicitud activa</button>
          <button disabled={busyId === message.id} onClick={() => classify(message, 'reactivate')}>Reactivar y asociar</button>
          <button disabled={busyId === message.id} onClick={() => classify(message, 'related')}>Crear solicitud relacionada</button>
        </>}
        <button disabled={busyId === message.id} onClick={() => classify(message, 'ignore')}>Ignorar este correo</button>
      </div>
    </article>)}
    {!pending.isPending && !pending.data?.length && <p className="muted">No hay correos pendientes de clasificación.</p>}
  </Panel>
}
