import { describe, expect, it } from 'vitest'
import {
  processingRefreshKey,
  processingElapsedLabel,
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
