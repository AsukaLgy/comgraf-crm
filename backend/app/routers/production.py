import hashlib
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from ..db import get_db
from ..models import Attachment
from .. import schemas as s, services as service
from ..security import current_user, can
from ..repository import get_case, row
from ..config import settings
from .common import case_response

router = APIRouter(prefix='/api', tags=['Producción y cobranza'])


@router.post('/cases/{case_id}/production/stage')
def stage(case_id: int, data: s.StageInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.change_stage(db, user, case_id, data))


@router.post('/cases/{case_id}/production/block')
def block(case_id: int, data: s.ReasonInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.block(db, user, case_id, data))


@router.post('/cases/{case_id}/production/unblock')
def unblock(case_id: int, data: s.ReasonInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.block(db, user, case_id, data, True))


@router.post('/cases/{case_id}/production/schedule')
def schedule(case_id: int, data: s.ScheduleInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.schedule(db, user, case_id, data))


@router.post('/cases/{case_id}/production/cancel')
def cancel(case_id: int, data: s.CancellationInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.cancel(db, user, case_id, data))


@router.post('/cases/{case_id}/production/cancel/resolve')
def resolve_cancellation(case_id: int, data: s.CancellationInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.resolve_cancellation_request(db, user, case_id, data))


@router.post('/cases/{case_id}/production/refunds')
def refund(case_id: int, data: s.RefundInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.refund(db, user, case_id, data))


@router.post('/cases/{case_id}/deliveries')
def deliver(case_id: int, data: s.DeliveryWithPaymentInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.deliver(db, user, case_id, data))


@router.post('/cases/{case_id}/payments')
def pay(case_id: int, data: s.PaymentInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.pay(db, user, case_id, data))


@router.post('/cases/{case_id}/adjustments')
def adjust(case_id: int, data: s.AdjustmentInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.adjust(db, user, case_id, data))


@router.post('/cases/{case_id}/payments/{entry_id}/reverse')
def reverse(case_id: int, entry_id: int, data: s.ReasonInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.reverse_payment(db, user, case_id, entry_id, data))


@router.post('/cases/{case_id}/attachments', status_code=201)
def upload(case_id: int, file: UploadFile = File(...), art_version: str = Form(''), user=Depends(current_user), db=Depends(get_db, scope='function')):
    case = get_case(db, user, case_id, lock=True)
    if not (can(db, user, 'production') and case.mode == 'Producción') and not (can(db, user, 'attention') and (case.owner_id == user.id or can(db, user, 'assign'))):
        raise HTTPException(403, 'No tienes permiso para adjuntar archivos a este caso')
    if len(art_version) > 100: raise HTTPException(422, 'Versión demasiado larga')
    filename = Path((file.filename or '').replace('\\', '/')).name[:240]
    ext = Path(filename).suffix.lower()
    mime = {'.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.txt': 'text/plain'}.get(ext)
    if not mime: raise HTTPException(422, 'Formatos permitidos: PDF, PNG, JPG y TXT. Exporta los artes editables a PDF.')
    content = file.file.read(20 * 1024 * 1024 + 1)
    if not content or len(content) > 20 * 1024 * 1024: raise HTTPException(422, 'El archivo debe tener entre 1 byte y 20 MB')
    if ext == '.pdf' and not content.startswith(b'%PDF-'): raise HTTPException(422, 'PDF no válido')
    if ext == '.png' and not content.startswith(b'\x89PNG\r\n\x1a\n'): raise HTTPException(422, 'PNG no válido')
    if ext in ('.jpg', '.jpeg') and not content.startswith(b'\xff\xd8\xff'): raise HTTPException(422, 'JPEG no válido')
    if ext == '.txt':
        try: content.decode('utf-8')
        except UnicodeDecodeError: raise HTTPException(422, 'El texto debe ser UTF-8')
    directory = Path(settings.upload_dir)
    directory.mkdir(parents=True, exist_ok=True)
    key = uuid.uuid4().hex + ext
    path = directory / key
    try:
        path.write_bytes(content)
        attachment = Attachment(case_id=case_id, actor_id=user.id, name=filename, storage_key=key, mime=mime, size=len(content), sha256=hashlib.sha256(content).hexdigest(), art_version=art_version)
        db.add(attachment)
        case.version += 1
        service.event(db, case, user, f'Archivo adjunto: {filename} · arte {art_version or "sin versión"}')
        db.flush()
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return row(attachment, ('storage_key',))


@router.get('/attachments/{attachment_id}')
def download(attachment_id: int, user=Depends(current_user), db=Depends(get_db, scope='function')):
    attachment = db.get(Attachment, attachment_id)
    if not attachment: raise HTTPException(404, 'Archivo no encontrado')
    get_case(db, user, attachment.case_id)
    path = Path(settings.upload_dir) / attachment.storage_key
    if not path.is_file(): raise HTTPException(404, 'Archivo no disponible; revisa el respaldo de adjuntos')
    return FileResponse(path, media_type=attachment.mime, filename=attachment.name, headers={'X-Content-Type-Options': 'nosniff'})
