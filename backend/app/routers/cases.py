from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from ..db import get_db
from ..models import Case, Client, Contact, Draft, SavedView
from .. import schemas as s, services as service
from ..security import current_user, require
from ..repository import row, get_case, case_detail, cases_list, case_counts
from .common import case_response

router = APIRouter(prefix='/api', tags=['Expedientes'])


@router.get('/cases')
def list_cases(request: Request, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return cases_list(db, user, dict(request.query_params))


@router.get('/counts')
def counts(user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_counts(db, user)


@router.post('/cases', status_code=201)
def create(data: s.CaseInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'attention')
    if not db.get(Client, data.client_id): raise HTTPException(422, 'Cliente no encontrado')
    if data.contact_id:
        contact = db.get(Contact, data.contact_id)
        if not contact or contact.client_id != data.client_id: raise HTTPException(422, 'El contacto no pertenece al cliente')
    if data.related_case_id: get_case(db, user, data.related_case_id)
    case = Case(**data.model_dump())
    db.add(case)
    db.flush()
    service.event(db, case, user, f'Caso de {case.kind.lower()} creado por {case.channel}', data={'action': 'created'})
    return case_response(db, user, case)


@router.get('/cases/{case_id}')
def detail(case_id: int, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_detail(db, user, get_case(db, user, case_id))


@router.post('/cases/{case_id}/take')
def take(case_id: int, data: s.Mutation, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.take(db, user, case_id, data))


@router.post('/cases/{case_id}/assign')
def assign(case_id: int, data: s.Assignment, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.assign(db, user, case_id, data))


@router.post('/cases/{case_id}/attention')
def attention(case_id: int, data: s.CaseUpdate, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.update_case(db, user, case_id, data))


@router.post('/cases/{case_id}/events')
def events(case_id: int, data: s.EventInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.add_event(db, user, case_id, data))


@router.post('/cases/{case_id}/tasks')
def tasks(case_id: int, data: s.TaskInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.add_task(db, user, case_id, data))


@router.post('/cases/{case_id}/tasks/{task_id}/complete')
def complete(case_id: int, task_id: int, data: s.Mutation, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.complete_task(db, user, case_id, task_id, data))


@router.put('/cases/{case_id}/tasks/{task_id}')
def reschedule_task(case_id: int, task_id: int, data: s.TaskReschedule, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.reschedule_task(db, user, case_id, task_id, data))


@router.post('/cases/{case_id}/quotes')
def quote(case_id: int, data: s.QuoteInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.add_quote(db, user, case_id, data))


@router.post('/cases/{case_id}/quotes/{quote_id}/decision')
def decision(case_id: int, quote_id: int, data: s.QuoteDecision, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.decide_quote(db, user, case_id, quote_id, data))


@router.post('/cases/{case_id}/convert')
def convert(case_id: int, data: s.Conversion, user=Depends(current_user), db=Depends(get_db, scope='function')):
    return case_response(db, user, service.convert(db, user, case_id, data))


@router.get('/views')
def views(user=Depends(current_user), db=Depends(get_db, scope='function')):
    return [row(v) for v in db.scalars(select(SavedView).where(SavedView.user_id == user.id))]


@router.post('/views')
def save_view(data: s.ViewInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    allowed = {'view', 'status', 'mode', 'priority', 'stage', 'payment', 'blocked', 'due', 'action', 'sort', 'page'}
    if set(data.filters) - allowed: raise HTTPException(422, 'Filtros no reconocidos')
    view = db.scalar(select(SavedView).where(SavedView.user_id == user.id, SavedView.name == data.name))
    if not view:
        view = SavedView(user_id=user.id, name=data.name)
        db.add(view)
    view.filters = data.filters
    db.flush()
    return row(view)


@router.put('/cases/{case_id}/drafts/{channel}')
def draft(case_id: int, channel: str, data: s.DraftInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'attention')
    get_case(db, user, case_id, lock=True)
    if channel not in ('Nota', 'Llamada', 'Visita', 'Correo', 'WhatsApp'): raise HTTPException(422, 'Canal no reconocido')
    draft = db.scalar(select(Draft).where(Draft.case_id == case_id, Draft.user_id == user.id, Draft.channel == channel))
    if draft and draft.version != data.version: raise HTTPException(409, 'El borrador cambió en otra sesión')
    if not draft:
        if data.version: raise HTTPException(409, 'El borrador cambió')
        draft = Draft(case_id=case_id, user_id=user.id, channel=channel, version=0)
        db.add(draft)
    draft.body = data.body
    draft.version += 1
    db.flush()
    return row(draft)
