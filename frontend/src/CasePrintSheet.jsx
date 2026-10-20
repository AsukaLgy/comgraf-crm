import { Link } from 'react-router-dom'
import { Printer } from 'lucide-react'
import { date } from './api'
import { Specifications } from './ProductForm'
import OrderQRCode from './OrderQRCode'

export default function CasePrintSheet({ id, record, person }) {
  const production = record.production
  return (
    <article className="print-sheet">
      <div className="print-actions">
        <button onClick={() => window.print()}>
          <Printer size={16} />
          Imprimir / Guardar PDF
        </button>
        <Link to={`/cases/${id}${production?.id ? `?production_id=${production.id}` : ''}`}>Volver a la solicitud</Link>
      </div>
      <header>
        <div>
          <div className="brand-name">COMGRAF</div>
          <p>Comunicación Gráfica · Ficha de taller</p>
        </div>
        <h1>{record.number}<br/><small>{production?.order_number || 'Orden de producción'}</small></h1>
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
          <OrderQRCode production={production} caseNumber={record.number} description={production.instructions || record.subject} client={record.client.name} />
        </>
      )}
    </article>
  )
}
