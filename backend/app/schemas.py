from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
Quantity = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]
# This is the server contract; frontend/src/domain.js mirrors these choices for form controls.
Status = Literal['Abierto', 'Cerrado']
Stage = Literal['NEW', 'APPROVAL', 'PRINTING', 'WORKSHOP', 'READY']
STAGE_LABELS = {
    'NEW': 'Nuevo',
    'APPROVAL': 'Aprobación',
    'PRINTING': 'Impresión',
    'WORKSHOP': 'Encuadernación',
    'READY': 'Listo para entregar',
}
NEXT_STAGE = {'NEW': 'APPROVAL', 'APPROVAL': 'PRINTING', 'PRINTING': 'WORKSHOP', 'WORKSHOP': 'READY'}
PREVIOUS_STAGES = {
    'APPROVAL': {'NEW'},
    'PRINTING': {'NEW', 'APPROVAL'},
    'WORKSHOP': {'NEW', 'APPROVAL', 'PRINTING'},
    'READY': {'NEW', 'APPROVAL', 'PRINTING', 'WORKSHOP'},
}
Product = Literal['Tarjetas de presentación', 'Talonarios y facturas', 'Talonarios de factura CAI', 'Volantes y flyers', 'Libretas y cuadernos', 'Banners y lonas', 'Otros trabajos']


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class BindingScanInput(Input):
    scan_id: str = Field(pattern=r'^CGOP1:([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12})$')


class PartialReleaseRequestInput(BindingScanInput):
    comment: str = Field(min_length=1, max_length=2000)
    request_key: str = Field(min_length=20, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')


class PartialReleaseDecisionInput(Input):
    approve: bool
    comment: str = Field(min_length=1, max_length=1000)
    request_key: str = Field(min_length=20, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')


# ---- Sesión, usuarios y perfiles comerciales ----
class LoginInput(Input):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class UserInput(Input):
    username: str = Field(min_length=3, max_length=80, pattern=r'^[a-zA-Z0-9_.-]+$')
    name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=12, max_length=200)
    roles: list[str] = Field(min_length=1, max_length=10)
    capacity: int = Field(default=20, ge=1, le=500)


class UserUpdate(Input):
    name: str = Field(min_length=2, max_length=160)
    roles: list[str] = Field(min_length=1, max_length=10)
    active: bool
    capacity: int = Field(ge=1, le=500)
    password: str | None = Field(default=None, min_length=12, max_length=200)


class ContactInput(Input):
    name: str = Field(min_length=1, max_length=180)
    phone: str = Field(default='', max_length=60)
    email: str = Field(default='', max_length=180)
    position: str = Field(default='', max_length=120)


class ClientInput(Input):
    name: str = Field(min_length=1, max_length=180)
    company: str = Field(default='', max_length=180)
    rtn: str = Field(default='', max_length=30)
    phones: list[Annotated[str, Field(max_length=60)]] = Field(default_factory=list, max_length=20)
    emails: list[Annotated[str, Field(max_length=180)]] = Field(default_factory=list, max_length=20)
    addresses: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list, max_length=20)
    observations: str = Field(default='', max_length=10000)
    provisional: bool = True


class ClientUpdate(ClientInput):
    version: int


class CaseInput(Input):
    client_id: int
    contact_id: int | None = None
    related_case_id: int | None = None
    subject: str = Field(min_length=1, max_length=240)
    description: str = Field(default='', max_length=20000)
    kind: Literal['Consulta', 'Cotización', 'Solicitud', 'Reclamo', 'Seguimiento'] = 'Consulta'
    channel: Literal['Presencial', 'Llamada', 'Correo', 'WhatsApp', 'Otro'] = 'Presencial'
    priority: Literal['Baja', 'Normal', 'Alta', 'Urgente'] = 'Normal'


class Mutation(Input):
    version: int = Field(ge=1)
    key: str = Field(min_length=16, max_length=80)
    production_id: int | None = Field(default=None, ge=1)


# ---- Atención, comunicación y tareas ----
class Assignment(Mutation):
    owner_id: int | None = None


class CaseUpdate(Mutation):
    status: Status
    priority: Literal['Baja', 'Normal', 'Alta', 'Urgente'] | None = None
    next_action: str | None = Field(default=None, max_length=500)


class CaseDetailsInput(Mutation):
    priority: Literal['Baja', 'Normal', 'Alta', 'Urgente'] | None = None
    next_action: str | None = Field(default=None, max_length=500)

    @model_validator(mode='after')
    def has_change(self):
        if self.priority is None and self.next_action is None:
            raise ValueError('Indica prioridad o próxima acción')
        return self


class TaskReschedule(Mutation):
    due_at: datetime

    @field_validator('due_at')
    @classmethod
    def utc(cls, value):
        return TaskInput.utc(value)


class EventInput(Mutation):
    kind: Literal['Nota interna', 'Interacción manual']
    channel: Literal['Nota', 'Correo', 'WhatsApp']
    body: str = Field(min_length=1, max_length=20000)
    direction: Literal['Entrante', 'Saliente', 'Interna'] = 'Interna'


class TaskInput(Mutation):
    title: str = Field(min_length=1, max_length=500)
    owner_id: int
    due_at: datetime

    @field_validator('due_at')
    @classmethod
    def utc(cls, value):
        if value.tzinfo is None:
            raise ValueError('Incluir zona horaria en la fecha')
        return value.astimezone(timezone.utc).replace(tzinfo=None)


class TaskCompletionInput(Mutation):
    note: str = Field(default='', max_length=20000)
    schedule_next_pickup: bool = False


class CaseLifecycleInput(Mutation):
    pass


class Specs(Input):
    width: Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=3)] | None = None
    height: Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=3)] | None = None
    measure_unit: Literal['cm', 'm', 'mm'] = 'cm'
    material: str = Field(default='', max_length=200)
    sides: Literal['1', '2'] | None = None
    inks: str = Field(default='', max_length=120)
    copies: int | None = Field(default=None, ge=0, le=20)
    numbering: str = Field(default='', max_length=200)
    sheets: int | None = Field(default=None, ge=1, le=10000)
    binding: str = Field(default='', max_length=120)
    finishes: str = Field(default='', max_length=300)
    detail: str = Field(default='', max_length=3000)
    cai_format: Literal['Media carta', 'Cuarto de carta', '8.5 × 7.25', '8.5 × 8.5', 'Carta'] | None = None
    games_per_pad: Literal[25, 50] | None = None
    paper_type: Literal['Bond', 'Químico'] | None = None
    additional_copies: Literal[1, 2, 3] | None = None


# ---- Cotización y conversión a una orden activa ----
class ItemInput(Input):
    product: Product
    quantity: Quantity
    unit: str = Field(default='unidades', min_length=1, max_length=30)
    unit_price: Money = Decimal('0')
    specs: Specs = Field(default_factory=Specs)

    @model_validator(mode='after')
    def cai_contract(self):
        cai_fields = ('cai_format', 'games_per_pad', 'paper_type', 'additional_copies')
        if self.product == 'Talonarios de factura CAI':
            if self.unit != 'talonarios':
                raise ValueError('La unidad comercial de Talonarios de factura CAI debe ser talonarios')
            if self.quantity != self.quantity.to_integral_value():
                raise ValueError('La cantidad de talonarios debe ser un entero positivo')
            if any(getattr(self.specs, name) is None for name in cai_fields):
                raise ValueError('Completa formato, juegos, papel y copias del talonario CAI')
        elif any(getattr(self.specs, name) is not None for name in cai_fields):
            raise ValueError('Las especificaciones CAI solo se permiten para Talonarios de factura CAI')
        return self


class QuoteInput(Mutation):
    items: list[ItemInput] = Field(min_length=1, max_length=100)
    currency: Literal['HNL'] = 'HNL'
    valid_until: date


class QuoteDecision(Mutation):
    status: Literal['Aceptada', 'Rechazada']
    evidence: str = Field(min_length=3, max_length=3000)


class Conversion(Mutation):
    quote_id: int | None = None
    items: list[ItemInput] = Field(default_factory=list, max_length=100)
    requested_date: date | None = None
    committed_date: date | None = None
    currency: Literal['HNL'] = 'HNL'
    payment_terms: Literal['Contado', 'Crédito'] = 'Contado'
    advance_required: Money = Decimal('0')
    payment_due_date: date
    delivery_method: str = Field(min_length=1, max_length=120)
    instructions: str = Field(default='', max_length=10000)
    contact_id: int | None = Field(default=None, ge=1)
    phone: str = Field(default='', max_length=60)
    email: str = Field(default='', max_length=180)


class StageInput(Mutation):
    stage: Stage
    reason: str = Field(default='', max_length=3000)


# ---- Producción, entregas y movimientos financieros ----
class ReasonInput(Mutation):
    reason: str = Field(min_length=3, max_length=3000)


class CancellationInput(Mutation):
    reason: str = Field(min_length=3, max_length=3000)
    treatment: Literal['SIN_CARGO', 'IMPORTE_FINAL'] | None = None
    final_amount: Money | None = None

    @model_validator(mode='after')
    def cancellation_choice(self):
        if self.treatment == 'IMPORTE_FINAL' and self.final_amount is None:
            raise ValueError('Indica el importe final acordado')
        if self.treatment == 'SIN_CARGO' and self.final_amount not in (None, Decimal('0')):
            raise ValueError('Sin cargo fija el importe final en cero')
        if self.treatment is None and self.final_amount is not None:
            raise ValueError('El importe requiere un tratamiento económico')
        return self


class RefundInput(Mutation):
    amount: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]
    reference: str = Field(min_length=1, max_length=240)


class ScheduleInput(Mutation):
    committed_date: date
    scheduled_at: datetime

    @field_validator('scheduled_at')
    @classmethod
    def utc(cls, value):
        return TaskInput.utc(value)


class DeliveryInput(Mutation):
    item_id: int
    quantity: Quantity
    delivered_at: datetime
    receiver: str = Field(min_length=1, max_length=180)
    method: str = Field(min_length=1, max_length=100)
    evidence: str = Field(min_length=3, max_length=3000)

    @field_validator('delivered_at')
    @classmethod
    def utc(cls, value):
        return TaskInput.utc(value)


class DeliveryWithPaymentInput(DeliveryInput):
    payment_amount: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)] | None = None
    payment_reference: str | None = Field(default=None, min_length=1, max_length=240)


class PaymentInput(Mutation):
    amount: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]
    kind: Literal['Anticipo', 'Abono'] = 'Abono'
    reference: str = Field(min_length=1, max_length=240)


class AdjustmentInput(Mutation):
    amount: Annotated[Decimal, Field(max_digits=14, decimal_places=2)]
    reason: str = Field(min_length=3, max_length=3000)
    due_date: date


class DraftInput(Input):
    body: str = Field(max_length=20000)
    version: int = Field(ge=0)
    email_to: str = Field(default='', max_length=1000)
    email_subject: str = Field(default='', max_length=998)
    email_attachment_ids: list[int] = Field(default_factory=list, max_length=20)
    email_thread_root: str = Field(default='', max_length=255)


class MailConfigurationInput(Input):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, hide_input_in_errors=True)
    email: str = Field(min_length=3, max_length=254)
    application_password: SecretStr | None = Field(default=None, max_length=200)


class MailSendInput(Input):
    to: list[str] = Field(min_length=1, max_length=10)
    subject: str = Field(min_length=1, max_length=998)
    body: str = Field(min_length=1, max_length=20000)
    attachment_ids: list[int] = Field(default_factory=list, max_length=20)
    thread_root: str | None = Field(default=None, max_length=255)
    send_key: str = Field(min_length=16, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')


class MailClassifyInput(Input):
    action: Literal['link', 'reactivate', 'related', 'new', 'ignore']
    case_id: int | None = None
    client_id: int | None = None


class MailRetryInput(Input):
    confirm_uncertain: bool = False


# ---- Preferencias de bandeja ----
class ViewInput(Input):
    name: str = Field(min_length=1, max_length=100)
    filters: dict
