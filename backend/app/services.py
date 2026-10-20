import hashlib
import json
import uuid
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from fastapi import HTTPException
from sqlalchemy import select, func
from .models import Case, Client, Contact, Delivery, Event, Idempotency, Item, Ledger, PartialReleaseRequest, Production, Quote, Task, User, now
from .security import can, require, require_binding_scanner
from .repository import get_case, today, row, delivery_status, local_day, quantity_text, balance, attention_predicates, case_completion_blockers
from . import schemas as s
from .cai import CAI_BINDINGS, cai_summary

NEXT_STAGE = s.NEXT_STAGE
PREVIOUS_STAGES = s.PREVIOUS_STAGES
CURRENCY_HNL = 'HNL'


def fail(message, code=422):
    raise HTTPException(code, message)


def event(db, case, user, body, kind='Sistema', channel='Sistema', data=None):
    db.add(Event(case_id=case.id, actor_id=user.id if user else None, body=body, kind=kind, channel=channel, data=data or {}))
    case.updated_at = now()


def attach_incoming_mail(db, case, message, body, actor=None, *, event_kind='Correo entrante', event_data=None,
                         reopen_origin='incoming_email', reopen_body='Atención abierta automáticamente por correo entrante'):
    """Apply the shared attention and history rules for an imported or classified message."""
    message.case_id = case.id
    if not message.thread_root:
        message.thread_root = message.message_id or message.in_reply_to
    if case.lifecycle == 'Completado':
        message.status = 'Pendiente de clasificación'
        return False
    message.status = 'Vinculado'
    case.pending_message = True
    if case.status == 'Cerrado':
        previous = case.status
        case.status = 'Abierto'
        case.closed_at = None
        event(db, case, actor, reopen_body, data={
            'action': 'attention_status', 'from': previous, 'to': 'Abierto', 'origin': reopen_origin,
        })
    data = {
        'direction': 'Entrante', 'email_message_id': message.id, 'message_id': message.message_id,
        'thread_root': message.thread_root,
    }
    data.update(event_data or {})
    event(db, case, actor, body, kind=event_kind, channel='Correo', data=data)
    case.version += 1
    return True


def create_incoming_email_case(db, client, message, subject, description, event_body):
    """Create the standard open, unassigned attention case for a newly classified sender."""
    case = Case(client_id=client.id, subject=(subject or 'Consulta recibida por correo')[:240],
        description=(description or '')[:5000], kind='Consulta', channel='Correo', owner_id=None,
        status='Abierto', mode='Atención', pending_message=True, lifecycle='Activo')
    db.add(case)
    db.flush()
    attach_incoming_mail(db, case, message, event_body, event_data={'automatic': True})
    return case


def finalize_case(db, case, actor_id, *, origin, action_trigger, regularization_run=None):
    """Persist one lifecycle transition and its audit event for every completion path."""
    if case.lifecycle != 'Activo':
        return False
    completed_at = now()
    old_status = case.status
    case.lifecycle = 'Completado'
    case.completed_at = completed_at
    case.completed_by_id = actor_id
    case.status = 'Cerrado'
    case.closed_at = completed_at
    body = 'Solicitud regularizada durante la actualización' if origin == 'historical_regularization' else 'Solicitud completada'
    db.add(Event(case_id=case.id, actor_id=actor_id, kind='Sistema', channel='Sistema',
        body=body, data={
            'action': 'case_completed', 'lifecycle_from': 'Activo', 'lifecycle_to': 'Completado',
            'attention_from': old_status, 'attention_to': 'Cerrado', 'origin': origin,
            'action_trigger': action_trigger,
            'regularization_run': regularization_run,
            'completed_at': completed_at.isoformat() + 'Z', 'actor_id': actor_id}))
    case.updated_at = completed_at
    return True


def complete_resolved_production(db, case, user, action_trigger):
    """Automatic completion is evaluated only after an operation resolves real work."""
    productions = db.scalars(select(Production).where(Production.case_id == case.id)).all()
    if case.lifecycle != 'Activo' or not productions:
        return False
    if any(not production.cancelled_at and delivery_status(db, case.id, production.id) != 'Completa' for production in productions): return False
    if case_completion_blockers(db, case, show_finances=True):
        return False
    return finalize_case(db, case, user.id if user else None, origin='automatic', action_trigger=action_trigger)


def historical_completion_candidates(db):
    """Read-only preview candidates: open-attention active orders otherwise fully resolved."""
    cases = db.scalars(select(Case).where(
        Case.lifecycle == 'Activo', Case.status == 'Abierto',
        select(Production.id).where(Production.case_id == Case.id).exists(),
    ).order_by(Case.id)).all()
    candidates = []
    for case in cases:
        productions = db.scalars(select(Production).where(Production.case_id == case.id)).all()
        if productions and all((p.cancelled_at and not p.cancellation_review_pending) or delivery_status(db, case.id, p.id) == 'Completa' for p in productions) and not case_completion_blockers(db, case, show_finances=True):
            candidates.append(case)
    return candidates


def regularize_historical_completions(db, regularization_run=None):
    """Apply an idempotent, audited update to previewed records only."""
    run_id = regularization_run or str(uuid.uuid4())
    changed = []
    for case in historical_completion_candidates(db):
        # Protect against a concurrent change between preview and execution.
        db.refresh(case, with_for_update=True)
        if case.lifecycle != 'Activo' or case.status != 'Abierto':
            continue
        if case_completion_blockers(db, case, show_finances=True):
            continue
        case.version += 1
        if finalize_case(db, case, None, origin='historical_regularization',
                action_trigger='regularize_resolved_production', regularization_run=run_id):
            changed.append(case)
    return run_id, changed


# ---- Concurrencia, idempotencia y asignación principal ----
def mutation(db, user, case_id, data, action, permission, *, allow_completed=False):
    require(db, user, permission)
    case = get_case(db, user, case_id, lock=True)
    if permission == 'attention' and not can(db, user, 'assign') and case.owner_id != user.id:
        fail('Toma la solicitud antes de modificarla', 403)
    payload = data.model_dump(mode='json', exclude={'version', 'key'})
    signature = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    previous = db.scalar(select(Idempotency).where(Idempotency.case_id == case_id, Idempotency.key == data.key))
    if previous:
        if previous.action != action or previous.request_hash != signature or previous.actor_id != user.id:
            fail('La clave de reintento ya pertenece a otra operación', 409)
        return case, False
    if case.lifecycle == 'Completado' and not allow_completed:
        fail('La solicitud está completada. Reactívala antes de registrar nuevas gestiones.', 409)
    if case.version != data.version:
        fail('La solicitud cambió. Actualiza los datos y revisa el formulario antes de reintentar.', 409)
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
    if case.lifecycle == 'Completado': fail('La solicitud está completada y no se puede tomar desde una bandeja activa', 409)
    if case.owner_id == user.id:
        return case
    if case.owner_id:
        fail('Otro trabajador ya tomó esta solicitud', 409)
    pending = attention_predicates()['pending_assignment']
    count = db.scalar(select(func.count()).select_from(Case).where(Case.owner_id == user.id, pending))
    if count >= user.capacity: fail('Alcanzaste tu capacidad de solicitudes pendientes (atención, producción, seguimiento o saldo)', 409)
    if case.version != data.version: fail('La solicitud cambió. Actualiza la bandeja.', 409)
    case.owner_id = user.id
    case.version += 1
    event(db, case, user, f'{user.name} tomó la solicitud', data={'action': 'assigned'})
    return case


def assign(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'assign', 'assign')
    if not changed: return case
    if data.owner_id: active_user(db, data.owner_id, 'attention')
    previous = case.owner_id
    case.owner_id = data.owner_id
    event(db, case, user, 'Responsable principal de la solicitud actualizado', data={'action': 'assigned', 'previous_owner_id': previous, 'owner_id': data.owner_id})
    return case


def update_case(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'update', 'attention')
    if not changed: return case
    old = case.status
    if old != 'Cerrado' and data.status == 'Cerrado':
        if case.pending_message:
            fail('Registra la respuesta a la comunicación entrante antes de cerrar atención', 409)
        if db.scalar(select(Task.id).where(Task.case_id == case_id, Task.done_at.is_(None), Task.due_at <= now()).limit(1)):
            fail('Resuelve o reprograma los seguimientos vencidos antes de cerrar atención', 409)
        case.closed_at = now()
    elif old == 'Cerrado' and data.status == 'Abierto':
        case.closed_at = None
    case.status = data.status
    if data.priority is not None: case.priority = data.priority
    if data.next_action is not None: case.next_action = data.next_action
    event(db, case, user, f'Atención: {old} → {data.status}', data={'action': 'attention_status', 'from': old, 'to': data.status, 'origin': 'manual'})
    if data.status == 'Cerrado':
        complete_resolved_production(db, case, user, 'attention_resolved')
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
    if case.lifecycle == 'Completado': fail('La solicitud ya está completada', 409)
    blockers = case_completion_blockers(db, case, show_finances=True)
    if blockers: fail('No se puede completar la solicitud: ' + ' '.join(blockers), 409)
    finalize_case(db, case, user.id, origin='manual', action_trigger='manual_completion')
    return case


def reactivate_case(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'case_reactivate', 'attention', allow_completed=True)
    if not changed: return case
    if case.lifecycle != 'Completado': fail('La solicitud ya está activa', 409)
    old_completed_at, old_completed_by = case.completed_at, case.completed_by_id
    case.lifecycle = 'Activo'
    case.completed_at = None
    case.completed_by_id = None
    case.status = 'Abierto'
    case.closed_at = None
    event(db, case, user, 'Solicitud reactivada', data={
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
        if data.direction == 'Saliente':
            # A manual reply can resolve a manual inbound interaction, but it
            # cannot silently mark an imported email as handled.
            from .models import MailMessage
            from sqlalchemy import select
            unhandled_email = db.scalar(select(MailMessage.id).where(
                MailMessage.case_id == case.id, MailMessage.direction == 'Entrante',
                MailMessage.status == 'Vinculado', MailMessage.attended_at.is_(None),
            ).limit(1))
            if not unhandled_email:
                case.pending_message = False
        if data.direction == 'Saliente' and case.first_response_at is None:
            case.first_response_at = now()
        if data.direction == 'Saliente':
            complete_resolved_production(db, case, user, 'incoming_message_handled')
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


def schedule_pickup_followup(db, case, user, production_id=None, due_at=None):
    query = select(Task.id).where(Task.case_id == case.id, Task.category == 'PICKUP', Task.done_at.is_(None), Task.production_id == production_id)
    open_pickup = db.scalar(query.limit(1))
    if open_pickup:
        return db.get(Task, open_pickup)
    due_at = due_at or now() + timedelta(days=7)
    task = Task(case_id=case.id, production_id=production_id, owner_id=case.owner_id, category='PICKUP', title='Contactar al cliente por trabajo listo para recoger', due_at=due_at)
    db.add(task)
    event(db, case, user, 'Seguimiento de recogida programado para siete días después de quedar listo', data={
        'action': 'pickup_followup_created', 'production_id': production_id, 'due_at': due_at.isoformat() + 'Z', 'owner_id': case.owner_id})
    return task


def complete_task(db, user, case_id, task_id, data):
    case, changed = mutation(db, user, case_id, data, f'task:{task_id}', 'attention')
    if not changed: return case
    task = db.get(Task, task_id)
    if not task or task.case_id != case_id: fail('Seguimiento no encontrado', 404)
    if task.done_at: fail('El seguimiento ya está completado', 409)
    production = get_production(db, case, data.production_id) if data.schedule_next_pickup else None
    if data.schedule_next_pickup and (task.category != 'PICKUP' or not production or production.cancelled_at or delivery_status(db, case.id, production.id) == 'Completa'):
        fail('No se puede programar otro seguimiento de recogida para esta orden')
    if data.schedule_next_pickup and task.production_id != production.id: fail('El seguimiento pertenece a otra orden', 409)
    task.done_at = now()
    event(db, case, user, f'Seguimiento completado: {task.title}')
    if data.note.strip():
        event(db, case, user, data.note.strip(), 'Nota interna', 'Nota', {'direction': 'Interna', 'manual': True, 'source': 'pickup_followup'})
    if data.schedule_next_pickup:
        due_at = now() + timedelta(days=7)
        next_task = Task(case_id=case.id, production_id=production.id, owner_id=case.owner_id, category='PICKUP', title='Contactar al cliente por trabajo listo para recoger', due_at=due_at)
        db.add(next_task)
        event(db, case, user, f'Siguiente seguimiento de recogida programado para OP-{production.id:05d}', data={'action': 'pickup_followup_created', 'production_id': production.id, 'due_at': due_at.isoformat() + 'Z', 'owner_id': case.owner_id})
    complete_resolved_production(db, case, user, 'followup_resolved')
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
    items = [i.model_dump(mode='json') for i in data.items]
    if data.quote_id:
        quote = db.get(Quote, data.quote_id)
        if not quote or quote.case_id != case.id or quote.status != 'Aceptada': fail('Selecciona una cotización aceptada de esta solicitud')
        if db.scalar(select(Production.id).where(Production.quote_id == quote.id)):
            fail('Esta cotización ya está vinculada a una orden. Usa “Agregar orden” y especifica los productos del nuevo trabajo.', 409)
        if quote.currency != CURRENCY_HNL: fail('Esta cotización histórica está en USD. Revísala y crea una nueva versión en HNL antes de generar producción.', 409)
        quoted_items = quote.items or []
        needs_binding = any(
            item.get('product') == 'Talonarios de factura CAI'
            and not (item.get('specs') or {}).get('cai_binding')
            for item in quoted_items
        )
        if needs_binding:
            if len(data.items) != len(quoted_items):
                fail('Selecciona el tipo de encuadernación para cada partida CAI antes de generar la orden')

            def clean(value):
                if isinstance(value, dict):
                    return {key: clean(part) for key, part in value.items() if part not in (None, '')}
                if isinstance(value, list):
                    return [clean(part) for part in value]
                return value

            resolved_items = deepcopy(quoted_items)
            for index, (original, submitted) in enumerate(zip(quoted_items, data.items)):
                chosen = submitted.model_dump(mode='json')
                if (
                    original.get('product') != chosen.get('product')
                    or Decimal(str(original.get('quantity'))) != submitted.quantity
                    or original.get('unit') != submitted.unit
                    or Decimal(str(original.get('unit_price'))) != submitted.unit_price
                ):
                    fail('Al completar una cotización histórica solo puedes indicar la encuadernación pendiente')

                source_specs = dict(original.get('specs') or {})
                chosen_specs = dict(chosen.get('specs') or {})
                source_binding = source_specs.pop('cai_binding', None)
                chosen_binding = chosen_specs.pop('cai_binding', None)
                if clean(source_specs) != clean(chosen_specs):
                    fail('Al completar una cotización histórica solo puedes indicar la encuadernación pendiente')
                if original.get('product') == 'Talonarios de factura CAI':
                    if source_binding:
                        if source_binding != chosen_binding:
                            fail('La encuadernación cotizada no puede cambiar al generar la orden')
                    elif chosen_binding not in CAI_BINDINGS:
                        fail('Selecciona un tipo de encuadernación válido para cada partida CAI')
                    else:
                        resolved_items[index]['specs'] = {
                            **(resolved_items[index].get('specs') or {}),
                            'cai_binding': chosen_binding,
                        }
            items = resolved_items
        else:
            items = quoted_items
    else:
        quote = None
    if not items: fail('Agrega al menos un producto')
    contact_id = data.contact_id if 'contact_id' in data.model_fields_set else case.contact_id
    contact = db.get(Contact, contact_id) if contact_id else None
    if contact_id and (not contact or contact.client_id != case.client_id):
        fail('El contacto seleccionado no pertenece al cliente de esta solicitud')
    client = db.get(Client, case.client_id)
    allowed_phones, allowed_emails = set(client.phones or []), set(client.emails or [])
    for registered in db.scalars(select(Contact).where(Contact.client_id == client.id)):
        if registered.phone: allowed_phones.add(registered.phone)
        if registered.email: allowed_emails.add(registered.email)
    phone = data.phone or (contact.phone if contact and contact.phone else (next(iter(allowed_phones)) if len(allowed_phones) == 1 else ''))
    email = data.email or (contact.email if contact and contact.email else (next(iter(allowed_emails)) if len(allowed_emails) == 1 else ''))
    if phone and phone not in allowed_phones: fail('Selecciona un teléfono registrado del cliente')
    if email and email not in allowed_emails: fail('Selecciona un correo registrado del cliente')
    client_snapshot = {
        'client_id': client.id, 'contact_id': contact.id if contact else None,
        'company': client.company or '', 'person': contact.name if contact else (client.name if not client.company else ''),
        'rtn': client.rtn or '', 'phone': phone, 'email': email,
    }
    price = total(items)
    if data.advance_required > price: fail('El anticipo no puede superar el precio')
    production = Production(case_id=case.id, created_by_id=user.id, quote_id=data.quote_id, contact_id=contact.id if contact else None,
        client_snapshot=client_snapshot, stage='APPROVAL',
        requested_date=data.requested_date, original_committed_date=data.committed_date,
        committed_date=data.committed_date, price=price, currency=CURRENCY_HNL,
        payment_terms=data.payment_terms, advance_required=data.advance_required,
        payment_due_date=data.payment_due_date, delivery_method=data.delivery_method, instructions=data.instructions)
    db.add(production)
    db.flush()
    order_items = [Item(case_id=case.id, production_id=production.id, **item) for item in items]
    db.add_all(order_items)
    db.flush()
    production.specs_snapshot = {'items': [{**row(item, ('substage',)), **({'cai_summary': cai_summary({'product': item.product, 'quantity': item.quantity, 'specs': item.specs})} if item.product == 'Talonarios de factura CAI' else {})} for item in order_items]}
    db.add(Ledger(case_id=case.id, production_id=production.id, kind='Obligación', amount=price, currency=CURRENCY_HNL,
        due_date=data.payment_due_date, reference='Orden de producción', actor_id=user.id))
    case.mode = 'Producción'
    order_number = f'OP-{case.id:05d}'
    quote_number = f'COT-{quote.id:05d}' if quote else None
    order_number = f'OP-{production.id:05d}'
    body = f'{user.name} creó {order_number} en Aprobación'
    if quote:
        body += f' a partir de la cotización {quote_number}, versión {quote.version}'
    event(db, case, user, body, data={'action': 'converted', 'order_id': production.id, 'order_number': order_number, 'stage': 'APPROVAL',
        'quote_id': quote.id if quote else None, 'quote_number': quote_number,
        'quote_version': quote.version if quote else None, 'currency': CURRENCY_HNL})
    return case


def get_production(db, case, production_id=None, *, allow_cancelled=False):
    if production_id is not None:
        production = db.scalar(select(Production).where(Production.id == production_id, Production.case_id == case.id))
    else:
        orders = db.scalars(select(Production).where(Production.case_id == case.id).order_by(Production.id)).all()
        if len(orders) > 1: fail('Esta solicitud tiene varias órdenes. Selecciona una orden específica.', 409)
        production = orders[0] if orders else None
    if not production: fail('No existe esa orden de producción', 404)
    if production.cancelled_at and not allow_cancelled: fail('La producción está cancelada', 409)
    return production


# ---- Confirmación física de encuadernación y liberación de órdenes ----
def _scanner_context(db, user, qr_text, *, lock=False):
    require_binding_scanner(db, user)
    scan_id = qr_text[6:]
    ref = db.scalar(select(Production).where(Production.scan_id == scan_id))
    if not ref:
        fail('El QR no corresponde a una orden de Comgraf', 404)
    case_query = select(Case).where(Case.id == ref.case_id)
    if lock:
        case_query = case_query.with_for_update()
    case = db.scalar(case_query.execution_options(populate_existing=lock))
    orders_query = select(Production).where(Production.case_id == case.id).order_by(Production.id)
    if lock:
        orders_query = orders_query.with_for_update()
    orders = db.scalars(orders_query.execution_options(populate_existing=lock)).all()
    production = next((order for order in orders if order.scan_id == scan_id), None)
    if not production:
        fail('La orden cambió mientras se leía el QR. Escanéala otra vez.', 409)
    return case, orders, production


def _scan_confirmed(production):
    return production.binding_confirmed_at is not None and production.binding_confirmed_cycle == production.workflow_cycle


def _release_group_pending(db, case, orders):
    result = []
    for order in orders:
        if order.cancelled_at or order.stage == 'READY' or delivery_status(db, case.id, order.id) == 'Completa':
            continue
        if order.blocked_since:
            reason = 'Bloqueada'
        elif order.classification_required or not order.stage:
            reason = 'Etapa histórica pendiente de clasificación'
        elif order.stage != 'WORKSHOP':
            reason = f'Etapa actual: {s.STAGE_LABELS.get(order.stage, order.stage)}'
        elif not _scan_confirmed(order):
            reason = 'Pendiente de escaneo'
        else:
            continue
        result.append({'production_id': order.id, 'order_number': f'OP-{order.id:05d}', 'stage': order.stage,
            'stage_label': s.STAGE_LABELS.get(order.stage, 'Pendiente de clasificación'), 'reason': reason})
    return result


def _scanner_order_summary(db, case, production):
    client = db.get(Client, case.client_id)
    items = db.scalars(select(Item).where(Item.production_id == production.id).order_by(Item.id)).all()
    return {'id': production.id, 'order_number': f'OP-{production.id:05d}',
        'case_number': f'CG-{case.id:06d}', 'subject': case.subject, 'client': client.name if client else '',
        'stage': production.stage, 'stage_label': s.STAGE_LABELS.get(production.stage, 'Pendiente de clasificación'),
        'blocked': bool(production.blocked_since), 'cancelled': bool(production.cancelled_at),
        'delivery_complete': delivery_status(db, case.id, production.id) == 'Completa',
        'confirmed': _scan_confirmed(production), 'confirmed_at': production.binding_confirmed_at,
        'items': [{'product': item.product, 'quantity': str(item.quantity), 'unit': item.unit} for item in items]}


def _scanner_group_status(db, case, production, orders):
    missing = _release_group_pending(db, case, orders)
    return {'order': _scanner_order_summary(db, case, production), 'pending_orders': missing,
        'can_confirm': case.lifecycle == 'Activo' and production.stage == 'WORKSHOP' and not production.blocked_since
            and not production.cancelled_at and delivery_status(db, case.id, production.id) != 'Completa'
            and not _scan_confirmed(production)}


def scan_lookup(db, user, qr_text):
    case, orders, production = _scanner_context(db, user, qr_text)
    return _scanner_group_status(db, case, production, orders) | {
        'lifecycle': case.lifecycle,
        'reason': ('La solicitud está completada.' if case.lifecycle != 'Activo' else
            'La orden está anulada.' if production.cancelled_at else
            'La orden ya fue entregada por completo.' if delivery_status(db, case.id, production.id) == 'Completa' else
            'Resuelve el bloqueo antes de confirmar.' if production.blocked_since else
            f'La orden está en {s.STAGE_LABELS.get(production.stage, "una etapa pendiente de clasificación")}; escanea solo al terminar Encuadernación.' if production.stage != 'WORKSHOP' else
            'Esta orden ya fue confirmada para el ciclo actual.' if _scan_confirmed(production) else None)}


def scan_binding(db, user, qr_text):
    case, orders, production = _scanner_context(db, user, qr_text, lock=True)
    # A retry can arrive after the first response was lost and the group was
    # already moved to READY. Treat it as an idempotent read of the outcome.
    if _scan_confirmed(production):
        return _scanner_group_status(db, case, production, orders) | {'result': 'already_confirmed',
            'message': 'Esta orden ya fue confirmada para el ciclo actual.'}
    if case.lifecycle != 'Activo':
        fail('La solicitud está completada; no se puede confirmar escaneo.', 409)
    if production.cancelled_at:
        fail('La orden está anulada; no se puede confirmar escaneo.', 409)
    if delivery_status(db, case.id, production.id) == 'Completa':
        fail('La orden ya está completamente entregada.', 409)
    if production.blocked_since:
        fail('Resuelve el bloqueo antes de confirmar la encuadernación.', 409)
    if production.classification_required or production.stage is None:
        fail('La etapa histórica debe clasificarse en el CRM antes de confirmar.', 409)
    if production.stage != 'WORKSHOP':
        fail(f"La orden está en {s.STAGE_LABELS.get(production.stage, production.stage)}. No puede saltar etapas desde esta terminal.", 409)
    confirmed_at = now()
    case.version += 1
    production.binding_confirmed_cycle = production.workflow_cycle
    production.binding_confirmed_at = confirmed_at
    production.binding_confirmed_by_id = user.id
    event(db, case, user, f"Encuadernación confirmada por escáner en OP-{production.id:05d}.", data={
        'action': 'binding_scan_confirmed', 'production_id': production.id, 'order_number': f'OP-{production.id:05d}',
        'production_cycle': production.workflow_cycle, 'origin': 'scanner', 'confirmed_at': confirmed_at.isoformat() + 'Z'})

    missing = _release_group_pending(db, case, orders)
    pending_group = [order for order in orders if not order.cancelled_at and order.stage != 'READY'
        and delivery_status(db, case.id, order.id) != 'Completa']
    if pending_group and not missing:
        released_at = now()
        for order in pending_group:
            previous = order.stage
            order.stage = 'READY'
            order.stage_started_at = released_at
            event(db, case, user, f"OP-{order.id:05d}: Encuadernación → Listo para entregar por liberación de grupo tras escaneo.",
                data={'action': 'stage', 'production_id': order.id, 'order_number': f'OP-{order.id:05d}',
                    'from': previous, 'to': 'READY', 'origin': 'binding_scan_group', 'seconds': 0})
            schedule_pickup_followup(db, case, user, order.id)
        ids = [order.id for order in pending_group]
        stale_requests = db.scalars(select(PartialReleaseRequest).where(PartialReleaseRequest.production_id.in_(ids),
            PartialReleaseRequest.status == 'Pendiente').with_for_update()).all()
        for request in stale_requests:
            request.status = 'Invalidada'
            request.decided_at = released_at
            event(db, case, user, f"Se invalidó la excepción pendiente de OP-{request.production_id:05d} porque el grupo se liberó normalmente.", data={
                'action': 'partial_release_invalidated', 'request_id': request.id,
                'production_id': request.production_id, 'origin': 'binding_scan_group'})
        event(db, case, user, 'Grupo de órdenes habilitado para entrega tras confirmar la encuadernación.',
            data={'action': 'binding_group_released', 'production_ids': ids, 'origin': 'scanner', 'released_at': released_at.isoformat() + 'Z'})
        return _scanner_group_status(db, case, production, orders) | {'result': 'released', 'released_orders': [f'OP-{pid:05d}' for pid in ids],
            'message': 'Encuadernación confirmada. Las órdenes del grupo están listas para entregar.'}

    return _scanner_group_status(db, case, production, orders) | {'result': 'confirmed_waiting', 'released_orders': [],
        'message': 'Encuadernación confirmada; pendiente de completar el grupo.'}


def request_partial_release(db, user, data):
    case, orders, production = _scanner_context(db, user, data.scan_id, lock=True)
    existing_key = db.scalar(select(PartialReleaseRequest).where(PartialReleaseRequest.request_key == data.request_key))
    if existing_key:
        if existing_key.requester_id != user.id or existing_key.production_id != production.id or existing_key.request_comment != data.comment:
            fail('La clave de reintento ya pertenece a otra solicitud.', 409)
        return {'id': existing_key.id, 'status': existing_key.status, 'order': _scanner_order_summary(db, case, production),
            'pending_orders': _release_group_pending(db, case, orders), 'message': 'La solicitud de excepción ya está registrada.'}
    if (case.lifecycle != 'Activo' or production.cancelled_at or production.blocked_since or production.stage != 'WORKSHOP'
        or delivery_status(db, case.id, production.id) == 'Completa' or not _scan_confirmed(production)):
        fail('La orden debe tener una confirmación vigente de encuadernación y estar activa, desbloqueada y sin entregar.', 409)
    missing = _release_group_pending(db, case, orders)
    if not missing:
        fail('El grupo no tiene otras órdenes pendientes. La liberación normal ya está disponible.', 409)
    prior = db.scalar(select(PartialReleaseRequest.id).where(PartialReleaseRequest.production_id == production.id,
        PartialReleaseRequest.production_cycle == production.workflow_cycle, PartialReleaseRequest.status == 'Pendiente'))
    if prior:
        fail('Ya hay una excepción pendiente para esta orden y este ciclo.', 409)
    comment = data.comment.strip()
    if not comment:
        fail('Escribe el motivo operativo de la liberación parcial.')
    request = PartialReleaseRequest(case_id=case.id, production_id=production.id, production_cycle=production.workflow_cycle,
        requester_id=user.id, request_comment=comment, request_key=data.request_key)
    case.version += 1
    db.add(request)
    db.flush()
    event(db, case, user, f"Se solicitó liberar OP-{production.id:05d} antes de completar el grupo. {comment}", data={
        'action': 'partial_release_requested', 'request_id': request.id, 'production_id': production.id,
        'production_cycle': production.workflow_cycle, 'origin': 'scanner'})
    return {'id': request.id, 'status': request.status, 'order': _scanner_order_summary(db, case, production),
        'pending_orders': missing, 'message': 'Solicitud de excepción enviada para autorización.'}


def _partial_release_view(db, request):
    case = db.get(Case, request.case_id)
    production = db.get(Production, request.production_id)
    requester = db.get(User, request.requester_id)
    return {'id': request.id, 'status': request.status, 'case_number': f'CG-{case.id:06d}',
        'subject': case.subject, 'production_id': production.id, 'order_number': f'OP-{production.id:05d}',
        'stage': production.stage, 'stage_label': s.STAGE_LABELS.get(production.stage, 'Pendiente de clasificación'),
        'production_cycle': request.production_cycle, 'current_cycle': production.workflow_cycle,
        'requester': requester.name if requester else 'Cuenta desactivada', 'request_comment': request.request_comment,
        'pending_orders': _release_group_pending(db, case, db.scalars(select(Production).where(Production.case_id == case.id).order_by(Production.id)).all()),
        'created_at': request.created_at, 'decision_comment': request.decision_comment}


def pending_partial_releases(db, user):
    require(db, user, 'partial_release_approve')
    requests = db.scalars(select(PartialReleaseRequest).where(PartialReleaseRequest.status == 'Pendiente').order_by(PartialReleaseRequest.created_at, PartialReleaseRequest.id)).all()
    return [_partial_release_view(db, request) for request in requests]


def scanner_exception_result(db, user, request_id):
    require_binding_scanner(db, user)
    request = db.get(PartialReleaseRequest, request_id)
    if not request or request.requester_id != user.id:
        fail('No existe una solicitud de excepción propia con ese identificador.', 404)
    case = db.get(Case, request.case_id)
    production = db.get(Production, request.production_id)
    orders = db.scalars(select(Production).where(Production.case_id == case.id).order_by(Production.id)).all()
    return {'id': request.id, 'status': request.status,
        'decision_comment': request.decision_comment,
        'order': _scanner_order_summary(db, case, production),
        'pending_orders': _release_group_pending(db, case, orders)}


def decide_partial_release(db, user, request_id, data):
    require(db, user, 'partial_release_approve')
    ref = db.get(PartialReleaseRequest, request_id)
    if not ref:
        fail('Solicitud de liberación no encontrada', 404)
    case = db.scalar(select(Case).where(Case.id == ref.case_id).with_for_update().execution_options(populate_existing=True))
    orders = db.scalars(select(Production).where(Production.case_id == case.id).order_by(Production.id).with_for_update().execution_options(populate_existing=True)).all()
    request = db.scalar(select(PartialReleaseRequest).where(PartialReleaseRequest.id == request_id).with_for_update())
    production = next((order for order in orders if order.id == request.production_id), None)
    if request.status != 'Pendiente':
        expected = 'Aprobada' if data.approve else 'Rechazada'
        if request.status == expected and request.approver_id == user.id and request.decision_comment == data.comment:
            return _partial_release_view(db, request) | {'message': 'La decisión ya estaba registrada.'}
        fail('La excepción ya tuvo una decisión.', 409)
    if request.requester_id == user.id:
        fail('La persona que solicitó la excepción no puede autorizarla.', 403)
    valid = bool(case.lifecycle == 'Activo' and production and request.production_cycle == production.workflow_cycle
        and _scan_confirmed(production) and production.stage == 'WORKSHOP' and not production.blocked_since
        and not production.cancelled_at and delivery_status(db, case.id, production.id) != 'Completa')
    if not valid:
        request.status = 'Invalidada'
        request.decided_at = now()
        case.version += 1
        event(db, case, user, f"La excepción de OP-{request.production_id:05d} se invalidó porque cambió la orden o el ciclo.", data={
            'action': 'partial_release_invalidated', 'request_id': request.id, 'production_id': request.production_id,
            'origin': 'revalidation'})
        return _partial_release_view(db, request) | {'message': 'La orden cambió: la aprobación pendiente quedó invalidada y no se liberó.'}
    comment = data.comment.strip()
    if not comment:
        fail('Escribe una justificación para registrar la decisión.')
    decided_at = now()
    request.status = 'Aprobada' if data.approve else 'Rechazada'
    request.approver_id = user.id
    request.decision_comment = comment
    request.decided_at = decided_at
    case.version += 1
    if data.approve:
        old = production.stage
        production.stage = 'READY'
        production.stage_started_at = decided_at
        event(db, case, user, f"OP-{production.id:05d}: Encuadernación → Listo para entregar por liberación parcial autorizada. {comment}", data={
            'action': 'stage', 'production_id': production.id, 'order_number': f'OP-{production.id:05d}', 'from': old, 'to': 'READY',
            'origin': 'partial_release', 'request_id': request.id, 'requester_id': request.requester_id,
            'approver_id': user.id, 'request_comment': request.request_comment, 'decision_comment': comment,
            'production_cycle': production.workflow_cycle})
        schedule_pickup_followup(db, case, user, production.id)
    event(db, case, user, f"Excepción de liberación de OP-{production.id:05d}: {request.status}. {comment}", data={
        'action': f'partial_release_{"approved" if data.approve else "rejected"}', 'request_id': request.id,
        'production_id': production.id, 'requester_id': request.requester_id, 'approver_id': user.id,
        'origin': 'crm'})
    return _partial_release_view(db, request) | {'message': 'La orden quedó lista para entregar.' if data.approve else 'La excepción fue rechazada; la orden conserva su confirmación y sigue pendiente del grupo.'}


# ---- Ciclo de producción: etapas, bloqueos y programación ----
def change_stage(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'stage', 'production')
    if not changed: return case
    production = get_production(db, case, data.production_id)
    if data.stage == 'READY':
        fail('Listo para entregar requiere el escaneo del grupo o una excepción autorizada.', 409)
    if production.blocked_since: fail('Resuelve el bloqueo antes de cambiar de etapa')
    if production.classification_required or production.stage is None:
        legacy = production.legacy_stage
        production.stage = data.stage
        production.classification_required = False
        production.stage_started_at = now()
        event(db, case, user, f'OP-{production.id:05d}: clasificación explícita de etapa histórica: {legacy or "Sin etapa legible"} → {s.STAGE_LABELS[data.stage]}', data={'action': 'stage_classified', 'production_id': production.id, 'legacy_stage': legacy, 'stage': data.stage, 'origin': 'manual_classification'})
        return case
    old = production.stage
    if data.stage == old: fail('La etapa ya está activa', 409)
    if delivery_status(db, case.id, production.id) == 'Completa': fail('La entrega está completa; no se puede reabrir esta orden', 409)
    next_stage = NEXT_STAGE.get(old)
    moving_back = data.stage in PREVIOUS_STAGES.get(old, set())
    if data.stage != next_stage and not moving_back: fail('La etapa debe avanzar a la siguiente o retroceder con motivo explícito')
    if moving_back and not data.reason: fail('El retroceso requiere motivo')
    if delivery_status(db, case.id, production.id) != 'Pendiente' and moving_back: fail('Esta orden ya tiene entregas; no se puede retroceder la etapa')
    duration = (now() - production.stage_started_at).total_seconds()
    if moving_back and (production.binding_confirmed_at is not None or old == 'READY'):
        previous_cycle = production.workflow_cycle
        production.workflow_cycle += 1
        production.binding_confirmed_cycle = None
        production.binding_confirmed_at = None
        production.binding_confirmed_by_id = None
        event(db, case, user, f'Confirmación de encuadernación de OP-{production.id:05d} invalidada por reclamo; ciclo {production.workflow_cycle} iniciado.', data={
            'action': 'binding_scan_invalidated', 'production_id': production.id, 'previous_cycle': previous_cycle,
            'production_cycle': production.workflow_cycle, 'origin': 'rework'})
    production.stage, production.stage_started_at = data.stage, now()
    event(db, case, user, f'OP-{production.id:05d}: {s.STAGE_LABELS[old]} → {s.STAGE_LABELS[data.stage]}' + (f'. {data.reason}' if data.reason else ''), data={'action': 'stage', 'production_id': production.id, 'order_number': f'OP-{production.id:05d}', 'from': old, 'to': data.stage, 'seconds': duration, 'rework': moving_back})
    return case


def block(db, user, case_id, data, unblock=False):
    case, changed = mutation(db, user, case_id, data, 'unblock' if unblock else 'block', 'production')
    if not changed: return case
    production = get_production(db, case, data.production_id)
    if delivery_status(db, case.id, production.id) == 'Completa': fail('El trabajo ya fue entregado')
    if unblock:
        if not production.blocked_since: fail('El trabajo no está bloqueado')
        seconds = (now() - production.blocked_since).total_seconds()
        production.blocked_since, production.block_reason = None, None
        event(db, case, user, f'Bloqueo resuelto en OP-{production.id:05d}: {data.reason}', data={'action': 'unblocked', 'production_id': production.id, 'seconds': seconds})
    else:
        if production.blocked_since: fail('Ya existe un bloqueo activo', 409)
        production.blocked_since, production.block_reason = now(), data.reason
        event(db, case, user, f'OP-{production.id:05d} bloqueada: {data.reason}', data={'action': 'blocked', 'production_id': production.id})
    return case


def schedule(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'schedule', 'production')
    if not changed: return case
    production = get_production(db, case, data.production_id)
    if delivery_status(db, case.id, production.id) == 'Completa': fail('El trabajo ya fue entregado')
    previous_scheduled, previous_committed = production.scheduled_at, production.committed_date
    if not production.original_committed_date: production.original_committed_date = data.committed_date
    production.committed_date, production.scheduled_at = data.committed_date, data.scheduled_at
    event(db, case, user, f'Programación y compromiso actualizados para OP-{production.id:05d}', data={'action': 'schedule', 'production_id': production.id, 'previous': {'scheduled_at': previous_scheduled.isoformat() + 'Z' if previous_scheduled else None, 'committed_date': str(previous_committed) if previous_committed else None}, 'current': {'scheduled_at': data.scheduled_at.isoformat() + 'Z', 'committed_date': str(data.committed_date)}})
    return case


def cancel(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'cancel', 'production')
    if not changed: return case
    production = get_production(db, case, data.production_id)
    if not production: fail('No existe una orden de producción')
    if production.cancelled_at: fail('La producción ya está anulada; usa la acción de resolución económica', 409)
    if delivery_status(db, case.id, production.id) == 'Completa':
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
    totals = cancellation_financials(db, case.id, production.id)
    event(db, case, user, f'OP-{production.id:05d} anulada: {data.reason}', data={'action': 'cancelled', 'production_id': production.id, 'reason': data.reason,
        'stage': production.cancelled_stage, 'seconds': duration,
        'economic_status': 'resolved' if not production.cancellation_review_pending else 'review_pending',
        'final_amount': str(production.cancellation_final_amount) if production.cancellation_final_amount is not None else None,
        'adjustment': str(totals.get('adjustment', Decimal('0')))})
    return case


def resolve_cancellation_request(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'cancel_resolution', 'adjust')
    if not changed: return case
    require(db, user, 'finance')
    production = get_production(db, case, data.production_id, allow_cancelled=True)
    if not production or not production.cancelled_at or not production.cancellation_review_pending:
        fail('Esta solicitud no tiene una resolución económica de anulación pendiente', 409)
    if delivery_status(db, case.id, production.id) == 'Completa':
        fail('La orden está completamente entregada; registra una devolución o reclamo por separado', 409)
    resolve_cancellation(db, user, case, production, data)
    complete_resolved_production(db, case, user, 'cancellation_resolved')
    return case


def cancellation_financials(db, case_id, production_id=None):
    query = select(Ledger).where(Ledger.case_id == case_id)
    if production_id is not None: query = query.where(Ledger.production_id == production_id)
    entries = list(db.scalars(query.order_by(Ledger.id)))
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
    totals = cancellation_financials(db, case.id, production.id)
    adjustment = (final_amount - totals['charges']).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if adjustment:
        db.add(Ledger(case_id=case.id, production_id=production.id, kind='Ajuste de anulación', amount=adjustment,
            currency=production.currency, due_date=None, reference='Resolución de anulación comercial',
            reason=data.reason, actor_id=user.id))
    production.cancellation_final_amount = final_amount
    production.cancellation_review_pending = False
    production.cancellation_resolved_at = now()
    production.cancellation_reason = production.cancellation_reason or data.reason
    production.cancellation_actor_id = user.id
    resulting_balance = totals['current_balance'] + adjustment
    event(db, case, user, f'Anulación resuelta para OP-{production.id:05d}: cargo final {final_amount} HNL; ajuste {adjustment} HNL', data={
        'action': 'cancellation_resolved', 'production_id': production.id, 'reason': data.reason, 'treatment': data.treatment,
        'final_amount': str(final_amount), 'previous_charges': str(totals['charges']),
        'net_payments_received': str(totals['net_received']), 'adjustment': str(adjustment),
        'resulting_balance': str(resulting_balance), 'actor_id': user.id,
        'resolved_at': now().isoformat() + 'Z'})


def refund(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'refund', 'finance')
    if not changed: return case
    production = get_production(db, case, data.production_id, allow_cancelled=True)
    if not production:
        fail('No existe una orden de producción')
    is_resolved_cancellation = production.cancelled_at and not production.cancellation_review_pending and production.cancellation_final_amount is not None
    if not is_resolved_cancellation and delivery_status(db, case.id, production.id) != 'Completa':
        fail('La devolución requiere una anulación resuelta o una entrega completa')
    if production.currency != CURRENCY_HNL: fail('La orden histórica requiere revisión manual de moneda', 409)
    due = max(Decimal('0'), -Decimal(balance(db, case.id, production.id)['balance']))
    if data.amount > due: fail('La devolución supera el saldo a favor pendiente')
    db.add(Ledger(case_id=case.id, production_id=production.id, kind='Devolución', amount=data.amount, currency=production.currency,
        reference=data.reference, reason='Devolución realizada y confirmada por el usuario', actor_id=user.id))
    event(db, case, user, f'Devolución registrada: {data.amount} {production.currency}. {data.reference}',
        data={'action': 'refund', 'amount': str(data.amount), 'reference': data.reference, 'actor_id': user.id})
    complete_resolved_production(db, case, user, 'refund_recorded')
    return case


# ---- Entregas y control operativo de cobros ----
def deliver(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'delivery', 'production')
    if not changed: return case
    production = get_production(db, case, data.production_id)
    if production.stage != 'READY' or production.classification_required or production.blocked_since: fail('El trabajo debe estar clasificado, listo y sin bloqueos')
    if data.delivered_at > now(): fail('La entrega no puede estar en el futuro')
    item = db.get(Item, data.item_id)
    if not item or item.case_id != case.id or item.production_id != production.id: fail('Partida no pertenece a esta orden', 404)
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
        current_balance = Decimal(balance(db, case.id, production.id)['balance'])
        if current_balance <= 0 or payment_amount > current_balance: fail('El pago debe ser positivo y no superar el saldo actual')
        db.add(Ledger(case_id=case.id, production_id=production.id, kind='Abono', amount=-payment_amount, currency=production.currency, reference=payment_reference, actor_id=user.id))
        event(db, case, user, f'Abono OP-{production.id:05d}: {payment_amount} {production.currency}. {payment_reference}', data={'financial': True, 'action': 'payment', 'production_id': production.id})
    event(db, case, user, f'Entrega OP-{production.id:05d}: {quantity_text(data.quantity)} {item.unit} de {item.product} a {data.receiver}', data={'action': 'delivery', 'production_id': production.id, 'item_id': item.id, 'on_time_original': bool(production.original_committed_date and local_day(data.delivered_at) <= production.original_committed_date)})
    if delivery_status(db, case.id, production.id) == 'Completa':
        duration = (now() - production.stage_started_at).total_seconds()
        event(db, case, user, f'Entrega completa de OP-{production.id:05d}. Situación de pago conservada.', data={'action': 'delivery_completed', 'production_id': production.id, 'stage': production.stage, 'seconds': duration})
        pickup_tasks = db.scalars(select(Task).where(Task.case_id == case.id, Task.production_id == production.id, Task.category == 'PICKUP', Task.done_at.is_(None))).all()
        for task in pickup_tasks: task.done_at = now()
        if pickup_tasks:
            event(db, case, user, 'Seguimiento de recogida resuelto automáticamente por entrega completa', data={'action': 'pickup_followup_resolved', 'task_ids': [task.id for task in pickup_tasks], 'origin': 'automatic'})
    complete_resolved_production(db, case, user, 'delivery_recorded')
    return case


def pay(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'payment', 'finance')
    if not changed: return case
    production = get_production(db, case, data.production_id)
    if production.currency != CURRENCY_HNL: fail('Esta orden histórica está en USD. No registres el abono como lempiras; requiere revisión explícita de moneda.', 409)
    db.add(Ledger(case_id=case.id, production_id=production.id, kind=data.kind, amount=-data.amount, currency=production.currency, reference=data.reference, actor_id=user.id))
    event(db, case, user, f'{data.kind} OP-{production.id:05d}: {data.amount} {production.currency}. {data.reference}', data={'financial': True, 'action': 'payment', 'production_id': production.id})
    complete_resolved_production(db, case, user, 'payment_recorded')
    return case


def adjust(db, user, case_id, data):
    case, changed = mutation(db, user, case_id, data, 'adjust', 'adjust')
    if not changed: return case
    production = get_production(db, case, data.production_id)
    if not data.amount: fail('El ajuste requiere un importe distinto de cero')
    if production.currency != CURRENCY_HNL: fail('Esta orden histórica está en USD y requiere revisión explícita de moneda.', 409)
    db.add(Ledger(case_id=case.id, production_id=production.id, kind='Ajuste', amount=data.amount, currency=production.currency, due_date=data.due_date, reference='Ajuste autorizado', reason=data.reason, actor_id=user.id))
    event(db, case, user, f'Ajuste OP-{production.id:05d}: {data.amount} {production.currency}. {data.reason}', data={'financial': True, 'production_id': production.id})
    complete_resolved_production(db, case, user, 'financial_adjustment')
    return case


def reverse_payment(db, user, case_id, entry_id, data):
    case, changed = mutation(db, user, case_id, data, f'reverse:{entry_id}', 'adjust')
    if not changed: return case
    entry = db.get(Ledger, entry_id)
    if not entry or entry.case_id != case.id or entry.kind not in ('Anticipo', 'Abono'): fail('Selecciona un abono o anticipo')
    if db.scalar(select(Ledger.id).where(Ledger.reverses_id == entry.id)): fail('El pago ya fue revertido', 409)
    production = get_production(db, case, data.production_id)
    if entry.production_id != production.id: fail('El pago pertenece a otra orden', 409)
    db.add(Ledger(case_id=case.id, production_id=production.id, kind='Reversión de pago', amount=-entry.amount, currency=entry.currency, due_date=production.payment_due_date, reference=f'Reversión #{entry.id}', reason=data.reason, reverses_id=entry.id, actor_id=user.id))
    event(db, case, user, f'Pago #{entry.id} de OP-{production.id:05d} revertido: {data.reason}', data={'financial': True, 'production_id': production.id})
    complete_resolved_production(db, case, user, 'payment_reversed')
    return case
