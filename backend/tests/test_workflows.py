from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
import uuid
from sqlalchemy import select
from app.db import engine, SessionLocal
from app.models import Case, Production, Ledger, Item
from app.repository import today


def key(): return str(uuid.uuid4())


def must(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def create(client, subject='Consulta presencial'):
    customer = must(client.post('/api/clients', json={'name':'María Álvarez', 'company':'Gráfica Norte', 'rtn':'0801-1990-00123', 'phones':['+504 9999-1234'], 'emails':['maria@example.test']}), 201)
    case = must(client.post('/api/cases', json={'client_id':customer['id'], 'subject':subject}), 201)
    return customer, case


def mutate(client, case, path, body=None, expected=200):
    return must(client.post(f'/api/cases/{case["id"]}/{path}', json={'version':case['version'], 'key':key(), **(body or {})}), expected)


def item():
    return {'product':'Tarjetas de presentación','quantity':'100','unit':'unidades','unit_price':'10.00','specs':{'width':'9.000','height':'5.000','material':'Couché 300 g','sides':'2','inks':'CMYK'}}


def conversion(owner=None):
    return {'items':[item()],'owner_id':owner,'committed_date':str(today()+timedelta(days=3)), 'payment_due_date':str(today()-timedelta(days=1)), 'delivery_method':'Retiro en taller','advance_required':'100.00'}


def test_attention_history_search_and_drafts(clients):
    api=clients['admin']
    customer,c=create(api)
    original=c['id']
    c=mutate(api,c,'take')
    c=mutate(api,c,'events',{'kind':'Nota interna','channel':'Nota','body':'Consulta de precios sin fabricación'})
    assert c['first_response_at'] is None
    c=mutate(api,c,'events',{'kind':'Interacción manual','channel':'Visita','direction':'Saliente','body':'Atendimos la consulta presencial'})
    assert c['first_response_at']
    for channel in ['Nota','Correo','WhatsApp']:
        must(api.put(f'/api/cases/{original}/drafts/{channel}',json={'body':f'Borrador {channel}','version':0}))
    c=mutate(api,c,'tasks',{'title':'Confirmar información','owner_id':c['owner_id'],'due_at':(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()})
    assert must(api.get('/api/cases?view=action'))['total']==1
    c=mutate(api,c,f'tasks/{c["tasks"][0]["id"]}/complete')
    c=mutate(api,c,'attention',{'status':'Cerrado','priority':'Normal','reason':'Consulta resuelta','summary':'No requiere producción'})
    assert c['mode']=='Atención' and c['production'] is None
    assert len(c['drafts'])==3
    for term in ['maria alvarez','0801199000123','50499991234','maria@example.test',c['number']]:
        result=must(api.get('/api/cases',params={'view':'all','q':term}))
        assert result['total']==1
    assert must(api.get('/api/clients/'+str(customer['id'])))['cases'][0]['id']==original
    assert must(api.get('/api/cases?view=history'))['total']==1
    assert must(api.get('/api/counts'))['history']==1
    engine.dispose()
    assert must(api.get(f'/api/cases/{original}'))['status']=='Cerrado'


def test_production_quote_delivery_debt_and_idempotent_payment(clients,accounts):
    api=clients['admin']
    _,c=create(api,'Tarjetas para sucursal')
    c=mutate(api,c,'take')
    c=mutate(api,c,'events',{'kind':'Nota interna','channel':'Nota','body':'Conservar esta conversación'})
    c=mutate(api,c,'quotes',{'items':[item()],'currency':'HNL','valid_until':str(today()+timedelta(days=15))})
    quote=c['quotes'][0]
    assert c['mode']=='Atención'
    c=mutate(api,c,f'quotes/{quote["id"]}/decision',{'status':'Aceptada','evidence':'Cliente acepta presencialmente la versión 1'})
    c=mutate(api,c,'convert',{**conversion(accounts['printer']['id']),'items':[],'quote_id':quote['id']})
    assert c['id']==quote['case_id'] and c['mode']=='Producción'
    assert c['items'][0]['quantity']=='100.000' and c['production']['stage']=='Preparación'
    assert any(e['body']=='Conservar esta conversación' for e in c['events'])
    assert c['balance']=='1000.00'
    c=mutate(api,c,'production/schedule',{'scheduled_at':datetime.now(timezone.utc).isoformat(),'committed_date':str(today()+timedelta(days=2)),'reason':'Turno de taller confirmado'})
    assert len(must(api.get('/api/calendar')))==2
    c=mutate(api,c,'production/stage',{'stage':'Programado'})
    mutate(api,c,'production/stage',{'stage':'En producción'},422)
    upload=api.post(f'/api/cases/{c["id"]}/attachments',files={'file':('arte.pdf',b'%PDF-1.4\n%%EOF','application/pdf')},data={'art_version':'v1'})
    must(upload,201)
    c=must(api.get(f'/api/cases/{c["id"]}'))
    mutate(api,c,'production/approve',{'art_version':'v1','evidence':'Aprobado por cliente'},422)
    payment={'version':c['version'],'key':key(),'kind':'Anticipo','amount':'100.00','reference':'Recibo 001'}
    c=must(api.post(f'/api/cases/{c["id"]}/payments',json=payment))
    repeated=must(api.post(f'/api/cases/{c["id"]}/payments',json=payment))
    assert repeated['balance']=='900.00' and len(repeated['ledger'])==2
    c=mutate(api,c,'production/approve',{'art_version':'v1','evidence':'Aprobado por cliente'})
    assert c['production']['approved_snapshot']['items'][0]['specs']['material']=='Couché 300 g'
    c=mutate(api,c,'production/stage',{'stage':'En producción'})
    c=mutate(api,c,'production/block',{'reason':'Falta material'})
    mutate(api,c,'production/stage',{'stage':'Calidad'},422)
    c=mutate(api,c,'production/unblock',{'reason':'Material recibido'})
    c=mutate(api,c,'production/stage',{'stage':'Calidad'})
    mutate(api,c,'production/stage',{'stage':'En producción'},422)
    c=mutate(api,c,'production/stage',{'stage':'En producción','reason':'Retrabajo por diferencia de color'})
    c=mutate(api,c,'production/stage',{'stage':'Calidad'})
    c=mutate(api,c,'production/stage',{'stage':'Listo para entregar'})
    assert c['delivery_status']=='Pendiente'
    delivery={'item_id':c['items'][0]['id'],'quantity':'40','delivered_at':datetime.now(timezone.utc).isoformat(),'receiver':'María','method':'Retiro','evidence':'Recibo firmado 01'}
    c=mutate(api,c,'deliveries',delivery)
    assert c['delivery_status']=='Parcial' and c['production']['stage']=='Listo para entregar'
    c=mutate(api,c,'attention',{'status':'Cerrado','priority':'Normal','reason':'Atención concluida','summary':'Taller y cobranza continúan'})
    assert must(api.get('/api/cases?view=balances'))['total']==1
    c=mutate(api,c,'deliveries',{**delivery,'quantity':'60'})
    assert c['production']['stage']=='Entregado' and c['status']=='Cerrado'
    assert Decimal(c['overdue_balance'])==Decimal('900')
    c=mutate(api,c,'payments',{'amount':'900','reference':'Recibo 002'})
    assert c['payment_status']=='Pagado'
    entry=c['ledger'][-1]['id']
    c=mutate(api,c,f'payments/{entry}/reverse',{'reason':'Transferencia rechazada'})
    assert c['balance']=='900.00'
    mutate(api,c,f'payments/{entry}/reverse',{'reason':'No duplicar reversión'},409)
    kpi=must(api.get('/api/indicators'))
    assert kpi['rework_events']==1 and kpi['case_conversion']['numerator']==1


def test_simultaneous_take_convert_and_payment(clients,accounts):
    api=clients['admin'];_,c=create(api)
    def take(client):return client.post(f'/api/cases/{c["id"]}/take',json={'version':c['version'],'key':key()})
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(take,[clients['agent'],clients['agent2']]))
    assert sorted(r.status_code for r in results)==[200,409]
    c=must(api.get(f'/api/cases/{c["id"]}'))
    payload={'version':c['version'],'key':key(),**conversion(accounts['printer']['id'])}
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:api.post(f'/api/cases/{c["id"]}/convert',json=payload),range(2)))
    assert all(r.status_code==200 for r in results)
    c=must(api.get(f'/api/cases/{c["id"]}'))
    assert len(c['items'])==1 and len(c['ledger'])==1
    mutate(api,c,'convert',conversion(),409)
    payload={'version':c['version'],'key':key(),'amount':'50','reference':'Concurrente'}
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:api.post(f'/api/cases/{c["id"]}/payments',json=payload),range(2)))
    assert all(r.status_code==200 for r in results)
    c=must(api.get(f'/api/cases/{c["id"]}'))
    assert len(c['ledger'])==2 and c['balance']=='950.00'


def test_permissions_files_csrf_and_cancellation(clients,accounts):
    api=clients['admin'];_,c=create(api)
    assert clients['printer'].get(f'/api/cases/{c["id"]}').status_code==404
    assert clients['agent'].post('/api/users',json={'username':'bad','name':'Bad','password':'no-default-password','roles':['Administrador']}).status_code==403
    assert api.post('/api/clients',json={'name':'CSRF'},headers={'X-Comgraf-Request':'0'}).status_code==403
    assert api.post('/api/clients',json={'name':'CSRF'},headers={'Origin':'https://external.invalid'}).status_code==403
    c=mutate(api,c,'take');c=mutate(api,c,'convert',conversion())
    printer=must(clients['printer'].get(f'/api/cases/{c["id"]}'))
    assert 'balance' not in printer and 'price' not in printer['production'] and printer['ledger']==[]
    mutate(clients['printer'],c,'payments',{'amount':'5','reference':'No permitido'},403)
    mutate(clients['cashier'],c,'production/stage',{'stage':'Programado'},403)
    mutate(clients['agent'],c,'events',{'kind':'Nota interna','channel':'WhatsApp','body':'No enviar nota'},403)
    mutate(api,c,'events',{'kind':'Nota interna','channel':'WhatsApp','body':'No enviar nota'},422)
    bad=api.post(f'/api/cases/{c["id"]}/attachments',files={'file':('evil.pdf',b'<script>bad</script>','application/pdf')})
    assert bad.status_code==422
    c=mutate(api,c,'payments',{'amount':'100','reference':'Anticipo'})
    c=mutate(api,c,'production/cancel',{'reason':'Cliente cancela pedido'})
    assert c['balance']=='900.00' and len(c['ledger'])==2 and c['production']['cancelled_at']
