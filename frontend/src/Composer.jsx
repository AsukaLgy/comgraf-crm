import {useEffect, useRef, useState} from 'react'
import {useQueryClient} from '@tanstack/react-query'
import {api, uid} from './api'
import {ErrorBox, Select} from './components'

const CHANNELS=['Nota','Llamada','Visita','Correo','WhatsApp']
export default function Composer({record,user}){
  const qc=useQueryClient(),[channel,setChannel]=useState('Nota'),[drafts,setDrafts]=useState(()=>Object.fromEntries(CHANNELS.map(c=>{const server=record.drafts.find(d=>d.channel===c);const local=localStorage.getItem(`comgraf.draft.${user.id}.${record.id}.${c}`);return [c,local??server?.body??'']}))),[error,setError]=useState(null),[busy,setBusy]=useState(false),[saved,setSaved]=useState(''),[direction,setDirection]=useState('Saliente')
  const versions=useRef(Object.fromEntries(record.drafts.map(d=>[d.channel,d.version]))),key=useRef(uid()),timer=useRef(),locked=useRef(false),queue=useRef(Promise.resolve()),latest=useRef(drafts)
  const storageKey=c=>`comgraf.draft.${user.id}.${record.id}.${c}`
  useEffect(()=>()=>clearTimeout(timer.current),[])
  function persist(c,body){
    queue.current=queue.current.catch(()=>{}).then(async()=>{const result=await api(`/cases/${record.id}/drafts/${c}`,{method:'PUT',body:{body,version:versions.current[c]||0}});versions.current[c]=result.version;setSaved('Borrador guardado')}).catch(err=>{setError(err);setSaved('Borrador conservado en este navegador')})
    return queue.current
  }
  function edit(text){setDrafts(d=>({...d,[channel]:text}));latest.current={...latest.current,[channel]:text};localStorage.setItem(storageKey(channel),text);setSaved('Guardando borrador…');key.current=uid();clearTimeout(timer.current);timer.current=setTimeout(()=>persist(channel,text),650)}
  function switchChannel(next){clearTimeout(timer.current);persist(channel,latest.current[channel]);setChannel(next);setError(null);setSaved('');key.current=uid()}
  async function send(e){e.preventDefault();if(locked.current)return;locked.current=true;setBusy(true);setError(null);clearTimeout(timer.current)
    try {await queue.current;const result=await api(`/cases/${record.id}/events`,{method:'POST',body:{version:record.version,key:key.current,kind:channel==='Nota'?'Nota interna':'Interacción manual',channel,body:drafts[channel],direction:channel==='Nota'?'Interna':direction}});qc.setQueryData(['case',String(record.id)],result);setDrafts(d=>({...d,[channel]:''}));latest.current[channel]='';localStorage.removeItem(storageKey(channel));await persist(channel,'');key.current=uid();qc.invalidateQueries({queryKey:['counts']})}
    catch(err){setError(err);if(err.status===409)qc.invalidateQueries({queryKey:['case',String(record.id)]})}finally{locked.current=false;setBusy(false)}
  }
  return <form className="composer" onSubmit={send}><div className="tabs" role="tablist" aria-label="Canal de comunicación">{CHANNELS.map(c=><button role="tab" aria-selected={channel===c} key={c} type="button" disabled={busy} className={channel===c?'active':''} onClick={()=>switchChannel(c)}>{c==='Nota'?'Nota interna':c}</button>)}</div>{['Correo','WhatsApp'].includes(channel)&&<div className="channel-status">{channel} no configurado · Puedes guardar un borrador o registrar una conversación realizada fuera de Comgraf.</div>}<textarea aria-label={channel==='Nota'?'Nota interna':'Registro de interacción'} placeholder={channel==='Nota'?'Escribe una nota para el equipo…':'Registra lo que se conversó con el cliente…'} rows={4} required value={drafts[channel]} onChange={e=>edit(e.target.value)} disabled={busy}/><div className="composer-footer"><small role="status">{saved||'Solo visible para el equipo'}</small>{channel!=='Nota'&&<Select label="Dirección registrada" options={['Saliente','Entrante']} value={direction} onChange={e=>{setDirection(e.target.value);key.current=uid()}}/>}<button className="primary" disabled={busy||!drafts[channel].trim()}>{busy?'Guardando…':channel==='Nota'?'Guardar nota':'Registrar interacción manual'}</button></div><ErrorBox error={error}/></form>
}
