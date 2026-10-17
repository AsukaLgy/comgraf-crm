import {HNL_CURRENCY} from './domain'

export async function api(path, options = {}) {
  const {body, ...rest} = options
  let response
  try { response = await fetch('/api' + path, {credentials: 'same-origin', ...rest, headers: {'X-Comgraf-Request': '1', ...(body instanceof FormData ? {} : {'Content-Type': 'application/json'}), ...rest.headers}, body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body)}) }
  catch { throw new Error('No se pudo conectar. Tus datos permanecen en el formulario.') }
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(x => `${x.loc.slice(1).join('.')}: ${x.msg}`).join(' · ') : data.detail
    const error = new Error(detail || 'No se pudo guardar. Reintenta.'); error.status = response.status; throw error
  }
  return data
}
export const can = (user, permission) => user?.permissions?.some(p => p === '*' || p === permission)
export const money = (v, currency = HNL_CURRENCY) => new Intl.NumberFormat('es-HN', {style:'currency',currency}).format(Number(v || 0))
export const quantity = v => new Intl.NumberFormat('es-HN', {maximumFractionDigits:3}).format(Number(v||0))
export let timezone = 'America/Tegucigalpa'
export const setTimezone = value => { timezone = value }
export const date = value => value ? new Intl.DateTimeFormat('es-HN', {timeZone: timezone, year:'numeric', month:'short', day:'2-digit', ...(value.includes('T') ? {hour:'2-digit',minute:'2-digit'} : {})}).format(new Date(value.length === 10 ? value + 'T12:00:00Z' : value)) : 'Sin fecha'
export const localDate = () => new Intl.DateTimeFormat('en-CA', {timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())
export const dayKey = value => value.length === 10 ? value : new Intl.DateTimeFormat('en-CA', {timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(value))
export const localInput = value => {
  if (!value) return ''
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value)).map(p=>[p.type,p.value]))
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`
}
export function toUTC(local) {
  let guess = new Date(local + ':00Z')
  const intended = guess.getTime()
  for (let n=0;n<2;n++) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(guess).map(p=>[p.type,p.value]))
    const rendered = Date.parse(`${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}Z`)
    guess = new Date(guess.getTime() + intended - rendered)
  }
  return guess.toISOString()
}
export const uid = () => globalThis.crypto?.randomUUID ? crypto.randomUUID() : Array.from(crypto.getRandomValues(new Uint8Array(20)), x => x.toString(16).padStart(2,'0')).join('')
