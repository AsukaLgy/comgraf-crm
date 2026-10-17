from collections import defaultdict
from decimal import Decimal
from fastapi import APIRouter, Depends
from sqlalchemy import select
from ..db import get_db
from ..models import Case, Production, Task, Event, Quote, Delivery, now
from ..security import current_user, require, can
from ..repository import visibility, today, balance, row, delivery_status, local_day
from ..integrations import provider_status

router = APIRouter(prefix='/api', tags=['Calendario e indicadores'])


@router.get('/channels')
def channels(user=Depends(current_user)):
    return provider_status()


@router.get('/calendar')
def calendar(user=Depends(current_user), db=Depends(get_db, scope='function')):
    events = []
    for case in db.scalars(select(Case).where(visibility(db, user))):
        label = f'CG-{case.id:06d} · {case.subject}'
        for task in db.scalars(select(Task).where(Task.case_id == case.id, Task.done_at.is_(None))):
            events.append({'id': f't{task.id}', 'case_id': case.id, 'type': 'Seguimiento', 'date': row(task)['due_at'], 'title': f'{label} · {task.title}', 'owner_id': task.owner_id})
        production = db.get(Production, case.id)
        if production and not production.cancelled_at and production.stage != 'Entregado':
            if production.scheduled_at: events.append({'id': f'p{case.id}', 'case_id': case.id, 'type': 'Producción', 'date': row(production)['scheduled_at'], 'title': label, 'owner_id': production.owner_id})
            if production.committed_date: events.append({'id': f'd{case.id}', 'case_id': case.id, 'type': 'Entrega', 'date': str(production.committed_date), 'title': label, 'original': str(production.original_committed_date)})
    return sorted(events, key=lambda e: e['date'])


@router.get('/indicators')
def indicators(user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'reports')
    cases = list(db.scalars(select(Case).where(visibility(db, user))))
    ids = [c.id for c in cases]
    events = list(db.scalars(select(Event).where(Event.case_id.in_(ids))))
    quotes = list(db.scalars(select(Quote).where(Quote.case_id.in_(ids))))
    first = [(c.first_response_at - c.created_at).total_seconds() for c in cases if c.first_response_at]
    unassigned = [{'id': c.id, 'number': f'CG-{c.id:06d}', 'hours': round((now() - c.created_at).total_seconds() / 3600, 2)} for c in cases if c.owner_id is None and c.status != 'Cerrado']
    stages = defaultdict(float)
    blocked = 0
    rework = 0
    for e in events:
        if e.data.get('action') == 'stage': stages[e.data['from']] += e.data.get('seconds', 0)
        if e.data.get('action') == 'unblocked': blocked += e.data.get('seconds', 0)
        if e.data.get('rework'): rework += 1
    financial = defaultdict(lambda: {'delivered_pending': Decimal(0), 'overdue': Decimal(0)})
    completed, ontime = 0, 0
    for case in cases:
        production = db.get(Production, case.id)
        if not production: continue
        if production.stage != 'Entregado' and not production.cancelled_at:
            stages[production.stage] += (now() - production.stage_started_at).total_seconds()
        if production.blocked_since:
            blocked += ((production.cancelled_at or now()) - production.blocked_since).total_seconds()
        if production.stage == 'Entregado':
            completed += 1
            delivery_dates = list(db.scalars(select(Delivery.delivered_at).where(Delivery.case_id == case.id)))
            ontime += bool(production.original_committed_date and delivery_dates and max(local_day(d) for d in delivery_dates) <= production.original_committed_date)
        b = balance(db, case.id)
        if delivery_status(db, case.id) != 'Pendiente': financial[production.currency]['delivered_pending'] += max(Decimal(0), Decimal(b['balance']))
        financial[production.currency]['overdue'] += Decimal(b['overdue_balance'])
    accepted = sum(q.status == 'Aceptada' for q in quotes)
    converted = sum(c.mode == 'Producción' for c in cases)
    return {'first_human_response_hours': round(sum(first) / len(first) / 3600, 2) if first else None,
        'first_response_sample': len(first), 'unassigned': unassigned,
        'overdue_followups': len(list(db.scalars(select(Task.id).where(Task.case_id.in_(ids), Task.done_at.is_(None), Task.due_at < now())))),
        'case_conversion': {'numerator': converted, 'denominator': len(cases)},
        'quote_acceptance': {'numerator': accepted, 'denominator': len(quotes)},
        'quote_conversion': {'numerator': len(list(db.scalars(select(Production.case_id).where(Production.case_id.in_(ids), Production.quote_id.is_not(None))))), 'denominator': accepted},
        'stage_hours': {k: round(v / 3600, 2) for k, v in stages.items()}, 'blocked_hours': round(blocked / 3600, 2),
        'rework_events': rework, 'on_time_deliveries': {'numerator': ontime, 'denominator': completed},
        'financial': {currency: {k: str(v) for k, v in values.items()} for currency, values in financial.items()},
        'manual_notice': 'Primera respuesta humana y puntualidad dependen de las interacciones y entregas registradas manualmente. Asignaciones y notas internas no cuentan como respuesta.'}
