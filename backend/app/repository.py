import re
import unicodedata
from datetime import datetime, timezone, date as date_type
from decimal import Decimal
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select, or_, func, case as sql_case, and_, not_, literal
from sqlalchemy.orm import aliased
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
        raise HTTPException(404, 'Solicitud no disponible')
    return case


def balance(db, case_id, production_id=None):
    query = select(Ledger).where(Ledger.case_id == case_id)
    if production_id is not None: query = query.where(Ledger.production_id == production_id)
    entries = list(db.scalars(query.order_by(Ledger.id)))
    if production_id is None:
        currencies = set(db.scalars(select(Production.currency).where(Production.case_id == case_id)))
        if not currencies: currencies = {entry.currency for entry in entries}
        if len(currencies) > 1:
            return {'balance': None, 'overdue_balance': None, 'payment_status': 'Saldo por orden',
                'financial_by_currency': {currency: str(sum((e.amount for e in entries if e.currency == currency), Decimal('0'))) for currency in sorted(currencies)}}
    elif entries and len({entry.currency for entry in entries}) > 1:
        return {'balance': None, 'overdue_balance': None, 'payment_status': 'Revisión de moneda',
            'financial_by_currency': {currency: str(sum((e.amount for e in entries if e.currency == currency), Decimal('0'))) for currency in sorted({entry.currency for entry in entries})}}
    total = sum((entry.amount for entry in entries), Decimal('0'))
    # Los créditos se imputan primero al saldo vencido; se conserva el saldo neto total.
    overdue_charges = sum((e.amount for e in entries if e.amount > 0 and e.due_date and e.due_date < today()), Decimal('0'))
    credits = -sum((e.amount for e in entries if e.amount < 0), Decimal('0'))
    overdue = max(Decimal('0'), min(total, overdue_charges - credits))
    return {'balance': str(total), 'overdue_balance': str(overdue),
            'payment_status': 'Vencido' if overdue > 0 else 'Pendiente' if total > 0 else 'A favor' if total < 0 else 'Pagado'}


def delivery_status(db, case_id, production_id=None):
    item_query = select(Item).where(Item.case_id == case_id)
    delivery_query = select(Delivery.item_id, func.sum(Delivery.quantity)).where(Delivery.case_id == case_id)
    if production_id is not None:
        item_query = item_query.where(Item.production_id == production_id)
        delivery_query = delivery_query.join(Item, Item.id == Delivery.item_id).where(Item.production_id == production_id)
    items = list(db.scalars(item_query))
    quantities = dict(db.execute(delivery_query.group_by(Delivery.item_id)).all())
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
    _total, _overdue, _payment, _has_debt, _has_credit, _has_overdue = financial_expressions()
    has_debt, has_credit, _has_overdue = financial_status_predicates()
    delivered_balance = has_debt
    refund_pending = has_credit
    cancellation_review = select(Production.case_id).where(Production.case_id == Case.id, Production.cancelled_at.is_not(None), Production.cancellation_review_pending.is_(True)).correlate(Case).exists()
    requires_action = and_(Case.lifecycle == 'Activo', or_(Case.owner_id.is_(None), Case.status == 'Abierto', Case.pending_message.is_(True), overdue_task))
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
    raw_total = func.coalesce(select(func.sum(amount)).where(Ledger.case_id == Case.id).correlate(Case).scalar_subquery(), 0)
    production_currency_count = select(func.count(func.distinct(Production.currency))).where(Production.case_id == Case.id).correlate(Case).scalar_subquery()
    ledger_currency_count = select(func.count(func.distinct(Ledger.currency))).where(Ledger.case_id == Case.id).correlate(Case).scalar_subquery()
    one_currency = and_(production_currency_count <= 1, ledger_currency_count <= 1)
    total = sql_case((one_currency, raw_total), else_=None)
    overdue_charges = func.coalesce(select(func.sum(sql_case((and_(amount > 0, Ledger.due_date < today()), amount), else_=0))).where(Ledger.case_id == Case.id).correlate(Case).scalar_subquery(), 0)
    credits = func.coalesce(select(func.sum(sql_case((amount < 0, -amount), else_=0))).where(Ledger.case_id == Case.id).correlate(Case).scalar_subquery(), 0)
    cancellation_review = select(Production.case_id).where(Production.case_id == Case.id, Production.cancelled_at.is_not(None), Production.cancellation_review_pending.is_(True)).correlate(Case).exists()
    has_debt, has_credit, has_overdue = financial_status_predicates()
    calculated_overdue = sql_case((has_overdue, overdue_charges), else_=0)
    overdue = sql_case((cancellation_review, 0), (one_currency, calculated_overdue), else_=None)
    payment = sql_case((cancellation_review, 'Revisión de anulación'), (has_overdue, 'Vencido'), (has_debt, 'Pendiente'), (has_credit, 'A favor'), else_='Pagado')
    return total, overdue, payment, has_debt, has_credit, has_overdue


def financial_status_predicates():
    current = select(func.coalesce(func.sum(Ledger.amount), 0)).where(
        Ledger.production_id == Production.id, Ledger.currency == Production.currency).correlate(Production).scalar_subquery()
    overdue_charges = select(func.coalesce(func.sum(sql_case((and_(Ledger.amount > 0, Ledger.due_date < today()), Ledger.amount), else_=0)), 0)).where(
        Ledger.production_id == Production.id, Ledger.currency == Production.currency).correlate(Production).scalar_subquery()
    credits = select(func.coalesce(func.sum(sql_case((Ledger.amount < 0, -Ledger.amount), else_=0)), 0)).where(
        Ledger.production_id == Production.id, Ledger.currency == Production.currency).correlate(Production).scalar_subquery()
    order_overdue = func.least(current, func.greatest(0, overdue_charges - credits))
    debt = select(Production.id).where(Production.case_id == Case.id, current > 0).correlate(Case).exists()
    credit = select(Production.id).where(Production.case_id == Case.id, current < 0).correlate(Case).exists()
    overdue = select(Production.id).where(Production.case_id == Case.id, current > 0, order_overdue > 0).correlate(Case).exists()
    return debt, credit, overdue


# ---- Resúmenes y bandejas ----
def case_summary(db, user, case):
    result = row(case)
    result['number'] = f'CG-{case.id:06d}'
    result['client'] = row(db.get(Client, case.client_id), ('search_text',))
    result['owner_name'] = db.get(User, case.owner_id).name if case.owner_id else None
    productions = list(db.scalars(select(Production).where(Production.case_id == case.id).order_by(Production.id)))
    production = productions[-1] if productions else None
    result['production'] = row(production)
    if result['production']: result['production']['order_number'] = f'OP-{production.id:05d}'
    result['productions'] = [row(p) for p in productions]
    result['production_count'] = len(productions)
    result['production_pending_count'] = sum(not p.cancelled_at and delivery_status(db, case.id, p.id) != 'Completa' for p in productions)
    result['delivery_status'] = delivery_status(db, case.id, production.id) if production else 'Pendiente'
    result['requires_action'] = case.lifecycle == 'Activo' and (
        case.owner_id is None or case.status == 'Abierto' or case.pending_message
        or bool(db.scalar(select(Task.id).where(Task.case_id == case.id, Task.done_at.is_(None), Task.due_at <= now()).limit(1)))
    )
    result['attention_status_label'] = case.status
    if production_row := result['production']:
        result['production']['stage_label'] = STAGE_LABELS.get(production_row['stage'])
        if production_row.get('classification_required'):
            result['production']['stage_label'] = 'Pendiente de clasificación'
        if result['delivery_status'] == 'Completa': result['production']['result_label'] = 'Entregado'
        result['production']['active_elapsed_seconds'] = None if production.cancelled_at or result['delivery_status'] == 'Completa' or production.classification_required else max(0, (now() - production.stage_started_at).total_seconds())
    for production_row, production_obj in zip(result['productions'], productions):
        status = delivery_status(db, case.id, production_obj.id)
        production_row['delivery_status'] = status
        production_row['order_number'] = f'OP-{production_obj.id:05d}'
        production_row['stage_label'] = STAGE_LABELS.get(production_obj.stage) if production_obj.stage else None
        if production_obj.classification_required: production_row['stage_label'] = 'Pendiente de clasificación'
        if status == 'Completa': production_row['result_label'] = 'Entregado'
        production_row['active_elapsed_seconds'] = None if production_obj.cancelled_at or status == 'Completa' or production_obj.classification_required else max(0, (now() - production_obj.stage_started_at).total_seconds())
    if can(db, user, 'finance') or can(db, user, 'attention'):
        result.update(balance(db, case.id))
        if production and production.cancelled_at and production.cancellation_review_pending:
            result['overdue_balance'] = '0.00'
    elif production:
        for order_row in result['productions']:
            for key in ('cancellation_final_amount', 'cancellation_reason', 'cancellation_actor_id', 'price', 'advance_required', 'payment_terms', 'payment_due_date'):
                order_row.pop(key, None)
            for snapshot_name in ('approved_snapshot', 'specs_snapshot'):
                if order_row.get(snapshot_name):
                    snap = dict(order_row[snapshot_name])
                    snap['items'] = [{k: v for k, v in i.items() if k != 'unit_price'} for i in snap['items']]
                    order_row[snapshot_name] = snap
        result['production'] = next((o for o in result['productions'] if o['id'] == production.id), None)
    if production and production.cancelled_at and production.cancellation_review_pending:
        result['payment_status'] = 'Revisión de anulación'
        result['cancellation_review_pending'] = True
    return result


def case_detail(db, user, case, production_id=None):
    result = case_summary(db, user, case)
    from .security import can
    result['completion_blockers'] = case_completion_blockers(db, case, show_finances=can(db, user, 'finance') or can(db, user, 'attention'))
    commercial = can(db, user, 'attention') or can(db, user, 'finance')
    if production_id is not None:
        selected = db.scalar(select(Production).where(Production.id == production_id, Production.case_id == case.id))
        if not selected: raise HTTPException(404, 'Orden de producción no encontrada en esta solicitud')
        result['production'] = next((p for p in result['productions'] if p['id'] == production_id), None)
        result['delivery_status'] = delivery_status(db, case.id, production_id)
    elif result['productions']:
        result['production'] = next((p for p in result['productions'] if p['id'] == case.id), result['productions'][0])
    selected_production_id = result['production']['id'] if result.get('production') else None
    if selected_production_id is not None:
        selected_obj = db.get(Production, selected_production_id)
        result['delivery_status'] = delivery_status(db, case.id, selected_production_id)
        if commercial:
            result.pop('financial_by_currency', None)
            result.update(balance(db, case.id, selected_production_id))
            if selected_obj.cancelled_at and selected_obj.cancellation_review_pending:
                result['payment_status'] = 'Revisión de anulación'
                result['overdue_balance'] = '0.00'
    for name, model in [('events', Event), ('tasks', Task), ('items', Item), ('attachments', Attachment), ('deliveries', Delivery)]:
        query = select(model).where(model.case_id == case.id)
        if model == Item and selected_production_id is not None: query = query.where(Item.production_id == selected_production_id)
        if model == Delivery and selected_production_id is not None: query = query.join(Item, Item.id == Delivery.item_id).where(Item.production_id == selected_production_id)
        objects = db.scalars(query.order_by(model.id))
        result[name] = [row(o, ('storage_key',) if model == Attachment else ('unit_price',) if model == Item and not commercial else ()) for o in objects]
        if model == Item:
            for item in result[name]: item.pop('substage', None)
    if not commercial:
        result['events'] = [e for e in result['events'] if not e['data'].get('financial')]
    result['quotes'] = []
    if commercial:
        for quote in db.scalars(select(Quote).where(Quote.case_id == case.id).order_by(Quote.version)):
            value = row(quote)
            linked = db.scalar(select(Production.id).where(Production.quote_id == quote.id))
            value['production_id'] = linked
            value['order_number'] = f'OP-{linked:05d}' if linked else None
            result['quotes'].append(value)
    ledger_query = select(Ledger).where(Ledger.case_id == case.id)
    if selected_production_id is not None: ledger_query = ledger_query.where(Ledger.production_id == selected_production_id)
    result['ledger'] = [row(e) for e in db.scalars(ledger_query.order_by(Ledger.id))] if commercial else []
    result['drafts'] = [row(d) for d in db.scalars(select(Draft).where(Draft.case_id == case.id, Draft.user_id == user.id).order_by(Draft.id))]
    return result


def case_completion_blockers(db, case, *, show_finances=True):
    """Return the shared list of unresolved work that prevents lifecycle completion."""
    blockers = []
    productions = db.scalars(select(Production).where(Production.case_id == case.id).order_by(Production.id)).all()
    for production in productions:
        order_number = f'OP-{production.id:05d}'
        if production.classification_required or production.stage is None:
            blockers.append(f'{order_number}: la etapa histórica requiere clasificación.')
        if production.blocked_since:
            blockers.append(f'{order_number}: hay un bloqueo operativo pendiente.')
        if production.cancellation_review_pending:
            blockers.append(f'{order_number}: la anulación requiere resolución económica.')
        if not production.cancelled_at and delivery_status(db, case.id, production.id) != 'Completa':
            items = db.scalars(select(Item).where(Item.production_id == production.id).order_by(Item.id)).all()
            remaining = []
            for item in items:
                delivered = db.scalar(select(func.coalesce(func.sum(Delivery.quantity), 0)).where(Delivery.item_id == item.id)) or 0
                quantity_left = item.quantity - delivered
                if quantity_left > 0:
                    remaining.append(f'Falta entregar {quantity_text(quantity_left)} {item.unit} de {item.product}.')
            blockers.extend(remaining or ['La producción sigue activa y pendiente de entrega.'])
        entries = db.scalars(select(Ledger).where(Ledger.production_id == production.id)).all()
        if not any(entry.kind == 'Obligación' and entry.currency == production.currency for entry in entries):
            blockers.append(f'{order_number}: no hay un importe que permita verificar el pago.')
        if any(entry.amount != 0 and entry.currency != production.currency for entry in entries):
            blockers.append(f'{order_number}: los movimientos usan monedas distintas.')
        current_balance = Decimal(balance(db, case.id, production.id)['balance'])
        if current_balance > 0:
            amount = f"{current_balance:,.2f}" if show_finances else ''
            blockers.append(f"{order_number}: quedan {production.currency} {amount} por cobrar." if show_finances else f'{order_number}: queda un saldo por cobrar.')
        elif current_balance < 0:
            amount = f"{abs(current_balance):,.2f}" if show_finances else ''
            blockers.append(f"{order_number}: hay {production.currency} {amount} pendientes de devolución." if show_finances else f'{order_number}: hay una devolución o saldo a favor pendiente.')
    open_tasks = db.scalars(select(Task).where(Task.case_id == case.id, Task.done_at.is_(None)).order_by(Task.due_at, Task.id)).all()
    for task in open_tasks:
        blockers.append(f"Hay un seguimiento pendiente para el {task.due_at.date().isoformat()}: {task.title}.")
    if case.pending_message:
        blockers.append('Hay una comunicación entrante pendiente de atender.')
    open_quotes = db.scalars(select(Quote).where(Quote.case_id == case.id, Quote.status == 'Pendiente').order_by(Quote.version)).all()
    if open_quotes:
        for quote in open_quotes:
            blockers.append(f"La cotización COT-{quote.id:05d}, versión {quote.version}, aún requiere una decisión o gestión.")
    return blockers


def case_query(db, user, filters):
    view = filters.get('view', 'pool')
    if view == 'pool':
        # Legacy URL and saved filters must never hide active unassigned work.
        filters = {key: value for key, value in filters.items() if key in ('view', 'sort', 'page', 'page_size')}
    # An actionable inbox is defined by actionable events and follow-ups, not attention status.
    if view in ('action', 'mine-action'):
        filters = {key: value for key, value in filters.items() if key not in ('status', 'lifecycle')}
    elif view == 'mine':
        filters = {key: value for key, value in filters.items() if key != 'lifecycle'}
    predicates = attention_predicates()
    total, overdue, payment, has_debt, has_credit, has_overdue = financial_expressions()
    delivery_state = sql_case((predicates['complete_delivery'], 'Completa'), (predicates['has_delivery'], 'Parcial'), else_='Pendiente')
    unfinished_order = select(Production.id).where(
        Production.case_id == Case.id, Production.cancelled_at.is_(None),
        select(Item.id).where(Item.production_id == Production.id,
            func.coalesce(select(func.sum(Delivery.quantity)).where(Delivery.item_id == Item.id).correlate(Item).scalar_subquery(), 0) < Item.quantity,
        ).correlate(Production).exists(),
    ).correlate(Case).exists()
    latest_id = select(func.max(Production.id)).where(Production.case_id == Case.id).correlate(Case).scalar_subquery()
    latest_production = aliased(Production)
    received = func.coalesce(select(func.sum(sql_case((Ledger.kind.in_(('Anticipo', 'Abono', 'Reversión de pago', 'Devolución')), -Ledger.amount), else_=0))).where(Ledger.production_id == latest_id).correlate(Case).scalar_subquery(), 0)
    query = select(Case, Client, User, latest_production, delivery_state, total, overdue, payment, predicates['requires_action'], received).join(Client, Client.id == Case.client_id).outerjoin(User, User.id == Case.owner_id).outerjoin(latest_production, and_(latest_production.case_id == Case.id, latest_production.id == latest_id)).where(visibility(db, user))
    if view not in ('history', 'all', 'finalized', 'mine'):
        query = query.where(Case.lifecycle == 'Activo')
    elif view == 'finalized':
        query = query.where(Case.lifecycle == 'Completado')
    financial = can(db, user, 'finance') or can(db, user, 'attention')
    if (view in ('balances', 'overdue', 'refunds', 'cancellation-review') or filters.get('payment')) and not financial:
        raise HTTPException(403, 'No tienes acceso a los saldos')
    if view == 'pool': query = query.where(Case.lifecycle == 'Activo', Case.owner_id.is_(None))
    elif view == 'action': query = query.where(predicates['requires_action'])
    elif view == 'mine': query = query.where(Case.owner_id == user.id, Case.lifecycle == 'Activo')
    elif view == 'mine-action': query = query.where(Case.owner_id == user.id, predicates['requires_action'])
    elif view == 'production': query = query.where(unfinished_order)
    elif view == 'ready': query = query.where(select(Production.id).where(Production.case_id == Case.id, Production.stage == 'READY', Production.classification_required.is_(False), Production.cancelled_at.is_(None)).exists())
    elif view == 'balances': query = query.where(has_debt, not_(predicates['cancellation_review']))
    elif view == 'refunds': query = query.where(has_credit)
    elif view == 'cancellation-review': query = query.where(predicates['cancellation_review'])
    elif view == 'overdue': query = query.where(has_overdue)
    elif view == 'history': pass
    elif view == 'finalized': pass
    elif view == 'all': pass
    else: raise HTTPException(422, 'Bandeja no reconocida')
    if filters.get('client_id'):
        try: client_id = int(filters['client_id'])
        except (TypeError, ValueError): raise HTTPException(422, 'client_id debe ser entero')
        if client_id < 1: raise HTTPException(422, 'client_id debe ser positivo')
        query = query.where(Case.client_id == client_id)
    if filters.get('status'): query = query.where(Case.status == filters['status'])
    if filters.get('lifecycle') in ('Activo', 'Completado'): query = query.where(Case.lifecycle == filters['lifecycle'])
    if filters.get('mode'): query = query.where(Case.mode == filters['mode'])
    if filters.get('priority'): query = query.where(Case.priority == filters['priority'])
    if filters.get('stage'): query = query.where(select(Production.id).where(Production.case_id == Case.id, Production.stage == filters['stage']).exists())
    if filters.get('payment'): query = query.where(payment == filters['payment'])
    if filters.get('blocked') == 'yes': query = query.where(select(Production.id).where(Production.case_id == Case.id, Production.blocked_since.is_not(None)).exists())
    if filters.get('due'):
        try: due_date = date_type.fromisoformat(str(filters['due']))
        except ValueError: raise HTTPException(422, 'due debe usar formato YYYY-MM-DD')
        query = query.where(select(Production.id).where(Production.case_id == Case.id, Production.committed_date <= due_date).exists())
    if filters.get('action'): query = query.where(func.lower(Case.next_action).contains(str(filters['action']).lower()))
    if filters.get('q'):
        raw_term = str(filters['q']).strip()
        term = normalize(raw_term)
        numeric_case = raw_term.isdigit() or (term.startswith('cg') and term[2:].isdigit())
        case_id = int(raw_term if raw_term.isdigit() else term[2:]) if numeric_case else None
        query = query.where(or_(Client.search_text.contains(term), func.lower(Case.subject).contains(raw_term.lower()), Case.id == case_id if case_id is not None else literal(False)))
    ordering = filters.get('sort', 'newest')
    if ordering == 'oldest': query = query.order_by(Case.created_at.asc(), Case.id.asc())
    elif ordering == 'due': query = query.order_by(Case.created_at.desc(), Case.id.desc())
    elif view == 'ready':
        payment_group = sql_case((received <= 0, 0), (total == 0, 2), (total < 0, 3), else_=1)
        query = query.order_by(payment_group.asc(), latest_production.stage_started_at.asc(), Case.id.asc())
    else: query = query.order_by(Case.created_at.desc(), Case.id.desc())
    return query


def _case_projection(db, user, entry, financial):
    case, client, owner, production, delivery_state, total, overdue, payment, requires_action, received = entry
    result = row(case)
    result['number'] = f'CG-{case.id:06d}'
    result['client'] = row(client, ('search_text',))
    result['owner_name'] = owner.name if owner else None
    result['production'] = row(production)
    if production:
        result['production']['order_number'] = f'OP-{production.id:05d}'
        result['production']['stage_label'] = STAGE_LABELS.get(production.stage) if production.stage else 'Pendiente de clasificación'
    result['delivery_status'] = delivery_state
    orders = db.scalars(select(Production).where(Production.case_id == case.id).order_by(Production.id)).all()
    result['productions'] = [{'id': order.id, 'order_number': f'OP-{order.id:05d}', 'stage': order.stage,
        'stage_label': STAGE_LABELS.get(order.stage) if order.stage else 'Pendiente de clasificación',
        'committed_date': str(order.committed_date) if order.committed_date else None,
        'delivery_status': delivery_status(db, case.id, order.id), 'cancelled_at': row(order)['cancelled_at'], 'blocked_since': row(order)['blocked_since']}
        for order in orders]
    result['production_count'] = len(orders)
    result['production_pending_count'] = sum(not order.cancelled_at and delivery_status(db, case.id, order.id) != 'Completa' for order in orders)
    result['requires_action'] = bool(requires_action)
    result['attention_status_label'] = case.status
    result['lifecycle'] = case.lifecycle
    result['completed_at'] = row(case)['completed_at']
    result['completed_by_id'] = case.completed_by_id
    if production:
        result['delivery_status'] = delivery_status(db, case.id, production.id)
        result['production']['stage_label'] = STAGE_LABELS.get(production.stage)
        if production.classification_required: result['production']['stage_label'] = 'Pendiente de clasificación'
        if result['delivery_status'] == 'Completa': result['production']['result_label'] = 'Entregado'
        result['production']['active_elapsed_seconds'] = None if production.cancelled_at or result['delivery_status'] == 'Completa' or production.classification_required else max(0, (now() - production.stage_started_at).total_seconds())
        if production.stage == 'READY':
            ready_seconds = max(0, (now() - production.stage_started_at).total_seconds())
            result['production']['ready_days'] = int(ready_seconds // 86400)
            result['production']['ready_at'] = row(production)['stage_started_at']
    if financial:
        result.update({'balance': str(total) if total is not None else None, 'overdue_balance': str(overdue) if overdue is not None else None, 'payment_status': payment, 'received_amount': str(received)})
        if production and production.stage == 'READY':
            order_balance = balance(db, case.id, production.id)['balance']
            result['pickup_payment_status'] = 'Sin anticipo' if received <= 0 else 'Saldo por revisar' if order_balance is None else 'Pagado' if Decimal(order_balance) == 0 else 'A favor' if Decimal(order_balance) < 0 else 'Con anticipo'
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
    views = ['pool', 'action', 'mine', 'mine-action', 'production', 'ready', 'history', 'finalized']
    if can(db, user, 'finance') or can(db, user, 'attention'):
        views += ['balances', 'overdue', 'refunds', 'cancellation-review']
    result = {view: db.scalar(select(func.count()).select_from(case_query(db, user, {'view': view}).order_by(None).subquery())) or 0 for view in views}
    result['production_orders'] = orders_count(db, user, 'production')
    result['ready_orders'] = orders_count(db, user, 'ready')
    return result


def _order_query(db, user, view, filters=None):
    filters = filters or {}
    has_item = select(Item.id).where(Item.production_id == Production.id).correlate(Production).exists()
    short_item = select(Item.id).where(Item.production_id == Production.id,
        func.coalesce(select(func.sum(Delivery.quantity)).where(Delivery.item_id == Item.id).correlate(Item).scalar_subquery(), 0) < Item.quantity).correlate(Production).exists()
    complete = and_(has_item, not_(short_item))
    query = select(Production, Case, Client).join(Case, Case.id == Production.case_id).join(Client, Client.id == Case.client_id).where(
        visibility(db, user), Case.lifecycle == 'Activo', Production.cancelled_at.is_(None), not_(complete))
    if view == 'ready': query = query.where(Production.stage == 'READY', Production.classification_required.is_(False), Production.blocked_since.is_(None))
    elif view != 'production': raise HTTPException(422, 'Bandeja de órdenes no reconocida')
    if filters.get('stage'): query = query.where(Production.stage == filters['stage'])
    if filters.get('due'):
        try: due_date = date_type.fromisoformat(str(filters['due']))
        except ValueError: raise HTTPException(422, 'due debe usar formato YYYY-MM-DD')
        query = query.where(Production.committed_date <= due_date)
    if filters.get('blocked') == 'yes': query = query.where(Production.blocked_since.is_not(None))
    return query.order_by(sql_case((Production.committed_date.is_(None), 1), else_=0), Production.committed_date.asc(), Production.id.asc())


def orders_count(db, user, view):
    return db.scalar(select(func.count()).select_from(_order_query(db, user, view).order_by(None).subquery())) or 0


def orders_list(db, user, filters):
    view = filters.get('view', 'production')
    query = _order_query(db, user, view, filters)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    try: page = max(1, int(filters.get('page', 1)))
    except (TypeError, ValueError): page = 1
    try: page_size = min(100, max(1, int(filters.get('page_size', 25))))
    except (TypeError, ValueError): page_size = 25
    financial = can(db, user, 'finance') or can(db, user, 'attention')
    result = []
    for production, case, client in db.execute(query.offset((page - 1) * page_size).limit(page_size)):
        order = row(production)
        order.update({'order_number': f'OP-{production.id:05d}', 'case_id': case.id,
            'case_number': f'CG-{case.id:06d}', 'subject': case.subject, 'client': row(client, ('search_text',)),
            'owner_id': case.owner_id, 'stage_label': STAGE_LABELS.get(production.stage) if production.stage else 'Pendiente de clasificación',
            'delivery_status': delivery_status(db, case.id, production.id)})
        order['item_count'] = db.scalar(select(func.count()).select_from(Item).where(Item.production_id == production.id)) or 0
        pending_quantities = []
        for item in db.scalars(select(Item).where(Item.production_id == production.id).order_by(Item.id)):
            delivered = db.scalar(select(func.coalesce(func.sum(Delivery.quantity), 0)).where(Delivery.item_id == item.id)) or 0
            remaining = item.quantity - delivered
            if remaining > 0: pending_quantities.append({'product': item.product, 'quantity': str(remaining), 'unit': item.unit})
        order['pending_quantities'] = pending_quantities
        if financial:
            order.update(balance(db, case.id, production.id))
        else:
            for key in ('price', 'advance_required', 'payment_terms', 'payment_due_date'): order.pop(key, None)
            for snapshot in ('specs_snapshot', 'approved_snapshot'):
                if order.get(snapshot):
                    value = dict(order[snapshot]); value['items'] = [{k: v for k, v in i.items() if k != 'unit_price'} for i in value['items']]; order[snapshot] = value
        result.append(order)
    return {'items': result, 'total': total, 'page': page, 'page_size': page_size, 'count_type': 'órdenes'}
