import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import RegroupPropertiesDialog from './RegroupPropertiesDialog'

afterEach(cleanup)

describe('RegroupPropertiesDialog', () => {
  it('explains filename changes and shows Group Arrangement Agent progress', () => {
    render(<RegroupPropertiesDialog open projectName="Test project" busy onClose={() => undefined} onSubmit={() => undefined} />)

    expect(screen.getByRole('dialog', { name: 'Rearrange Test project' })).toBeTruthy()
    expect(screen.getByText('REVISE PROJECT GROUPING')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Rearrange Test project' })).toBeTruthy()
    expect(screen.getByText(/Group Arrangement Agent update the property grouping tree and property names/)).toBeTruthy()
    const button = screen.getByRole('button', { name: 'Generating proposal' })
    expect((button as HTMLButtonElement).disabled).toBe(true)
    expect(button.querySelector('.spinner')).toBeTruthy()
  })
})
