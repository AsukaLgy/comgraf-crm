import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from typing import get_args
from fastapi import HTTPException
from sqlalchemy import select, func
from .models import Case, Delivery, Event, Idempotency, Item, Ledger, Production, Quote, Task, User, now
from .security import can, require
from .repository import get_case, today, row, delivery_status, local_day, quantity_text
from .schemas import Stage

# Keep workflow ordering derived from the server-validated stage values.
STAGES = get_args(Stage)
CURRENCY_HNL = 'HNL'


def fail(message, code=422):
    raise HTTPException(code, message)


def event(db, case, user, body, kind='Sistema', channel='Sistema', data=None):
    db.add(Event(case_id=case.id, actor_id=user.id, body=body, kind=kind, channel=channel, data=data or {}))
    case.updated_at = now()


# ---- Concurrencia, idempotencia y asignación principal ----
def mutation(db, user, case_id, data, action, permission):
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
    if case.owner_id == user.id:
        return case
    if case.owner_id:
        fail('Otro trabajador ya tomó este caso', 409)
    if case.status == 'Cerrado': fail('El caso está cerrado', 409)
    count = db.scalar(select(func.count()).select_from(Case).where(Case.owner_id == user.id, Case.status != 'Cerrado'))
    if count >= user.capacity: fail('Alcanzaste tu capacidad de casos abiertos', 409)
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
    if old == 'Cerrado' and data.status != old:
        require(db, user, 'reopen')
        if not data.reason: fail('La reapertura requiere motivo')
        case.closed_at = None
    if old != 'Cerrado' and data.status == 'Cerrado':
        if not data.reason or not data.summary: fail('Indica motivo y resumen del cierre')
        if db.scalar(select(Task.id).where(Task.case_id == case_id, Task.done_at.is_(None)).limit(1)):
            fail('Completa los seguimientos pendientes antes de cerrar')
        case.closed_at = now()
        case.close_reason = data.reason
        case.close_summary = data.summary
    case.status, case.priority = data.status, data.priority
    case.next_action, case.pending_message = data.next_action, data.pending_message
    event(db, case, user, f'Atención: {old} → {data.status}. {data.reason} {data.summary}', data={'action': 'attention_status', 'from': old, 'to': data.status})
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
        if data.direction == 'Entrante': case.pending_message = True
        else:
            case.pending_message = False
            if case.first_response_at is None: case.first_response_at = now()
    return case


def add_task(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'task', 'attention')
    if not changed: return case
    active_user(db, data.owner_id, 'attention')
    if case.status == 'Cerrado': fail('Reabre el caso antes de programar un seguimiento')
    db.add(Task(case_id=case.id, owner_id=data.owner_id, title=data.title, due_at=data.due_at))
    case.next_action = data.title
    event(db, case, user, f'Seguimiento programado: {data.title}', data={'due_at': data.due_at.isoformat() + 'Z', 'owner_id': data.owner_id})
    return case


def complete_task(db, user, case_id, task_id, data):
    case, changed = mutation(db, user, case_id, data, f'task:{task_id}', 'attention')
    if not changed: return case
    task = db.get(Task, task_id)
    if not task or task.case_id != case_id: fail('Seguimiento no encontrado', 404)
    if task.done_at: fail('El seguimiento ya está completado', 409)
    task.done_at = now()
    event(db, case, user, f'Seguimiento completado: {task.title}')
    return case


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
    if case.status == 'Cerrado': fail('Reabre la atención antes de convertir')
    items = [i.model_dump(mode='json') for i in data.items]
    if data.quote_id:
        quote = db.get(Quote, data.quote_id)
        if not quote or quote.case_id != case.id or quote.status != 'Aceptada': fail('Selecciona una cotización aceptada de este caso')
        if quote.currency != CURRENCY_HNL: fail('Esta cotización histórica está en USD. Revísala y crea una nueva versión en HNL antes de generar producción.', 409)
        items = quote.items
    if not items: fail('Agrega al menos un producto')
    price = total(items)
    if data.advance_required > price: fail('El anticipo no puede superar el precio')
    production = Production(case_id=case.id, quote_id=data.quote_id,
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
    event(db, case, user, f'Orden de producción generada por {user.name} · Preparación', data={'action': 'converted', 'quote_id': data.quote_id, 'currency': CURRENCY_HNL})
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
    old = production.stage
    source, target = STAGES.index(old), STAGES.index(data.stage)
    if target == source: fail('La etapa ya está activa', 409)
    if production.blocked_since: fail('Resuelve el bloqueo antes de cambiar de etapa')
    if target > source + 1: fail('No se pueden saltar etapas')
    if target < source and not data.reason: fail('El retroceso requiere motivo')
    if old == 'Entregado': fail('Un trabajo entregado no puede retroceder; abre un caso relacionado de retrabajo')
    if target == 1 and (not production.scheduled_at or not production.committed_date):
        fail('Define inicio programado y fecha comprometida antes de avanzar')
    if data.stage == 'Entregado': fail('La etapa Entregado se establece al completar las entregas')
    if target < source and delivery_status(db, case.id) != 'Pendiente': fail('Ya existen entregas; registra el retrabajo en un caso relacionado')
    duration = (now() - production.stage_started_at).total_seconds()
    production.stage, production.stage_started_at = data.stage, now()
    event(db, case, user, f'Producción: {old} → {data.stage}. {data.reason}', data={'action': 'stage', 'from': old, 'to': data.stage, 'seconds': duration, 'rework': target < source})
    return case


def block(db, user, case_id, data, unblock=False):
    case, changed = mutation(db, user, case_id, data, 'unblock' if unblock else 'block', 'production')
    if not changed: return case
    production = get_production(db, case)
    if production.stage == 'Entregado': fail('El trabajo ya fue entregado')
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
    if production.stage == 'Entregado': fail('El trabajo ya fue entregado')
    previous_scheduled, previous_committed = production.scheduled_at, production.committed_date
    if not production.original_committed_date: production.original_committed_date = data.committed_date
    production.committed_date, production.scheduled_at = data.committed_date, data.scheduled_at
    event(db, case, user, 'Programación y compromiso actualizados', data={'action': 'schedule', 'previous': {'scheduled_at': previous_scheduled.isoformat() + 'Z' if previous_scheduled else None, 'committed_date': str(previous_committed) if previous_committed else None}, 'current': {'scheduled_at': data.scheduled_at.isoformat() + 'Z', 'committed_date': str(data.committed_date)}})
    return case


def cancel(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'cancel', 'assign')
    if not changed: return case
    production = get_production(db, case)
    if production.stage == 'Entregado': fail('No se puede cancelar una producción completamente entregada')
    production.cancelled_at = now()
    event(db, case, user, f'Producción cancelada: {data.reason}. Los cobros y entregas se conservan.', data={'action': 'cancelled'})
    return case


# ---- Entregas y control operativo de cobros ----
def deliver(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'delivery', 'production')
    if not changed: return case
    production = get_production(db, case)
    if production.stage != 'Listo para entregar' or production.blocked_since: fail('El trabajo debe estar listo y sin bloqueos')
    if data.delivered_at > now(): fail('La entrega no puede estar en el futuro')
    item = db.get(Item, data.item_id)
    if not item or item.case_id != case.id: fail('Partida no encontrada', 404)
    delivered = db.scalar(select(func.coalesce(func.sum(Delivery.quantity), 0)).where(Delivery.item_id == item.id))
    if delivered + data.quantity > item.quantity: fail('La cantidad supera lo pendiente de esta partida')
    db.add(Delivery(case_id=case.id, item_id=item.id, quantity=data.quantity, delivered_at=data.delivered_at, receiver=data.receiver, method=data.method, evidence=data.evidence, actor_id=user.id))
    db.flush()
    event(db, case, user, f'Entrega: {quantity_text(data.quantity)} {item.unit} de {item.product} a {data.receiver}', data={'action': 'delivery', 'item_id': item.id, 'on_time_original': bool(production.original_committed_date and local_day(data.delivered_at) <= production.original_committed_date)})
    if delivery_status(db, case.id) == 'Completa':
        duration = (now() - production.stage_started_at).total_seconds()
        production.stage, production.stage_started_at = 'Entregado', now()
        event(db, case, user, 'Entrega completa. La situación de pago se conserva.', data={'action': 'stage', 'from': 'Listo para entregar', 'to': 'Entregado', 'seconds': duration})
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
