// Shared UI vocabulary. Keep these labels aligned with backend/app/schemas.py;
// the server remains authoritative for validation and state transitions.
export const PRODUCTION_STAGE = Object.freeze({
  PREPARATION: 'Preparación',
  SCHEDULED: 'Programado',
  IN_PRODUCTION: 'En producción',
  QUALITY: 'Calidad',
  READY: 'Listo para entregar',
  DELIVERED: 'Entregado',
})

export const PRODUCTION_STAGES = Object.freeze(Object.values(PRODUCTION_STAGE))

export const CASE_STATUSES = Object.freeze([
  'Nuevo',
  'En atención',
  'Esperando al cliente',
  'Esperando gestión interna',
  'Cerrado',
])

export const CASE_PRIORITIES = Object.freeze(['Baja', 'Normal', 'Alta', 'Urgente'])

export const PRODUCT = Object.freeze({
  BUSINESS_CARDS: 'Tarjetas de presentación',
  FORMS: 'Talonarios y facturas',
  FLYERS: 'Volantes y flyers',
  NOTEBOOKS: 'Libretas y cuadernos',
  BANNERS: 'Banners y lonas',
  OTHER: 'Otros trabajos',
})

export const PRODUCTS = Object.freeze(Object.values(PRODUCT))
export const PAYMENT_TERMS = Object.freeze(['Contado', 'Crédito'])
export const HNL_CURRENCY = 'HNL'
