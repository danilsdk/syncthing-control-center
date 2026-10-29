import { MockProvider } from './mock'

describe('MockProvider keeps the demo flow explicit', () => {
  it('creates a rule and changes its version when toggled', async () => {
    const provider = new MockProvider()
    const folders = await provider.listFolders()
    const created = await provider.createRule({ folder_id: folders[0].id, timezone: 'UTC', days_of_week: [6], start_time: '10:00', end_time: '12:00', enabled: true })
    expect(created.version).toBe(1)
    const disabled = await provider.setRuleEnabled(created, false)
    expect(disabled.enabled).toBe(false)
    expect(disabled.version).toBe(2)
  })

  it('adds a pending manual resume to history', async () => {
    const provider = new MockProvider()
    const folder = (await provider.listFolders())[0]
    const before = await provider.listExecutions()
    const execution = await provider.resume(folder.id)
    const after = await provider.listExecutions()
    expect(after).toHaveLength(before.length + 1)
    expect(execution.status).toBe('pending')
    expect(execution.source).toBe('manual')
  })
})
