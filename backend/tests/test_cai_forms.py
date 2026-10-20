from decimal import Decimal
from itertools import product
import pytest
from pydantic import ValidationError
from app.schemas import ItemInput
from test_workflows import create, key, must, mutate, conversion


FORMATS = ['Media carta', 'Cuarto de carta', '8.5 × 7.25', '8.5 × 8.5', 'Carta']


def cai_item(**changes):
    item = {'product':'Talonarios de factura CAI','quantity':'10','unit':'talonarios','unit_price':'125.00',
            'specs':{'cai_format':'Media carta','games_per_pad':50,'paper_type':'Químico','additional_copies':2,'detail':'Original y copias autocopiativas'}}
    item.update(changes)
    return item


def test_all_cai_selector_combinations_are_valid_and_closed():
    for form, games, paper, copies in product(FORMATS, (25,50), ('Bond','Químico'), (1,2,3)):
        ItemInput.model_validate(cai_item(specs={'cai_format':form,'games_per_pad':games,'paper_type':paper,'additional_copies':copies}))
    with pytest.raises(ValidationError):
        ItemInput.model_validate(cai_item(specs={'cai_format':'Folio','games_per_pad':50,'paper_type':'Bond','additional_copies':1}))
    with pytest.raises(ValidationError):
        ItemInput.model_validate(cai_item(quantity='1.5'))
    with pytest.raises(ValidationError):
        ItemInput.model_validate(cai_item(unit='juegos'))


def test_cai_order_uses_pads_as_commercial_quantity_and_snapshots_client(clients):
    api=clients['admin']; customer,case=create(api,'Facturación CAI')
    invalid=api.post(f"/api/cases/{case['id']}/convert",json={**conversion(),'items':[cai_item(specs={'cai_format':'Folio','games_per_pad':50,'paper_type':'Químico','additional_copies':2})],'version':case['version'],'key':key()})
    assert invalid.status_code==422
    order=mutate(api,case,'convert',{**conversion(),'items':[cai_item()]})
    production=order['production']
    assert production['price']=='1250.00' and production['stage']=='APPROVAL'
    summary=production['specs_snapshot']['items'][0]['cai_summary']
    assert summary=={'total_games':500,'sheets_per_game':3,'finished_sheets':1500}
    assert order['items'][0]['quantity']=='10.000' and order['items'][0]['unit']=='talonarios'
    assert production['client_snapshot']=={'client_id':customer['id'],'contact_id':None,'company':'Gráfica Norte','person':'','rtn':'0801-1990-00123','phone':'+504 9999-1234','email':'maria@example.test'}

    changed={'name':customer['name'],'company':'Empresa actualizada','rtn':'111','phones':customer['phones'],'emails':customer['emails'],'addresses':customer['addresses'],'observations':customer['observations'],'provisional':customer['provisional'],'version':customer['version']}
    assert api.put(f"/api/clients/{customer['id']}",json=changed).status_code==200
    reopened=must(api.get(f"/api/cases/{case['id']}?production_id={production['id']}"))
    assert reopened['production']['client_snapshot']['company']=='Gráfica Norte'
    second=mutate(api,reopened,'convert',{**conversion(),'items':[cai_item(quantity='2')]})
    assert second['production']['scan_id']!=production['scan_id']
    first_again=must(api.get(f"/api/cases/{case['id']}?production_id={production['id']}"))
    assert first_again['production']['scan_id']==production['scan_id']


def test_cai_contact_is_scoped_and_quote_conversion_preserves_specs(clients):
    api=clients['admin']; customer,case=create(api,'Cotización CAI')
    contact=must(api.post(f"/api/clients/{customer['id']}/contacts",json={'name':'Ana Gómez','phone':'9999-1111','email':'ana@example.test'}),201)
    quote=mutate(api,case,'quotes',{'items':[cai_item()],'currency':'HNL','valid_until':'2099-12-31'})
    quote_id=quote['quotes'][-1]['id']
    quote=mutate(api,quote,f'quotes/{quote_id}/decision',{'status':'Aceptada','evidence':'Aprobada por el cliente'})
    converted=mutate(api,quote,'convert',{**conversion(),'quote_id':quote_id,'items':[],'contact_id':contact['id'],'phone':'9999-1111','email':'ana@example.test'})
    saved=converted['production']['specs_snapshot']['items'][0]
    assert saved['specs']['cai_format']=='Media carta' and saved['specs']['games_per_pad']==50
    assert converted['production']['contact_id']==contact['id']
    assert converted['production']['client_snapshot']['person']=='Ana Gómez'
    assert converted['production']['client_snapshot']['phone']=='9999-1111'

    other=must(api.post('/api/clients',json={'name':'Otra persona','phones':[],'emails':[]}),201)
    foreign=must(api.post(f"/api/clients/{other['id']}/contacts",json={'name':'Contacto ajeno'}),201)
    # Create a second active request; the out-of-scope contact must not be attachable.
    case2=must(api.post('/api/cases',json={'client_id':customer['id'],'subject':'Otra orden CAI'}),201)
    invalid=api.post(f"/api/cases/{case2['id']}/convert",json={**conversion(),'items':[cai_item()],'contact_id':foreign['id'],'version':case2['version'],'key':key()})
    assert invalid.status_code==422 and 'no pertenece' in invalid.text
