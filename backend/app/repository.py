import re
import unicodedata
from datetime import datetime, timezone, date as date_type
from decimal import Decimal
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select, or_, func, case as sql_case, and_, not_, literal
from .models import Attachment, Case, Client, Delivery, Draft, Event, Item, Ledger, Production, Quote, Task, User, now
from .security import can
from .config import settings
from .schemas import STAGE_LABELS


# ---- Normalización, fechas y serialización HTTP ----
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


# ---- Proyecciones del expediente ----
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
    # Los créditos se imputan primero al saldo vencido; se conserva el saldo neto total.
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


def attention_predicates():
    overdue_task = select(Task.id).where(Task.case_id == Case.id, Task.done_at.is_(None), Task.due_at <= now()).correlate(Case).exists()
    pending_task = select(Task.id).where(Task.case_id == Case.id, Task.done_at.is_(None)).correlate(Case).exists()
    has_delivery = select(Delivery.id).where(Delivery.case_id == Case.id).correlate(Case).exists()
    short_item = select(Item.id).where(
        Item.case_id == Case.id,
        func.coalesce(select(func.sum(Delivery.quantity)).where(Delivery.item_id == Item.id).correlate(Item).scalar_subquery(), 0) < Item.quantity,
    ).correlate(Case).exists()
    has_item = select(Item.id).where(Item.case_id == Case.id).correlate(Case).exists()
    complete_delivery = and_(has_item, not_(short_item))
    active_production = select(Production.case_id).where(Production.case_id == Case.id, Production.cancelled_at.is_(None)).correlate(Case).exists()
    total, _overdue, _payment = financial_expressions()
    delivered_balance = total > 0
    refund_pending = total < 0
    cancellation_review = select(Production.case_id).where(Production.case_id == Case.id, Production.cancelled_at.is_not(None), Production.cancellation_review_pending.is_(True)).correlate(Case).exists()
    requires_action = or_(Case.status == 'Abierto', Case.pending_message.is_(True), overdue_task)
    pending_assignment = or_(requires_action, and_(active_production, not_(complete_delivery)), pending_task, delivered_balance, refund_pending, cancellation_review)
    return {
        'overdue_task': overdue_task,
        'pending_task': pending_task,
        'has_delivery': has_delivery,
        'complete_delivery': complete_delivery,
        'active_production': active_production,
        'delivered_balance': delivered_balance,
        'refund_pending': refund_pending,
        'cancellation_review': cancellation_review,
        'requires_action': requires_action,
        'pending_assignment': pending_assignment,
    }


def financial_expressions():
    amount = Ledger.amount
    total = func.coalesce(select(func.sum(amount)).where(Ledger.case_id == Case.id).correlate(Case).scalar_subquery(), 0)
    overdue_charges = func.coalesce(select(func.sum(sql_case((and_(amount > 0, Ledger.due_date < today()), amount), else_=0))).where(Ledger.case_id == Case.id).correlate(Case).scalar_subquery(), 0)
    credits = func.coalesce(select(func.sum(sql_case((amount < 0, -amount), else_=0))).where(Ledger.case_id == Case.id).correlate(Case).scalar_subquery(), 0)
    cancellation_review = select(Production.case_id).where(Production.case_id == Case.id, Production.cancelled_at.is_not(None), Production.cancellation_review_pending.is_(True)).correlate(Case).exists()
    calculated_overdue = sql_case((total > 0, func.least(total, func.greatest(0, overdue_charges - credits))), else_=0)
    overdue = sql_case((cancellation_review, 0), else_=calculated_overdue)
    payment = sql_case((cancellation_review, 'Revisi\u00f3n de anulaci\u00f3n'), (overdue > 0, 'Vencido'), (total > 0, 'Pendiente'), (total < 0, 'A favor'), else_='Pagado')
    return total, overdue, payment


# ---- Resúmenes y bandejas ----
def case_summary(db, user, case):
    result = row(case)
    result['number'] = f'CG-{case.id:06d}'
    result['client'] = row(db.get(Client, case.client_id), ('search_text',))
    result['owner_name'] = db.get(User, case.owner_id).name if case.owner_id else None
    production = db.get(Production, case.id)
    result['production'] = row(production)
    result['delivery_status'] = delivery_status(db, case.id)
    result['requires_action'] = case.status == 'Abierto' or case.pending_message or bool(db.scalar(select(Task.id).where(Task.case_id == case.id, Task.done_at.is_(None), Task.due_at <= now()).limit(1)))
    result['attention_status_label'] = case.status
    if production_row := result['production']:
        result['production']['stage_label'] = STAGE_LABELS.get(production_row['stage'])
        if production_row.get('classification_required'):
            result['production']['stage_label'] = 'Pendiente de clasificación'
        if result['delivery_status'] == 'Completa': result['production']['result_label'] = 'Entregado'
        result['production']['active_elapsed_seconds'] = None if production.cancelled_at or result['delivery_status'] == 'Completa' or production.classification_required else max(0, (now() - production.stage_started_at).total_seconds())
    if can(db, user, 'finance') or can(db, user, 'attention'):
        result.update(balance(db, case.id))
        if production and production.cancelled_at and production.cancellation_review_pending:
            result['overdue_balance'] = '0.00'
    elif production:
        for key in ('cancellation_final_amount', 'cancellation_reason', 'cancellation_actor_id'):
            result['production'].pop(key, None)
        for key in ('price', 'advance_required', 'payment_terms', 'payment_due_date'):
            result['production'].pop(key, None)
        for snapshot_name in ('approved_snapshot', 'specs_snapshot'):
            if result['production'][snapshot_name]:
                snap = dict(result['production'][snapshot_name])
                snap['items'] = [{k: v for k, v in i.items() if k != 'unit_price'} for i in snap['items']]
                result['production'][snapshot_name] = snap
    if production and production.cancelled_at and production.cancellation_review_pending:
        result['payment_status'] = 'Revisión de anulación'
        result['cancellation_review_pending'] = True
    return result


def case_detail(db, user, case):
    result = case_summary(db, user, case)
    commercial = can(db, user, 'attention') or can(db, user, 'finance')
    for name, model in [('events', Event), ('tasks', Task), ('items', Item), ('attachments', Attachment), ('deliveries', Delivery)]:
        objects = db.scalars(select(model).where(model.case_id == case.id).order_by(model.id))
        result[name] = [row(o, ('storage_key',) if model == Attachment else ('unit_price',) if model == Item and not commercial else ()) for o in objects]
        if model == Item:
            for item in result[name]: item.pop('substage', None)
    if not commercial:
        result['events'] = [e for e in result['events'] if not e['data'].get('financial')]
    result['quotes'] = [row(q) for q in db.scalars(select(Quote).where(Quote.case_id == case.id).order_by(Quote.version))] if commercial else []
    result['ledger'] = [row(e) for e in db.scalars(select(Ledger).where(Ledger.case_id == case.id).order_by(Ledger.id))] if commercial else []
    result['drafts'] = [row(d) for d in db.scalars(select(Draft).where(Draft.case_id == case.id, Draft.user_id == user.id).order_by(Draft.id))]
    return result


def case_query(db, user, filters):
    view = filters.get('view', 'pool')
    predicates = attention_predicates()
    total, overdue, payment = financial_expressions()
    delivery_state = sql_case((predicates['complete_delivery'], 'Completa'), (predicates['has_delivery'], 'Parcial'), else_='Pendiente')
    query = select(Case, Client, User, Production, delivery_state, total, overdue, payment, predicates['requires_action']).join(Client, Client.id == Case.client_id).outerjoin(User, User.id == Case.owner_id).outerjoin(Production, Production.case_id == Case.id).where(visibility(db, user))
    financial = can(db, user, 'finance') or can(db, user, 'attention')
    if (view in ('balances', 'overdue', 'refunds', 'cancellation-review') or filters.get('payment')) and not financial:
        raise HTTPException(403, 'No tienes acceso a los saldos')
    if view == 'pool': query = query.where(Case.owner_id.is_(None), predicates['requires_action'])
    elif view == 'action': query = query.where(predicates['requires_action'])
    elif view == 'mine': query = query.where(Case.owner_id == user.id, predicates['pending_assignment'])
    elif view == 'production': query = query.where(Production.case_id.is_not(None), Production.cancelled_at.is_(None), not_(predicates['complete_delivery']))
    elif view == 'ready': query = query.where(Production.stage == 'READY', Production.classification_required.is_(False), Production.cancelled_at.is_(None), not_(predicates['complete_delivery']))
    elif view == 'balances': query = query.where(total > 0, not_(predicates['cancellation_review']))
    elif view == 'refunds': query = query.where(total < 0)
    elif view == 'cancellation-review': query = query.where(predicates['cancellation_review'])
    elif view == 'overdue': query = query.where(overdue > 0)
    elif view == 'history': pass
    elif view == 'finalized':
        no_open_task = not_(predicates['pending_task'])
        production_final = and_(Production.case_id.is_not(None), or_(and_(predicates['complete_delivery'], Production.cancelled_at.is_(None)), and_(Production.cancelled_at.is_not(None), not_(predicates['cancellation_review']))), total == 0, no_open_task, Case.pending_message.is_(False), Case.status == 'Cerrado')
        consultation_final = and_(Production.case_id.is_(None), Case.status == 'Cerrado', no_open_task, Case.pending_message.is_(False))
        query = query.where(or_(production_final, consultation_final))
    elif view == 'all': pass
    else: raise HTTPException(422, 'Bandeja no reconocida')
    if filters.get('client_id'):
        try: client_id = int(filters['client_id'])
        except (TypeError, ValueError): raise HTTPException(422, 'client_id debe ser entero')
        if client_id < 1: raise HTTPException(422, 'client_id debe ser positivo')
        query = query.where(Case.client_id == client_id)
    if filters.get('status'): query = query.where(Case.status == filters['status'])
    if filters.get('mode'): query = query.where(Case.mode == filters['mode'])
    if filters.get('priority'): query = query.where(Case.priority == filters['priority'])
    if filters.get('stage'): query = query.where(Production.stage == filters['stage'])
    if filters.get('payment'): query = query.where(payment == filters['payment'])
    if filters.get('blocked') == 'yes': query = query.where(Production.blocked_since.is_not(None))
    if filters.get('due'):
        try: due_date = date_type.fromisoformat(str(filters['due']))
        except ValueError: raise HTTPException(422, 'due debe usar formato YYYY-MM-DD')
        query = query.where(Production.committed_date <= due_date)
    if filters.get('action'): query = query.where(func.lower(Case.next_action).contains(str(filters['action']).lower()))
    if filters.get('q'):
        raw_term = str(filters['q']).strip()
        term = normalize(raw_term)
        numeric_case = raw_term.isdigit() or (term.startswith('cg') and term[2:].isdigit())
        case_id = int(raw_term if raw_term.isdigit() else term[2:]) if numeric_case else None
        query = query.where(or_(Client.search_text.contains(term), func.lower(Case.subject).contains(raw_term.lower()), Case.id == case_id if case_id is not None else literal(False)))
    ordering = filters.get('sort', 'newest')
    if ordering == 'oldest': query = query.order_by(Case.created_at.asc(), Case.id.asc())
    elif ordering == 'due': query = query.order_by(sql_case((Production.committed_date.is_(None), 1), else_=0), Production.committed_date.asc(), Case.id.asc())
    else: query = query.order_by(Case.created_at.desc(), Case.id.desc())
    return query


def _case_projection(db, user, entry, financial):
    case, client, owner, production, delivery_state, total, overdue, payment, requires_action = entry
    result = row(case)
    result['number'] = f'CG-{case.id:06d}'
    result['client'] = row(client, ('search_text',))
    result['owner_name'] = owner.name if owner else None
    result['production'] = row(production)
    result['delivery_status'] = delivery_state
    result['requires_action'] = bool(requires_action)
    result['attention_status_label'] = case.status
    if production:
        result['production']['stage_label'] = STAGE_LABELS.get(production.stage)
        if production.classification_required: result['production']['stage_label'] = 'Pendiente de clasificación'
        if delivery_state == 'Completa': result['production']['result_label'] = 'Entregado'
        result['production']['active_elapsed_seconds'] = None if production.cancelled_at or delivery_state == 'Completa' or production.classification_required else max(0, (now() - production.stage_started_at).total_seconds())
    if financial:
        result.update({'balance': str(total), 'overdue_balance': str(overdue), 'payment_status': payment})
    elif production:
        for key in ('price', 'advance_required', 'payment_terms', 'payment_due_date'): result['production'].pop(key, None)
        for snap in ('approved_snapshot', 'specs_snapshot'):
            if result['production'].get(snap): result['production'][snap]['items'] = [{k:v for k,v in i.items() if k!='unit_price'} for i in result['production'][snap]['items']]
    if production and production.cancelled_at and production.cancellation_review_pending:
        result['cancellation_review_pending'] = True
        result['payment_status'] = 'Revisión de anulación'
    return result


def cases_list(db, user, filters, *, paginated=True):
    query = case_query(db, user, filters)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    financial = can(db, user, 'finance') or can(db, user, 'attention')
    if not paginated:
        entries = db.execute(query).all()
        return [_case_projection(db, user, entry, financial) for entry in entries]
    try: page = max(1, int(filters.get('page', 1)))
    except (TypeError, ValueError): page = 1
    try: page_size = min(100, max(1, int(filters.get('page_size', 25))))
    except (TypeError, ValueError): page_size = 25
    entries = db.execute(query.offset((page - 1) * page_size).limit(page_size)).all()
    return {'items': [_case_projection(db, user, entry, financial) for entry in entries], 'total': total, 'page': page, 'page_size': page_size}


def case_counts(db, user):
    views = ['pool', 'action', 'mine', 'production', 'ready', 'history', 'finalized']
    if can(db, user, 'finance') or can(db, user, 'attention'):
        views += ['balances', 'overdue', 'refunds', 'cancellation-review']
    return {view: db.scalar(select(func.count()).select_from(case_query(db, user, {'view': view}).order_by(None).subquery())) or 0 for view in views}
