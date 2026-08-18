import { describe, expect, it } from 'vitest'
import {
  processingRefreshKey,
  processingElapsedLabel,
  processingStageDetail,
  processingStageLabel,
  shouldRefreshProjectCatalog,
} from './processing-status'

describe('processingStageLabel', () => {
  it.each([
    ['queued', 'Queued for processing'],
    ['dg-agent', 'Analyzing document'],
    ['ga-agent', 'Organizing property'],
    ['graph-rag', 'Generating graph nodes and edges'],
    ['graph-property-read', 'Reading property content'],
    ['graph-property-embedding', 'Updating property vectors'],
    ['graph-entity-extraction', 'Extracting entities and relations'],
    ['graph-entity-embedding', 'Updating entity vectors'],
    ['graph-property-relations', 'Generating property relations'],
    ['graph-snapshot', 'Writing candidate graph'],
    ['graph-activate', 'Activating candidate snapshot'],
  ])('describes %s as %s', (stage, label) => {
    expect(processingStageLabel(stage)).toBe(label)
  })
})

describe('processingStageDetail', () => {
  it('turns dynamic backend progress into a localizable message', () => {
    expect(processingStageDetail(
      'graph-entity-extraction',
      'Generating graph nodes and edges 2/5: 产品手册.md',
    )).toEqual({
      key: 'Generating graph nodes and edges {{current}}/{{total}}: {{filename}}',
      values: { current: 2, total: 5, filename: '产品手册.md' },
    })
  })

  it('keeps unknown provider errors unchanged', () => {
    expect(processingStageDetail('failed', 'provider request timed out')).toEqual({
      key: 'provider request timed out',
      values: undefined,
    })
  })
})

describe('processingElapsedLabel', () => {
  it('formats a live stage duration without showing sub-second noise', () => {
    expect(
      processingElapsedLabel('2026-08-16T00:00:00.000Z', Date.parse('2026-08-16T00:01:05.000Z')),
    ).toBe('1m 05s')
    expect(processingElapsedLabel(undefined)).toBe('')
  })
})

describe('processing refresh state', () => {
  it('changes when a completed job replaces the previous active state', () => {
    const previous = {
      locked: false,
      status: 'completed',
      stage: 'active',
      job_id: 'job-1',
      active_snapshot: 'snapshot-1',
    }
    const completedUpload = {
      locked: false,
      status: 'completed',
      stage: 'active',
      job_id: 'job-2',
      active_snapshot: 'snapshot-2',
    }

    expect(processingRefreshKey(completedUpload)).not.toBe(
      processingRefreshKey(previous),
    )
  })

  it('refreshes the project catalog only after processing unlocks', () => {
    expect(
      shouldRefreshProjectCatalog({ locked: true, status: 'running' }),
    ).toBe(false)
    expect(
      shouldRefreshProjectCatalog({ locked: false, status: 'completed' }),
    ).toBe(true)
  })
})
