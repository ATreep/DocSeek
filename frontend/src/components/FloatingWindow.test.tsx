import { describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import FloatingWindow from './FloatingWindow'

function Harness() {
  const [open, setOpen] = useState(true)
  return <><button type="button" onClick={() => setOpen(false)}>Dismiss</button><FloatingWindow open={open}>Panel content</FloatingWindow></>
}

describe('FloatingWindow', () => {
  it('keeps content mounted during the exit animation before dismissing it', async () => {
    render(<Harness />)
    expect(document.querySelector('.floating-window--enter')).toBeTruthy()
    screen.getByRole('button', { name: 'Dismiss' }).click()
    expect(screen.getByText('Panel content')).toBeTruthy()
    await waitFor(() => expect(document.querySelector('.floating-window--exit')).toBeTruthy())
    await waitFor(() => expect(screen.queryByText('Panel content')).toBeNull(), { timeout: 500 })
  })
})
