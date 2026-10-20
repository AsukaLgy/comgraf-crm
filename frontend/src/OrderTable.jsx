import {Link} from 'react-router-dom'
import {date, money} from './api'
import {Badge, Empty} from './components'

export default function OrderTable({items}){
  if(!items?.length)return <Empty>No hay órdenes en esta vista.</Empty>
  return <div className="table-wrap inbox-list-scroll" tabIndex={0} role="region" aria-label="Lista desplazable de órdenes de producción"><table className="case-table"><thead><tr><th>Orden / solicitud</th><th>Cliente</th><th>Etapa actual</th><th>Entrega comprometida</th><th>Partidas pendientes</th><th>Saldo</th></tr></thead><tbody>
    {items.map(order=><tr key={order.id}><td><Link to={`/cases/${order.case_id}?production_id=${order.id}`}><small className="case-number">{order.case_number}</small><strong>{order.order_number}</strong><small>{order.subject}</small></Link></td><td>{order.client.name}</td><td><Badge tone={order.stage==='READY'?'amber':'teal'}>{order.stage_label}</Badge></td><td>{order.committed_date?date(order.committed_date):'Sin fecha'}<small>{order.scheduled_at?`Inicio ${date(order.scheduled_at)}`:''}</small></td><td>{order.pending_quantities.map((item,index)=><small key={index}>{item.product}: {item.quantity} {item.unit}</small>)}</td><td>{order.balance===undefined?'—':order.balance===null?'Revisión por moneda':money(order.balance,order.currency)}</td></tr>)}
  </tbody></table></div>
}
