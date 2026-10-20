import {useRef, useState} from 'react'
import {useQuery, useQueryClient} from '@tanstack/react-query'
import {Plus, Trash2} from 'lucide-react'
import {api, can, uid, localDate, money} from './api'
import {FormDialog, Field, Select, Badge, ErrorBox} from './components'
import {PRODUCT, PRODUCTS, PAYMENT_TERMS, HNL_CURRENCY} from './domain'

const CAI = 'Talonarios de factura CAI'
const CAI_FORMATS = [
  {value:'Media carta',label:'Media carta — 5.5 × 8.5 pulgadas'},
  {value:'Cuarto de carta',label:'Cuarto de carta — 4.25 × 5.5 pulgadas'},
  {value:'8.5 × 7.25',label:'8.5 × 7.25 pulgadas'},
  {value:'8.5 × 8.5',label:'8.5 × 8.5 pulgadas'},
  {value:'Carta',label:'Carta — 8.5 × 11 pulgadas'},
]
const copyNames = {1: 'Original + 1 copia', 2: 'Original + 2 copias', 3: 'Original + 3 copias'}
export const newItem=()=>({product:PRODUCT.BUSINESS_CARDS,quantity:'100',unit:'unidades',unit_price:'0',specs:{measure_unit:'cm'}})
const SPEC_LABELS={width:'Ancho',height:'Alto',measure_unit:'Unidad de medida',material:'Material / papel',sides:'Caras',inks:'Tintas',copies:'Copias',numbering:'Numeración / rango',sheets:'Hojas',binding:'Encuadernación',finishes:'Acabados',detail:'Especificaciones adicionales'}
export function Specifications({specs}){return <dl className="specs">{Object.entries(specs||{}).filter(([,v])=>v!==null&&v!=='').map(([k,v])=><div key={k}><dt>{SPEC_LABELS[k]||k}</dt><dd>{v}</dd></div>)}</dl>}

function Caispecs({item, set, index}) {
  const specs=item.specs||{}
  const update=(key,value)=>set(index,'specs',{...specs,[key]:value})
  const integerOptions=(values)=>values.map(String)
  const games=Number(specs.games_per_pad||0), pads=Number(item.quantity||0), copies=Number(specs.additional_copies||0)
  const totalGames=pads*games, sheets=totalGames*(copies+1)
  return <div className="form-grid cai-fields">
    <Select label="Formato (pulgadas)" required options={CAI_FORMATS} value={specs.cai_format||''} onChange={e=>update('cai_format',e.target.value)}/>
    <Select label="Juegos por talonario" required options={integerOptions([25,50])} value={specs.games_per_pad==null?'':String(specs.games_per_pad)} onChange={e=>update('games_per_pad',Number(e.target.value))}/>
    <Select label="Tipo de papel" required options={['Bond','Químico']} value={specs.paper_type||''} onChange={e=>update('paper_type',e.target.value)}/>
    <div><Select label="Copias adicionales al original" required options={integerOptions([1,2,3])} value={specs.additional_copies==null?'':String(specs.additional_copies)} onChange={e=>update('additional_copies',Number(e.target.value))}/><small>{copyNames[copies]||'Selecciona la cantidad de copias.'}</small></div>
    <Field label="Cantidad de talonarios" type="number" min="1" step="1" required value={item.quantity} onChange={e=>set(index,'quantity',e.target.value)}/>
    <Field label="Precio por talonario (HNL)" type="number" min="0" step="0.01" required value={item.unit_price} onChange={e=>set(index,'unit_price',e.target.value)}/>
    <div className="cai-calculation full" aria-live="polite"><strong>{pads||0} talonarios · {totalGames||0} juegos</strong><span>{sheets||0} hojas del producto terminado</span><small>Resumen de producto terminado; no representa hojas carta de fabricación.</small></div>
    <Field label="Observaciones e instrucciones adicionales" className="full"><textarea value={specs.detail||''} rows={3} onChange={e=>update('detail',e.target.value)}/></Field>
  </div>
}

export function ItemsEditor({items,onChange}){
  const set=(index,key,value)=>onChange(items.map((item,i)=>i===index?{...item,[key]:value}:item))
  const spec=(index,key,value)=>set(index,'specs',{...items[index].specs,[key]:value})
  const changeProduct=(index,product)=>onChange(items.map((item,i)=>i!==index?item:product===CAI?{...item,product,quantity:'1',unit:'talonarios',specs:{cai_format:'Media carta',games_per_pad:25,paper_type:'Bond',additional_copies:1,detail:''}}:item.product===CAI?{...item,product,unit:'unidades',specs:{measure_unit:'cm'}}:{...item,product}))
  return <div className="items-editor">{items.map((item,index)=>{
    const notebook=item.product===PRODUCT.NOTEBOOKS, receipt=item.product===PRODUCT.FORMS, banner=item.product===PRODUCT.BANNERS, other=item.product===PRODUCT.OTHER, cai=item.product===CAI
    const input=(key,type='text')=><Field key={key} label={SPEC_LABELS[key]} type={type} min={type==='number'?'0':undefined} step={type==='number'?'0.001':undefined} value={item.specs[key]??''} onChange={e=>spec(index,key,e.target.value)}/>
    return <section className="item-editor" key={index}><div className="section-line"><strong>Partida {index+1}</strong>{items.length>1&&<button type="button" aria-label={'Eliminar partida '+(index+1)} className="icon-button" onClick={()=>onChange(items.filter((_,i)=>i!==index))}><Trash2 size={16}/></button>}</div><div className="form-grid"><Select label="Producto" options={PRODUCTS} value={item.product} onChange={e=>changeProduct(index,e.target.value)}/>
      {cai?<Caispecs item={item} set={set} index={index}/>:<><Field label="Cantidad" type="number" min="0.001" step="0.001" required value={item.quantity} onChange={e=>set(index,'quantity',e.target.value)}/><Field label="Unidad" required value={item.unit} onChange={e=>set(index,'unit',e.target.value)}/><Field label="Precio unitario (HNL)" type="number" min="0" step="0.01" required value={item.unit_price} onChange={e=>set(index,'unit_price',e.target.value)}/>{!other&&<>{input('width','number')}{input('height','number')}<Select label="Unidad de medida" options={['cm','mm','m']} value={item.specs.measure_unit||'cm'} onChange={e=>spec(index,'measure_unit',e.target.value)}/>{input('material')}{!banner&&<><Select label="Caras" options={['1','2']} empty="Por definir" value={item.specs.sides||''} onChange={e=>spec(index,'sides',e.target.value)}/>{input('inks')}</>}{receipt&&<>{input('copies','number')}{input('numbering')}</>}{notebook&&<>{input('sheets','number')}{input('binding')}</>}{input('finishes')}</>}<Field label="Especificaciones adicionales" className="full"><textarea value={item.specs.detail||''} rows={2} onChange={e=>spec(index,'detail',e.target.value)}/></Field></>}
    </div></section>
  })}<button type="button" onClick={()=>onChange([...items,newItem()])}><Plus size={15}/>Agregar producto</button></div>
}
function cleanItems(items){return items.map(item=>({...Object.fromEntries(Object.entries(item).filter(([key])=>key!=='cai_summary')),specs:Object.fromEntries(Object.entries(item.specs||{}).filter(([,v])=>v!==''&&v!==null).map(([k,v])=>[k,['copies','sheets','games_per_pad','additional_copies'].includes(k)?Number(v):v]))}))}

function ClientIdentity({record,user,v,set}) {
  const qc=useQueryClient(),client=record.client
  const contactsQuery=useQuery({queryKey:['client-contacts',client.id],queryFn:()=>api(`/clients/${client.id}/contacts`),enabled:can(user,'clients')})
  const contacts=contactsQuery.data||[]
  const [editing,setEditing]=useState(false),[saving,setSaving]=useState(false),[error,setError]=useState(null),[profile,setProfile]=useState(null),[newContact,setNewContact]=useState({name:'',phone:'',email:'',position:''})
  const phones=[...new Set([...(client.phones||[]),...contacts.map(c=>c.phone).filter(Boolean)])]
  const emails=[...new Set([...(client.emails||[]),...contacts.map(c=>c.email).filter(Boolean)])]
  const selected=contacts.find(c=>String(c.id)===String(v.contact_id))
  async function saveProfile(){setSaving(true);setError(null);try{const result=await api(`/clients/${client.id}`,{method:'PUT',body:{...profile,phones:profile.phones.split(',').map(x=>x.trim()).filter(Boolean),emails:profile.emails.split(',').map(x=>x.trim()).filter(Boolean),version:client.version}});Object.assign(client,result);await qc.invalidateQueries({queryKey:['client-contacts',client.id]});await qc.invalidateQueries({queryKey:['case',String(record.id)]});setEditing(false);setProfile(null)}catch(e){setError(e)}finally{setSaving(false)}}
  async function addContact(){if(!newContact.name.trim())return;setSaving(true);setError(null);try{const contact=await api(`/clients/${client.id}/contacts`,{method:'POST',body:newContact});await qc.invalidateQueries({queryKey:['client-contacts',client.id]});set('contact_id',String(contact.id));set('phone',contact.phone||'');set('email',contact.email||'');setNewContact({name:'',phone:'',email:'',position:''})}catch(e){setError(e)}finally{setSaving(false)}}
  return <section className="cai-customer"><div className="section-line"><strong>Datos del cliente</strong>{can(user,'clients')&&<button type="button" className="text-button" onClick={()=>{setProfile({name:client.name,company:client.company||'',rtn:client.rtn||'',phones:(client.phones||[]).join(', '),emails:(client.emails||[]).join(', '),addresses:(client.addresses||[]),observations:client.observations||'',provisional:client.provisional});setEditing(x=>!x)}}>{editing?'Cerrar edición':'Completar perfil'}</button>}</div>
    <div className="cai-customer-summary"><span><b>Empresa:</b> {client.company||'Falta en el perfil'}</span><span><b>Persona:</b> {selected?.name||(client.company?'No seleccionada':client.name)||'Falta en el perfil'}</span><span><b>RTN:</b> {client.rtn||'Falta en el perfil'}</span></div>
    <div className="form-grid"><Select label="Contacto" options={contacts.map(c=>({value:String(c.id),label:c.name}))} empty="Sin contacto específico" value={v.contact_id||''} onChange={e=>{const id=e.target.value,contact=contacts.find(c=>String(c.id)===id);set('contact_id',id);set('phone',contact?.phone||'');set('email',contact?.email||'')}}/><Select label="Teléfono registrado" options={phones} empty="Sin teléfono" value={v.phone||''} onChange={e=>set('phone',e.target.value)}/><Select label="Correo registrado" options={emails} empty="Sin correo" value={v.email||''} onChange={e=>set('email',e.target.value)}/></div>
    {(!client.rtn||!client.company||(!client.name&&client.company))&&<small className="cai-missing">Faltan datos del cliente. Puedes completar el perfil con los permisos existentes; teléfono y correo no son obligatorios.</small>}
    {editing&&profile&&<div className="cai-profile-editor"><div className="form-grid"><Field label="Nombre de persona / cliente" value={profile.name} onChange={e=>setProfile({...profile,name:e.target.value})}/><Field label="Empresa" value={profile.company} onChange={e=>setProfile({...profile,company:e.target.value})}/><Field label="RTN" value={profile.rtn} onChange={e=>setProfile({...profile,rtn:e.target.value})}/><Field label="Teléfonos registrados (separados por coma)" value={profile.phones} onChange={e=>setProfile({...profile,phones:e.target.value})}/><Field label="Correos registrados (separados por coma)" value={profile.emails} onChange={e=>setProfile({...profile,emails:e.target.value})}/></div><button type="button" disabled={saving} onClick={()=>saveProfile()}>Guardar perfil</button><div className="form-grid cai-contact-add"><Field label="Agregar contacto" value={newContact.name} onChange={e=>setNewContact({...newContact,name:e.target.value})}/><Field label="Teléfono" value={newContact.phone} onChange={e=>setNewContact({...newContact,phone:e.target.value})}/><Field label="Correo" value={newContact.email} onChange={e=>setNewContact({...newContact,email:e.target.value})}/><button type="button" disabled={saving||!newContact.name.trim()} onClick={addContact}>Agregar contacto</button></div></div>}
    <ErrorBox error={error}/>
  </section>
}

export default function ProductForm({record, user, quote=false, source=null, onClose, onCreated}){
  const qc=useQueryClient(),key=useRef(uid()),signature=useRef(''),[created,setCreated]=useState(null),[createdVersion,setCreatedVersion]=useState(record.version),[advanceError,setAdvanceError]=useState(null),[advancing,setAdvancing]=useState(false)
  const accepted=record.quotes.filter(q=>q.status==='Aceptada'&&q.currency===HNL_CURRENCY&&!q.production_id)
  const oldUsd=!!source&&source.currency!==HNL_CURRENCY
  const initial={items:source?.items ? source.items.map(i=>oldUsd?{...i,unit_price:''}:i) : [newItem()],quote_id:!quote&&source?.id&&!oldUsd?String(source.id):'',currency:HNL_CURRENCY,valid_until:localDate(),requested_date:'',committed_date:'',payment_terms:PAYMENT_TERMS[0],advance_required:'0',payment_due_date:localDate(),delivery_method:'Retiro en taller',instructions:'',contact_id:record.contact_id?String(record.contact_id):'',phone:record.case_contact?.phone||(record.client.phones||[]).length===1?record.case_contact?.phone||record.client.phones[0]:'',email:record.case_contact?.email||(record.client.emails||[]).length===1?record.case_contact?.email||record.client.emails[0]:''}
  async function save(values){
    let body=quote?{items:cleanItems(values.items),currency:HNL_CURRENCY,valid_until:values.valid_until}:{...values,contact_id:values.contact_id?Number(values.contact_id):null,currency:HNL_CURRENCY,quote_id:values.quote_id?Number(values.quote_id):null,requested_date:values.requested_date||null,committed_date:values.committed_date||null,items:values.quote_id?[]:cleanItems(values.items)}
    if(!quote)delete body.valid_until
    const next=JSON.stringify(body);if(signature.current&&signature.current!==next)key.current=uid();signature.current=next
    try{const result=await api(`/cases/${record.id}/${quote?'quotes':'convert'}`,{method:'POST',body:{...body,version:record.version,key:key.current}});qc.setQueryData(['case',String(record.id),String(result.production?.id||'')],result);await qc.invalidateQueries({predicate:q=>q.queryKey[0]!=='case'});if(!quote){setCreated(result.production);setCreatedVersion(result.version);onCreated?.(result.production);setAdvanceError(null);return false}}
    catch(error){if(error.status===409)await qc.invalidateQueries({queryKey:['case',String(record.id)]});throw error}
  }
  async function advance(){if(!created)return;setAdvancing(true);setAdvanceError(null);try{const result=await api(`/cases/${record.id}/production/stage`,{method:'POST',body:{production_id:created.id,stage:'PRINTING',reason:'',version:createdVersion,key:uid()}});qc.setQueryData(['case',String(record.id),String(created.id)],result);await qc.invalidateQueries({predicate:q=>q.queryKey[0]!=='case'});onClose()}catch(error){setAdvanceError(error)}finally{setAdvancing(false)}}
  return <FormDialog wide title={quote?'Preparar nueva versión de cotización':'Generar orden de producción'} initial={initial} onClose={onClose} onSave={save} submit={quote?'Guardar cotización':'Generar orden de producción'} afterSave={()=> <div className="dialog-body">
    <div className="notice"><strong>Orden creada en Aprobación.</strong><p>¿Querés pasarla a Impresión ahora?</p><small>Elegí Impresión si el trabajo ya está aprobado y va a comenzar a imprimirse.</small></div><p>{created?.order_number||`OP-${String(created?.id||'').padStart(5,'0')}`} queda guardada en la solicitud.</p><ErrorBox error={advanceError}/><div className="actions"><button type="button" onClick={onClose}>Mantener en Aprobación</button><button type="button" className="primary" disabled={advancing} onClick={advance}>{advancing?'Actualizando…':'Pasar a Impresión'}</button></div>
  </div>} render={(v,set)=><>
    <div className="notice">{quote?'Cotizar no inicia fabricación. Cada versión conserva sus partidas e importes en lempiras.':'Esta orden se agregará a la solicitud en la etapa Aprobación. Agregá todos los productos que compartirán etapa y programación.'}</div>
    {!quote&&<ClientIdentity record={record} user={user} v={v} set={set}/>}
    {oldUsd&&<div className="notice">La cotización seleccionada conserva precios históricos en dólares. Revisa y captura precios nuevos en lempiras; no se aplicó conversión cambiaria.</div>}
    {!quote&&accepted.length>0&&<Select label="Reutilizar cotización aceptada en HNL" empty="Especificar productos manualmente" options={accepted.map(q=>({value:String(q.id),label:`Versión ${q.version} · ${money(q.total,'HNL')}`}))} value={v.quote_id} onChange={e=>{const q=accepted.find(x=>String(x.id)===e.target.value);set('quote_id',e.target.value);if(q)set('items',q.items)}}/>}
    {v.quote_id&&!quote?<div className="notice">Se reutilizarán las partidas y precios de la versión aceptada. <Badge>{v.items.length} productos</Badge></div>:<ItemsEditor items={v.items} onChange={items=>set('items',items)}/>}
    <div className="form-grid">{quote?<Field label="Vigente hasta" type="date" required value={v.valid_until} onChange={e=>set('valid_until',e.target.value)}/>:<>
    <Field label="Fecha solicitada" type="date" value={v.requested_date} onChange={e=>set('requested_date',e.target.value)}/><Field label="Fecha comprometida" type="date" value={v.committed_date} onChange={e=>set('committed_date',e.target.value)}/><Select label="Condición de pago" options={PAYMENT_TERMS} value={v.payment_terms} onChange={e=>set('payment_terms',e.target.value)}/><Field label="Anticipo requerido (HNL)" type="number" min="0" step="0.01" required value={v.advance_required} onChange={e=>set('advance_required',e.target.value)}/><Field label="Vencimiento del saldo" required type="date" value={v.payment_due_date} onChange={e=>set('payment_due_date',e.target.value)}/><Field label="Modalidad de entrega" required value={v.delivery_method} onChange={e=>set('delivery_method',e.target.value)}/><Field label="Instrucciones de taller" className="full"><textarea rows={3} value={v.instructions} onChange={e=>set('instructions',e.target.value)}/></Field></>}</div>
    <div className="total">Total estimado <strong>{money(v.items.reduce((sum,i)=>sum+Number(i.quantity)*Number(i.unit_price),0),'HNL')}</strong><small>Importes en lempiras; el servidor calcula el total decimal. En CAI, el precio se multiplica solo por cantidad de talonarios.</small></div>
  </>}/>
}
