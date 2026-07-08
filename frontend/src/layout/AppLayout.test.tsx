import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, test, vi } from 'vitest'

import AppLayout from './AppLayout'
import {
  getCredentialsStatus,
  hasAdminToken,
  setAdminToken,
} from '../api/client'

vi.mock('../api/client', () => ({
  clearCredentials: vi.fn(),
  getCredentialsStatus: vi.fn(),
  hasAdminToken: vi.fn(),
  setAdminToken: vi.fn(),
  setCredentials: vi.fn(),
}))

const mockedHasAdminToken = vi.mocked(hasAdminToken)
const mockedGetCredentialsStatus = vi.mocked(getCredentialsStatus)
const mockedSetAdminToken = vi.mocked(setAdminToken)

function renderLayout(
  mode: 'offline' | 'qwen' | 'cloud' = 'offline',
  onModeChange = vi.fn(),
) {
  render(
    <MemoryRouter
      initialEntries={['/']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route
          element={(
            <AppLayout
              mode={mode}
              onModeChange={onModeChange}
              darkMode={false}
              onDarkModeChange={vi.fn()}
            />
          )}
        >
          <Route index element={<div>Page content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe('AppLayout access and execution provenance', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedHasAdminToken.mockReturnValue(false)
    mockedGetCredentialsStatus.mockResolvedValue({
      live_enabled: false,
      cloud_mutations_enabled: false,
      qwen_key_configured: false,
      has_credentials: false,
      region: '',
    })
  })

  test('administrator can connect without entering cloud credentials', async () => {
    const user = userEvent.setup()
    renderLayout()

    await user.click(screen.getByRole('button', { name: /connect to Sage API/i }))
    await user.type(screen.getByLabelText(/Sage Administration Token/i), 'admin-secret')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(mockedSetAdminToken).toHaveBeenCalledWith('admin-secret')
    expect(mockedGetCredentialsStatus).toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  test('real cloud mode is visibly distinguished from simulation', () => {
    renderLayout('cloud')
    expect(screen.getByText('LIVE CLOUD')).toBeInTheDocument()
  })

  test('disabled server safety switches reset a persisted cloud mode', async () => {
    const onModeChange = vi.fn()
    mockedHasAdminToken.mockReturnValue(true)
    renderLayout('cloud', onModeChange)

    await waitFor(() => expect(onModeChange).toHaveBeenCalledWith('offline'))
  })
})
