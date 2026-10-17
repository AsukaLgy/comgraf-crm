from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from ..db import get_db
from ..models import Client, Contact
from ..schemas import ClientInput, ClientUpdate, ContactInput
from ..security import current_user, require
from ..repository import normalize, row, cases_list, get_case, case_detail

router = APIRouter(prefix='/api/clients', tags=['Clientes'])


def search_text(client, contacts=()):
    return normalize(' '.join([client.name, client.company, client.rtn, *client.phones, *client.emails, *[f'{c.name} {c.phone} {c.email}' for c in contacts]]))


@router.get('')
def clients(q: str = '', user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'clients')
    query = select(Client).order_by(Client.name)
    if q: query = query.where(Client.search_text.contains(normalize(q)))
    return [row(c, ('search_text',)) for c in db.scalars(query)]


@router.post('', status_code=201)
def create_client(data: ClientInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'clients')
    client = Client(**data.model_dump())
    client.search_text = search_text(client)
    db.add(client)
    db.flush()
    return row(client, ('search_text',))


@router.get('/{client_id}/contacts')
def contacts(client_id: int, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'clients')
    if not db.get(Client, client_id): raise HTTPException(404, 'Cliente no encontrado')
    return [row(c) for c in db.scalars(select(Contact).where(Contact.client_id == client_id).order_by(Contact.name))]


@router.get('/{client_id}')
def profile(client_id: int, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'clients')
    client = db.get(Client, client_id)
    if not client: raise HTTPException(404, 'Cliente no encontrado')
    cases = cases_list(db, user, {'view': 'all', 'client_id': client_id}, paginated=False)
    return {**row(client, ('search_text',)), 'contacts': [row(c) for c in db.scalars(select(Contact).where(Contact.client_id == client_id))],
            'cases': [case_detail(db, user, get_case(db, user, c['id'])) for c in cases]}


@router.put('/{client_id}')
def update(client_id: int, data: ClientUpdate, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'clients')
    client = db.scalar(select(Client).where(Client.id == client_id).with_for_update())
    if not client: raise HTTPException(404, 'Cliente no encontrado')
    if client.version != data.version: raise HTTPException(409, 'El cliente cambió; actualiza su perfil')
    for key, value in data.model_dump(exclude={'version'}).items(): setattr(client, key, value)
    client.version += 1
    client.search_text = search_text(client, db.scalars(select(Contact).where(Contact.client_id == client_id)))
    return row(client, ('search_text',))


@router.post('/{client_id}/contacts', status_code=201)
def contact(client_id: int, data: ContactInput, user=Depends(current_user), db=Depends(get_db, scope='function')):
    require(db, user, 'clients')
    client = db.scalar(select(Client).where(Client.id == client_id).with_for_update())
    if not client: raise HTTPException(404, 'Cliente no encontrado')
    contact = Contact(client_id=client_id, **data.model_dump())
    db.add(contact)
    db.flush()
    client.search_text = search_text(client, db.scalars(select(Contact).where(Contact.client_id == client_id)))
    client.version += 1
    return row(contact)
