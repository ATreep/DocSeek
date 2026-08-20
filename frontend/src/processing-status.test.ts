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
    ['graph-entity-generation', 'Generating entity nodes'],
    ['graph-entity-property-relations', 'Generating entity nodes and property relations'],
    ['graph-entity-merging', 'Merging redundant entities'],
    ['graph-entity-relations', 'Generating entity relations'],
    ['graph-entity-embedding', 'Updating entity vectors'],
    ['graph-property-relations', 'Generating property relations'],
    ['graph-snapshot', 'Writing candidate graph'],
    ['graph-activate', 'Activating candidate snapshot'],
  ])('describes %s as %s', (stage, label) => {
    expect(processingStageLabel(stage)).toBe(label)
  })
})

describe('processingStageDetail', () => {
  it('maps batched merge and relation progress details', () => {
    expect(processingStageDetail(
      'graph-entity-property-relations',
      'Generating entity nodes and property relations: 42%',
    )).toEqual({
      key: 'Generating entity nodes and property relations: {{percent}}%',
      values: { percent: 42 },
    })
    expect(processingStageDetail(
      'graph-entity-property-relations',
      'Generating entity nodes and property relations: entities 7/17 · property relations 42%',
    )).toEqual({
      key: 'Generating entity nodes and property relations: {{percent}}%',
      values: { percent: 42 },
    })
    expect(processingStageDetail(
      'graph-prune',
      'Removing 12 properties from both graphs',
    )).toEqual({
      key: 'Removing {{count}} properties from both graphs',
      values: { count: 12 },
    })
    expect(processingStageDetail(
      'graph-entity-merging',
      'Generating redundant entity merge proposals: 42%',
    )).toEqual({
      key: 'Generating redundant entity merge proposals: {{percent}}%',
      values: { percent: 42 },
    })
    expect(processingStageDetail(
      'graph-entity-relations',
      'Generating relations for entities: 62%',
    )).toEqual({
      key: 'Generating relations for entities: {{percent}}%',
      values: { percent: 62 },
    })
    expect(processingStageDetail(
      'graph-property-relations',
      'Generating property relations: 100%',
    )).toEqual({
      key: 'Generating property relations: {{percent}}%',
      values: { percent: 100 },
    })
  })

  it('turns dynamic backend progress into a localizable message', () => {
    expect(processingStageDetail(
      'graph-entity-generation',
      'Generating entity nodes: 40%',
    )).toEqual({
      key: 'Generating entity nodes: {{percent}}%',
      values: { percent: 40 },
    })
    expect(processingStageDetail(
      'graph-entity-generation',
      'Generating entity nodes 2/5: resuming completed properties',
    )).toEqual({
      key: 'Generating entity nodes {{current}}/{{total}}: resuming completed properties',
      values: { current: 2, total: 5 },
    })
    expect(processingStageDetail(
      'graph-entity-generation',
      'Generating entity nodes 2/5: 产品手册.md',
    )).toEqual({
      key: 'Generating entity nodes {{current}}/{{total}}: {{filename}}',
      values: { current: 2, total: 5, filename: '产品手册.md' },
    })
    expect(processingStageDetail(
      'graph-entity-generation',
      'Generating entity nodes 0/5',
    )).toEqual({
      key: 'Generating entity nodes {{current}}/{{total}}',
      values: { current: 0, total: 5 },
    })
    expect(processingStageDetail(
      'graph-entity-generation',
      'Generating entity nodes for 5 properties in parallel',
    )).toEqual({
      key: 'Generating entity nodes {{current}}/{{total}}',
      values: { current: 0, total: 5 },
    })
    expect(processingStageDetail(
      'graph-entity-relations',
      'Generating complete relations for 12 entities',
    )).toEqual({
      key: 'Generating complete relations for {{count}} entities',
      values: { count: 12 },
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
