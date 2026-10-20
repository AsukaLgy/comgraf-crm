from fastapi import APIRouter, Depends, Query
from .. import schemas as s, services as service
from ..db import get_db
from ..security import current_user

router = APIRouter(prefix='/api/scanner', tags=['Escáner de encuadernación'])


@router.get('/lookup')
def lookup(qr: str = Query(pattern=r'^CGOP1:[0-9a-fA-F-]{36}$'), user=Depends(current_user), db=Depends(get_db, scope='function')):
    return service.scan_lookup(db, user, qr)


@router.post('/scan')
def scan(data: s.BindingScanInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return service.scan_binding(db, user, data.scan_id)


@router.post('/exceptions', status_code=201)
def request_exception(data: s.PartialReleaseRequestInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return service.request_partial_release(db, user, data)


@router.get('/exceptions')
def exceptions(user=Depends(current_user), db=Depends(get_db, scope='function')):
    return service.pending_partial_releases(db, user)


@router.get('/exceptions/{request_id}')
def exception_result(request_id: int, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return service.scanner_exception_result(db, user, request_id)


@router.post('/exceptions/{request_id}/decision')
def decide_exception(request_id: int, data: s.PartialReleaseDecisionInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return service.decide_partial_release(db, user, request_id, data)
