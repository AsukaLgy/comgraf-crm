import { useEffect, useState } from 'react'
import QRCode from 'qrcode'
import './OrderQRCode.css'

export default function OrderQRCode({ production, caseNumber, description, client, compact = false, numberBelow = false }) {
  const [image, setImage] = useState({ scanId: '', data: '', error: false, loading: false, retry: -1 })
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true
    if (production?.scan_id) {
      QRCode.toDataURL(`CGOP1:${production.scan_id}`, { errorCorrectionLevel: 'M', margin: 4, width: 320, color:{dark:'#000000',light:'#ffffff'} })
        .then((value) => active && setImage({ scanId: production.scan_id, data: value, error:false, loading:false, retry }))
        .catch(() => active && setImage({ scanId: production.scan_id, data: '', error:true, loading:false, retry }))
    }
    return () => { active = false }
  }, [production?.scan_id,retry])
  if (!production?.scan_id) return null
  const currentImage = image.scanId === production.scan_id && image.retry === retry
  const imageUrl = currentImage ? image.data : ''
  const imageError = currentImage && image.error
  return (
    <section className={`${compact ? 'order-qr compact' : 'order-qr'}${numberBelow?' cai-number-below':''}`} aria-label={`QR de ${production.order_number}`}>
      {imageUrl ? <img src={imageUrl} alt={`QR estable de la orden ${production.order_number}`} /> : <div className="qr-placeholder">{imageError?<><span>No se pudo generar el QR.</span><button type="button" onClick={()=>setRetry(x=>x+1)}>Reintentar QR</button></>:<span>Preparando QR…</span>}</div>}
      {numberBelow&&<strong className="qr-number">{production.order_number || `OP-${String(production.id).padStart(5, '0')}`}</strong>}
      <div className={numberBelow?'order-qr-details':''}>
        {!numberBelow&&<strong>{production.order_number || `OP-${String(production.id).padStart(5, '0')}`}</strong>}
        <span>Solicitud {caseNumber}</span>
        <span>{description || 'Orden de producción'}</span>
        {client && <span>Cliente: {client}</span>}
        <small>Confirmación de encuadernación · Identificador estable</small>
        {imageError&&<strong className="qr-error">No imprimir esta ficha sin QR. Reintentá generarlo.</strong>}
      </div>
    </section>
  )
}
