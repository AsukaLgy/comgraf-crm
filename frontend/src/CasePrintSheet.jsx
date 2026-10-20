import { Link } from 'react-router-dom'
import { Printer } from 'lucide-react'
import { date } from './api'
import { Specifications } from './ProductForm'
import OrderQRCode from './OrderQRCode'

const caiDimensions = {
  'Media carta': '5.5 × 8.5',
  'Cuarto de carta': '4.25 × 5.5',
  '8.5 × 7.25': '8.5 × 7.25',
  '8.5 × 8.5': '8.5 × 8.5',
  Carta: '8.5 × 11',
}

function printTextChunks(text, maxLength = 380) {
  const source = String(text || '')
  const chunks = []
  let offset = 0
  while (source.length - offset > maxLength) {
    let end = source.lastIndexOf(' ', offset + maxLength)
    if (end < offset + Math.floor(maxLength * 0.6)) end = offset + maxLength
    chunks.push(source.slice(offset, end))
    offset = end
  }
  if (offset < source.length) chunks.push(source.slice(offset))
  return chunks
}

export default function CasePrintSheet({ id, record, person }) {
  const production = record.production
  const printItems =
    production?.specs_snapshot?.items || production?.approved_snapshot?.items || record.items || []
  const isCai = printItems.some((item) => item.product === 'Talonarios de factura CAI')
  if (production && isCai) {
    const identity = production.client_snapshot || {
      company: record.client.company || '',
      person: record.case_contact?.name || (!record.client.company ? record.client.name : ''),
      rtn: record.client.rtn || '',
      phone: record.case_contact?.phone || record.client.phones?.[0] || '',
      email: record.case_contact?.email || record.client.emails?.[0] || '',
    }
    return (
      <article className="print-sheet cai-print">
        <div className="print-actions">
          <button onClick={() => window.print()}>
            <Printer size={16} />
            Imprimir / Guardar PDF
          </button>
          <Link to={`/cases/${id}?production_id=${production.id}`}>Volver a la orden</Link>
        </div>
        <header className="cai-print-header">
          <div className="cai-print-heading">
            <div className="brand-name">
              Comgraf <small>| Comunicación Gráfica</small>
            </div>
            <div className="cai-header-id">
              <strong>{production.order_number}</strong>
              <span>Solicitud {record.number}</span>
            </div>
            <h1>Orden de producción — Talonarios de factura CAI</h1>
            <div className="cai-meta">
              <span>
                <b>Creada:</b> {date(production.created_at)} · {person(production.created_by_id)}
              </span>
              {production.committed_date && (
                <span>
                  <b>Fecha comprometida:</b> {date(production.committed_date)}
                </span>
              )}
            </div>
          </div>
          <div className="cai-qr">
            <OrderQRCode
              production={production}
              caseNumber={record.number}
              description="Talonarios de factura CAI"
              client={identity.company || identity.person}
              numberBelow
            />
          </div>
        </header>
        <section className="cai-client">
          <h2>Datos del cliente</h2>
          <div>
            <span>
              <b>Empresa:</b> {identity.company || '—'}
            </span>
            <span>
              <b>Persona:</b> {identity.person || '—'}
            </span>
            <span>
              <b>RTN:</b> {identity.rtn || '—'}
            </span>
            <span>
              <b>Teléfono:</b> {identity.phone || '—'}
            </span>
            <span>
              <b>Correo:</b> {identity.email || '—'}
            </span>
          </div>
        </section>
        <h2 className="cai-items-title">
          Especificaciones de la orden · {production.order_number}
        </h2>
        {printItems.map((item, index) => {
          const specs = item.specs || {},
            quantity = Number(item.quantity || 0)
          return (
            <section className="cai-item" key={item.id || index}>
              <h3>
                {production.order_number} · Partida {index + 1} · {item.product}
              </h3>
              <dl>
                <div>
                  <dt>Formato</dt>
                  <dd>
                    {specs.cai_format || '—'}
                    {caiDimensions[specs.cai_format]
                      ? ` (${caiDimensions[specs.cai_format]} pulgadas)`
                      : ''}
                  </dd>
                </div>
                <div>
                  <dt>Tipo de papel</dt>
                  <dd>{specs.paper_type || '—'}</dd>
                </div>
                <div>
                  <dt>Encuadernación:</dt>
                  <dd>{specs.cai_binding || 'No especificada'}</dd>
                </div>
                <div>
                  <dt>Número de copias:</dt>
                  <dd>{specs.additional_copies ?? '—'}</dd>
                </div>
                <div>
                  <dt>Cantidad de talonarios</dt>
                  <dd>{quantity}</dd>
                </div>
              </dl>
              {printTextChunks(specs.detail).map((chunk, chunkIndex) => (
                <p className="prewrap cai-observations" key={chunkIndex}>
                  <b>
                    {production.order_number} · Observaciones
                    {chunkIndex ? ' (continuación)' : ''}:
                  </b>{' '}
                  {chunk}
                </p>
              ))}
            </section>
          )
        })}
        {production.instructions && (
          <section className="cai-item">
            <h3>{production.order_number} · Instrucciones de producción</h3>
            {printTextChunks(production.instructions).map((chunk, chunkIndex) => (
              <p className="prewrap cai-observations" key={chunkIndex}>
                <b>
                  {production.order_number} · Instrucciones
                  {chunkIndex ? ' (continuación)' : ''}:
                </b>{' '}
                {chunk}
              </p>
            ))}
          </section>
        )}
      </article>
    )
  }
  return (
    <article className="print-sheet">
      <div className="print-actions">
        <button onClick={() => window.print()}>
          <Printer size={16} />
          Imprimir / Guardar PDF
        </button>
        <Link to={`/cases/${id}${production?.id ? `?production_id=${production.id}` : ''}`}>
          Volver a la solicitud
        </Link>
      </div>
      <header>
        <div>
          <div className="brand-name">COMGRAF</div>
          <p>Comunicación Gráfica · Ficha de taller</p>
        </div>
        <h1>
          {record.number}
          <br />
          <small>{production?.order_number || 'Orden de producción'}</small>
        </h1>
      </header>
      {!production ? (
        <p>Esta solicitud está en Atención y no tiene ficha de producción.</p>
      ) : (
        <>
          <h2>{record.subject}</h2>
          <p>
            Cliente: {record.client.name} · Responsable de la solicitud: {person(record.owner_id)}
          </p>
          <p>
            Compromiso: {date(production.committed_date)} · Etapa:{' '}
            {production.stage_label || 'Pendiente de clasificación'}
            {record.delivery_status === 'Completa' ? ' · Entregado' : ''}
            {production.cancelled_at ? ' · ANULADO' : ''}
          </p>
          {(
            production.specs_snapshot?.items ||
            production.approved_snapshot?.items ||
            record.items
          ).map((item) => (
            <section key={item.id}>
              <h3>
                {item.product} · {item.quantity} {item.unit}
              </h3>
              <Specifications specs={item.specs} />
            </section>
          ))}
          <h3>Instrucciones</h3>
          <p className="prewrap">{production.instructions || 'Sin instrucciones adicionales'}</p>
          <h3>Archivos de referencia</h3>
          {record.attachments.map((attachment) => (
            <p key={attachment.id}>
              <a href={`/api/attachments/${attachment.id}`}>{attachment.name}</a>
              {' · '}Arte {attachment.art_version || '—'} · SHA256:{' '}
              <small>{attachment.sha256}</small>
            </p>
          ))}
          <p>Modalidad de entrega: {production.delivery_method}</p>
          <OrderQRCode
            production={production}
            caseNumber={record.number}
            description={production.instructions || record.subject}
            client={record.client.name}
          />
        </>
      )}
    </article>
  )
}
