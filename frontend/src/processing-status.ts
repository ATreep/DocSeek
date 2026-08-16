const STAGE_LABELS: Record<string, string> = {
  queued: 'Queued for processing',
  'dg-agent': 'Analyzing document',
  'ga-agent': 'Organizing property',
  'graph-rag': 'Generating graph nodes and edges',
  'graph-property-read': 'Reading property content',
  'graph-property-embedding': 'Updating property vectors',
  'graph-entity-extraction': 'Extracting entities and relations',
  'graph-entity-embedding': 'Updating entity vectors',
  'graph-property-relations': 'Generating property relations',
  'graph-snapshot': 'Writing candidate graph',
  'graph-activate': 'Activating candidate snapshot',
  active: 'Activating candidate snapshot',
  cancelled: 'Processing cancelled',
  failed: 'Candidate build failed',
  recovery: 'Recovering interrupted build',
}

export type ProcessingRefreshStatus = {
  locked: boolean
  status: string
  stage?: string | null
  job_id?: string | null
  candidate_snapshot?: string | null
  active_snapshot?: string | null
}

export function processingStageLabel(stage?: string | null): string {
  return (stage && STAGE_LABELS[stage]) || 'Preparing candidate build'
}

export function processingElapsedLabel(
  stageStartedAt?: string | null,
  now = Date.now(),
): string {
  if (!stageStartedAt) return ''
  const started = Date.parse(stageStartedAt)
  if (!Number.isFinite(started)) return ''
  const seconds = Math.max(0, Math.floor((now - started) / 1000))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ${String(seconds % 60).padStart(2, '0')}s`
}

export function processingRefreshKey(status: ProcessingRefreshStatus): string {
  return [
    status.job_id || 'none',
    status.status || 'idle',
    status.stage || 'none',
    status.locked ? 'locked' : 'unlocked',
    status.candidate_snapshot || 'none',
    status.active_snapshot || 'none',
  ].join(':')
}

export function shouldRefreshProjectCatalog(
  status: ProcessingRefreshStatus,
): boolean {
  return !status.locked
}
