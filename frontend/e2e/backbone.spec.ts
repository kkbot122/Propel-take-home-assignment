import { expect, test } from '@playwright/test'

test('operator completes the surveyed-span backbone workflow', async ({ page, request }) => {
  const pageErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error.message))

  const resetResponse = await request.post('/api/simulator/reset')
  expect(resetResponse.ok()).toBeTruthy()

  await page.goto('/')
  await expect(page.getByText('System online', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'No active outages' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'OpenStreetMap' })).toBeVisible()
  await expect(page.getByText('FDR-001 source', { exact: true })).toBeVisible()
  await expect(page.getByText('DT-001', { exact: true })).toBeVisible()
  await expect(page.getByText('DT-002', { exact: true })).toBeVisible()

  const faultStartedAt = Date.now()
  await page.getByRole('button', { name: 'Inject span fault A' }).click()
  await expect(
    page.getByText('SPAN FAULT injected. Waiting for telemetry correlation and localization.'),
  ).toBeVisible()

  const detail = page.getByRole('region', { name: 'P-001 → P-002' })
  await expect(detail).toBeVisible({ timeout: 45_000 })
  await expect(page.getByText('SPAN FAULT focus', { exact: true })).toBeVisible()
  const faultToVisibleMs = Date.now() - faultStartedAt

  const activeResponse = await request.get('/api/incidents?status=ACTIVE&limit=100')
  expect(activeResponse.ok()).toBeTruthy()
  const activeIncidents = (await activeResponse.json()) as Array<{
    incident_id: string
    ticket_id: string
    suspected_asset_id: string
    affected_pole_count: number
    affected_pole_ids: string[]
  }>
  expect(activeIncidents).toHaveLength(1)
  expect(activeIncidents[0]).toMatchObject({
    suspected_asset_id: 'P-001->P-002',
    affected_pole_count: 3,
    affected_pole_ids: ['P-002', 'P-003', 'P-004'],
  })
  const ticketId = activeIncidents[0].ticket_id

  const replayResponse = await request.post('/api/telemetry', {
    data: {
      device_id: 'DEV-P-002',
      pole_id: 'P-002',
      event: 'power_lost',
      energized: false,
      ts: new Date().toISOString(),
      seq: 1,
      battery_mv: 3480,
      rssi: -91,
      fw: '1.4.2',
    },
  })
  expect(replayResponse.status()).toBe(202)

  await detail.getByRole('button', { name: 'Acknowledge incident' }).click()
  await expect(detail.locator('.detail-header .status-pill')).toHaveText('ACKNOWLEDGED')

  await detail.getByLabel('Crew identifier').fill('Crew-VS09')
  await detail.getByRole('button', { name: 'Assign crew' }).click()
  await expect(detail.locator('.detail-header .status-pill')).toHaveText('CREW ASSIGNED')

  await detail.getByRole('button', { name: 'Claim physical repair' }).click()
  await expect(detail.locator('.detail-header .status-pill')).toHaveText('RESOLVED')
  await expect(detail.getByText('Repair not verified', { exact: true })).toBeVisible()
  await expect(detail.getByText('3 eligible poles remain dark.', { exact: false })).toBeVisible()
  await expect(detail.getByRole('button', { name: 'Verify restoration' })).toHaveCount(0)
  await expect(detail.getByRole('button', { name: 'Close ticket' })).toHaveCount(0)

  const restorationStartedAt = Date.now()
  await page.getByRole('button', { name: 'Send selected repair telemetry' }).click()
  await expect(detail.locator('.detail-header .status-pill')).toHaveText('CLOSED', {
    timeout: 45_000,
  })
  const restorationToClosedMs = Date.now() - restorationStartedAt

  await expect(detail.getByText('Restoration verified', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'No active outages' })).toBeVisible()
  const timelineStates = await detail.locator('.ticket-timeline strong').allTextContents()
  expect(timelineStates).toEqual([
    'DETECTED',
    'ACKNOWLEDGED',
    'CREW ASSIGNED',
    'RESOLVED',
    'VERIFIED',
    'CLOSED',
  ])

  const ticketResponse = await request.get(`/api/tickets/${ticketId}`)
  expect(ticketResponse.ok()).toBeTruthy()
  const ticket = (await ticketResponse.json()) as {
    status: string
    restoration_status: string
    events: Array<{ to_status: string }>
  }
  expect(ticket.status).toBe('CLOSED')
  expect(ticket.restoration_status).toBe('RESTORATION_VERIFIED')
  expect(ticket.events.map((event) => event.to_status.replaceAll('_', ' '))).toEqual(
    timelineStates,
  )
  const finalActiveResponse = await request.get('/api/incidents?status=ACTIVE&limit=100')
  expect(await finalActiveResponse.json()).toEqual([])
  expect(pageErrors).toEqual([])

  console.log(
    `VS09_METRIC fault_to_visible_ms=${faultToVisibleMs} restoration_to_closed_ms=${restorationToClosedMs}`,
  )
})
