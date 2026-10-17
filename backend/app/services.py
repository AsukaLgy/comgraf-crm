import hashlib
import json
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from sqlalchemy import select, func
from .models import Case, Delivery, Event, Idempotency, Item, Ledger, Production, Quote, Task, User, now
from .security import can, require
from .repository import get_case, today, row, delivery_status, local_day, quantity_text, balance, attention_predicates, case_completion_blockers
from . import schemas as s

NEXT_STAGE = s.NEXT_STAGE
PREVIOUS_STAGES = s.PREVIOUS_STAGES
CURRENCY_HNL = 'HNL'


def fail(message, code=422):
    raise HTTPException(code, message)


def event(db, case, user, body, kind='Sistema', channel='Sistema', data=None):
    db.add(Event(case_id=case.id, actor_id=user.id, body=body, kind=kind, channel=channel, data=data or {}))
    case.updated_at = now()


# ---- Concurrencia, idempotencia y asignación principal ----
def mutation(db, user, case_id, data, action, permission, *, allow_completed=False):
    require(db, user, permission)
    case = get_case(db, user, case_id, lock=True)
    if permission == 'attention' and not can(db, user, 'assign') and case.owner_id != user.id:
        fail('Toma el caso antes de modificarlo', 403)
    payload = data.model_dump(mode='json', exclude={'version', 'key'})
    signature = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    previous = db.scalar(select(Idempotency).where(Idempotency.case_id == case_id, Idempotency.key == data.key))
    if previous:
        if previous.action != action or previous.request_hash != signature or previous.actor_id != user.id:
            fail('La clave de reintento ya pertenece a otra operación', 409)
        return case, False
    if case.lifecycle == 'Completado' and not allow_completed:
        fail('El caso está completado. Reactívalo antes de registrar nuevas gestiones.', 409)
    if case.version != data.version:
        fail('El expediente cambió. Actualiza los datos y revisa el formulario antes de reintentar.', 409)
    db.add(Idempotency(case_id=case_id, key=data.key, request_hash=signature, action=action, actor_id=user.id))
    case.version += 1
    return case, True


def active_user(db, user_id, role_permission=None):
    user = db.get(User, user_id) if user_id else None
    if not user or not user.active:
        fail('Selecciona un trabajador activo')
    if role_permission and not can(db, user, role_permission):
        fail('El trabajador no tiene el permiso requerido para esta asignación')
    return user


def take(db, user, case_id, data):
    require(db, user, 'attention')
    # Lock user first, serializing capacity checks across different cases.
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    case = get_case(db, user, case_id, lock=True)
    if case.lifecycle == 'Completado': fail('El caso está completado y no se puede tomar desde una bandeja activa', 409)
    if case.owner_id == user.id:
        return case
    if case.owner_id:
        fail('Otro trabajador ya tomó este caso', 409)
    pending = attention_predicates()['pending_assignment']
    count = db.scalar(select(func.count()).select_from(Case).where(Case.owner_id == user.id, pending))
    if count >= user.capacity: fail('Alcanzaste tu capacidad de expedientes pendientes (atención, producción, seguimiento o saldo)', 409)
    if case.version != data.version: fail('El caso cambió. Actualiza la bandeja.', 409)
    case.owner_id = user.id
    case.version += 1
    event(db, case, user, f'{user.name} tomó el caso', data={'action': 'assigned'})
    return case


def assign(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'assign', 'assign')
    if not changed: return case
    if data.owner_id: active_user(db, data.owner_id, 'attention')
    previous = case.owner_id
    case.owner_id = data.owner_id
    event(db, case, user, 'Responsable principal del expediente actualizado', data={'action': 'assigned', 'previous_owner_id': previous, 'owner_id': data.owner_id})
    return case


def update_case(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'update', 'attention')
    if not changed: return case
    old = case.status
    if old != 'Cerrado' and data.status == 'Cerrado':
        if db.scalar(select(Task.id).where(Task.case_id == case_id, Task.done_at.is_(None), Task.due_at <= now()).limit(1)):
            fail('Resuelve o reprograma los seguimientos vencidos antes de cerrar atención', 409)
        case.closed_at = now()
        case.pending_message = False
    elif old == 'Cerrado' and data.status == 'Abierto':
        case.closed_at = None
    case.status = data.status
    if data.priority is not None: case.priority = data.priority
    if data.next_action is not None: case.next_action = data.next_action
    event(db, case, user, f'Atención: {old} → {data.status}', data={'action': 'attention_status', 'from': old, 'to': data.status, 'origin': 'manual'})
    return case


def update_case_details(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'case_details', 'attention')
    if not changed: return case
    previous = {'priority': case.priority, 'next_action': case.next_action}
    if data.priority is not None: case.priority = data.priority
    if data.next_action is not None: case.next_action = data.next_action
    event(db, case, user, 'Datos de atención actualizados', data={
        'action': 'case_details', 'previous': previous,
        'current': {'priority': case.priority, 'next_action': case.next_action}})
    return case


def complete_case(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'case_complete', 'attention', allow_completed=True)
    if not changed: return case
    if case.lifecycle == 'Completado': fail('El caso ya está completado', 409)
    blockers = case_completion_blockers(db, case, show_finances=True)
    if blockers: fail('No se puede completar el caso: ' + ' '.join(blockers), 409)
    completed_at = now()
    old_status = case.status
    case.lifecycle = 'Completado'
    case.completed_at = completed_at
    case.completed_by_id = user.id
    case.status = 'Cerrado'
    case.closed_at = completed_at
    event(db, case, user, 'Caso completado', data={
        'action': 'case_completed', 'lifecycle_from': 'Activo', 'lifecycle_to': 'Completado',
        'attention_from': old_status, 'attention_to': 'Cerrado', 'origin': 'manual',
        'completed_at': completed_at.isoformat() + 'Z', 'actor_id': user.id})
    return case


def reactivate_case(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'case_reactivate', 'attention', allow_completed=True)
    if not changed: return case
    if case.lifecycle != 'Completado': fail('El caso ya está activo', 409)
    old_completed_at, old_completed_by = case.completed_at, case.completed_by_id
    case.lifecycle = 'Activo'
    case.completed_at = None
    case.completed_by_id = None
    case.status = 'Abierto'
    case.closed_at = None
    event(db, case, user, 'Caso reactivado', data={
        'action': 'case_reactivated', 'lifecycle_from': 'Completado', 'lifecycle_to': 'Activo',
        'attention_from': 'Cerrado', 'attention_to': 'Abierto', 'origin': 'manual',
        'previous_completed_at': old_completed_at.isoformat() + 'Z' if old_completed_at else None,
        'previous_completed_by_id': old_completed_by})
    return case


# ---- Actividad y seguimientos de atención ----
def add_event(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'event', 'attention')
    if not changed: return case
    if data.kind == 'Nota interna' and (data.channel != 'Nota' or data.direction != 'Interna'):
        fail('Una nota interna solo puede guardarse en el canal Nota')
    if data.kind == 'Interacción manual' and data.channel == 'Nota': fail('Selecciona el canal de la interacción')
    event(db, case, user, data.body, data.kind, data.channel, {'direction': data.direction, 'manual': True})
    if data.kind == 'Interacción manual':
        if data.direction == 'Entrante':
            case.pending_message = True
            if case.status == 'Cerrado':
                old = case.status
                case.status = 'Abierto'
                event(db, case, user, 'Atención abierta automáticamente por una interacción entrante', data={'action': 'attention_status', 'from': old, 'to': 'Abierto', 'origin': 'automatic'})
        if data.direction == 'Saliente' and case.first_response_at is None:
            case.first_response_at = now()
    return case


def add_task(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'task', 'attention')
    if not changed: return case
    active_user(db, data.owner_id, 'attention')
    db.add(Task(case_id=case.id, owner_id=data.owner_id, title=data.title, due_at=data.due_at))
    case.next_action = data.title
    event(db, case, user, f'Seguimiento programado: {data.title}', data={'due_at': data.due_at.isoformat() + 'Z', 'owner_id': data.owner_id})
    if data.due_at <= now() and case.status == 'Cerrado':
        old = case.status
        case.status = 'Abierto'
        event(db, case, user, 'Atención abierta automáticamente por un seguimiento vencido', data={'action': 'attention_status', 'from': old, 'to': 'Abierto', 'origin': 'automatic'})
    return case


def schedule_pickup_followup(db, case, user, due_at=None):
    open_pickup = db.scalar(select(Task.id).where(Task.case_id == case.id, Task.category == 'PICKUP', Task.done_at.is_(None)).limit(1))
    if open_pickup:
        return db.get(Task, open_pickup)
    due_at = due_at or now() + timedelta(days=7)
    task = Task(case_id=case.id, owner_id=case.owner_id, category='PICKUP', title='Contactar al cliente por trabajo listo para recoger', due_at=due_at)
    db.add(task)
    event(db, case, user, 'Seguimiento de recogida programado para siete días después de quedar listo', data={
        'action': 'pickup_followup_created', 'due_at': due_at.isoformat() + 'Z', 'owner_id': case.owner_id})
    return task


def complete_task(db, user, case_id, task_id, data):
    case, changed = mutation(db, user, case_id, data, f'task:{task_id}', 'attention')
    if not changed: return case
    task = db.get(Task, task_id)
    if not task or task.case_id != case_id: fail('Seguimiento no encontrado', 404)
    if task.done_at: fail('El seguimiento ya está completado', 409)
    production = db.get(Production, case.id)
    if data.schedule_next_pickup and (task.category != 'PICKUP' or not production or production.cancelled_at or delivery_status(db, case.id) == 'Completa'):
        fail('No se puede programar otro seguimiento de recogida para este trabajo')
    task.done_at = now()
    event(db, case, user, f'Seguimiento completado: {task.title}')
    if data.note.strip():
        event(db, case, user, data.note.strip(), 'Nota interna', 'Nota', {'direction': 'Interna', 'manual': True, 'source': 'pickup_followup'})
    if data.schedule_next_pickup:
        due_at = now() + timedelta(days=7)
        next_task = Task(case_id=case.id, owner_id=case.owner_id, category='PICKUP', title='Contactar al cliente por trabajo listo para recoger', due_at=due_at)
        db.add(next_task)
        event(db, case, user, 'Siguiente seguimiento de recogida programado', data={'action': 'pickup_followup_created', 'due_at': due_at.isoformat() + 'Z', 'owner_id': case.owner_id})
    return case


def reschedule_task(db, user, case_id, task_id, data):
    case, changed = mutation(db, user, case_id, data, f'task_reschedule:{task_id}', 'attention')
    if not changed: return case
    task = db.get(Task, task_id)
    if not task or task.case_id != case_id: fail('Seguimiento no encontrado', 404)
    if task.done_at: fail('No se puede reprogramar un seguimiento completado', 409)
    previous = task.due_at
    task.due_at = data.due_at
    event(db, case, user, f'Seguimiento reprogramado: {task.title}', data={'action': 'task_rescheduled', 'task_id': task.id, 'previous_due_at': previous.isoformat() + 'Z', 'due_at': data.due_at.isoformat() + 'Z'})
    if data.due_at <= now() and case.status == 'Cerrado':
        old = case.status
        case.status = 'Abierto'
        event(db, case, user, 'Atención abierta automáticamente por un seguimiento vencido', data={'action': 'attention_status', 'from': old, 'to': 'Abierto', 'origin': 'automatic'})
    return case


def reopen_overdue_attention(db):
    """Idempotent recovery pass; called at startup and periodically by the app."""
    due_cases = db.scalars(select(Case).where(
        Case.status == 'Cerrado',
        Case.lifecycle == 'Activo',
        select(Task.id).where(Task.case_id == Case.id, Task.done_at.is_(None), Task.due_at <= now()).exists(),
    ).with_for_update()).all()
    for case in due_cases:
        case.status = 'Abierto'
        case.version += 1
        db.add(Event(case_id=case.id, actor_id=None, kind='Sistema', channel='Sistema',
            body='Atención abierta automáticamente por vencimiento de seguimiento',
            data={'action': 'attention_status', 'from': 'Cerrado', 'to': 'Abierto', 'origin': 'automatic'}))
    return len(due_cases)


def total(items):
    amount = sum((Decimal(i['quantity']) * Decimal(i['unit_price']) for i in items), Decimal('0')).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    if amount > Decimal('999999999999.99'):
        fail('El importe total supera el máximo permitido')
    return amount


# ---- Cotizaciones y ampliación del expediente a producción ----
def add_quote(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'quote', 'attention')
    if not changed: return case
    if data.valid_until < today(): fail('La vigencia ya terminó')
    items = [i.model_dump(mode='json') for i in data.items]
    version = (db.scalar(select(func.max(Quote.version)).where(Quote.case_id == case.id)) or 0) + 1
    db.add(Quote(case_id=case.id, version=version, items=items, total=total(items), currency=CURRENCY_HNL, valid_until=data.valid_until))
    event(db, case, user, f'Cotización v{version} preparada', data={'financial': True})
    return case


def decide_quote(db, user, case_id, quote_id, data):
    case, changed = mutation(db, user, case_id, data, f'quote_decision:{quote_id}', 'attention')
    if not changed: return case
    quote = db.get(Quote, quote_id)
    if not quote or quote.case_id != case.id: fail('Cotización no encontrada', 404)
    if quote.status != 'Pendiente': fail('La versión ya tiene una decisión. Crea una versión nueva para revisarla.', 409)
    if data.status == 'Aceptada' and quote.valid_until < today(): fail('Cotización vencida')
    quote.status, quote.decision_evidence, quote.decided_at = data.status, data.evidence, now()
    event(db, case, user, f'Cotización v{quote.version}: {data.status}. {data.evidence}', data={'financial': True, 'quote_id': quote.id})
    return case


def convert(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'convert', 'attention')
    if not changed: return case
    if case.mode == 'Producción': fail('El expediente ya está en producción', 409)
    items = [i.model_dump(mode='json') for i in data.items]
    if data.quote_id:
        quote = db.get(Quote, data.quote_id)
        if not quote or quote.case_id != case.id or quote.status != 'Aceptada': fail('Selecciona una cotización aceptada de este caso')
        if quote.currency != CURRENCY_HNL: fail('Esta cotización histórica está en USD. Revísala y crea una nueva versión en HNL antes de generar producción.', 409)
        items = quote.items
    else:
        quote = None
    if not items: fail('Agrega al menos un producto')
    price = total(items)
    if data.advance_required > price: fail('El anticipo no puede superar el precio')
    production = Production(case_id=case.id, quote_id=data.quote_id, stage='NEW',
        requested_date=data.requested_date, original_committed_date=data.committed_date,
        committed_date=data.committed_date, price=price, currency=CURRENCY_HNL,
        payment_terms=data.payment_terms, advance_required=data.advance_required,
        payment_due_date=data.payment_due_date, delivery_method=data.delivery_method, instructions=data.instructions)
    db.add(production)
    db.flush()
    order_items = [Item(case_id=case.id, **item) for item in items]
    db.add_all(order_items)
    db.flush()
    production.specs_snapshot = {'items': [row(item, ('substage',)) for item in order_items]}
    db.add(Ledger(case_id=case.id, kind='Obligación', amount=price, currency=CURRENCY_HNL,
        due_date=data.payment_due_date, reference='Orden de producción', actor_id=user.id))
    case.mode = 'Producción'
    order_number = f'OP-{case.id:05d}'
    quote_number = f'COT-{quote.id:05d}' if quote else None
    body = f'{user.name} generó la orden {order_number}'
    if quote:
        body += f' a partir de la cotización {quote_number}, versión {quote.version}'
    event(db, case, user, body, data={'action': 'converted', 'order_number': order_number,
        'quote_id': quote.id if quote else None, 'quote_number': quote_number,
        'quote_version': quote.version if quote else None, 'currency': CURRENCY_HNL})
    return case


def get_production(db, case):
    production = db.get(Production, case.id)
    if not production: fail('El caso sigue en Atención')
    if production.cancelled_at: fail('La producción está cancelada', 409)
    return production


# ---- Ciclo de producción: etapas, bloqueos y programación ----
def change_stage(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'stage', 'production')
    if not changed: return case
    production = get_production(db, case)
    if production.blocked_since: fail('Resuelve el bloqueo antes de cambiar de etapa')
    if production.classification_required or production.stage is None:
        legacy = production.legacy_stage
        production.stage = data.stage
        production.classification_required = False
        production.stage_started_at = now()
        event(db, case, user, f'Clasificación explícita de etapa histórica: {legacy or "Sin etapa legible"} → {s.STAGE_LABELS[data.stage]}', data={'action': 'stage_classified', 'legacy_stage': legacy, 'stage': data.stage, 'origin': 'manual_classification'})
        if data.stage == 'READY': schedule_pickup_followup(db, case, user)
        return case
    old = production.stage
    if data.stage == old: fail('La etapa ya está activa', 409)
    if delivery_status(db, case.id) == 'Completa': fail('La entrega está completa; no se puede reabrir la producción desde este caso', 409)
    next_stage = NEXT_STAGE.get(old)
    moving_back = data.stage in PREVIOUS_STAGES.get(old, set())
    if data.stage != next_stage and not moving_back: fail('La etapa debe avanzar a la siguiente o retroceder con motivo explícito')
    if moving_back and not data.reason: fail('El retroceso requiere motivo')
    if delivery_status(db, case.id) != 'Pendiente' and moving_back: fail('Ya existen entregas; registra el retrabajo en un caso relacionado')
    duration = (now() - production.stage_started_at).total_seconds()
    production.stage, production.stage_started_at = data.stage, now()
    event(db, case, user, f'Producción: {s.STAGE_LABELS[old]} → {s.STAGE_LABELS[data.stage]}' + (f'. {data.reason}' if data.reason else ''), data={'action': 'stage', 'from': old, 'to': data.stage, 'seconds': duration, 'rework': moving_back})
    if data.stage == 'READY': schedule_pickup_followup(db, case, user)
    return case


def block(db, user, case_id, data, unblock=False):
    case, changed = mutation(db, user, case_id, data, 'unblock' if unblock else 'block', 'production')
    if not changed: return case
    production = get_production(db, case)
    if delivery_status(db, case.id) == 'Completa': fail('El trabajo ya fue entregado')
    if unblock:
        if not production.blocked_since: fail('El trabajo no está bloqueado')
        seconds = (now() - production.blocked_since).total_seconds()
        production.blocked_since, production.block_reason = None, None
        event(db, case, user, f'Bloqueo resuelto: {data.reason}', data={'action': 'unblocked', 'seconds': seconds})
    else:
        if production.blocked_since: fail('Ya existe un bloqueo activo', 409)
        production.blocked_since, production.block_reason = now(), data.reason
        event(db, case, user, f'Producción bloqueada: {data.reason}', data={'action': 'blocked'})
    return case


def schedule(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'schedule', 'production')
    if not changed: return case
    production = get_production(db, case)
    if delivery_status(db, case.id) == 'Completa': fail('El trabajo ya fue entregado')
    previous_scheduled, previous_committed = production.scheduled_at, production.committed_date
    if not production.original_committed_date: production.original_committed_date = data.committed_date
    production.committed_date, production.scheduled_at = data.committed_date, data.scheduled_at
    event(db, case, user, 'Programación y compromiso actualizados', data={'action': 'schedule', 'previous': {'scheduled_at': previous_scheduled.isoformat() + 'Z' if previous_scheduled else None, 'committed_date': str(previous_committed) if previous_committed else None}, 'current': {'scheduled_at': data.scheduled_at.isoformat() + 'Z', 'committed_date': str(data.committed_date)}})
    return case


def cancel(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'cancel', 'production')
    if not changed: return case
    production = db.get(Production, case.id)
    if not production: fail('No existe una orden de producción')
    if production.cancelled_at: fail('La producción ya está anulada; usa la acción de resolución económica', 409)
    if delivery_status(db, case.id) == 'Completa':
        fail('La orden está completamente entregada; gestiona devolución o reclamo sin anular producción')
    production.cancelled_stage = production.stage
    production.cancellation_reason = data.reason
    production.cancelled_at = now()
    production.cancellation_actor_id = user.id
    duration = max(0, (production.cancelled_at - production.stage_started_at).total_seconds())
    can_resolve = can(db, user, 'finance') and can(db, user, 'adjust')
    if can_resolve and data.treatment is None:
        fail('Selecciona el tratamiento económico antes de confirmar la anulación')
    if data.treatment is not None:
        if not can_resolve: fail('El tratamiento económico requiere permisos financieros y de ajustes', 403)
        resolve_cancellation(db, user, case, production, data)
    else:
        production.cancellation_review_pending = True
    totals = cancellation_financials(db, case.id)
    event(db, case, user, f'Trabajo anulado: {data.reason}', data={'action': 'cancelled', 'reason': data.reason,
        'stage': production.cancelled_stage, 'seconds': duration,
        'economic_status': 'resolved' if not production.cancellation_review_pending else 'review_pending',
        'final_amount': str(production.cancellation_final_amount) if production.cancellation_final_amount is not None else None,
        'adjustment': str(totals.get('adjustment', Decimal('0')))})
    return case


def resolve_cancellation_request(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'cancel_resolution', 'adjust')
    if not changed: return case
    require(db, user, 'finance')
    production = db.get(Production, case.id)
    if not production or not production.cancelled_at or not production.cancellation_review_pending:
        fail('Este expediente no tiene una resolución económica de anulación pendiente', 409)
    if delivery_status(db, case.id) == 'Completa':
        fail('La orden está completamente entregada; registra una devolución o reclamo por separado', 409)
    resolve_cancellation(db, user, case, production, data)
    return case


def cancellation_financials(db, case_id):
    entries = list(db.scalars(select(Ledger).where(Ledger.case_id == case_id).order_by(Ledger.id)))
    charges = sum((e.amount for e in entries if e.kind in ('Obligación', 'Ajuste', 'Ajuste de anulación')), Decimal('0'))
    received = -sum((e.amount for e in entries if e.kind in ('Anticipo', 'Abono', 'Reversión de pago')), Decimal('0'))
    refunded = sum((e.amount for e in entries if e.kind == 'Devolución'), Decimal('0'))
    return {'charges': charges, 'payments_received': received, 'refunds_paid': refunded,
        'net_received': received - refunded,
        'current_balance': sum((e.amount for e in entries), Decimal('0'))}


def resolve_cancellation(db, user, case, production, data):
    require(db, user, 'finance')
    require(db, user, 'adjust')
    if production.currency != CURRENCY_HNL:
        fail('La orden histórica está en USD; requiere revisión manual de moneda y no puede ajustarse en HNL', 409)
    if data.treatment is None: fail('Selecciona el tratamiento económico de la anulación')
    final_amount = Decimal('0.00') if data.treatment == 'SIN_CARGO' else data.final_amount
    if final_amount is None: fail('Indica el importe final acordado')
    totals = cancellation_financials(db, case.id)
    adjustment = (final_amount - totals['charges']).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if adjustment:
        db.add(Ledger(case_id=case.id, kind='Ajuste de anulación', amount=adjustment,
            currency=production.currency, due_date=None, reference='Resolución de anulación comercial',
            reason=data.reason, actor_id=user.id))
    production.cancellation_final_amount = final_amount
    production.cancellation_review_pending = False
    production.cancellation_resolved_at = now()
    production.cancellation_reason = production.cancellation_reason or data.reason
    production.cancellation_actor_id = user.id
    resulting_balance = totals['current_balance'] + adjustment
    event(db, case, user, f'Anulación resuelta: cargo final {final_amount} HNL; ajuste {adjustment} HNL', data={
        'action': 'cancellation_resolved', 'reason': data.reason, 'treatment': data.treatment,
        'final_amount': str(final_amount), 'previous_charges': str(totals['charges']),
        'net_payments_received': str(totals['net_received']), 'adjustment': str(adjustment),
        'resulting_balance': str(resulting_balance), 'actor_id': user.id,
        'resolved_at': now().isoformat() + 'Z'})


def refund(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'refund', 'finance')
    if not changed: return case
    production = db.get(Production, case.id)
    if not production:
        fail('No existe una orden de producción')
    is_resolved_cancellation = production.cancelled_at and not production.cancellation_review_pending and production.cancellation_final_amount is not None
    if not is_resolved_cancellation and delivery_status(db, case.id) != 'Completa':
        fail('La devolución requiere una anulación resuelta o una entrega completa')
    if production.currency != CURRENCY_HNL: fail('La orden histórica requiere revisión manual de moneda', 409)
    due = max(Decimal('0'), -Decimal(balance(db, case.id)['balance']))
    if data.amount > due: fail('La devolución supera el saldo a favor pendiente')
    db.add(Ledger(case_id=case.id, kind='Devolución', amount=data.amount, currency=production.currency,
        reference=data.reference, reason='Devolución realizada y confirmada por el usuario', actor_id=user.id))
    event(db, case, user, f'Devolución registrada: {data.amount} {production.currency}. {data.reference}',
        data={'action': 'refund', 'amount': str(data.amount), 'reference': data.reference, 'actor_id': user.id})
    return case


# ---- Entregas y control operativo de cobros ----
def deliver(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'delivery', 'production')
    if not changed: return case
    production = get_production(db, case)
    if production.stage != 'READY' or production.classification_required or production.blocked_since: fail('El trabajo debe estar clasificado, listo y sin bloqueos')
    if data.delivered_at > now(): fail('La entrega no puede estar en el futuro')
    item = db.get(Item, data.item_id)
    if not item or item.case_id != case.id: fail('Partida no encontrada', 404)
    delivered = db.scalar(select(func.coalesce(func.sum(Delivery.quantity), 0)).where(Delivery.item_id == item.id))
    if delivered + data.quantity > item.quantity: fail('La cantidad supera lo pendiente de esta partida')
    db.add(Delivery(case_id=case.id, item_id=item.id, quantity=data.quantity, delivered_at=data.delivered_at, receiver=data.receiver, method=data.method, evidence=data.evidence, actor_id=user.id))
    db.flush()
    payment_amount = getattr(data, 'payment_amount', None)
    payment_reference = getattr(data, 'payment_reference', None)
    if bool(payment_amount) != bool(payment_reference): fail('Para registrar un pago junto a la entrega indica importe y referencia')
    if payment_amount:
        require(db, user, 'finance')
        if production.currency != CURRENCY_HNL: fail('Esta orden histórica está en USD; no admite pagos nuevos en HNL', 409)
        current_balance = Decimal(balance(db, case.id)['balance'])
        if current_balance <= 0 or payment_amount > current_balance: fail('El pago debe ser positivo y no superar el saldo actual')
        db.add(Ledger(case_id=case.id, kind='Abono', amount=-payment_amount, currency=production.currency, reference=payment_reference, actor_id=user.id))
        event(db, case, user, f'Abono: {payment_amount} {production.currency}. {payment_reference}', data={'financial': True, 'action': 'payment'})
    event(db, case, user, f'Entrega: {quantity_text(data.quantity)} {item.unit} de {item.product} a {data.receiver}', data={'action': 'delivery', 'item_id': item.id, 'on_time_original': bool(production.original_committed_date and local_day(data.delivered_at) <= production.original_committed_date)})
    if delivery_status(db, case.id) == 'Completa':
        duration = (now() - production.stage_started_at).total_seconds()
        event(db, case, user, 'Entrega completa. Se detiene el tiempo activo de producción y la situación de pago se conserva.', data={'action': 'delivery_completed', 'stage': production.stage, 'seconds': duration})
        pickup_tasks = db.scalars(select(Task).where(Task.case_id == case.id, Task.category == 'PICKUP', Task.done_at.is_(None))).all()
        for task in pickup_tasks: task.done_at = now()
        if pickup_tasks:
            event(db, case, user, 'Seguimiento de recogida resuelto automáticamente por entrega completa', data={'action': 'pickup_followup_resolved', 'task_ids': [task.id for task in pickup_tasks], 'origin': 'automatic'})
    return case


def pay(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'payment', 'finance')
    if not changed: return case
    production = db.get(Production, case.id)
    if not production: fail('Registra primero la orden y su obligación')
    if production.currency != CURRENCY_HNL: fail('Esta orden histórica está en USD. No registres el abono como lempiras; requiere revisión explícita de moneda.', 409)
    db.add(Ledger(case_id=case.id, kind=data.kind, amount=-data.amount, currency=production.currency, reference=data.reference, actor_id=user.id))
    event(db, case, user, f'{data.kind}: {data.amount} {production.currency}. {data.reference}', data={'financial': True, 'action': 'payment'})
    return case


def adjust(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'adjust', 'adjust')
    if not changed: return case
    production = db.get(Production, case.id)
    if not production or not data.amount: fail('El ajuste requiere una producción y un importe distinto de cero')
    if production.currency != CURRENCY_HNL: fail('Esta orden histórica está en USD y requiere revisión explícita de moneda.', 409)
    db.add(Ledger(case_id=case.id, kind='Ajuste', amount=data.amount, currency=production.currency, due_date=data.due_date, reference='Ajuste autorizado', reason=data.reason, actor_id=user.id))
    event(db, case, user, f'Ajuste: {data.amount} {production.currency}. {data.reason}', data={'financial': True})
    return case


def reverse_payment(db, user, case_id, entry_id, data):
    case, changed = mutation(db, user, case_id, data, f'reverse:{entry_id}', 'adjust')
    if not changed: return case
    entry = db.get(Ledger, entry_id)
    if not entry or entry.case_id != case.id or entry.kind not in ('Anticipo', 'Abono'): fail('Selecciona un abono o anticipo')
    if db.scalar(select(Ledger.id).where(Ledger.reverses_id == entry.id)): fail('El pago ya fue revertido', 409)
    production = db.get(Production, case.id)
    db.add(Ledger(case_id=case.id, kind='Reversión de pago', amount=-entry.amount, currency=entry.currency, due_date=production.payment_due_date, reference=f'Reversión #{entry.id}', reason=data.reason, reverses_id=entry.id, actor_id=user.id))
    event(db, case, user, f'Pago #{entry.id} revertido: {data.reason}', data={'financial': True})
    return case
