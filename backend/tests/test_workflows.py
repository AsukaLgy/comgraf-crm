from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app.main import app
from app.db import engine, SessionLocal
from app.config import settings
from app.models import Case, Production, Ledger, Item, Quote, Task, Role, User, Event, Delivery, SavedView
from app.repository import today
from app.services import reopen_overdue_attention, historical_completion_candidates, regularize_historical_completions


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


def scan_order(clients, case):
    qr = f"CGOP1:{case['production']['scan_id']}"
    return must(clients['scanner'].post('/api/scanner/scan', json={'scan_id': qr}))


def move_to_bindery(api, case, production_id=None):
    current = mutate(api, case, 'production/stage', {'production_id': production_id, 'stage': 'PRINTING'} if production_id else {'stage': 'PRINTING'})
    return mutate(api, current, 'production/stage', {'production_id': production_id, 'stage': 'WORKSHOP'} if production_id else {'stage': 'WORKSHOP'})


def release_single_order(clients, case):
    api = clients['admin']
    current = move_to_bindery(api, case, case['production']['id'])
    result = scan_order(clients, current)
    assert result['result'] == 'released'
    return must(api.get(f"/api/cases/{case['id']}?production_id={case['production']['id']}"))


def item():
    return {'product':'Tarjetas de presentación','quantity':'100','unit':'unidades','unit_price':'10.00','specs':{'width':'9.000','height':'5.000','material':'Couché 300 g','sides':'2','inks':'CMYK'}}


def conversion(owner=None):
    return {'items':[item()],'committed_date':str(today()+timedelta(days=3)), 'payment_due_date':str(today()-timedelta(days=1)), 'delivery_method':'Retiro en taller','advance_required':'100.00','payment_terms':'Contado'}


def test_multiple_orders_keep_products_stages_deliveries_and_calendar_separate(clients):
    api=clients['admin'];_,c=create(api,'Paquete para dos sucursales')
    c=mutate(api,c,'take')
    first_items=[item(),{**item(),'product':'Volantes y flyers','quantity':'500','unit_price':'2.00'}]
    first=mutate(api,c,'convert',{**conversion(),'items':first_items})
    first_id=first['production']['id']
    assert first['production']['stage']=='APPROVAL' and first['production']['created_by_id']
    assert first['production_count']==1 and len(first['items'])==2
    assert not any(e['data'].get('action')=='stage' for e in first['events'])

    second=mutate(api,first,'convert',{**conversion(),'items':[{**item(),'product':'Banners y lonas','quantity':'2','unit_price':'250.00'}]})
    second_id=second['production']['id']
    assert second_id!=first_id and second['production_count']==2 and len(second['items'])==1
    assert second['production']['stage']=='APPROVAL'
    second=mutate(api,second,'production/stage',{'production_id':second_id,'stage':'PRINTING'})
    first_view=must(api.get(f'/api/cases/{c["id"]}?production_id={first_id}'))
    second_view=must(api.get(f'/api/cases/{c["id"]}?production_id={second_id}'))
    assert first_view['production']['stage']=='APPROVAL' and len(first_view['items'])==2
    assert second_view['production']['stage']=='PRINTING' and len(second_view['items'])==1
    assert first_view['balance']=='2000.00' and second_view['balance']=='500.00'
    assert must(api.get('/api/cases',params={'view':'production','stage':'PRINTING'}))['total']==1

    first_view=move_to_bindery(api,first_view,first_id)
    result=scan_order(clients,first_view)
    assert result['result']=='confirmed_waiting'
    assert result['pending_orders'][0]['order_number']==f'OP-{second_id:05d}'
    assert result['pending_orders'][0]['reason']=='Etapa actual: Impresión'
    second_view=must(api.get(f'/api/cases/{c["id"]}?production_id={second_id}'))
    second_view=mutate(api,second_view,'production/stage',{'production_id':second_id,'stage':'WORKSHOP'})
    result=scan_order(clients,second_view)
    assert result['result']=='released' and set(result['released_orders'])=={f'OP-{first_id:05d}',f'OP-{second_id:05d}'}
    first_view=must(api.get(f'/api/cases/{c["id"]}?production_id={first_id}'))
    first_item=first_view['items'][0]
    first_view=mutate(api,first_view,'deliveries',{'production_id':first_id,'item_id':first_item['id'],'quantity':'40','delivered_at':datetime.now(timezone.utc).isoformat(),'receiver':'Cliente','method':'Retiro','evidence':'Entrega parcial OP inicial'})
    second_view=must(api.get(f'/api/cases/{c["id"]}?production_id={second_id}'))
    assert first_view['delivery_status']=='Parcial' and len(first_view['deliveries'])==1
    assert second_view['delivery_status']=='Pendiente' and not second_view['deliveries']
    assert second_view['production']['stage']=='READY'

    all_orders=must(api.get('/api/orders?view=production'))
    ready_orders=must(api.get('/api/orders?view=ready'))
    counts=must(api.get('/api/counts'))
    assert all_orders['total']==2 and len(all_orders['items'])==2 and all_orders['count_type']=='órdenes'
    assert ready_orders['total']==2 and {row['id'] for row in ready_orders['items']}=={first_id,second_id}
    assert ready_orders['items'][0]['id']==first_id
    assert ready_orders['items'][0]['pending_quantities'][0]['quantity']=='60.000'
    assert counts['production_orders']==2 and counts['ready_orders']==2
    assert must(api.get('/api/cases',params={'view':'production','stage':'PRINTING'}))['total']==0

    events=must(api.get('/api/calendar'))
    commitments=[e for e in events if e['type']=='Entrega comprometida']
    assert len(commitments)==2 and {e['production_id'] for e in commitments}=={first_id,second_id}
    assert {e['stage'] for e in commitments}=={'Listo para entregar'}


def test_binding_scans_are_idempotent_grouped_and_scanner_role_is_limited(clients, accounts):
    api=clients['admin']; scanner=clients['scanner']; _,case=create(api,'Grupo de encuadernación')
    case=mutate(api,case,'take'); first=mutate(api,case,'convert',conversion())
    first_id=first['production']['id']; first_qr='CGOP1:'+first['production']['scan_id']
    second=mutate(api,first,'convert',conversion()); second_id=second['production']['id']
    second_qr='CGOP1:'+second['production']['scan_id']
    assert first_qr!=second_qr
    assert must(api.get(f'/api/cases/{case["id"]}?production_id={first_id}'))['production']['scan_id']==first['production']['scan_id']
    first=move_to_bindery(api,must(api.get(f'/api/cases/{case["id"]}?production_id={first_id}')),first_id)
    second=must(api.get(f'/api/cases/{case["id"]}?production_id={second_id}'))
    second=mutate(api,second,'production/stage',{'production_id':second_id,'stage':'PRINTING'})
    lookup=must(scanner.get('/api/scanner/lookup',params={'qr':first_qr}))
    assert lookup['order']['order_number']==f'OP-{first_id:05d}' and lookup['order']['items'][0]['product']=='Tarjetas de presentación'
    assert not {'balance','price','ledger','payment_status','quotes','notes'} & set(lookup)
    assert scanner.get(f'/api/cases/{case["id"]}').status_code==403
    assert scanner.get('/api/users').status_code==403
    assert scanner.get('/api/orders').status_code==403
    assert scanner.post(f'/api/cases/{case["id"]}/production/stage',json={'stage':'READY'}).status_code==403
    assert clients['coordinator'].get('/api/scanner/lookup',params={'qr':first_qr}).status_code==403

    first=must(api.get(f'/api/cases/{case["id"]}?production_id={first_id}'))
    blocked=mutate(api,first,'production/block',{'production_id':first_id,'reason':'Revisión de acabado'})
    assert scanner.post('/api/scanner/scan',json={'scan_id':first_qr}).status_code==409
    first=mutate(api,blocked,'production/unblock',{'production_id':first_id,'reason':'Acabado revisado'})
    with ThreadPoolExecutor(2) as pool:
        simultaneous=list(pool.map(lambda client: client.post('/api/scanner/scan',json={'scan_id':first_qr}),
            [scanner,clients['scanner2']]))
    assert [response.status_code for response in simultaneous]==[200,200]
    results=[response.json() for response in simultaneous]
    assert {result['result'] for result in results}=={'confirmed_waiting','already_confirmed'}
    waiting=next(result for result in results if result['result']=='confirmed_waiting')
    assert waiting['pending_orders'][0]['reason']=='Etapa actual: Impresión'
    repeated=must(scanner.post('/api/scanner/scan',json={'scan_id':first_qr}))
    assert repeated['result']=='already_confirmed'
    assert scanner.post('/api/scanner/scan',json={'scan_id':second_qr}).status_code==409
    first_after=must(api.get(f'/api/cases/{case["id"]}?production_id={first_id}'))
    assert first_after['production']['stage']=='WORKSHOP' and first_after['production']['binding_confirmed_cycle']==0
    second=must(api.get(f'/api/cases/{case["id"]}?production_id={second_id}'))
    second=mutate(api,second,'production/stage',{'production_id':second_id,'stage':'WORKSHOP'})
    released=must(scanner.post('/api/scanner/scan',json={'scan_id':second_qr}))
    assert released['result']=='released' and released['released_orders']==[f'OP-{first_id:05d}',f'OP-{second_id:05d}']
    assert scanner.post('/api/scanner/scan',json={'scan_id':second_qr}).json()['result']=='already_confirmed'
    after=must(api.get(f'/api/cases/{case["id"]}?production_id={first_id}'))
    assert after['production']['stage']=='READY' and not after['deliveries'] and after['balance']=='1000.00'
    confirmations=[event for event in after['events'] if event['data'].get('action')=='binding_scan_confirmed' and event['data'].get('production_id')==first_id]
    assert len(confirmations)==1 and confirmations[0]['actor_id'] in {accounts['scanner']['id'],accounts['scanner2']['id']}

    # A real reclamo starts a new bindery cycle without rewinding its sibling.
    after=mutate(api,after,'production/stage',{'production_id':first_id,'stage':'WORKSHOP','reason':'Reclamo: corregir acabado'})
    assert after['production']['workflow_cycle']==1 and after['production']['binding_confirmed_at'] is None
    scan_again=must(scanner.post('/api/scanner/scan',json={'scan_id':first_qr}))
    assert scan_again['result']=='released'
    sibling=must(api.get(f'/api/cases/{case["id"]}?production_id={second_id}'))
    assert sibling['production']['stage']=='READY'
    after_reclaim=must(api.get(f'/api/cases/{case["id"]}?production_id={first_id}'))
    confirmations=[event for event in after_reclaim['events'] if event['data'].get('action')=='binding_scan_confirmed' and event['data'].get('production_id')==first_id]
    assert len(confirmations)==2


def test_partial_release_requires_real_authorization_and_revalidates_cycle(clients):
    api=clients['admin']; scanner=clients['scanner']; coordinator=clients['coordinator']
    _,case=create(api,'Excepción de liberación')
    case=mutate(api,case,'take'); first=mutate(api,case,'convert',conversion()); first_id=first['production']['id']
    second=mutate(api,first,'convert',conversion())
    first=move_to_bindery(api,must(api.get(f'/api/cases/{case["id"]}?production_id={first_id}')),first_id)
    scan_id='CGOP1:'+first['production']['scan_id']
    waiting=must(scanner.post('/api/scanner/scan',json={'scan_id':scan_id}))
    assert waiting['result']=='confirmed_waiting'
    assert scanner.post('/api/scanner/exceptions',json={'scan_id':scan_id,'comment':' ','request_key':key()}).status_code==422
    request_body={'scan_id':scan_id,'comment':'El cliente retira esta orden hoy','request_key':key()}
    request=must(scanner.post('/api/scanner/exceptions',json=request_body),201)
    replay=must(scanner.post('/api/scanner/exceptions',json=request_body),201)
    assert replay['id']==request['id'] and coordinator.get('/api/scanner/exceptions').status_code==200
    assert scanner.get('/api/scanner/exceptions').status_code==403
    assert scanner.post(f'/api/scanner/exceptions/{request["id"]}/decision',json={'approve':True,'comment':'Autorizo la salida','request_key':key()}).status_code==403
    approvals=must(coordinator.get('/api/scanner/exceptions'))
    assert approvals[0]['request_comment']==request_body['comment'] and approvals[0]['pending_orders']
    decision={'approve':True,'comment':'Coordinación confirmó la excepción operativa','request_key':key()}
    approved=must(coordinator.post(f'/api/scanner/exceptions/{request["id"]}/decision',json=decision))
    assert approved['status']=='Aprobada'
    repeated=must(coordinator.post(f'/api/scanner/exceptions/{request["id"]}/decision',json=decision))
    assert repeated['status']=='Aprobada'
    order=must(api.get(f'/api/cases/{case["id"]}?production_id={first_id}'))
    other=must(api.get(f'/api/cases/{case["id"]}?production_id={second["production"]["id"]}'))
    assert order['production']['stage']=='READY' and other['production']['stage']=='APPROVAL'
    assert order['lifecycle']=='Activo' and not order['deliveries'] and order['balance']=='1000.00'
    scanner_result=must(scanner.get(f'/api/scanner/exceptions/{request["id"]}'))
    assert scanner_result['status']=='Aprobada' and scanner_result['order']['stage']=='READY'

    # A pending approval tied to an old cycle cannot release a reworked order.
    _,stale_case=create(api,'Excepción invalidada por reclamo')
    stale_case=mutate(api,stale_case,'take'); first=mutate(api,stale_case,'convert',conversion()); stale_id=first['production']['id']
    second=mutate(api,first,'convert',conversion())
    first=move_to_bindery(api,must(api.get(f'/api/cases/{stale_case["id"]}?production_id={stale_id}')),stale_id)
    stale_qr='CGOP1:'+first['production']['scan_id']
    must(scanner.post('/api/scanner/scan',json={'scan_id':stale_qr}))
    stale_request=must(scanner.post('/api/scanner/exceptions',json={'scan_id':stale_qr,'comment':'Liberación pedida antes del reclamo','request_key':key()}),201)
    first=must(api.get(f'/api/cases/{stale_case["id"]}?production_id={stale_id}'))
    first=mutate(api,first,'production/stage',{'production_id':stale_id,'stage':'PRINTING','reason':'Corrección por reclamo'})
    first=mutate(api,first,'production/stage',{'production_id':stale_id,'stage':'WORKSHOP'})
    invalidated=must(coordinator.post(f'/api/scanner/exceptions/{stale_request["id"]}/decision',json={'approve':True,'comment':'Revisada por coordinación','request_key':key()}))
    assert invalidated['status']=='Invalidada'
    first=must(api.get(f'/api/cases/{stale_case["id"]}?production_id={stale_id}'))
    assert first['production']['stage']=='WORKSHOP' and first['production']['workflow_cycle']==1


def test_manual_ready_transition_is_forbidden(clients):
    api=clients['admin']; _,case=create(api,'Transición manual controlada')
    case=mutate(api,case,'take'); case=mutate(api,case,'convert',conversion())
    case=move_to_bindery(api,case,case['production']['id'])
    mutate(api,case,'production/stage',{'stage':'READY'},409)
    current=must(api.get(f'/api/cases/{case["id"]}'))
    assert current['production']['stage']=='WORKSHOP'


def test_attention_history_search_and_drafts(clients):
    api=clients['admin']
    customer,c=create(api)
    original=c['id']
    c=mutate(api,c,'take')
    c=mutate(api,c,'events',{'kind':'Nota interna','channel':'Nota','body':'Consulta de precios sin fabricación'})
    assert c['first_response_at'] is None
    c=mutate(api,c,'events',{'kind':'Interacción manual','channel':'WhatsApp','direction':'Saliente','body':'Atendimos la consulta presencial'})
    assert c['first_response_at']
    for channel in ['Nota','Correo','WhatsApp']:
        must(api.put(f'/api/cases/{original}/drafts/{channel}',json={'body':f'Borrador {channel}','version':0}))
    c=mutate(api,c,'tasks',{'title':'Confirmar información','owner_id':c['owner_id'],'due_at':(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()})
    assert must(api.get('/api/cases?view=action'))['total']==1
    c=mutate(api,c,f'tasks/{c["tasks"][0]["id"]}/complete')
    c=mutate(api,c,'attention',{'status':'Cerrado'})
    assert c['mode']=='Atención' and c['production'] is None
    assert len(c['drafts'])==3
    for term in ['maria alvarez','0801199000123','50499991234','maria@example.test',c['number']]:
        result=must(api.get('/api/cases',params={'view':'all','q':term}))
        assert result['total']==1
    assert must(api.get('/api/clients/'+str(customer['id'])))['cases'][0]['id']==original
    with SessionLocal.begin() as db:
        db.add(Event(case_id=original,actor_id=c['owner_id'],kind='Historical',channel='Llamada',body='Legacy phone record',data={}))
        db.add(Event(case_id=original,actor_id=c['owner_id'],kind='Historical',channel='Visita',body='Legacy visit record',data={}))
    historical=must(api.get(f'/api/cases/{original}'))
    assert {event['channel'] for event in historical['events'] if event['body'].startswith('Legacy ')}=={'Llamada','Visita'}
    assert must(api.get('/api/cases?view=history'))['total']==1
    assert must(api.get('/api/counts'))['history']==1
    engine.dispose()
    assert must(api.get(f'/api/cases/{original}'))['status']=='Cerrado'


def test_attention_can_open_and_close_without_reason_and_incoming_reopens(clients):
    api=clients['admin'];_,c=create(api,'Atención independiente del trabajo')
    c=mutate(api,c,'take')
    c=mutate(api,c,'events',{'kind':'Interacción manual','channel':'WhatsApp','direction':'Entrante','body':'Incoming message before closure'})
    assert c['pending_message'] is True
    must(api.post(f'/api/cases/{c["id"]}/attention',json={'version':c['version'],'key':key(),'status':'Cerrado'}),409)
    assert must(api.get('/api/cases?view=action'))['total']==1
    c=mutate(api,c,'events',{'kind':'Interacción manual','channel':'WhatsApp','direction':'Saliente','body':'Respuesta al mensaje entrante'})
    assert c['pending_message'] is False
    c=mutate(api,c,'attention',{'status':'Cerrado'})
    assert c['status']=='Cerrado' and c['production'] is None and c['close_reason'] is None and c['pending_message'] is False
    assert must(api.get('/api/cases?view=action'))['total']==0
    c=mutate(api,c,'events',{'kind':'Nota interna','channel':'Nota','body':'Nota guardada después del cierre'})
    assert c['status']=='Cerrado' and must(api.get('/api/cases?view=action'))['total']==0
    c=mutate(api,c,'attention',{'status':'Abierto'})
    assert c['status']=='Abierto' and must(api.get('/api/cases?view=action'))['total']==1
    c=mutate(api,c,'attention',{'status':'Cerrado'})
    c=mutate(api,c,'events',{'kind':'Interacción manual','channel':'WhatsApp','direction':'Entrante','body':'Nueva consulta entrante'})
    assert c['status']=='Abierto' and c['pending_message']
    automated=[e for e in c['events'] if e['data'].get('action')=='attention_status' and e['data'].get('origin')=='automatic']
    assert len(automated)==1


def test_unassigned_pool_ignores_filters_and_take_persists_in_personal_views(clients,accounts):
    api=clients['admin'];_,c=create(api,'Sin asignar y cerrada')
    c=mutate(api,c,'attention',{'status':'Cerrado'})
    query={'view':'pool','status':'Abierto','mode':'Producción','priority':'Urgente','stage':'WORKSHOP','payment':'Pagado','due':'2000-01-01','sort':'oldest'}
    pool=must(api.get('/api/cases',params=query))
    assert pool['total']==1 and pool['items'][0]['id']==c['id']
    assert must(api.get('/api/counts'))['pool']==1
    with SessionLocal.begin() as db:
        db.add(SavedView(user_id=accounts['admin']['id'],name='Pool heredado',filters=query))
    assert must(api.get('/api/views'))[0]['filters']=={'view':'pool','sort':'oldest'}
    c=mutate(api,c,'take')
    assert c['owner_id']==accounts['admin']['id'] and c['status']=='Cerrado'
    assert must(api.get('/api/cases?view=pool'))['total']==0
    assert must(api.get('/api/cases?view=mine'))['total']==1
    assert must(api.get('/api/cases?view=mine-action'))['total']==0
    assert must(api.get('/api/counts'))['mine']==1 and must(api.get('/api/counts'))['pool']==0
    second_session=TestClient(app,headers={'X-Comgraf-Request':'1','X-Comgraf-Contract':'4','X-Comgraf-Build':'1.4.0'})
    must(second_session.post('/api/auth/login',json={'username':'admin','password':accounts['admin']['password']}))
    persisted=must(second_session.get('/api/cases?view=mine'))
    assert persisted['total']==1 and persisted['items'][0]['owner_id']==accounts['admin']['id']


def test_actionable_views_require_active_and_ignore_saved_status_and_lifecycle(clients):
    api=clients['admin'];_,c=create(api,'Seguimiento aunque atención cerrada')
    c=mutate(api,c,'take')
    future=(datetime.now(timezone.utc)+timedelta(days=3)).isoformat()
    c=mutate(api,c,'tasks',{'title':'Revisar con el cliente','owner_id':c['owner_id'],'due_at':future})
    c=mutate(api,c,'attention',{'status':'Cerrado'})
    assert must(api.get('/api/cases?view=mine&lifecycle=Completado'))['total']==1
    assert must(api.get('/api/cases?view=mine-action&status=Cerrado&lifecycle=Completado'))['total']==0
    with SessionLocal.begin() as db:
        task=db.scalar(select(Task).where(Task.case_id==c['id']))
        task.due_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(minutes=1)
    assert must(api.get('/api/cases?view=mine-action&status=Cerrado&lifecycle=Completado'))['total']==1
    assert must(api.get('/api/cases?view=action&status=Cerrado&lifecycle=Completado'))['total']==1
    assert must(api.get('/api/cases?view=mine-action'))['items'][0]['requires_action'] is True


def test_reassignment_moves_active_work_between_personal_views(clients,accounts):
    admin=clients['admin']; agent=clients['agent']; agent2=clients['agent2']
    _,c=create(admin,'Solicitud para reasignar')
    c=mutate(agent,c,'take')
    c=mutate(admin,c,'convert',conversion())
    changed=must(admin.post(f'/api/cases/{c["id"]}/assign',json={'version':c['version'],'key':key(),'owner_id':accounts['agent2']['id']}))
    assert changed['owner_id']==accounts['agent2']['id'] and changed['status']=='Abierto' and changed['lifecycle']=='Activo'
    assert changed['production']['stage']=='APPROVAL'
    assert must(agent.get('/api/cases?view=mine'))['total']==0
    mine2=must(agent2.get('/api/cases?view=mine'))
    assert mine2['total']==1 and mine2['items'][0]['id']==c['id']
    assert must(agent2.get('/api/cases?view=mine-action'))['total']==1


def test_automatic_production_completion_waits_for_delivery_payment_and_tasks(clients,accounts):
    api=clients['admin'];_,c=create(api,'Completar producción resuelta')
    c=mutate(api,c,'take');c=mutate(api,c,'convert',conversion())
    c=release_single_order(clients,c)
    c=mutate(api,c,'attention',{'status':'Cerrado'})
    c=mutate(api,c,'payments',{'amount':'1000.00','reference':'Pago completo'})
    assert c['lifecycle']=='Activo' and c['status']=='Cerrado'
    assert must(api.get('/api/cases?view=finalized'))['total']==0
    future=(datetime.now(timezone.utc)+timedelta(days=5)).isoformat()
    c=mutate(api,c,'tasks',{'title':'Confirmar entrega final','owner_id':c['owner_id'],'due_at':future})
    delivery={'item_id':c['items'][0]['id'],'quantity':'40','delivered_at':datetime.now(timezone.utc).isoformat(),'receiver':'Cliente','method':'Retiro','evidence':'Entrega parcial'}
    c=mutate(api,c,'deliveries',delivery)
    assert c['lifecycle']=='Activo' and c['delivery_status']=='Parcial'
    c=mutate(api,c,'deliveries',{**delivery,'quantity':'60'})
    assert c['lifecycle']=='Activo' and c['delivery_status']=='Completa'
    assert must(api.get('/api/cases?view=balances'))['total']==0
    assert must(api.get('/api/cases?view=mine'))['total']==1
    task=next(t for t in c['tasks'] if t['title']=='Confirmar entrega final')
    c=mutate(api,c,f'tasks/{task["id"]}/complete')
    assert c['lifecycle']=='Completado' and c['status']=='Cerrado' and c['owner_id']==accounts['admin']['id']
    completed=[e for e in c['events'] if e['data'].get('action')=='case_completed']
    assert len(completed)==1 and completed[0]['data']['origin']=='automatic'
    assert completed[0]['data']['action_trigger']=='followup_resolved'
    assert must(api.get('/api/cases?view=mine'))['total']==0
    assert must(api.get('/api/cases?view=finalized'))['total']==1
    c=mutate(api,c,'reactivate')
    assert c['lifecycle']=='Activo'
    assert must(api.get(f'/api/cases/{c["id"]}'))['lifecycle']=='Activo'
    c=mutate(api,c,'attention',{'status':'Cerrado'})
    assert c['lifecycle']=='Completado'
    assert len([e for e in c['events'] if e['data'].get('action')=='case_completed'])==2


def test_delivered_production_with_unpaid_balance_is_not_completed(clients):
    api=clients['admin'];_,c=create(api,'Entrega con saldo pendiente')
    c=mutate(api,c,'take');c=mutate(api,c,'convert',conversion())
    c=release_single_order(clients,c)
    delivery={'item_id':c['items'][0]['id'],'quantity':'100','delivered_at':datetime.now(timezone.utc).isoformat(),'receiver':'Cliente','method':'Retiro','evidence':'Trabajo entregado'}
    c=mutate(api,c,'deliveries',delivery)
    assert c['delivery_status']=='Completa' and c['balance']=='1000.00' and c['lifecycle']=='Activo'
    assert must(api.get('/api/cases?view=balances'))['total']==1
    assert must(api.get('/api/cases?view=finalized'))['total']==0


def test_historical_completion_regularization_preview_and_idempotence(clients):
    api=clients['admin'];_,c=create(api,'Regularización histórica')
    c=mutate(api,c,'take');c=mutate(api,c,'convert',conversion())
    with SessionLocal.begin() as db:
        case=db.get(Case,c['id']); production=db.get(Production,c['production']['id'])
        production.stage='READY'
        db.add(Delivery(case_id=case.id,item_id=db.scalar(select(Item.id).where(Item.case_id==case.id)),quantity=100,delivered_at=datetime.now(timezone.utc).replace(tzinfo=None),receiver='Cliente',method='Retiro',evidence='Histórico de entrega',actor_id=case.owner_id))
        db.add(Ledger(case_id=case.id,production_id=production.id,kind='Abono',amount=Decimal('-1000.00'),currency='HNL',reference='Histórico de pago',actor_id=case.owner_id))
        case.status='Abierto'
    with SessionLocal() as db:
        candidates=historical_completion_candidates(db)
        assert [case.id for case in candidates]==[c['id']]
        assert db.get(Case,c['id']).lifecycle=='Activo'
    with SessionLocal.begin() as db:
        run_id,changed=regularize_historical_completions(db,'test-regularization')
        assert run_id=='test-regularization' and [case.id for case in changed]==[c['id']]
    with SessionLocal.begin() as db:
        case=db.get(Case,c['id']);events=[e for e in db.scalars(select(Event).where(Event.case_id==c['id'])).all() if e.data.get('action')=='case_completed']
        assert case.lifecycle=='Completado' and case.completed_by_id is None
        assert len(events)==1 and events[0].data['origin']=='historical_regularization'
        assert events[0].body=='Solicitud regularizada durante la actualización'
        assert events[0].data['regularization_run']=='test-regularization'
        assert db.scalar(select(func.sum(Ledger.amount)).where(Ledger.case_id==c['id']))==0
        assert db.scalar(select(func.sum(Delivery.quantity)).where(Delivery.case_id==c['id']))==100
        assert regularize_historical_completions(db,'test-regularization')[1]==[]


def test_future_followup_close_then_due_recovery_and_reschedule(clients):
    api=clients['admin'];_,c=create(api,'Seguimiento independiente')
    c=mutate(api,c,'take')
    due=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    c=mutate(api,c,'tasks',{'title':'Llamar después','owner_id':c['owner_id'],'due_at':due})
    task=c['tasks'][0]
    c=mutate(api,c,'attention',{'status':'Cerrado'})
    assert c['status']=='Cerrado' and c['tasks'][0]['done_at'] is None
    assert must(api.get('/api/cases?view=action'))['total']==0
    with SessionLocal.begin() as db:
        db.get(Task,task['id']).due_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(minutes=1)
    with SessionLocal.begin() as db:
        assert reopen_overdue_attention(db)==1
    c=must(api.get(f'/api/cases/{c["id"]}'))
    assert c['status']=='Abierto' and must(api.get('/api/cases?view=action'))['total']==1
    with SessionLocal.begin() as db:
        assert reopen_overdue_attention(db)==0
    automatic=[e for e in c['events'] if e['data'].get('origin')=='automatic']
    assert len(automatic)==1
    new_due=datetime.now(timezone.utc)+timedelta(days=2)
    payload={'version':c['version'],'key':key(),'due_at':new_due.isoformat()}
    c=must(api.put(f'/api/cases/{c["id"]}/tasks/{task["id"]}',json=payload))
    assert c['tasks'][0]['done_at'] is None and c['tasks'][0]['due_at'].startswith((new_due.date()).isoformat())


def test_personal_inboxes_include_closed_requests_and_count_only_due_followups(clients):
    api=clients['admin']
    _,actionable=create(api,'Solicitud inmediata')
    actionable=mutate(api,actionable,'take')

    _,waiting=create(api,'Solicitud en espera')
    waiting=mutate(api,waiting,'take')
    future=(datetime.now(timezone.utc)+timedelta(days=7)).isoformat()
    waiting=mutate(api,waiting,'tasks',{'title':'Contactar la próxima semana','owner_id':waiting['owner_id'],'due_at':future})
    task_id=waiting['tasks'][0]['id']
    waiting=mutate(api,waiting,'attention',{'status':'Cerrado'})

    mine=must(api.get('/api/cases?view=mine'))
    mine_action=must(api.get('/api/cases?view=mine-action'))
    assert mine['total']==2 and {row['id'] for row in mine['items']}=={actionable['id'],waiting['id']}
    assert mine_action['total']==1 and mine_action['items'][0]['id']==actionable['id']
    assert must(api.get('/api/cases?view=mine&status=Cerrado'))['total']==1
    assert must(api.get('/api/cases?view=mine-action&status=Cerrado'))['total']==1
    counts=must(api.get('/api/counts'))
    assert counts['mine']==2 and counts['mine-action']==1

    saved=must(api.post('/api/views',json={'name':'Requieren mi acción','filters':{'view':'mine-action','status':'Cerrado'}}))
    assert saved['filters']=={'view':'mine-action'}

    with SessionLocal.begin() as db:
        db.get(Task,task_id).due_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(minutes=1)
    mine_action=must(api.get('/api/cases?view=mine-action'))
    assert mine_action['total']==2 and {row['id'] for row in mine_action['items']}=={actionable['id'],waiting['id']}
    assert must(api.get('/api/counts'))['mine-action']==2


def test_explicit_completion_reactivation_and_future_task_blockers(clients):
    api=clients['admin'];_,c=create(api,'Resolved consultation')
    c=mutate(api,c,'take')
    c=mutate(api,c,'attention',{'status':'Cerrado'})
    assert c['lifecycle']=='Activo' and must(api.get('/api/cases?view=mine'))['total']==1
    due=(datetime.now(timezone.utc)+timedelta(days=8)).isoformat()
    c=mutate(api,c,'tasks',{'title':'Confirm delivery','owner_id':c['owner_id'],'due_at':due})
    blocked=api.post(f'/api/cases/{c["id"]}/complete',json={'version':c['version'],'key':key()})
    assert blocked.status_code==409 and 'seguimiento pendiente' in blocked.json()['detail']
    assert must(api.get(f'/api/cases/{c["id"]}'))['tasks'][0]['done_at'] is None
    c=mutate(api,c,f'tasks/{c["tasks"][0]["id"]}/complete')
    completion={'version':c['version'],'key':key()}
    c=must(api.post(f'/api/cases/{c["id"]}/complete',json=completion))
    replay=must(api.post(f'/api/cases/{c["id"]}/complete',json=completion))
    assert replay['lifecycle']=='Completado'
    assert must(api.get('/api/cases?view=mine'))['total']==0
    assert must(api.get('/api/cases?view=finalized'))['total']==1
    assert c['status']=='Cerrado' and c['completed_by_id']
    c=mutate(api,c,'reactivate')
    assert c['lifecycle']=='Activo' and c['status']=='Abierto' and c['completed_at'] is None
    assert len([e for e in c['events'] if e['data'].get('action') in ('case_completed','case_reactivated')])==2


def test_completion_reports_live_production_and_financial_blockers(clients):
    api=clients['admin'];_,c=create(api,'Production still pending')
    c=mutate(api,c,'take');c=mutate(api,c,'convert',conversion())
    blocked=api.post(f'/api/cases/{c["id"]}/complete',json={'version':c['version'],'key':key()})
    assert blocked.status_code==409 and 'Falta entregar 100' in blocked.json()['detail'] and '1,000.00 por cobrar' in blocked.json()['detail']
    current=must(api.get(f'/api/cases/{c["id"]}'))
    assert current['lifecycle']=='Activo' and current['production']['stage']=='APPROVAL' and current['balance']=='1000.00'


def test_weekly_pickup_followup_partial_and_full_delivery(clients):
    api=clients['admin'];_,c=create(api,'Waiting for pickup')
    c=mutate(api,c,'take');c=mutate(api,c,'convert',conversion())
    c=release_single_order(clients,c)
    pickup=[task for task in c['tasks'] if task['category']=='PICKUP']
    assert len(pickup)==1 and pickup[0]['owner_id']==c['owner_id']
    assert datetime.fromisoformat(pickup[0]['due_at'].replace('Z','+00:00')).date() >= (datetime.now(timezone.utc)+timedelta(days=6)).date()
    assert api.get('/api/cases?view=ready').json()['total']==1
    c=mutate(api,c,f'tasks/{pickup[0]["id"]}/complete',{'note':'Customer will pick up Friday','schedule_next_pickup':True})
    next_task=next(task for task in c['tasks'] if task['category']=='PICKUP' and task['done_at'] is None)
    assert datetime.fromisoformat(next_task['due_at'].replace('Z','+00:00')).date() >= (datetime.now(timezone.utc)+timedelta(days=6)).date()
    delivery={'item_id':c['items'][0]['id'],'quantity':'40','delivered_at':datetime.now(timezone.utc).isoformat(),'receiver':'Customer','method':'Pickup','evidence':'Partial delivery'}
    c=mutate(api,c,'deliveries',delivery)
    assert next(task for task in c['tasks'] if task['id']==next_task['id'])['done_at'] is None
    c=mutate(api,c,'deliveries',{**delivery,'quantity':'60'})
    assert c['delivery_status']=='Completa'
    assert next(task for task in c['tasks'] if task['id']==next_task['id'])['done_at'] is not None
    assert len([e for e in c['events'] if e['data'].get('action')=='pickup_followup_resolved'])==1


def test_delivery_and_payment_are_one_idempotent_financial_operation(clients):
    api=clients['admin'];_,c=create(api,'Entrega con abono')
    c=mutate(api,c,'take');c=mutate(api,c,'convert',conversion())
    c=release_single_order(clients,c)
    owner=c['owner_id']; stage=c['production']['stage']
    c=mutate(api,c,'attention',{'status':'Cerrado'})
    assert c['production']['stage']==stage and c['owner_id']==owner
    assert must(api.get('/api/cases?view=mine'))['total']==1
    assert must(api.get('/api/cases?view=action'))['total']==0
    payload={'version':c['version'],'key':key(),'item_id':c['items'][0]['id'],'quantity':'40','delivered_at':datetime.now(timezone.utc).isoformat(),'receiver':'Cliente','method':'Retiro','evidence':'Recibo parcial','payment_amount':'100.00','payment_reference':'Recibo HNL 02'}
    c=must(api.post(f'/api/cases/{c["id"]}/deliveries',json=payload))
    replay=must(api.post(f'/api/cases/{c["id"]}/deliveries',json=payload))
    assert replay['delivery_status']=='Parcial' and len(replay['deliveries'])==1
    assert len(replay['ledger'])==2 and replay['balance']=='900.00'
    bad={**payload,'version':replay['version'],'key':key(),'quantity':'1','payment_amount':'9999'}
    assert api.post(f'/api/cases/{c["id"]}/deliveries',json=bad).status_code==422
    unchanged=must(api.get(f'/api/cases/{c["id"]}'))
    assert len(unchanged['deliveries'])==1 and len(unchanged['ledger'])==2
    printer=clients['printer']
    forbidden={**payload,'version':unchanged['version'],'key':key(),'quantity':'1','payment_amount':'10','payment_reference':'No permiso'}
    assert printer.post(f'/api/cases/{c["id"]}/deliveries',json=forbidden).status_code==403
    unchanged=must(api.get(f'/api/cases/{c["id"]}'))
    assert len(unchanged['deliveries'])==1 and len(unchanged['ledger'])==2
    final={**payload,'version':unchanged['version'],'key':key(),'quantity':'60','payment_amount':None,'payment_reference':None}
    c=must(api.post(f'/api/cases/{c["id"]}/deliveries',json=final))
    assert c['delivery_status']=='Completa' and c['production']['stage']=='READY'
    assert c['production']['result_label']=='Entregado' and c['production']['active_elapsed_seconds'] is None
    assert must(api.get('/api/cases?view=production'))['total']==0
    assert must(api.get('/api/cases?view=balances'))['total']==1


def test_historical_ambiguous_stage_requires_explicit_classification(clients):
    api=clients['admin'];_,c=create(api,'Clasificación heredada')
    c=mutate(api,c,'take');c=mutate(api,c,'convert',conversion())
    with SessionLocal.begin() as db:
        production=db.get(Production,c['production']['id'])
        production.legacy_stage='En producción'
        production.stage=None
        production.classification_required=True
    c=must(api.get(f'/api/cases/{c["id"]}'))
    assert c['production']['stage'] is None and c['production']['legacy_stage']=='En producción'
    assert c['production']['stage_label']=='Pendiente de clasificación' and c['production']['active_elapsed_seconds'] is None
    c=mutate(api,c,'production/stage',{'stage':'PRINTING'})
    assert c['production']['stage']=='PRINTING' and not c['production']['classification_required']
    classified=[e for e in c['events'] if e['data'].get('action')=='stage_classified']
    assert len(classified)==1 and classified[0]['data']['origin']=='manual_classification'


def test_server_pagination_is_stable_and_counts_match_filters(clients):
    api=clients['admin']
    for n in range(5):
        create(api, f'Paginación {n}')
    first=must(api.get('/api/cases',params={'view':'action','page':1,'page_size':2}))
    second=must(api.get('/api/cases',params={'view':'action','page':2,'page_size':2}))
    third=must(api.get('/api/cases',params={'view':'action','page':3,'page_size':2}))
    ids=[r['id'] for page in (first,second,third) for r in page['items']]
    assert len(ids)==len(set(ids))==5
    assert first['total']==second['total']==third['total']==must(api.get('/api/counts'))['action']==5
    assert ids==sorted(ids,reverse=True)
    assert must(api.get('/api/cases',params={'view':'pool','page_size':2}))['total']==must(api.get('/api/counts'))['pool']==5


def test_client_contact_relations_and_new_hnl_contract(clients):
    api=clients['admin']
    customer,c=create(api,'Validar cliente y lempiras')
    other=must(api.post('/api/clients',json={'name':'Otro cliente'}),201)
    contact=must(api.post(f'/api/clients/{customer["id"]}/contacts',json={'name':'Contacto comercial','phone':'9999'}),201)
    assert must(api.get(f'/api/clients/{customer["id"]}/contacts'))==[contact]
    assert api.post('/api/cases',json={'client_id':other['id'],'contact_id':contact['id'],'subject':'Relación incorrecta'}).status_code==422
    c=mutate(api,c,'take')
    quote_body={'version':c['version'],'key':key(),'items':[item()],'currency':'USD','valid_until':str(today()+timedelta(days=10))}
    assert api.post(f'/api/cases/{c["id"]}/quotes',json=quote_body).status_code==422
    base={'version':c['version'],'key':key(),**conversion()}
    assert api.post(f'/api/cases/{c["id"]}/convert',json={**base,'currency':'USD'}).status_code==422
    assert api.post(f'/api/cases/{c["id"]}/convert',json={**base,'key':key(),'payment_terms':'Contado y Crédito'}).status_code==422
    c=must(api.post(f'/api/cases/{c["id"]}/convert',json=base))
    assert c['production']['currency']=='HNL' and c['production']['payment_terms']=='Contado'
    assert c['production']['authorized_at'] is None and c['production']['specs_snapshot']['items']
    assert c['production']['owner_id'] is None and 'substage' not in c['items'][0] and 'substage' not in c['production']['specs_snapshot']['items'][0]
    assert len([e for e in c['ledger'] if e['kind']=='Obligación'])==1
    assert api.post(f'/api/cases/{c["id"]}/production/approve',json={}).status_code in (404,405)
    assert api.post(f'/api/cases/{c["id"]}/production/items/{c["items"][0]["id"]}',json={}).status_code in (404,405)


def test_legacy_usd_records_are_not_reused_or_repriced(clients,accounts):
    api=clients['admin'];_,c=create(api,'Trabajo USD histórico');c=mutate(api,c,'take')
    with SessionLocal.begin() as db:
        quote=Quote(case_id=c['id'],version=1,items=[item()],total=Decimal('1000'),currency='USD',valid_until=today()+timedelta(days=2),status='Aceptada')
        db.add(quote);db.flush();quote_id=quote.id
    c=must(api.get(f'/api/cases/{c["id"]}'))
    result=api.post(f'/api/cases/{c["id"]}/convert',json={'version':c['version'],'key':key(),**conversion(),'items':[],'quote_id':quote_id})
    assert result.status_code==409 and 'USD' in result.json()['detail']
    assert must(api.get(f'/api/cases/{c["id"]}'))['production'] is None
    with SessionLocal.begin() as db:
        case=db.get(Case,c['id']);case.mode='Producción'
        db.add(Production(case_id=case.id,price=Decimal('1000'),currency='USD',payment_terms='Crédito comercial 30 días',advance_required=0,payment_due_date=today(),delivery_method='Retiro'))
        db.flush()
        production_id=db.scalar(select(Production.id).where(Production.case_id==case.id))
        product=Item(case_id=case.id,production_id=production_id,product=item()['product'],quantity=Decimal('100'),unit='unidades',unit_price=Decimal('10'),specs={})
        db.add(product);db.add(Ledger(case_id=case.id,production_id=production_id,kind='Obligación',amount=Decimal('1000'),currency='USD',due_date=today(),reference='Histórico USD',actor_id=accounts['admin']['id']))
    c=must(api.get(f'/api/cases/{c["id"]}'))
    assert c['production']['currency']=='USD' and c['production']['payment_terms']=='Crédito comercial 30 días' and c['balance']=='1000.00'
    payment=api.post(f'/api/cases/{c["id"]}/payments',json={'version':c['version'],'key':key(),'amount':'10','reference':'No mezclar moneda'})
    assert payment.status_code==409
    unchanged=must(api.get(f'/api/cases/{c["id"]}'))
    assert unchanged['balance']=='1000.00' and len(unchanged['ledger'])==1


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
    assert c['items'][0]['quantity']=='100.000' and c['production']['stage']=='APPROVAL'
    assert any(e['body']=='Conservar esta conversación' for e in c['events'])
    generated=next(e for e in c['events'] if e['data'].get('action')=='converted')
    assert generated['data']['order_number']==f'OP-{c["production"]["id"]:05d}'
    assert generated['data']['quote_number']==f'COT-{quote["id"]:05d}' and generated['data']['quote_version']==quote['version']
    assert c['balance']=='1000.00'
    c=mutate(api,c,'production/schedule',{'scheduled_at':datetime.now(timezone.utc).isoformat(),'committed_date':str(today()+timedelta(days=2))})
    schedule_event=next(e for e in c['events'] if e['data'].get('action')=='schedule')
    assert schedule_event['data']['current']['committed_date']==str(today()+timedelta(days=2))
    assert len(must(api.get('/api/calendar')))==2
    c=mutate(api,c,'production/stage',{'stage':'PRINTING'})
    assert c['production']['authorized_at'] is None
    upload=api.post(f'/api/cases/{c["id"]}/attachments',files={'file':('arte.pdf',b'%PDF-1.4\n%%EOF','application/pdf')},data={'art_version':'v1'})
    must(upload,201)
    c=must(api.get(f'/api/cases/{c["id"]}'))
    payment={'version':c['version'],'key':key(),'kind':'Anticipo','amount':'100.00','reference':'Recibo 001'}
    c=must(api.post(f'/api/cases/{c["id"]}/payments',json=payment))
    repeated=must(api.post(f'/api/cases/{c["id"]}/payments',json=payment))
    assert repeated['balance']=='900.00' and len(repeated['ledger'])==2
    assert c['production']['specs_snapshot']['items'][0]['specs']['material']=='Couché 300 g'
    c=mutate(api,c,'production/block',{'reason':'Falta material'})
    mutate(api,c,'production/stage',{'stage':'READY'},409)
    c=mutate(api,c,'production/unblock',{'reason':'Material recibido'})
    c=mutate(api,c,'production/stage',{'stage':'WORKSHOP'})
    mutate(api,c,'production/stage',{'stage':'APPROVAL'},422)
    c=mutate(api,c,'production/stage',{'stage':'PRINTING','reason':'Retrabajo por diferencia de color'})
    c=mutate(api,c,'production/stage',{'stage':'WORKSHOP'})
    c=must(api.get(f'/api/cases/{c["id"]}'))
    result=scan_order(clients,c)
    assert result['result']=='released'
    c=must(api.get(f'/api/cases/{c["id"]}'))
    assert c['delivery_status']=='Pendiente'
    delivery={'item_id':c['items'][0]['id'],'quantity':'40','delivered_at':datetime.now(timezone.utc).isoformat(),'receiver':'María','method':'Retiro','evidence':'Recibo firmado 01'}
    c=mutate(api,c,'deliveries',delivery)
    assert c['delivery_status']=='Parcial' and c['production']['stage']=='READY'
    c=mutate(api,c,'attention',{'status':'Cerrado'})
    assert must(api.get('/api/cases?view=balances'))['total']==1
    c=mutate(api,c,'deliveries',{**delivery,'quantity':'60'})
    assert c['production']['stage']=='READY' and c['delivery_status']=='Completa' and c['production']['result_label']=='Entregado' and c['status']=='Cerrado'
    assert Decimal(c['overdue_balance'])==Decimal('900')
    c=mutate(api,c,'payments',{'amount':'900','reference':'Recibo 002'})
    assert c['payment_status']=='Pagado'
    assert c['lifecycle']=='Completado'
    repeated_scan=must(clients['scanner'].post('/api/scanner/scan',json={'scan_id':'CGOP1:'+c['production']['scan_id']}))
    assert repeated_scan['result']=='already_confirmed' and repeated_scan['order']['delivery_complete']
    c=mutate(api,c,'reactivate')
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
    c=mutate(api,c,'convert',conversion())
    assert c['production_count']==2 and len(c['ledger'])==1
    payload={'version':c['version'],'key':key(),'production_id':c['production']['id'],'amount':'50','reference':'Concurrente'}
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:api.post(f'/api/cases/{c["id"]}/payments',json=payload),range(2)))
    assert all(r.status_code==200 for r in results)
    c=must(api.get(f'/api/cases/{c["id"]}?production_id={c["production"]["id"]}'))
    assert len(c['ledger'])==2 and c['balance']=='950.00'


def test_permissions_files_csrf_and_cancellation(clients,accounts):
    api=clients['admin'];_,c=create(api)
    assert must(api.get('/api/auth/me'))['timezone']==settings.app_timezone
    assert clients['printer'].get(f'/api/cases/{c["id"]}').status_code==404
    assert clients['agent'].post('/api/users',json={'username':'bad','name':'Bad','password':'no-default-password','roles':['Administrador']}).status_code==403
    assert api.post('/api/clients',json={'name':'CSRF'},headers={'X-Comgraf-Request':'0'}).status_code==403
    assert api.post('/api/clients',json={'name':'CSRF'},headers={'Origin':'https://external.invalid'}).status_code==403
    c=mutate(api,c,'take');c=mutate(api,c,'convert',conversion())
    printer=must(clients['printer'].get(f'/api/cases/{c["id"]}'))
    assert 'balance' not in printer and 'price' not in printer['production'] and printer['ledger']==[]
    mutate(clients['printer'],c,'payments',{'amount':'5','reference':'No permitido'},403)
    mutate(clients['cashier'],c,'production/stage',{'stage':'APPROVAL'},403)
    mutate(clients['agent'],c,'events',{'kind':'Nota interna','channel':'WhatsApp','body':'No enviar nota'},403)
    mutate(api,c,'events',{'kind':'Nota interna','channel':'WhatsApp','body':'No enviar nota'},422)
    bad=api.post(f'/api/cases/{c["id"]}/attachments',files={'file':('evil.pdf',b'<script>bad</script>','application/pdf')})
    assert bad.status_code==422
    c=mutate(api,c,'payments',{'amount':'100','reference':'Anticipo'})
    c=mutate(api,c,'production/cancel',{'reason':'Cliente cancela pedido','treatment':'IMPORTE_FINAL','final_amount':'1000.00'})
    assert c['balance']=='900.00' and len(c['ledger'])==2 and c['production']['cancelled_at']


def test_cancellation_final_amount_refunds_and_idempotency(clients):
    api=clients['admin']
    _,c=create(api,'Anulación con anticipo')
    c=mutate(api,c,'take'); c=mutate(api,c,'convert',conversion())
    c=mutate(api,c,'payments',{'amount':'300.00','reference':'Anticipo real'})
    body={'version':c['version'],'key':key(),'reason':'Cliente acordó conservar solo diseño','treatment':'IMPORTE_FINAL','final_amount':'200.00'}
    c=must(api.post(f'/api/cases/{c["id"]}/production/cancel',json=body))
    assert c['balance']=='-100.00' and c['payment_status']=='A favor'
    assert c['production']['cancellation_final_amount']=='200.00'
    assert [entry['kind'] for entry in c['ledger']][-1]=='Ajuste de anulación'
    same=must(api.post(f'/api/cases/{c["id"]}/production/cancel',json=body))
    assert same['balance']=='-100.00' and len(same['ledger'])==3
    refund_body={'version':c['version'],'key':key(),'amount':'50.00','reference':'Transferencia 01'}
    c=must(api.post(f'/api/cases/{c["id"]}/production/refunds',json=refund_body))
    assert c['balance']=='-50.00'
    over={**refund_body,'version':c['version'],'key':key(),'amount':'50.01'}
    assert api.post(f'/api/cases/{c["id"]}/production/refunds',json=over).status_code==422
    refund_body['version']=c['version']
    c=must(api.post(f'/api/cases/{c["id"]}/production/refunds',json=refund_body))
    repeated=must(api.post(f'/api/cases/{c["id"]}/production/refunds',json=refund_body))
    assert repeated['balance']=='-50.00' and len(repeated['ledger'])==4
    assert c['lifecycle']=='Activo'
    assert api.get('/api/cases?view=mine').json()['total']==1
    assert api.get('/api/cases?view=finalized').json()['total']==0
    assert api.get('/api/cases?view=refunds').json()['total']==1


def test_cancellation_without_payment_clears_charge_and_cannot_fake_refund(clients):
    api=clients['admin'];_,c=create(api,'Anulación sin anticipo')
    c=mutate(api,c,'take');c=mutate(api,c,'convert',conversion())
    c=mutate(api,c,'production/cancel',{'reason':'El cliente desistió','treatment':'SIN_CARGO'})
    assert c['balance']=='0.00' and c['payment_status']=='Pagado'
    assert c['production']['cancellation_final_amount']=='0.00'
    assert len([x for x in c['ledger'] if x['kind']=='Ajuste de anulación'])==1
    assert c['production']['active_elapsed_seconds'] is None
    c=mutate(api,c,'attention',{'status':'Cerrado'})
    assert c['lifecycle']=='Completado'
    assert api.get('/api/cases?view=mine').json()['total']==0
    assert api.get('/api/cases?view=finalized').json()['total']==1


def test_cancellation_without_finance_queues_review_then_resolves(clients,accounts):
    with SessionLocal.begin() as db:
        role_name='Anulador'+key()[:8]
        db.add(Role(name=role_name,permissions=['production']))
        user=User(username='cancelador',name='Cancelador',password_hash='unused',roles=[role_name],capacity=20)
        db.add(user);db.flush(); uid=user.id
    api=TestClient(app,headers={'X-Comgraf-Request':'1','X-Comgraf-Contract':'4'})
    with SessionLocal.begin() as db:
        user=db.get(User,uid)
        user.password_hash=__import__('app.security',fromlist=['passwords']).passwords.hash('cancelador-password-2026')
    must(api.post('/api/auth/login',json={'username':'cancelador','password':'cancelador-password-2026'}))
    admin=clients['admin'];_,c=create(admin,'Anulación con revisión')
    c=mutate(admin,c,'take');c=mutate(admin,c,'convert',conversion())
    c=must(api.post(f'/api/cases/{c["id"]}/production/cancel',json={'version':c['version'],'key':key(),'reason':'Solicitar revisión económica'}))
    assert c['production']['cancellation_review_pending'] and c['payment_status']=='Revisión de anulación'
    assert api.get('/api/cases?view=cancellation-review').status_code==403
    assert admin.get('/api/cases?view=cancellation-review').json()['total']==1
    denied=api.post(f'/api/cases/{c["id"]}/production/cancel/resolve',json={'version':c['version'],'key':key(),'reason':'No debe modificar el cargo','treatment':'SIN_CARGO'})
    assert denied.status_code==403
    resolved=mutate(clients['admin'],c,'production/cancel/resolve',{'reason':'Acuerdo revisado','treatment':'SIN_CARGO'})
    assert not resolved['production']['cancellation_review_pending'] and resolved['balance']=='0.00'
    api.close()


def test_due_sort_is_mysql_compatible_and_filters_validate(clients):
    api=clients['admin'];_,c=create(api,'Orden sin compromiso')
    c=mutate(api,c,'take');c=mutate(api,c,'convert',conversion())
    result=api.get('/api/cases?view=mine&sort=due')
    assert result.status_code==200, result.text
    assert result.json()['items'][0]['id']==c['id']
    assert api.get('/api/cases?view=mine&due=fecha-mala').status_code==422
    assert api.get('/api/cases?view=mine&client_id=texto').status_code==422
    mismatch=api.post(f'/api/cases/{c["id"]}/take',json={'version':c['version'],'key':key()},headers={'X-Comgraf-Contract':'1'})
    assert mismatch.status_code==409 and mismatch.json()['code']=='CLIENT_CONTRACT_MISMATCH'
