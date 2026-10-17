import {useRef} from 'react'
import {useQueryClient} from '@tanstack/react-query'
import {Plus, Trash2} from 'lucide-react'
import {api, uid, localDate, money} from './api'
import {FormDialog, Field, Select, Badge} from './components'

export const PRODUCTS=['Tarjetas de presentación','Talonarios y facturas','Volantes y flyers','Libretas y cuadernos','Banners y lonas','Otros trabajos']
export const newItem=()=>({product:PRODUCTS[0],quantity:'100',unit:'unidades',unit_price:'0',specs:{measure_unit:'cm'}})
const SPEC_LABELS={width:'Ancho',height:'Alto',measure_unit:'Unidad de medida',material:'Material / papel',sides:'Caras',inks:'Tintas',copies:'Copias',numbering:'Numeración / rango',sheets:'Hojas',binding:'Encuadernación',finishes:'Acabados',detail:'Especificaciones adicionales'}
export function Specifications({specs}){return <dl className="specs">{Object.entries(specs||{}).filter(([,v])=>v!==null&&v!=='').map(([k,v])=><div key={k}><dt>{SPEC_LABELS[k]||k}</dt><dd>{v}</dd></div>)}</dl>}
export function ItemsEditor({items,onChange}){
  const set=(index,key,value)=>onChange(items.map((item,i)=>i===index?{...item,[key]:value}:item))
  const spec=(index,key,value)=>set(index,'specs',{...items[index].specs,[key]:value})
  return <div className="items-editor">{items.map((item,index)=>{
    const notebook=item.product===PRODUCTS[3], receipt=item.product===PRODUCTS[1], banner=item.product===PRODUCTS[4], other=item.product===PRODUCTS[5]
    const input=(key,type='text')=><Field key={key} label={SPEC_LABELS[key]} type={type} min={type==='number'?'0':undefined} step={type==='number'?'0.001':undefined} value={item.specs[key]??''} onChange={e=>spec(index,key,e.target.value)}/>
    return <section className="item-editor" key={index}><div className="section-line"><strong>Partida {index+1}</strong>{items.length>1&&<button type="button" aria-label={'Eliminar partida '+(index+1)} className="icon-button" onClick={()=>onChange(items.filter((_,i)=>i!==index))}><Trash2 size={16}/></button>}</div><div className="form-grid"><Select label="Producto" options={PRODUCTS} value={item.product} onChange={e=>set(index,'product',e.target.value)}/><Field label="Cantidad" type="number" min="0.001" step="0.001" required value={item.quantity} onChange={e=>set(index,'quantity',e.target.value)}/><Field label="Unidad" required value={item.unit} onChange={e=>set(index,'unit',e.target.value)}/><Field label="Precio unitario" type="number" min="0" step="0.01" required value={item.unit_price} onChange={e=>set(index,'unit_price',e.target.value)}/>{!other&&<>{input('width','number')}{input('height','number')}<Select label="Unidad de medida" options={['cm','mm','m']} value={item.specs.measure_unit||'cm'} onChange={e=>spec(index,'measure_unit',e.target.value)}/>{input('material')}{!banner&&<><Select label="Caras" options={['1','2']} empty="Por definir" value={item.specs.sides||''} onChange={e=>spec(index,'sides',e.target.value)}/>{input('inks')}</>}{receipt&&<>{input('copies','number')}{input('numbering')}</>}{notebook&&<>{input('sheets','number')}{input('binding')}</>}{input('finishes')}</>}<Field label="Especificaciones adicionales" className="full"><textarea value={item.specs.detail||''} rows={2} onChange={e=>spec(index,'detail',e.target.value)}/></Field></div></section>
  })}<button type="button" onClick={()=>onChange([...items,newItem()])}><Plus size={15}/>Agregar producto</button></div>
}
function cleanItems(items){return items.map(item=>({...item,specs:Object.fromEntries(Object.entries(item.specs).filter(([,v])=>v!==''&&v!==null).map(([k,v])=>[k,['copies','sheets'].includes(k)?Number(v):v]))}))}
export default function ProductForm({record, users, quote=false, source=null, onClose}){
  const qc=useQueryClient(),key=useRef(uid()),signature=useRef('')
  const accepted=record.quotes.filter(q=>q.status==='Aceptada')
  const initial={items:source?.items || [newItem()],quote_id:!quote&&source?.id?String(source.id):'',currency:source?.currency||'HNL',valid_until:localDate(),owner_id:'',requested_date:'',committed_date:'',payment_terms:'Contado',advance_required:'0',payment_due_date:localDate(),delivery_method:'Retiro en taller',instructions:''}
  async function save(values){
    let body=quote?{items:cleanItems(values.items),currency:values.currency,valid_until:values.valid_until}:{...values,quote_id:values.quote_id?Number(values.quote_id):null,owner_id:values.owner_id?Number(values.owner_id):null,requested_date:values.requested_date||null,committed_date:values.committed_date||null,items:values.quote_id?[]:cleanItems(values.items)}
    if(!quote)delete body.valid_until
    const next=JSON.stringify(body);if(signature.current&&signature.current!==next)key.current=uid();signature.current=next
    try{const result=await api(`/cases/${record.id}/${quote?'quotes':'convert'}`,{method:'POST',body:{...body,version:record.version,key:key.current}});qc.setQueryData(['case',String(record.id)],result);await qc.invalidateQueries({predicate:q=>q.queryKey[0]!=='case'})}
    catch(error){if(error.status===409)await qc.invalidateQueries({queryKey:['case',String(record.id)]});throw error}
  }
  return <FormDialog wide title={quote?'Preparar nueva versión de cotización':'Generar orden de producción'} initial={initial} onClose={onClose} onSave={save} submit={quote?'Guardar cotización':'Guardar en Preparación'} render={(v,set)=><>
    <div className="notice">{quote?'Cotizar no autoriza fabricación. Cada versión conserva sus partidas e importes.':'Conservaremos este mismo expediente, sus conversaciones y sus archivos. La ejecución se autoriza después de preparar el trabajo.'}</div>
    {!quote&&accepted.length>0&&<Select label="Reutilizar cotización aceptada" empty="Especificar productos manualmente" options={accepted.map(q=>({value:String(q.id),label:`Versión ${q.version} · ${money(q.total,q.currency)}`}))} value={v.quote_id} onChange={e=>{const q=accepted.find(x=>String(x.id)===e.target.value);set('quote_id',e.target.value);if(q){set('items',q.items);set('currency',q.currency)}}}/>}
    {v.quote_id&&!quote?<div className="notice">Se reutilizarán las partidas y precios de la versión aceptada. <Badge>{v.items.length} productos</Badge></div>:<ItemsEditor items={v.items} onChange={items=>set('items',items)}/>}
    <div className="form-grid"><Select label="Moneda" options={['HNL','USD']} disabled={!!v.quote_id&&!quote} value={v.currency} onChange={e=>set('currency',e.target.value)}/>{quote?<Field label="Vigente hasta" type="date" required value={v.valid_until} onChange={e=>set('valid_until',e.target.value)}/>:<>
    <Select label="Responsable de producción" empty="Asignar después" options={users.filter(u=>u.active&&u.permissions.some(p=>p==='production'||p==='*')).map(u=>({value:u.id,label:u.name}))} value={v.owner_id} onChange={e=>set('owner_id',e.target.value)}/>
    <Field label="Fecha solicitada" type="date" value={v.requested_date} onChange={e=>set('requested_date',e.target.value)}/><Field label="Fecha comprometida" type="date" value={v.committed_date} onChange={e=>set('committed_date',e.target.value)}/><Field label="Condición de pago" required value={v.payment_terms} onChange={e=>set('payment_terms',e.target.value)}/><Field label="Anticipo requerido" type="number" min="0" step="0.01" required value={v.advance_required} onChange={e=>set('advance_required',e.target.value)}/><Field label="Vencimiento del saldo" required type="date" value={v.payment_due_date} onChange={e=>set('payment_due_date',e.target.value)}/><Field label="Modalidad de entrega" required value={v.delivery_method} onChange={e=>set('delivery_method',e.target.value)}/><Field label="Instrucciones de taller" className="full"><textarea rows={3} value={v.instructions} onChange={e=>set('instructions',e.target.value)}/></Field></>}</div>
    <div className="total">Total estimado <strong>{money(v.items.reduce((sum,i)=>sum+Number(i.quantity)*Number(i.unit_price),0),v.currency)}</strong><small>El servidor calcula y guarda el total decimal.</small></div>
  </>}/>
}
