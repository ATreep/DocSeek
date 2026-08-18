import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { changeDisplayLanguage, i18n } from './i18n'
import LanguageSwitcher from './components/LanguageSwitcher'

beforeEach(() => {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
    clear: () => values.clear(),
  })
})

afterEach(async () => {
  cleanup()
  localStorage.clear()
  await changeDisplayLanguage('en', { persistProfile: false })
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

describe('WebUI display language', () => {
  it('uses the canonical Chinese nouns for property and entity', async () => {
    await changeDisplayLanguage('zh', { persistProfile: false })

    expect(i18n.t('property')).toBe('资产')
    expect(i18n.t('entity')).toBe('实体')
    expect(i18n.t('Revise Project Tree')).toBe('修改项目树')
    expect(i18n.t('REVISE PROJECT GROUPING')).toBe('调整项目分组')
    expect(i18n.t('Rearrange {{projectName}}', { projectName: '示例项目' })).toBe('重新整理 示例项目')
    expect(i18n.t(
      'Generating graph nodes and edges {{current}}/{{total}}: {{filename}}',
      { current: 2, total: 5, filename: '产品手册.md' },
    )).toBe('正在生成图谱节点和边 2/5：产品手册.md')
  })

  it('switches language from the title-bar menu', async () => {
    const user = userEvent.setup()
    render(<LanguageSwitcher />)

    await user.click(screen.getByRole('button', { name: 'Language' }))
    await user.click(screen.getByRole('menuitem', { name: '中文' }))

    expect(i18n.language).toBe('zh')
    expect(localStorage.getItem('docseek-language')).toBe('zh')
    expect(screen.getByRole('button', { name: '语言' })).toBeTruthy()
  })
})
