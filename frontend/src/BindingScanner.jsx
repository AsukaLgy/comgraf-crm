import { useCallback, useEffect, useRef, useState } from 'react'
import { BrowserQRCodeReader } from '@zxing/browser'
import { Camera, CheckCircle2, LogOut, RefreshCw, ScanLine, ShieldAlert } from 'lucide-react'
import { api, date, uid } from './api'
import { ErrorBox } from './components'
import './BindingScanner.css'

const QR_FORMAT = /^CGOP1:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export default function BindingScanner({ user, onLogout }) {
  const video = useRef(null), controls = useRef(null), reader = useRef(null), busy = useRef(false), requestKey = useRef(''), cameraSession = useRef(0)
  const [camera, setCamera] = useState('idle'), [cameraError, setCameraError] = useState('')
  const [qr, setQr] = useState(''), [outcome, setOutcome] = useState(null), [busyState, setBusyState] = useState(false)
  const [comment, setComment] = useState(''), [exceptionMessage, setExceptionMessage] = useState(''), [exceptionBusy, setExceptionBusy] = useState(false)

  const stopCamera = useCallback(() => {
    cameraSession.current += 1
    controls.current?.stop()
    controls.current = null
    const stream = video.current?.srcObject
    stream?.getTracks?.().forEach((track) => track.stop())
    if (video.current) video.current.srcObject = null
    setCamera('idle')
  }, [])

  useEffect(() => () => {
    cameraSession.current += 1
    controls.current?.stop()
    video.current?.srcObject?.getTracks?.().forEach((track) => track.stop())
  }, [])

  useEffect(() => {
    const requestId = outcome?.exception_id
    if (!requestId || outcome.exception_status !== 'Pendiente') return undefined
    let active = true
    const timer = window.setInterval(async () => {
      try {
        const result = await api(`/scanner/exceptions/${requestId}`)
        if (!active) return
        setOutcome((current) => ({ ...current, exception_status: result.status,
          exception_decision: result.decision_comment, order: result.order,
          pending_orders: result.pending_orders }))
      } catch { /* Keep the submitted request visible and retry on the next interval. */ }
    }, 5000)
    return () => { active = false; window.clearInterval(timer) }
  }, [outcome?.exception_id, outcome?.exception_status])

  const confirmQr = useCallback(async (text) => {
    if (busy.current) return
    busy.current = true
    stopCamera()
    setQr(text)
    setOutcome(null)
    setBusyState(true)
    setComment('')
    setExceptionMessage('')
    requestKey.current = ''
    try {
      const result = await api('/scanner/scan', { method: 'POST', body: { scan_id: text } })
      setOutcome(result)
    } catch (error) {
      if (error.status) {
        let summary = null
        try { summary = await api('/scanner/lookup?qr=' + encodeURIComponent(text)) } catch { /* Preserve the server's scan error. */ }
        setOutcome({ ...(summary || {}), result: 'error', error: error.message, status: error.status })
      } else {
        setOutcome({ result: 'connection_error', error: 'No se confirmó el escaneo. Revisa la conexión y reintenta el mismo QR.' })
      }
    } finally {
      busy.current = false
      setBusyState(false)
    }
  }, [stopCamera])

  const startCamera = useCallback(async () => {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      setCameraError('La cámara requiere HTTPS confiable en la red local. Abre la dirección HTTPS de Comgraf desde el teléfono.')
      return
    }
    setCameraError('')
    setCamera('starting')
    const session = ++cameraSession.current
    try {
      if (!reader.current) reader.current = new BrowserQRCodeReader(undefined, { delayBetweenScanAttempts: 250 })
      const nextControls = await reader.current.decodeFromVideoDevice(undefined, video.current, (result, error) => {
        if (cameraSession.current !== session) return
        if (result && !busy.current) {
          const text = result.getText()
          if (QR_FORMAT.test(text)) void confirmQr(text)
          else {
            stopCamera()
            setOutcome({ result: 'invalid_qr', error: 'Ese código no es un QR de orden de Comgraf.' })
          }
        } else if (error && error.name !== 'NotFoundException') {
          setCameraError('La cámara no pudo leer el código. Ajusta la distancia y la iluminación, o vuelve a activar la cámara.')
        }
      })
      if (cameraSession.current !== session) {
        nextControls.stop()
        return
      }
      controls.current = nextControls
      setCamera('active')
    } catch (error) {
      if (cameraSession.current !== session) return
      setCamera('idle')
      setCameraError(error.name === 'NotAllowedError'
        ? 'El acceso a la cámara fue denegado. Permítelo para este sitio en la configuración de Chrome y vuelve a intentar.'
        : error.name === 'NotFoundError'
          ? 'Este teléfono no informó una cámara disponible.'
          : 'No se pudo iniciar la cámara. Revisa el permiso y vuelve a intentar.')
    }
  }, [confirmQr, stopCamera])

  useEffect(() => {
    const timer = window.setTimeout(() => { void startCamera() }, 0)
    return () => window.clearTimeout(timer)
  }, [startCamera])

  async function retryScan() {
    if (!qr) return
    await confirmQr(qr)
  }

  async function requestException() {
    setExceptionBusy(true)
    setExceptionMessage('')
    if (!requestKey.current) requestKey.current = uid()
    try {
      const result = await api('/scanner/exceptions', { method: 'POST', body: { scan_id: qr, comment, request_key: requestKey.current } })
      setExceptionMessage(result.message)
      setOutcome((current) => ({ ...current, exception_status: result.status, exception_id: result.id }))
    } catch (error) {
      setExceptionMessage(error.message)
    } finally { setExceptionBusy(false) }
  }

  const order = outcome?.order
  const canRequestPartial = order?.confirmed && order.stage === 'WORKSHOP' && !order.blocked && !order.cancelled && !order.delivery_complete && outcome?.pending_orders?.length > 0 && !outcome?.exception_status
  return (
    <main className="scanner-shell">
      <header className="scanner-header">
        <div><span className="scanner-mark"><ScanLine size={22}/></span><div><strong>COMGRAF</strong><small>Terminal de encuadernación</small></div></div>
        <button type="button" onClick={onLogout}><LogOut size={17}/> Cerrar sesión</button>
      </header>
      <section className="scanner-content">
        <p className="eyebrow">CONFIRMACIÓN DE TRABAJO FÍSICO</p>
        <h1>Escáner de encuadernación</h1>
        <p className="scanner-instruction">Escaneá el QR de la orden terminada.</p>
        <p className="scanner-account">Cuenta activa: <strong>{user.name}</strong>. El sistema registra esta cuenta como autora del escaneo.</p>
        <div className="scanner-camera">
          <video ref={video} muted playsInline aria-label="Vista de la cámara para escanear códigos QR" className={camera === 'active' || camera === 'starting' ? 'visible' : ''}/>
          {camera !== 'active' && <div className="camera-placeholder"><Camera size={34}/><span>{camera === 'starting' ? 'Activando cámara…' : 'La cámara está pausada'}</span></div>}
        </div>
        <p className="scanner-purpose">Escanear confirma la finalización de encuadernación. No registra una entrega, un pago ni la finalización de la solicitud.</p>
        <ErrorBox error={cameraError}/>
        {!outcome && camera !== 'active' && <button className="primary scanner-main-button" type="button" disabled={camera === 'starting'} onClick={startCamera}><Camera size={18}/> {camera === 'starting' ? 'Activando cámara…' : 'Activar cámara'}</button>}
        {camera === 'active' && <button className="scanner-main-button" type="button" onClick={stopCamera}>Pausar cámara</button>}
        {busyState && <p className="scanner-wait" role="status">Verificando la orden en Comgraf…</p>}
        {outcome && (
          <section className={`scanner-result ${outcome.result === 'released' || outcome.result === 'already_confirmed' ? 'success' : outcome.result === 'confirmed_waiting' ? 'pending' : 'failure'}`} aria-live="polite">
            <div className="scanner-result-heading">
              {outcome.result === 'released' || outcome.result === 'already_confirmed' ? <CheckCircle2/> : <ShieldAlert/>}
              <h2>{outcome.message || outcome.error}</h2>
            </div>
            {order && <>
              <p><strong>{order.order_number}</strong> · Solicitud {order.case_number}</p>
              <p>{order.client} · {order.subject}</p>
              <p>Etapa: <strong>{order.stage_label}</strong></p>
              {order.confirmed_at && <p>Confirmada: {date(order.confirmed_at)}</p>}
              <ul className="scanner-products">{order.items.map((item, index) => <li key={index}>{item.product}: {item.quantity} {item.unit}</li>)}</ul>
            </>}
            {outcome.pending_orders?.length > 0 && <div className="scanner-missing"><strong>Órdenes que faltan para liberar el grupo</strong><ul>{outcome.pending_orders.map((missing) => <li key={missing.production_id}><strong>{missing.order_number}</strong> · {missing.stage_label} · {missing.reason}</li>)}</ul></div>}
            {outcome.result === 'connection_error' && <button type="button" className="primary" onClick={retryScan}><RefreshCw size={16}/> Reintentar este QR</button>}
            {canRequestPartial && <div className="scanner-exception-form"><label htmlFor="partial-comment">Motivo para solicitar liberar solo esta orden</label><textarea id="partial-comment" rows="3" maxLength={2000} value={comment} onChange={(event) => setComment(event.target.value)}/><button type="button" disabled={!comment.trim() || exceptionBusy} onClick={requestException}>{exceptionBusy ? 'Enviando…' : 'Solicitar liberación parcial'}</button>{exceptionMessage && <p role="status">{exceptionMessage}</p>}</div>}
            {outcome.exception_status && <p role="status">Excepción: {outcome.exception_status}{outcome.exception_decision ? ` · ${outcome.exception_decision}` : ''}</p>}
          </section>
        )}
        {outcome && !busyState && <button type="button" className="scanner-main-button primary" onClick={() => {setOutcome(null);setQr('');setExceptionMessage('');void startCamera()}}><ScanLine size={18}/> Continuar escaneando</button>}
      </section>
    </main>
  )
}
