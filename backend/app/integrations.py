"""Provider seam only. No network send or public webhooks are enabled."""
from dataclasses import dataclass
from typing import Protocol
from sqlalchemy import select
from .models import ProviderInbox


@dataclass(frozen=True)
class Incoming:
    provider: str
    external_id: str
    payload: dict


class CommunicationProvider(Protocol):
    def send(self, recipient: str, body: str, external_key: str) -> str: ...
    def verify_and_parse(self, raw: bytes, headers: dict) -> Incoming: ...


class UnconfiguredProvider:
    def send(self, recipient, body, external_key):
        raise RuntimeError('Canal no configurado; guarda un borrador o registra una interacción manual')


def enqueue_incoming(db, incoming: Incoming):
    """Adapter must authenticate before calling. Never guess a case from a phone.

    Caller handles the unique-constraint race by retrying in a new transaction.
    Unclassified events stay in the inbox, avoiding erroneous case associations.
    """
    previous = db.scalar(select(ProviderInbox).where(ProviderInbox.provider == incoming.provider, ProviderInbox.external_id == incoming.external_id))
    if previous: return previous
    event = ProviderInbox(provider=incoming.provider, external_id=incoming.external_id, payload=incoming.payload)
    db.add(event)
    db.flush()
    return event


def provider_status():
    return [{'channel': c, 'configured': False, 'status': 'No configurado', 'manual': True} for c in ('Correo', 'WhatsApp')]
