import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select, or_, func
from .models import *
from .security import can
from .config import settings


def normalize(value):
    return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode().lower())


def today():
    return datetime.now(ZoneInfo(settings.app_timezone)).date()


def local_day(value):
    return value.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(settings.app_timezone)).date()


def quantity_text(value):
    return format(Decimal(value).normalize(), 'f')


def row(obj, exclude=()):
    if obj is None:
        return None
    result = {}
    for column in obj.__table__.columns:
        if column.name in exclude:
            continue
        value = getattr(obj, column.name)
        if isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, datetime):
            value = value.replace(tzinfo=timezone.utc).isoformat()
        result[column.name] = value
    return result


def visibility(db, user):
    if can(db, user, 'read_all') or can(db, user, 'attention'):
        return True
    if can(db, user, 'production') or can(db, user, 'finance'):
        return or_(Case.owner_id == user.id, Case.collection_owner_id == user.id, Case.mode == 'Producción')
    return False


def get_case(db, user, case_id, lock=False):
    query = select(Case).where(Case.id == case_id, visibility(db, user))
    if lock:
        query = query.with_for_update()
    case = db.scalar(query.execution_options(populate_existing=True))
    if not case:
        raise HTTPException(404, 'Expediente no disponible')
    return case


def balance(db, case_id):
    entries = list(db.scalars(select(Ledger).where(Ledger.case_id == case_id).order_by(Ledger.id)))
    total = sum((entry.amount for entry in entries), Decimal('0'))
    # Imputación FIFO de créditos a obligaciones vencidas. Reversiones conservan vencimiento.
    overdue_charges = sum((e.amount for e in entries if e.amount > 0 and e.due_date and e.due_date < today()), Decimal('0'))
    credits = -sum((e.amount for e in entries if e.amount < 0), Decimal('0'))
    overdue = max(Decimal('0'), min(total, overdue_charges - credits))
    return {'balance': str(total), 'overdue_balance': str(overdue),
            'payment_status': 'Vencido' if overdue > 0 else 'Pendiente' if total > 0 else 'A favor' if total < 0 else 'Pagado'}


def delivery_status(db, case_id):
    items = list(db.scalars(select(Item).where(Item.case_id == case_id)))
    quantities = dict(db.execute(select(Delivery.item_id, func.sum(Delivery.quantity)).where(Delivery.case_id == case_id).group_by(Delivery.item_id)).all())
    if items and all(quantities.get(i.id, 0) >= i.quantity for i in items):
        return 'Completa'
    return 'Parcial' if quantities else 'Pendiente'


def case_summary(db, user, case):
    result = row(case)
    result['number'] = f'CG-{case.id:06d}'
    result['client'] = row(db.get(Client, case.client_id), ('search_text',))
    result['owner_name'] = db.get(User, case.owner_id).name if case.owner_id else None
    production = db.get(Production, case.id)
    result['production'] = row(production)
    result['delivery_status'] = delivery_status(db, case.id)
    result['requires_action'] = case.pending_message or bool(db.scalar(select(Task.id).where(Task.case_id == case.id, Task.done_at.is_(None), Task.due_at < now()).limit(1)))
    if can(db, user, 'finance') or can(db, user, 'attention'):
        result.update(balance(db, case.id))
    elif production:
        for key in ('price', 'advance_required', 'payment_terms', 'payment_due_date'):
            result['production'].pop(key, None)
        if result['production']['approved_snapshot']:
            snap = dict(result['production']['approved_snapshot'])
            snap['items'] = [{k: v for k, v in i.items() if k != 'unit_price'} for i in snap['items']]
            result['production']['approved_snapshot'] = snap
    return result


def case_detail(db, user, case):
    result = case_summary(db, user, case)
    commercial = can(db, user, 'attention') or can(db, user, 'finance')
    for name, model in [('events', Event), ('tasks', Task), ('items', Item), ('attachments', Attachment), ('deliveries', Delivery)]:
        objects = db.scalars(select(model).where(model.case_id == case.id).order_by(model.id))
        result[name] = [row(o, ('storage_key',) if model == Attachment else ('unit_price',) if model == Item and not commercial else ()) for o in objects]
    if not commercial:
        result['events'] = [e for e in result['events'] if not e['data'].get('financial')]
    result['quotes'] = [row(q) for q in db.scalars(select(Quote).where(Quote.case_id == case.id).order_by(Quote.version))] if commercial else []
    result['ledger'] = [row(e) for e in db.scalars(select(Ledger).where(Ledger.case_id == case.id).order_by(Ledger.id))] if commercial else []
    result['drafts'] = [row(d) for d in db.scalars(select(Draft).where(Draft.case_id == case.id, Draft.user_id == user.id).order_by(Draft.id))]
    return result


def cases_list(db, user, filters):
    cases = db.scalars(select(Case).where(visibility(db, user)).order_by(Case.id.desc()))
    results = []
    view = filters.get('view', 'pool')
    financial = can(db, user, 'finance') or can(db, user, 'attention')
    if (view in ('balances', 'overdue') or filters.get('payment')) and not financial:
        raise HTTPException(403, 'No tienes acceso a los saldos')
    for case in cases:
        if view == 'pool' and (case.owner_id is not None or case.status == 'Cerrado'): continue
        if view == 'mine' and case.owner_id != user.id: continue
        if view == 'history' and case.status != 'Cerrado': continue
        if view == 'production' and case.mode != 'Producción': continue
        item = case_summary(db, user, case)
        prod = item['production'] or {}
        if view == 'action' and not item['requires_action']: continue
        if view == 'ready' and (prod.get('stage') != 'Listo para entregar' or prod.get('cancelled_at')): continue
        if view == 'balances' and not (item['delivery_status'] != 'Pendiente' and Decimal(item.get('balance', '0')) > 0): continue
        if view == 'overdue' and Decimal(item.get('overdue_balance', '0')) <= 0: continue
        if filters.get('client_id') and str(case.client_id) != str(filters['client_id']): continue
        if filters.get('status') and case.status != filters['status']: continue
        if filters.get('mode') and case.mode != filters['mode']: continue
        if filters.get('priority') and case.priority != filters['priority']: continue
        if filters.get('stage') and prod.get('stage') != filters['stage']: continue
        if filters.get('payment') and item.get('payment_status') != filters['payment']: continue
        if filters.get('blocked') == 'yes' and not prod.get('blocked_since'): continue
        if filters.get('due') and (not prod.get('committed_date') or str(prod['committed_date']) > filters['due']): continue
        if filters.get('action') and normalize(filters['action']) not in normalize(case.next_action): continue
        if filters.get('q'):
            term = normalize(filters['q'])
            client = db.get(Client, case.client_id)
            if not any(term in normalize(v) for v in [client.search_text, item['number'], case.subject]): continue
        results.append(item)
    sort = filters.get('sort', 'newest')
    if sort == 'oldest': results.reverse()
    if sort == 'due': results.sort(key=lambda c: str((c['production'] or {}).get('committed_date') or '9999'))
    return results
