import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, can } from './api'
import { Badge, ErrorBox, Field, FormDialog, Loading, Panel } from './components'

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
    <Panel title="Canales externos"><div className="profile-grid">{['Correo', 'WhatsApp'].map((channel) => <div key={channel}><h3>{channel} <Badge>No configurado</Badge></h3><p>Borradores y registro manual disponibles en cada solicitud. No se realizan envíos externos.</p></div>)}</div></Panel>
    <Panel title="Instalación"><p>Zona horaria: <strong>{user.timezone}</strong></p><p>Datos compartidos en MySQL. Adjuntos almacenados en el servidor. Los respaldos deben incluir ambos.</p></Panel>

    {modal?.type === 'user' && <FormDialog title={modal.user ? 'Editar usuario' : 'Crear usuario'} initial={modal.user ? { name: modal.user.name, roles: modal.user.roles, active: modal.user.active, capacity: modal.user.capacity, password: '' } : { username: '', name: '', password: '', roles: ['Atención'], capacity: 20 }} onClose={() => setModal(null)} onSave={async (value) => { const body = { ...value, capacity: Number(value.capacity) }; if (modal.user && !body.password) body.password = null; await api('/users' + (modal.user ? '/' + modal.user.id : ''), { method: modal.user ? 'PUT' : 'POST', body }); qc.invalidateQueries({ queryKey: ['users'] }) }} render={(value, set) => <>
      <div className="form-grid">{!modal.user && <Field label="Usuario" required pattern="[a-zA-Z0-9_.-]+" minLength={3} value={value.username} onChange={(event) => set('username', event.target.value)}/>}<Field label="Nombre" required value={value.name} onChange={(event) => set('name', event.target.value)}/><Field label={modal.user ? 'Nueva contraseña (opcional)' : 'Contraseña inicial'} type="password" autoComplete="new-password" minLength={12} required={!modal.user} value={value.password} onChange={(event) => set('password', event.target.value)}/><Field label="Capacidad de solicitudes abiertas" type="number" min="1" max="500" required value={value.capacity} onChange={(event) => set('capacity', event.target.value)}/>{modal.user && <Field label="Cuenta activa" type="checkbox" checked={value.active} onChange={(event) => set('active', event.target.checked)}/>}</div>
      <fieldset><legend>Roles</legend>{ROLES.map((role) => <label className="check-label" key={role}><input type="checkbox" checked={value.roles.includes(role)} onChange={(event) => updateUserRoles(value, set, role, event.target.checked)}/>{role}</label>)}{value.roles.includes('Escáner de encuadernación') && <p className="muted">Esta cuenta no tendrá acceso a clientes, órdenes completas, cobros, entregas ni administración.</p>}</fieldset>
    </>}/>}
    {modal?.type === 'role' && <FormDialog title={'Permisos · ' + modal.role.name} initial={{ permissions: modal.role.permissions }} onClose={() => setModal(null)} onSave={async (value) => { await api('/roles/' + encodeURIComponent(modal.role.name), { method: 'PUT', body: value }); qc.invalidateQueries({ queryKey: ['roles'] }); qc.invalidateQueries({ queryKey: ['users'] }) }} render={(value, set) => <fieldset><legend>Capacidades del rol</legend>{roles.data.available.filter((permission) => permission !== '*').map((permission) => <label className="check-label" key={permission}><input type="checkbox" checked={value.permissions.includes(permission)} onChange={(event) => set('permissions', event.target.checked ? [...value.permissions, permission] : value.permissions.filter((item) => item !== permission))}/>{permission}</label>)}</fieldset>}/>}
    {modal?.type === 'password' && <FormDialog title="Cambiar contraseña" initial={{ current_password: '', new_password: '' }} onClose={() => setModal(null)} onSave={async (value) => { await api('/auth/password', { method: 'POST', body: value }); qc.clear(); location.assign('/') }} render={(value, set) => <><Field label="Contraseña actual" type="password" autoComplete="current-password" required value={value.current_password} onChange={(event) => set('current_password', event.target.value)}/><Field label="Nueva contraseña" type="password" autoComplete="new-password" minLength={12} required value={value.new_password} onChange={(event) => set('new_password', event.target.value)}/></>}/>}
  </>
}
