import { test, expect } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
const credentials = JSON.parse(fs.readFileSync(path.resolve('../.runtime/e2e.json'), 'utf8'))
const headers = { 'X-Comgraf-Request': '1', 'X-Comgraf-Contract': '3', 'X-Comgraf-Build': '1.2.0' }
let errors = []
test.beforeEach(async ({ page }) => {
  errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto('/')
  await page.getByLabel('Usuario', { exact: true }).fill(credentials.username)
  await page.getByLabel('Contraseña', { exact: true }).fill(credentials.password)
  await page.getByRole('button', { name: 'Iniciar sesión', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Mi día', exact: true })).toBeVisible()
})
test.afterEach(() => expect(errors).toEqual([]))

async function createCase(page, subject) {
  const client = await page.request.post('/api/clients', {
    headers,
    data: { name: 'E2E · Cliente ' + Date.now() },
  })
  expect(client.ok()).toBeTruthy()
  const customer = await client.json()
  const response = await page.request.post('/api/cases', {
    headers,
    data: { client_id: customer.id, subject },
  })
  expect(response.ok()).toBeTruthy()
  const record = await response.json()
  await page.goto('/cases/' + record.id)
  await page.getByRole('button', { name: 'Tomar caso', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Tomar caso', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  return record
}

test('tomar un caso sin asignar desde la bandeja actualiza la fila', async ({ page }) => {
  const clientResponse = await page.request.post('/api/clients', {
    headers,
    data: { name: 'E2E · Pool ' + Date.now() },
  })
  expect(clientResponse.ok()).toBeTruthy()
  const client = await clientResponse.json()
  const response = await page.request.post('/api/cases', {
    headers,
    data: { client_id: client.id, subject: 'E2E caso para tomar desde bandeja' },
  })
  expect(response.ok()).toBeTruthy()
  await page.goto('/inbox/pool')
  const row = page.getByRole('row').filter({ hasText: 'E2E caso para tomar desde bandeja' })
  await row.getByRole('button', { name: 'Tomar caso' }).click()
  await expect(row).toHaveCount(0)
  await expect(page.getByRole('link', { name: /Mis casos/ })).toBeVisible()
})

test('anular con importe final calcula devolución y registra devolución real', async ({ page }) => {
  const record = await createCase(page, 'E2E Anulación comercial con devolución')
  const current = await (await page.request.get(`/api/cases/${record.id}`)).json()
  const convertedResponse = await page.request.post(`/api/cases/${record.id}/convert`, {
    headers,
    data: {
      version: current.version,
      key: `e2e-convert-${Date.now()}`,
      items: [
        {
          product: 'Tarjetas de presentación',
          quantity: '100',
          unit: 'unidades',
          unit_price: '10.00',
          specs: { width: '9', height: '5', material: 'Couché 300 g', sides: '2', inks: 'CMYK' },
        },
      ],
      currency: 'HNL',
      payment_terms: 'Contado',
      advance_required: '0.00',
      payment_due_date: '2099-12-31',
      delivery_method: 'Retiro en taller',
    },
  })
  expect(convertedResponse.ok()).toBeTruthy()
  const converted = await convertedResponse.json()
  const paidResponse = await page.request.post(`/api/cases/${record.id}/payments`, {
    headers,
    data: {
      version: converted.version,
      key: `e2e-payment-${Date.now()}`,
      amount: '300.00',
      kind: 'Anticipo',
      reference: 'Anticipo E2E',
    },
  })
  expect(paidResponse.ok()).toBeTruthy()

  await page.goto(`/cases/${record.id}`)
  await page.getByRole('button', { name: 'Producción', exact: true }).click()
  await page.getByRole('button', { name: 'Anular trabajo', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Motivo de anulación').fill('Se cancela antes de terminar el trabajo')
  await dialog.getByLabel('Tratamiento económico').selectOption('IMPORTE_FINAL')
  await dialog.getByLabel('Importe final acordado (HNL)').fill('200.00')
  await expect(dialog.getByText(/Pendiente de devolución/)).toBeVisible()
  await expect(dialog.getByText(/L\s*100\.00/)).toBeVisible()
  await dialog.getByRole('button', { name: 'Guardar' }).click()
  await expect(page.getByText(/Pendiente de devolución/).first()).toBeVisible()

  await page.getByRole('button', { name: 'Entregas y cobros', exact: true }).click()
  await page.getByRole('button', { name: 'Registrar devolución' }).click()
  const refundDialog = page.getByRole('dialog')
  await expect(refundDialog.getByLabel(/Importe a devolver/)).toHaveValue('100.00')
  await refundDialog.getByLabel('Referencia de la devolución').fill('Transferencia devuelta E2E')
  await refundDialog.getByRole('button', { name: 'Guardar' }).click()
  await expect(refundDialog).toHaveCount(0)
  const final = await (await page.request.get(`/api/cases/${record.id}`)).json()
  expect(final.balance).toBe('0.00')
  expect(final.ledger.filter((entry) => entry.kind === 'Devolución')).toHaveLength(1)
})

test('manual consultation can be closed and explicitly completed', async ({ page }) => {
  const record = await createCase(page, 'E2E consultation without production')
  await expect(page.getByLabel(/Etapas de producci/)).toHaveCount(0)
  await page.getByRole('tab', { name: 'Notas', exact: true }).click()
  await page
    .getByLabel('Nota interna', { exact: true })
    .fill('Consultation handled; no production needed.')
  await page.getByRole('button', { name: 'Guardar nota', exact: true }).click()
  await expect(
    page.getByText('Consultation handled; no production needed.', { exact: true }),
  ).toBeVisible()
  await page.getByRole('button', { name: /Cerrar atenci/ }).click()
  await page.getByRole('button', { name: 'Completar caso', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText(/expediente/)).toBeVisible()
  await dialog.getByRole('button', { name: 'Completar caso', exact: true }).click()
  await expect(page.getByText('Completado', { exact: true }).first()).toBeVisible()
  const completed = await (await page.request.get(`/api/cases/${record.id}`)).json()
  expect(completed.lifecycle).toBe('Completado')
  await page.getByRole('button', { name: 'Reactivar caso', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Completar caso', exact: true })).toBeVisible()
  const current = await (await page.request.get(`/api/cases/${record.id}`)).json()
  expect(current.lifecycle).toBe('Activo')
  expect(
    current.events.filter((event) =>
      ['case_completed', 'case_reactivated'].includes(event.data.action),
    ),
  ).toHaveLength(2)
})
test('borradores por canal sobreviven navegación y recarga', async ({ page }) => {
  const record = await createCase(page, 'E2E Borradores por canal')
  await page.getByLabel('Nota interna', { exact: true }).fill('Nota interna que nunca debe salir')
  await page.getByRole('tab', { name: 'Correo', exact: true }).click()
  await page.getByLabel('Registro de interacción').fill('Borrador de correo sin enviar')
  await page.getByRole('tab', { name: 'WhatsApp', exact: true }).click()
  await page.getByLabel('Registro de interacción').fill('Borrador de WhatsApp sin enviar')
  await expect(page.getByText(/WhatsApp no configurado/)).toBeVisible()
  await page.getByRole('tab', { name: 'Notas', exact: true }).click()
  await expect(page.getByLabel('Nota interna', { exact: true })).toHaveValue(
    'Nota interna que nunca debe salir',
  )
  await page.getByRole('link', { name: 'Mis casos', exact: true }).click()
  await page.goto('/cases/' + record.id)
  await page.getByRole('tab', { name: 'Correo', exact: true }).click()
  await expect(page.getByLabel('Registro de interacción')).toHaveValue(
    'Borrador de correo sin enviar',
  )
  await page.reload()
  await page.getByRole('tab', { name: 'WhatsApp', exact: true }).click()
  await expect(page.getByLabel('Registro de interacción')).toHaveValue(
    'Borrador de WhatsApp sin enviar',
  )
})

test('error real de validación conserva formulario y conversión mantiene expediente', async ({
  page,
}) => {
  const record = await createCase(page, 'E2E Conversión con recuperación de error')
  await page.getByRole('button', { name: 'Generar orden de producción', exact: true }).click()
  const d = page.getByRole('dialog')
  await d.getByLabel('Producto', { exact: true }).selectOption('Banners y lonas')
  await d.getByLabel('Cantidad', { exact: true }).fill('2')
  await d.getByLabel('Precio unitario (HNL)', { exact: true }).fill('250')
  await d.getByLabel('Ancho', { exact: true }).fill('1.25')
  await d.getByLabel('Alto', { exact: true }).fill('2.50')
  await d.getByLabel('Material / papel').fill('Lona 13 oz')
  await d.getByLabel('Anticipo requerido (HNL)').fill('1000')
  await d.getByLabel('Instrucciones de taller').fill('Ojillos cada 50 cm; conservar este texto')
  await d.getByRole('button', { name: /Generar orden de producción/ }).click()
  await expect(d.getByRole('alert')).toContainText('El anticipo no puede superar el precio')
  await expect(d.getByLabel('Instrucciones de taller')).toHaveValue(
    'Ojillos cada 50 cm; conservar este texto',
  )
  await expect(d.getByLabel('Ancho', { exact: true })).toHaveValue('1.25')
  await d.getByLabel('Anticipo requerido (HNL)').fill('0')
  await d.getByRole('button', { name: /Generar orden de producción/ }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page).toHaveURL(new RegExp('/cases/' + record.id + '$'))
  await expect(page.getByLabel('Etapas de producción')).toBeVisible()
  await page.getByRole('button', { name: 'Producción', exact: true }).click()
  await expect(
    page.getByText('Ojillos cada 50 cm; conservar este texto', { exact: true }),
  ).toBeVisible()
  await page.getByRole('link', { name: 'Ficha de taller' }).click()
  await expect(page.getByText(/NO AUTORIZADA/)).toHaveCount(0)
  await page.screenshot({ path: '../.runtime/comgraf-ficha.png', fullPage: true })
})

test('filtros y vistas conservados al volver desde el expediente', async ({ page }) => {
  const record = await createCase(page, 'E2E Filtros persistentes')
  await page.getByRole('link', { name: 'Mis casos', exact: true }).click()
  await page.getByLabel('Estado', { exact: true }).selectOption('Abierto')
  await page.getByLabel('Modo', { exact: true }).selectOption('Atención')
  await page.getByLabel('Orden', { exact: true }).selectOption('oldest')
  await page.getByRole('button', { name: 'Guardar vista' }).click()
  await page.getByLabel('Nombre de la vista').fill('Mi atención nueva')
  await page.getByRole('dialog').getByRole('button', { name: 'Guardar', exact: true }).click()
  await page.locator(`a[href="/cases/${record.id}"]`).click()
  await page.getByRole('button', { name: 'Volver a la bandeja' }).click()
  await expect(page.getByLabel('Estado', { exact: true })).toHaveValue('Abierto')
  await expect(page.getByLabel('Modo', { exact: true })).toHaveValue('Atención')
  await expect(page.getByLabel('Orden', { exact: true })).toHaveValue('oldest')
  await expect(page.getByRole('button', { name: 'Mi atención nueva', exact: true })).toBeVisible()
  await page.screenshot({ path: '../.runtime/comgraf-consola.png', fullPage: true })
})

test('crear cliente y caso desde formularios y encontrar su historial', async ({ page }) => {
  const name = 'E2E · Librería ' + Date.now()
  await page.getByRole('link', { name: 'Clientes', exact: true }).click()
  await page.getByRole('button', { name: 'Nuevo cliente' }).click()
  await page.getByRole('dialog').getByLabel('Nombre', { exact: true }).fill(name)
  await page.getByRole('dialog').getByLabel('Teléfonos (uno por línea)').fill('+504 9876-5432')
  await page.getByRole('dialog').getByRole('button', { name: 'Guardar', exact: true }).click()
  await page.getByRole('link', { name, exact: true }).click()
  await page.getByRole('button', { name: 'Nuevo caso', exact: true }).click()
  await page
    .getByRole('dialog')
    .getByLabel('Asunto', { exact: true })
    .fill('Consulta de libretas para librería')
  await page.getByRole('dialog').getByRole('button', { name: 'Guardar', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: 'Consulta de libretas para librería' }),
  ).toBeVisible()
  const caseId = new URL(page.url()).pathname.split('/').pop()
  await page.getByLabel('Búsqueda global').fill('50498765432')
  await page.getByLabel('Búsqueda global').press('Enter')
  await expect(page.locator(`a[href="/cases/${caseId}"]`)).toBeVisible()
})

test('autocompletado conserva relaciones y los datos al crear cliente en línea', async ({
  page,
}) => {
  const name = 'E2E Autocomplete ' + Date.now()
  const created = await page.request.post('/api/clients', {
    headers,
    data: {
      name,
      company: 'Impresos Norte',
      phones: ['+504 2222-1010'],
      emails: ['contacto@example.test'],
    },
  })
  expect(created.ok()).toBeTruthy()
  const client = await created.json()
  const contactResponse = await page.request.post(`/api/clients/${client.id}/contacts`, {
    headers,
    data: { name: 'Contacto principal', phone: '9999' },
  })
  expect(contactResponse.ok()).toBeTruthy()
  await page.getByRole('button', { name: 'Nuevo caso', exact: true }).click()
  let d = page.getByRole('dialog'),
    customer = d.getByLabel('Cliente')
  await customer.fill('Autocomplete ' + Date.now())
  await customer.fill(name.slice(0, 18))
  const option = d.getByRole('option').filter({ hasText: name }).first()
  await expect(option).toBeVisible()
  await option.click()
  await expect(d.getByLabel('Cliente')).toHaveValue(name)
  const contact = d.getByLabel('Contacto (opcional)')
  await contact.fill('Contacto pri')
  await expect(d.getByRole('option').filter({ hasText: 'Contacto principal' })).toBeVisible()
  await d.getByRole('option').filter({ hasText: 'Contacto principal' }).click()
  await d.getByLabel('Asunto').fill('Caso con cliente y contacto seleccionados')
  await d.getByRole('button', { name: 'Guardar', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: 'Caso con cliente y contacto seleccionados' }),
  ).toBeVisible()
  let saved = await page.request.get('/api/cases/' + new URL(page.url()).pathname.split('/').pop())
  expect(saved.ok()).toBeTruthy()
  let savedCase = await saved.json()
  expect(savedCase.client.id).toBe(client.id)
  expect(savedCase.contact_id).toBe((await contactResponse.json()).id)

  const alternateResponse = await page.request.post('/api/clients', {
    headers,
    data: { name: 'E2E Cliente alterno ' + Date.now() },
  })
  expect(alternateResponse.ok()).toBeTruthy()
  const alternate = await alternateResponse.json()
  await page.goto('/')
  await page.getByRole('button', { name: 'Nuevo caso', exact: true }).click()
  d = page.getByRole('dialog')
  await d.getByLabel('Cliente').fill(name)
  await expect(d.getByRole('option').filter({ hasText: name })).toBeVisible()
  await d.getByRole('option').filter({ hasText: name }).click()
  await d.getByLabel('Contacto (opcional)').fill('Contacto principal')
  await d.getByRole('option').filter({ hasText: 'Contacto principal' }).click()
  await d.getByLabel('Cliente').fill(alternate.name)
  await expect(d.getByRole('option').filter({ hasText: alternate.name })).toBeVisible()
  await d.getByRole('option').filter({ hasText: alternate.name }).click()
  await expect(d.getByLabel('Contacto (opcional)')).toHaveValue('')
  await d.getByLabel('Asunto').fill('El ID anterior no se conserva')
  await d.getByRole('button', { name: 'Guardar', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'El ID anterior no se conserva' })).toBeVisible()
  const secondCasePath = new URL(page.url()).pathname
  saved = await page.request.get('/api' + secondCasePath)
  expect(
    saved.ok(),
    `GET /api${secondCasePath}: ${saved.status()} ${await saved.text()}`,
  ).toBeTruthy()
  savedCase = await saved.json()
  expect(savedCase.client.id).toBe(alternate.id)
  expect(savedCase.contact_id ?? null).toBeNull()

  await page.goto('/')
  await page.getByRole('button', { name: 'Nuevo caso', exact: true }).click()
  d = page.getByRole('dialog')
  const provisional = 'Provisional en flujo ' + Date.now()
  await d.getByLabel('Asunto').fill('No perder este asunto')
  await d.getByLabel('Descripción').fill('Tampoco perder esta descripción')
  await d.getByLabel('Cliente').fill(provisional)
  await d.getByRole('button', { name: /Crear cliente/ }).click()
  await d.getByLabel('Nombre', { exact: true }).fill(provisional)
  await d.getByRole('button', { name: 'Crear y seleccionar cliente' }).click()
  await expect(d.getByLabel('Asunto')).toHaveValue('No perder este asunto')
  await expect(d.getByLabel('Descripción')).toHaveValue('Tampoco perder esta descripción')
  await d.getByRole('button', { name: 'Guardar', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'No perder este asunto' })).toBeVisible()
})

test('cotización aceptada, producción activa, bloqueo, entrega parcial y cobranza', async ({
  page,
}) => {
  const record = await createCase(page, 'E2E Ciclo de taller y cobranza')
  await page.getByRole('button', { name: 'Cotizaciones', exact: true }).click()
  await page.getByRole('button', { name: 'Nueva versión' }).click()
  let dialog = page.getByRole('dialog')
  await dialog.getByLabel('Cantidad').fill('100')
  await dialog.getByLabel('Precio unitario (HNL)').fill('10')
  await dialog.getByLabel('Ancho').fill('9')
  await dialog.getByLabel('Alto').fill('5')
  await dialog.getByLabel('Material / papel').fill('Couché 300 g')
  await dialog.getByLabel('Caras').selectOption('2')
  await dialog.getByLabel('Tintas').fill('CMYK')
  await dialog.getByLabel('Vigente hasta').fill('2099-01-01')
  await dialog.getByRole('button', { name: 'Guardar cotización' }).click()
  await expect(page.getByRole('heading', { name: /Versión 1/ })).toBeVisible()
  await page.getByRole('button', { name: 'Registrar decisión' }).click()
  dialog = page.getByRole('dialog')
  await dialog
    .getByLabel('Evidencia / comunicación del cliente')
    .fill('Aprobada presencialmente; evidencia verificada.')
  await dialog.getByRole('button', { name: 'Guardar' }).click()
  await expect(page.getByRole('button', { name: 'Generar orden con esta versión' })).toBeVisible()
  await page.getByRole('button', { name: 'Generar orden con esta versión' }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByLabel('Fecha comprometida').fill('2099-01-01')
  await dialog.getByLabel('Vencimiento del saldo').fill('2026-01-01')
  await expect(dialog.getByLabel('Condición de pago').locator('option')).toHaveText([
    'Contado',
    'Crédito',
  ])
  await dialog.getByLabel('Condición de pago').selectOption('Crédito')
  await expect(dialog.getByLabel('Moneda')).toHaveCount(0)
  await dialog.getByLabel('Anticipo requerido (HNL)').fill('100')
  await dialog.getByLabel('Modalidad de entrega').fill('Retiro en taller')
  await dialog.getByRole('button', { name: /Generar orden de producción/ }).click()
  await expect(page).toHaveURL(new RegExp('/cases/' + record.id + '$'))
  await page.reload()
  await page.getByRole('button', { name: 'Actividad', exact: true }).click()
  const orderLink = page.getByRole('link', { name: /Orden OP-/ })
  await expect(orderLink).toBeVisible()
  await orderLink.click()
  await expect(page.getByRole('heading', { name: 'Especificaciones de producción' })).toBeVisible()
  await page.getByRole('button', { name: 'Actividad', exact: true }).click()
  const quoteLink = page.getByRole('link', { name: /COT-.*versi/ })
  await expect(quoteLink).toBeVisible()
  await quoteLink.click()
  const converted = await (await page.request.get(`/api/cases/${record.id}`)).json()
  await expect(page.locator(`#quote-${converted.quotes[0].id}`)).toBeVisible()
  await page.getByRole('button', { name: 'Entregas y cobros' }).click()
  await page.getByRole('button', { name: 'Registrar abono' }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByLabel('Tipo', { exact: true }).selectOption('Anticipo')
  await dialog.getByLabel('Importe (HNL)').fill('100')
  await dialog.getByLabel('Referencia / comprobante').fill('Recibo E2E 0001')
  await dialog.getByRole('button', { name: 'Guardar' }).click()
  await page.getByRole('button', { name: 'Programar', exact: true }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByLabel(/Inicio programado/).fill('2026-10-16T10:00')
  await dialog.getByLabel('Fecha comprometida de entrega').fill('2099-01-01')
  await dialog.getByRole('button', { name: 'Guardar' }).click()
  await page.getByRole('button', { name: 'Archivos' }).click()
  await page.getByRole('button', { name: 'Adjuntar archivo' }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByLabel('Archivo').setInputFiles({
    name: 'arte-e2e.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF-1.4\n%%EOF'),
  })
  await dialog.getByLabel('Versión del arte').fill('v1')
  await dialog.getByRole('button', { name: 'Guardar' }).click()
  await page.getByRole('button', { name: 'Producción' }).click()
  for (const stage of ['Aprobación', 'Impresión', 'Taller', 'Listo para entregar']) {
    await page.getByRole('button', { name: `Avanzar a ${stage}` }).click()
    dialog = page.getByRole('dialog')
    await dialog.getByRole('button', { name: `Avanzar a ${stage}` }).click()
  }
  await expect(page.getByRole('button', { name: 'Registrar entrega', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Bloquear' }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByLabel('Motivo').fill('Inspección preventiva del pedido.')
  await dialog.getByRole('button', { name: 'Guardar' }).click()
  await page.getByRole('button', { name: 'Resolver bloqueo' }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByLabel('Motivo').fill('Inspección completada; material conforme.')
  await dialog.getByRole('button', { name: 'Guardar' }).click()
  await page.getByRole('button', { name: 'Entregas y cobros' }).click()
  await page.getByRole('button', { name: 'Registrar entrega' }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByLabel('Partida').selectOption({ index: 1 })
  await dialog.getByLabel('Cantidad entregada').fill('40')
  await dialog.getByLabel(/Fecha y hora/).fill('2026-10-16T10:00')
  await dialog.getByLabel('Recibido por').fill('DEMO · María')
  await dialog.getByLabel('Modalidad').fill('Retiro')
  await dialog
    .getByLabel('Evidencia / referencia del recibo')
    .fill('Comprobante E2E entrega parcial 40.')
  await dialog.getByLabel('Registrar pago con esta entrega').check()
  await expect(dialog.getByLabel(/Abono o liquidación \(HNL\)/)).toHaveValue('900.00')
  await dialog.getByLabel(/Abono o liquidación \(HNL\)/).fill('100')
  await dialog.getByLabel('Referencia del pago').fill('Recibo combinado E2E')
  await dialog.getByRole('button', { name: 'Guardar' }).click()
  await expect(page.getByText('Comprobante E2E entrega parcial 40.')).toBeVisible()
  await page.getByRole('button', { name: 'Cerrar atención' }).click()
  await expect(page.getByRole('button', { name: 'Abrir atención' })).toBeVisible()
  await expect(page.getByText('Parcial', { exact: true })).toBeVisible()
  const full = await page.request.get(`/api/cases/${record.id}`)
  expect(full.ok()).toBeTruthy()
  const current = await full.json()
  expect(current.mode).toBe('Producción')
  expect(current.production.stage).toBe('READY')
  expect(current.delivery_status).toBe('Parcial')
  expect(current.balance).toBe('800.00')
  expect(current.status).toBe('Cerrado')
  expect(current.ledger.filter((entry) => entry.kind === 'Abono')).toHaveLength(1)
  expect(current.production.authorized_at).toBeNull()
  expect(current.production.specs_snapshot.items[0].specs.material).toBe('Couché 300 g')

  await page.getByRole('button', { name: 'Registrar entrega' }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByLabel('Partida').selectOption({ index: 1 })
  await dialog.getByLabel('Cantidad entregada').fill('60')
  await dialog.getByLabel(/Fecha y hora/).fill('2026-10-17T10:00')
  await dialog.getByLabel('Recibido por').fill('DEMO · María')
  await dialog.getByLabel('Modalidad').fill('Retiro')
  await dialog
    .getByLabel('Evidencia / referencia del recibo')
    .fill('Comprobante E2E entrega final y pago.')
  await dialog.getByLabel('Registrar pago con esta entrega').check()
  await expect(dialog.getByLabel(/Abono o liquidación \(HNL\)/)).toHaveValue('800.00')
  await dialog.getByLabel('Referencia del pago').fill('Liquidación E2E')
  await dialog.getByRole('button', { name: 'Guardar' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  const settled = await page.request.get(`/api/cases/${record.id}`)
  expect(settled.ok()).toBeTruthy()
  const finalRecord = await settled.json()
  expect(finalRecord.delivery_status).toBe('Completa')
  expect(finalRecord.production.stage).toBe('READY')
  expect(finalRecord.production.active_elapsed_seconds).toBeNull()
  expect(finalRecord.balance).toBe('0.00')
  expect(finalRecord.payment_status).toBe('Pagado')
  expect(finalRecord.ledger.filter((entry) => entry.kind === 'Abono')).toHaveLength(2)
})

test('note template appends to an existing internal draft', async ({ page }) => {
  await createCase(page, 'E2E note template')
  const editor = page.getByLabel('Nota interna', { exact: true })
  await editor.fill('Existing internal note.')
  await page.getByRole('button', { name: 'Usar plantilla' }).click()
  await page.getByLabel('Plantilla').selectOption('Seguimiento de recogida')
  await page.getByLabel('Medio de contacto').fill('WhatsApp manual')
  await page.getByLabel('Respuesta').fill('Customer will pick up Friday')
  await page.getByRole('button', { name: /la nota/ }).click()
  await expect(editor).toHaveValue(
    /Existing internal note\.[\s\S]*Medio de contacto: WhatsApp manual[\s\S]*Customer will pick up Friday/,
  )
})
