import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import App from './App'

const apiMocks = vi.hoisted(() => ({
  request: vi.fn(),
}))

vi.mock('./api', () => ({
  request: apiMocks.request,
}))

vi.mock('./components/Login', () => ({
  default: () => <div>Sign in page</div>,
}))

vi.mock('./components/ProjectShell', () => ({
  default: () => <div>Project workspace</div>,
}))

beforeEach(() => {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
    clear: () => values.clear(),
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

describe('App session bootstrap', () => {
  it('clears an expired stored session before mounting the project workspace', async () => {
    localStorage.setItem('docseek-token', 'expired-token')
    apiMocks.request.mockRejectedValueOnce(new Error('Invalid or expired session'))

    render(<App />)

    expect(await screen.findByText('Sign in page')).toBeTruthy()
    expect(screen.queryByText('Project workspace')).toBeNull()
    expect(localStorage.getItem('docseek-token')).toBeNull()
  })

  it('mounts the project workspace after validating a stored session', async () => {
    localStorage.setItem('docseek-token', 'valid-token')
    apiMocks.request.mockResolvedValueOnce({ id: 'admin', username: 'admin' })

    render(<App />)

    expect(await screen.findByText('Project workspace')).toBeTruthy()
    expect(apiMocks.request).toHaveBeenCalledWith('/auth/me')
  })
})
