import { useEffect, useState } from 'react'
import QRCode from 'qrcode'
import './OrderQRCode.css'

export default function OrderQRCode({ production, caseNumber, description, client, compact = false }) {
  const [image, setImage] = useState({ scanId: '', data: '' })
  useEffect(() => {
    let active = true
    if (production?.scan_id) {
      QRCode.toDataURL(`CGOP1:${production.scan_id}`, { errorCorrectionLevel: 'M', margin: 2, width: 240 })
        .then((value) => active && setImage({ scanId: production.scan_id, data: value }))
        .catch(() => active && setImage({ scanId: production.scan_id, data: '' }))
    }
    return () => { active = false }
  }, [production?.scan_id])
  if (!production?.scan_id) return null
  const imageUrl = image.scanId === production.scan_id ? image.data : ''
  return (
    <section className={compact ? 'order-qr compact' : 'order-qr'} aria-label={`QR de ${production.order_number}`}>
      {imageUrl ? <img src={imageUrl} alt={`QR estable de la orden ${production.order_number}`} /> : <span className="qr-placeholder">Preparando QR…</span>}
      <div>
        <strong>{production.order_number || `OP-${String(production.id).padStart(5, '0')}`}</strong>
        <span>Solicitud {caseNumber}</span>
        <span>{description || 'Orden de producción'}</span>
        {client && <span>Cliente: {client}</span>}
        <small>Confirmación de encuadernación · Identificador estable</small>
      </div>
    </section>
  )
}
