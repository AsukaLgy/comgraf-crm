import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, SlidersHorizontal, Bookmark } from 'lucide-react'
import { api, can } from './api'
import { Badge, ErrorBox, Loading, Field, Select, FormDialog } from './components'
import { PRODUCTION_STAGES, CASE_STATUSES, CASE_PRIORITIES } from './domain'
import CaseTable from './CaseTable'
import OrderTable from './OrderTable'
import NewCase from './NewCase'

export const VIEW_LABELS = {
  pool: 'Solicitudes sin asignar',
  action: 'Requieren acción',
  mine: 'Mis solicitudes',
  'mine-action': 'Requieren mi acción',
  production: 'Órdenes en producción',
  ready: 'Listos para entregar',
  balances: 'Saldos por cobrar',
  overdue: 'Cobros vencidos',
  refunds: 'Devoluciones pendientes',
  'cancellation-review': 'Anulaciones por resolver',
  finalized: 'Completados',
  history: 'Historial',
  all: 'Todas las solicitudes',
}
export default function Inbox({ user, view = 'pool', home = false, search = false }) {
  const location = useLocation(),
    qc = useQueryClient(),
    nav = useNavigate(),
    [modal, setModal] = useState(null),
    [advanced, setAdvanced] = useState(false)
  const poolView = !search && view === 'pool'
  const orderView = !search && ['production', 'ready'].includes(view)
  const key = `comgraf.filters.${user.id}.${view}`
  const [filters, setFilters] = useState(() => {
    try {
      const stored = JSON.parse(sessionStorage.getItem(key)) || {}
      if (['action', 'mine-action'].includes(view)) {
        delete stored.status
        delete stored.lifecycle
      }
      if (view === 'mine') delete stored.lifecycle
      if (view === 'pool') {
        for (const name of Object.keys(stored)) if (!['sort', 'page'].includes(name)) delete stored[name]
      }
      return stored
    } catch {
      return {}
    }
  })
  const queryText = search ? new URLSearchParams(location.search).get('q') || '' : ''
  const actionableView = !search && ['action', 'mine-action'].includes(view)
  const compatibleFilters = Object.fromEntries(
    Object.entries(filters).filter(([name]) =>
      poolView
        ? ['sort', 'page'].includes(name)
        : view === 'mine'
          ? name !== 'lifecycle'
          : !actionableView || !['status', 'lifecycle'].includes(name),
    ),
  )
  const effective = {
    ...compatibleFilters,
    view: search ? 'all' : view,
    ...(search ? { q: queryText } : {}),
  }
  const list = useQuery({
    queryKey: [orderView ? 'orders' : 'cases', user.id, effective],
    queryFn: () => api((orderView ? '/orders?' : '/cases?') + new URLSearchParams(effective)),
  })
  const personalCounters = useQuery({
    queryKey: ['personal-counts', user.id, compatibleFilters],
    enabled: !search && (view === 'mine' || view === 'mine-action'),
    queryFn: async () => {
      const base = { ...compatibleFilters }
      delete base.page
      delete base.page_size
      const [mine, mineAction] = await Promise.all(
        ['mine', 'mine-action'].map((personalView) =>
          api('/cases?' + new URLSearchParams({ ...base, view: personalView, page_size: 1 })),
        ),
      )
      return { mine: mine.total, 'mine-action': mineAction.total }
    },
  })
  const counts = useQuery({
    queryKey: ['counts', user.id],
    queryFn: () => api('/counts'),
    refetchInterval: 30000,
  })
  const saved = useQuery({ queryKey: ['views'], queryFn: () => api('/views') })
  const clients = useQuery({
    queryKey: ['clients-search', queryText],
    queryFn: () => api('/clients?q=' + encodeURIComponent(queryText)),
    enabled: search && can(user, 'clients'),
  })
  const set = (name, value) =>
    setFilters((f) => ({ ...f, [name]: value, page: name === 'page' ? value : 1 }))
  useEffect(() => {
    sessionStorage.setItem(key, JSON.stringify(compatibleFilters))
  }, [compatibleFilters, key])
  useEffect(() => {
    if (list.data) {
      const position = Number(sessionStorage.getItem(key + '.scroll') || 0)
      const listPosition = Number(sessionStorage.getItem(key + '.list-scroll') || 0)
      requestAnimationFrame(() => {
        window.scrollTo(0, position)
        const table = document.querySelector('.inbox-list-scroll')
        if (table) table.scrollTop = listPosition
      })
    }
  }, [key, list.data, list.isPending])
  const remember = () => {
    sessionStorage.setItem(key + '.scroll', String(window.scrollY))
    const table = document.querySelector('.inbox-list-scroll')
    if (table) sessionStorage.setItem(key + '.list-scroll', String(table.scrollTop))
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">COMUNICACIÓN GRÁFICA / OPERACIONES</div>
          <h1>{home ? 'Mi día' : search ? 'Búsqueda global' : VIEW_LABELS[view]}</h1>
          <p>
            {home
              ? 'Una vista del trabajo que necesita tu atención.'
              : search
                ? `Resultados para «${queryText}»`
                : orderView ? 'Cada fila es una orden vinculada a su solicitud, con productos y fechas propios.' : 'Atención, producción y seguimiento desde una misma solicitud.'}
          </p>
        </div>
        {can(user, 'attention') && (
          <button className="primary" onClick={() => setModal('new')}>
            <Plus size={17} />
            Nueva solicitud
          </button>
        )}
      </div>
      {home && (
        <div className="metrics">
          {[
            ['pool', 'Solicitudes sin asignar', 'pool'],
            ['mine', 'Mis solicitudes', 'mine'],
            ['action', 'Requieren acción', 'action'],
            ['ready', 'Listos para entregar', 'ready'],
          ].map(([k, label, to]) => (
            <Link to={'/inbox/' + to} className="metric" key={k}>
              <span>{label}</span>
              <strong>{k==='ready'?counts.data?.ready_orders??'—':counts.data?.[k] ?? '—'}</strong>
              <small>Abrir bandeja →</small>
            </Link>
          ))}
        </div>
      )}
      {search && can(user, 'clients') && (
        <section className="panel">
          <div className="panel-title">
            <h2>Clientes coincidentes</h2>
            <Badge>{clients.data?.length || 0}</Badge>
          </div>
          <div className="client-results">
            {clients.data?.map((c) => (
              <Link key={c.id} to={'/clients/' + c.id}>
                <strong>{c.name}</strong>
                <small>
                  {c.company} · {c.phones.join(', ')} · {c.rtn}
                </small>
              </Link>
            ))}
            {clients.data?.length === 0 && (
              <p className="muted">
                No hay coincidencias de cliente. No se fusionan identidades automáticamente.
              </p>
            )}
          </div>
        </section>
      )}
      {['ready', 'balances', 'overdue', 'refunds', 'cancellation-review'].includes(view) && (
        <div className="tabs section-tabs">
          {[
            'ready',
            ...(can(user, 'finance') || can(user, 'attention')
              ? ['balances', 'overdue', 'refunds', 'cancellation-review']
              : []),
          ].map((v) => (
            <Link key={v} className={view === v ? 'active' : ''} to={'/inbox/' + v}>
              {VIEW_LABELS[v]} <Badge>{counts.data?.[v] ?? 0}</Badge>
            </Link>
          ))}
        </div>
      )}
      <section className="panel inbox">
        <div className="panel-title">
          <div className="tabs" role="group" aria-label="Bandejas de solicitudes">
            {home || view === 'pool' || view === 'action' ? (
              <>
                <Link className={view === 'pool' ? 'active' : ''} to="/inbox/pool">
                  Solicitudes sin asignar <Badge>{counts.data?.pool ?? 0}</Badge>
                </Link>
                <Link className={view === 'action' ? 'active' : ''} to="/inbox/action">
                  Requieren acción <Badge>{counts.data?.action ?? 0}</Badge>
                </Link>
              </>
            ) : view === 'mine' || view === 'mine-action' ? (
              <>
                <Link className={view === 'mine' ? 'active' : ''} to="/inbox/mine">
                  Todas mis solicitudes <Badge>{personalCounters.data?.mine ?? counts.data?.mine ?? 0}</Badge>
                </Link>
                <Link className={view === 'mine-action' ? 'active' : ''} to="/inbox/mine-action">
                  Requieren mi acción <Badge>{personalCounters.data?.['mine-action'] ?? counts.data?.['mine-action'] ?? 0}</Badge>
                </Link>
              </>
            ) : (
                <h2>
                {search ? 'Solicitudes' : VIEW_LABELS[view]} <Badge>{orderView?(view==='ready'?counts.data?.ready_orders??0:counts.data?.production_orders??0):list.data?.total ?? 0}</Badge>
              </h2>
            )}
          </div>
          {!poolView && <div className="actions">
            <button onClick={() => setAdvanced(!advanced)}>
              <SlidersHorizontal size={15} />
              Filtros
            </button>
            {!orderView && <button onClick={() => setModal('save')}>
              <Bookmark size={15} />
              Guardar vista
            </button>}
          </div>}
        </div>
        <div className="filters">
          {orderView ? null : poolView ? (
            <Select
              label="Orden"
              options={[
                { value: 'newest', label: 'Más recientes' },
                { value: 'oldest', label: 'Más antiguos' },
              ]}
              value={filters.sort || 'newest'}
              onChange={(e) => set('sort', e.target.value)}
            />
          ) : <>
          {['history', 'all'].includes(view) && (
            <Select
              label="Ciclo de vida"
              options={['Activo', 'Completado']}
              empty="Todos"
              value={filters.lifecycle || ''}
              onChange={(e) => set('lifecycle', e.target.value)}
            />
          )}
          {!actionableView && <Select
            label="Estado"
            empty="Todos los estados"
            options={CASE_STATUSES}
            value={filters.status || ''}
            onChange={(e) => set('status', e.target.value)}
          />}
          <Select
            label="Modo"
            empty="Todos los modos"
            options={['Atención', 'Producción']}
            value={filters.mode || ''}
            onChange={(e) => set('mode', e.target.value)}
          />
          <Select
            label="Prioridad"
            empty="Cualquier prioridad"
            options={CASE_PRIORITIES}
            value={filters.priority || ''}
            onChange={(e) => set('priority', e.target.value)}
          />
          <Select
            label="Orden"
            options={[
              { value: 'newest', label: 'Más recientes' },
              { value: 'oldest', label: 'Más antiguos' },
              { value: 'due', label: 'Fecha comprometida' },
            ]}
            value={filters.sort || 'newest'}
            onChange={(e) => set('sort', e.target.value)}
          />
          <button className="text-button" onClick={() => setFilters({})}>
            Limpiar
          </button>
          </>}
        </div>
        {advanced && !poolView && (
          <div className="filters advanced">
            <Select
              label="Etapa de producción"
              options={PRODUCTION_STAGES}
              empty="Todas"
              value={filters.stage || ''}
              onChange={(e) => set('stage', e.target.value)}
            />
            <Field
              label="Compromiso hasta"
              type="date"
              value={filters.due || ''}
              onChange={(e) => set('due', e.target.value)}
            />
            <Field
              label="Próxima acción contiene"
              value={filters.action || ''}
              onChange={(e) => set('action', e.target.value)}
            />
            <Select
              label="Bloqueo"
              options={[{ value: 'yes', label: 'Solo bloqueados' }]}
              empty="Todos"
              value={filters.blocked || ''}
              onChange={(e) => set('blocked', e.target.value)}
            />
            {(can(user, 'attention') || can(user, 'finance')) && (
              <Select
                label="Pago"
                options={['Pendiente', 'Vencido', 'Pagado', 'A favor']}
                empty="Todos"
                value={filters.payment || ''}
                onChange={(e) => set('payment', e.target.value)}
              />
            )}
          </div>
        )}
        {!poolView && !orderView && !!saved.data?.length && (
          <div className="saved-views">
            <span>Vistas guardadas</span>
            {saved.data.map((v) => (
              <button
                key={v.id}
                onClick={() => {
                  const destination = v.filters.view
                  const rest = { ...v.filters }
                  delete rest.view
                  if (destination === 'pool') {
                    for (const name of Object.keys(rest)) if (!['sort', 'page'].includes(name)) delete rest[name]
                  }
                  if (['action', 'mine-action'].includes(destination)) {
                    delete rest.status
                    delete rest.lifecycle
                  }
                  if (destination === 'mine') delete rest.lifecycle
                  setFilters(rest)
                  sessionStorage.setItem(
                    `comgraf.filters.${user.id}.${destination}`,
                    JSON.stringify(rest),
                  )
                  if (destination !== view) nav('/inbox/' + destination)
                }}
              >
                {v.name}
              </button>
            ))}
          </div>
        )}
        <ErrorBox error={list.error} />
        {list.isPending ? (
          <Loading />
        ) : (
          list.data && (orderView ? <OrderTable items={list.data.items}/> : <CaseTable items={list.data.items} remember={remember} user={user} />)
        )}
        <div className="list-footer">
          <span>
            {list.data?.total ?? 0} {orderView?'órdenes':'solicitudes'} en esta vista · página {list.data?.page ?? 1} de{' '}
            {Math.max(1, Math.ceil((list.data?.total ?? 0) / (list.data?.page_size ?? 25)))}
          </span>
          <div className="actions">
            <button
              disabled={!list.data || list.data.page <= 1}
              onClick={() => set('page', Math.max(1, (filters.page || 1) - 1))}
            >
              Anterior
            </button>
            <button
              disabled={!list.data || list.data.page * list.data.page_size >= list.data.total}
              onClick={() => set('page', (filters.page || 1) + 1)}
            >
              Siguiente
            </button>
          </div>
        </div>
      </section>
      {modal === 'new' && <NewCase user={user} onClose={() => setModal(null)} />}
      {modal === 'save' && !poolView && (
        <FormDialog
          title="Guardar vista"
          initial={{ name: '' }}
          onClose={() => setModal(null)}
          onSave={async (v) => {
            await api('/views', {
              method: 'POST',
              body: {
                name: v.name,
                filters: {
                  ...compatibleFilters,
                  ...(!actionableView && filters.status ? { status: filters.status } : {}),
                  view,
                },
              },
            })
            qc.invalidateQueries({ queryKey: ['views'] })
          }}
          render={(v, set) => (
            <Field
              label="Nombre de la vista"
              required
              value={v.name}
              onChange={(e) => set('name', e.target.value)}
            />
          )}
        />
      )}
    </>
  )
}
