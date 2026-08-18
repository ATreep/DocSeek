import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'

const styles = readFileSync('src/styles.css', 'utf8')

describe('responsive header layout', () => {
  it('keeps global search visible and gives it a full narrow-width row', () => {
    const normalized = styles.replace(/\s+/g, ' ')

    expect(normalized).not.toContain('.global-search { display: none; }')
    expect(normalized).toContain(
      '.global-search { grid-column: 1 / -1; grid-row: 2;',
    )
    expect(normalized).toContain(
      '.top-actions { grid-column: 3; grid-row: 1;',
    )
  })
})
