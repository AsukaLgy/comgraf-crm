from collections import defaultdict
from decimal import Decimal
from fastapi import APIRouter, Depends
from sqlalchemy import select
from ..db import get_db
from ..models import Case, Client, Item, Production, Task, Event, Quote, Delivery, now
from ..security import current_user, require
from ..repository import visibility, balance, row, delivery_status, local_day
from ..schemas import STAGE_LABELS
from ..integrations import provider_status

router = APIRouter(prefix='/api', tags=['Calendario e indicadores'])


@router.get('/channels')
def channels(user=Depends(current_user)):
    return provider_status()


@router.get('/calendar')
def calendar(user=Depends(current_user), db=Depends(get_db, scope='function')):
    events = []
    for case in db.scalars(select(Case).where(visibility(db, user))):
        client = db.get(Client, case.client_id)
        label = f'CG-{case.id:06d} · {client.name if client else case.subject}'
        for task in db.scalars(select(Task).where(Task.case_id == case.id, Task.done_at.is_(None))):
            order = db.get(Production, task.production_id) if task.production_id else None
            events.append({'id': f't{task.id}', 'case_id': case.id, 'production_id': order.id if order else None,
                'type': 'Seguimiento de atención', 'event_kind': 'followup', 'date': row(task)['due_at'],
                'title': f'{label}{f" · OP-{order.id:05d}" if order else ""} · {task.title}',
                'order_number': f'OP-{order.id:05d}' if order else None,
                'stage': STAGE_LABELS.get(order.stage) if order and order.stage else None, 'owner_id': task.owner_id})
        for production in db.scalars(select(Production).where(Production.case_id == case.id).order_by(Production.id)):
            if production.cancelled_at or delivery_status(db, case.id, production.id) == 'Completa': continue
            order_number = f'OP-{production.id:05d}'
            stage = STAGE_LABELS.get(production.stage) if production.stage else 'Pendiente de clasificación'
            order_context = f'{label} · {order_number}'
            if production.scheduled_at: events.append({'id': f'p{production.id}', 'case_id': case.id, 'production_id': production.id,
                'type': 'Inicio programado de producción', 'event_kind': 'scheduled', 'date': row(production)['scheduled_at'],
                'title': order_context, 'order_number': order_number, 'stage': stage, 'stage_label': stage, 'owner_id': case.owner_id})
            if production.committed_date: events.append({'id': f'd{production.id}', 'case_id': case.id, 'production_id': production.id,
                'type': 'Entrega comprometida', 'event_kind': 'committed', 'date': str(production.committed_date),
                'title': order_context, 'order_number': order_number,
                'stage': stage, 'stage_label': stage, 'original': str(production.original_committed_date) if production.original_committed_date else None})
    return sorted(events, key=lambda e: e['date'])


@router.get('/indicators')
def indicators(user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'reports')
    cases = list(db.scalars(select(Case).where(visibility(db, user))))
    ids = [c.id for c in cases]
    events = list(db.scalars(select(Event).where(Event.case_id.in_(ids))))
    quotes = list(db.scalars(select(Quote).where(Quote.case_id.in_(ids))))
    first = [(c.first_response_at - c.created_at).total_seconds() for c in cases if c.first_response_at]
    unassigned = [{'id': c.id, 'number': f'CG-{c.id:06d}', 'hours': round((now() - c.created_at).total_seconds() / 3600, 2)} for c in cases if c.owner_id is None and c.lifecycle == 'Activo']
    stages = defaultdict(float)
    blocked = 0
    rework = 0
    for e in events:
        if e.data.get('action') == 'stage': stages[STAGE_LABELS.get(e.data['from'], e.data['from'])] += e.data.get('seconds', 0)
        if e.data.get('action') in ('cancelled', 'delivery_completed') and e.data.get('stage'):
            stages[STAGE_LABELS.get(e.data['stage'], e.data['stage'])] += e.data.get('seconds', 0)
        if e.data.get('action') == 'unblocked': blocked += e.data.get('seconds', 0)
        if e.data.get('rework') and e.data.get('action') == 'stage': rework += 1
    financial = defaultdict(lambda: {'delivered_pending': Decimal(0), 'overdue': Decimal(0)})
    completed, ontime = 0, 0
    for case in cases:
        for production in db.scalars(select(Production).where(Production.case_id == case.id)):
            delivery = delivery_status(db, case.id, production.id)
            if delivery != 'Completa' and not production.cancelled_at and production.stage:
                stages[STAGE_LABELS.get(production.stage, production.stage)] += (now() - production.stage_started_at).total_seconds()
            if production.blocked_since:
                blocked += ((production.cancelled_at or now()) - production.blocked_since).total_seconds()
            if delivery == 'Completa':
                completed += 1
                delivery_dates = list(db.scalars(select(Delivery.delivered_at).join(Item, Item.id == Delivery.item_id).where(Item.production_id == production.id)))
                ontime += bool(production.original_committed_date and delivery_dates and max(local_day(d) for d in delivery_dates) <= production.original_committed_date)
            b = balance(db, case.id, production.id)
            if delivery != 'Pendiente': financial[production.currency]['delivered_pending'] += max(Decimal(0), Decimal(b['balance']))
            financial[production.currency]['overdue'] += Decimal(b['overdue_balance'])
    accepted = sum(q.status == 'Aceptada' for q in quotes)
    converted = sum(c.mode == 'Producción' for c in cases)
    return {'first_human_response_hours': round(sum(first) / len(first) / 3600, 2) if first else None,
        'first_response_sample': len(first), 'unassigned': unassigned,
        'overdue_followups': len(list(db.scalars(select(Task.id).join(Case, Case.id == Task.case_id).where(Task.case_id.in_(ids), Case.lifecycle == 'Activo', Task.done_at.is_(None), Task.due_at <= now())))),
        'case_conversion': {'numerator': converted, 'denominator': len(cases)},
        'quote_acceptance': {'numerator': accepted, 'denominator': len(quotes)},
        'quote_conversion': {'numerator': len(list(db.scalars(select(Production.id).where(Production.case_id.in_(ids), Production.quote_id.is_not(None))))), 'denominator': accepted},
        'stage_hours': {k: round(v / 3600, 2) for k, v in stages.items()}, 'blocked_hours': round(blocked / 3600, 2),
        'rework_events': rework, 'on_time_deliveries': {'numerator': ontime, 'denominator': completed},
        'financial': {currency: {k: str(v) for k, v in values.items()} for currency, values in financial.items()},
        'manual_notice': 'Primera respuesta humana y puntualidad dependen de las interacciones y entregas registradas manualmente. Asignaciones y notas internas no cuentan como respuesta.'}
