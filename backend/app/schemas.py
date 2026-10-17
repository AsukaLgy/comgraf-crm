from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
Quantity = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]
Status = Literal['Nuevo', 'En atención', 'Esperando al cliente', 'Esperando gestión interna', 'Cerrado']
Stage = Literal['Preparación', 'Programado', 'En producción', 'Calidad', 'Listo para entregar', 'Entregado']
Product = Literal['Tarjetas de presentación', 'Talonarios y facturas', 'Volantes y flyers', 'Libretas y cuadernos', 'Banners y lonas', 'Otros trabajos']


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


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


class Assignment(Mutation):
    owner_id: int | None = None
    responsibility: Literal['atencion', 'produccion', 'cobranza'] = 'atencion'


class CaseUpdate(Mutation):
    status: Status
    priority: Literal['Baja', 'Normal', 'Alta', 'Urgente']
    next_action: str = Field(default='', max_length=500)
    pending_message: bool = False
    reason: str = Field(default='', max_length=2000)
    summary: str = Field(default='', max_length=10000)


class EventInput(Mutation):
    kind: Literal['Nota interna', 'Interacción manual']
    channel: Literal['Nota', 'Llamada', 'Visita', 'Correo', 'WhatsApp']
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


class ItemInput(Input):
    product: Product
    quantity: Quantity
    unit: str = Field(default='unidades', min_length=1, max_length=30)
    unit_price: Money = Decimal('0')
    specs: Specs = Field(default_factory=Specs)


class QuoteInput(Mutation):
    items: list[ItemInput] = Field(min_length=1, max_length=100)
    currency: Literal['HNL', 'USD'] = 'HNL'
    valid_until: date


class QuoteDecision(Mutation):
    status: Literal['Aceptada', 'Rechazada']
    evidence: str = Field(min_length=3, max_length=3000)


class Conversion(Mutation):
    quote_id: int | None = None
    items: list[ItemInput] = Field(default_factory=list, max_length=100)
    owner_id: int | None = None
    requested_date: date | None = None
    committed_date: date | None = None
    currency: Literal['HNL', 'USD'] = 'HNL'
    payment_terms: str = Field(default='Contado', min_length=1, max_length=240)
    advance_required: Money = Decimal('0')
    payment_due_date: date
    delivery_method: str = Field(min_length=1, max_length=120)
    instructions: str = Field(default='', max_length=10000)


class Approval(Mutation):
    art_version: str = Field(min_length=1, max_length=100)
    evidence: str = Field(min_length=3, max_length=3000)


class StageInput(Mutation):
    stage: Stage
    reason: str = Field(default='', max_length=3000)


class ReasonInput(Mutation):
    reason: str = Field(min_length=3, max_length=3000)


class ScheduleInput(Mutation):
    committed_date: date
    scheduled_at: datetime
    reason: str = Field(min_length=3, max_length=3000)

    @field_validator('scheduled_at')
    @classmethod
    def utc(cls, value):
        return TaskInput.utc(value)


class SubstageInput(Mutation):
    substage: Literal['Diseño', 'Preprensa', 'Impresión', 'Acabados', 'Finalizado']
    reason: str = Field(default='', max_length=3000)


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


class ViewInput(Input):
    name: str = Field(min_length=1, max_length=100)
    filters: dict
