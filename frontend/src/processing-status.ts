const STAGE_LABELS: Record<string, string> = {
  queued: 'Queued for processing',
  'dg-agent': 'Analyzing document',
  'ga-agent': 'Organizing property',
  'graph-rag': 'Generating graph nodes and edges',
  'graph-property-read': 'Reading property content',
  'graph-property-embedding': 'Updating property vectors',
  'graph-entity-extraction': 'Extracting entities and relations',
  'graph-entity-generation': 'Generating entity nodes',
  'graph-entity-property-relations': 'Generating entity nodes and property relations',
  'graph-entity-merging': 'Merging redundant entities',
  'graph-entity-relations': 'Generating entity relations',
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

export type ProcessingStageDetail = {
  key: string
  values?: Record<string, string | number>
}

const EXACT_STAGE_DETAILS: Record<string, string> = {
  'Choosing a property group': 'Choosing a property group',
  'Activating candidate snapshot': 'Activating candidate snapshot',
  'Candidate snapshot active': 'Candidate snapshot active',
  'Preparing property pipeline': 'Preparing property pipeline',
  'Removing property graph nodes and relations': 'Removing property graph nodes and relations',
  'Applying redundant entity merges': 'Applying redundant entity merges',
  'Activating pruned graph snapshot': 'Activating pruned graph snapshot',
  'Property removed from both graphs': 'Property removed from both graphs',
}

export function processingStageDetail(
  _stage: string | null | undefined,
  detail: string,
): ProcessingStageDetail {
  const exact = EXACT_STAGE_DETAILS[detail]
  if (exact) return { key: exact, values: undefined }

  const rules: Array<{
    pattern: RegExp
    key: string
    values: (match: RegExpMatchArray) => Record<string, string | number>
  }> = [
    {
      pattern: /^Generating entity nodes: (\d+)%$/,
      key: 'Generating entity nodes: {{percent}}%',
      values: (match) => ({ percent: Number(match[1]) }),
    },
    {
      pattern: /^Generating entity nodes and property relations: (\d+)%$/,
      key: 'Generating entity nodes and property relations: {{percent}}%',
      values: (match) => ({ percent: Number(match[1]) }),
    },
    {
      pattern: /^Generating entity nodes and property relations: entities (\d+)\/(\d+) · property relations (\d+)%$/,
      key: 'Generating entity nodes and property relations: {{percent}}%',
      values: (match) => ({
        percent: Math.round(
          ((Number(match[1]) / Math.max(Number(match[2]), 1)) * 100
            + Number(match[3])) / 2,
        ),
      }),
    },
    {
      pattern: /^Generating redundant entity merge proposals: (\d+)%$/,
      key: 'Generating redundant entity merge proposals: {{percent}}%',
      values: (match) => ({ percent: Number(match[1]) }),
    },
    {
      pattern: /^Generating relations for entities: (\d+)%$/,
      key: 'Generating relations for entities: {{percent}}%',
      values: (match) => ({ percent: Number(match[1]) }),
    },
    {
      pattern: /^Generating redundant entity merge proposals\. (\d+)%$/,
      key: 'Generating redundant entity merge proposals: {{percent}}%',
      values: (match) => ({ percent: Number(match[1]) }),
    },
    {
      pattern: /^Generating relations for entities\. (\d+)%$/,
      key: 'Generating relations for entities: {{percent}}%',
      values: (match) => ({ percent: Number(match[1]) }),
    },
    {
      pattern: /^Generating property relations[:.] (\d+)%$/,
      key: 'Generating property relations: {{percent}}%',
      values: (match) => ({ percent: Number(match[1]) }),
    },
    {
      pattern: /^Removing (\d+) properties from both graphs$/,
      key: 'Removing {{count}} properties from both graphs',
      values: (match) => ({ count: Number(match[1]) }),
    },
    {
      pattern: /^(\d+) properties removed from both graphs$/,
      key: '{{count}} properties removed from both graphs',
      values: (match) => ({ count: Number(match[1]) }),
    },
    {
      pattern: /^Generating entity nodes (\d+)\/(\d+): resuming completed properties$/,
      key: 'Generating entity nodes {{current}}/{{total}}: resuming completed properties',
      values: (match) => ({ current: Number(match[1]), total: Number(match[2]) }),
    },
    {
      pattern: /^Generating entity nodes (\d+)\/(\d+)$/,
      key: 'Generating entity nodes {{current}}/{{total}}',
      values: (match) => ({ current: Number(match[1]), total: Number(match[2]) }),
    },
    {
      pattern: /^Generating entity nodes (\d+)\/(\d+): (.+)$/,
      key: 'Generating entity nodes {{current}}/{{total}}: {{filename}}',
      values: (match) => ({ current: Number(match[1]), total: Number(match[2]), filename: match[3] }),
    },
    {
      pattern: /^Generating entity nodes for (\d+) properties in parallel$/,
      key: 'Generating entity nodes {{current}}/{{total}}',
      values: (match) => ({ current: 0, total: Number(match[1]) }),
    },
    {
      pattern: /^Generating entity nodes for (\d+) properties in parallel; property relations are running$/,
      key: 'Generating entity nodes {{current}}/{{total}}',
      values: (match) => ({ current: 0, total: Number(match[1]) }),
    },
    {
      pattern: /^Generating complete relations for (\d+) entities$/,
      key: 'Generating complete relations for {{count}} entities',
      values: (match) => ({ count: Number(match[1]) }),
    },
    {
      pattern: /^Generating graph nodes and edges (\d+)\/(\d+): (.+)$/,
      key: 'Generating graph nodes and edges {{current}}/{{total}}: {{filename}}',
      values: (match) => ({ current: Number(match[1]), total: Number(match[2]), filename: match[3] }),
    },
    {
      pattern: /^Analyzing new property against (\d+) existing entities$/,
      key: 'Analyzing new property against {{count}} existing entities',
      values: (match) => ({ count: Number(match[1]) }),
    },
    {
      pattern: /^Preparing (\d+) new properties for entity extraction$/,
      key: 'Preparing {{count}} new properties for entity extraction',
      values: (match) => ({ count: Number(match[1]) }),
    },
    {
      pattern: /^Updating (\d+) changed property vectors$/,
      key: 'Updating {{count}} changed property vectors',
      values: (match) => ({ count: Number(match[1]) }),
    },
    {
      pattern: /^Writing (\d+) properties and (\d+) entities$/,
      key: 'Writing {{propertyCount}} properties and {{entityCount}} entities',
      values: (match) => ({ propertyCount: Number(match[1]), entityCount: Number(match[2]) }),
    },
    {
      pattern: /^Preparing (\d+) imported properties$/,
      key: 'Preparing {{count}} imported properties',
      values: (match) => ({ count: Number(match[1]) }),
    },
    {
      pattern: /^Preparing (\d+) properties$/,
      key: 'Preparing {{count}} properties',
      values: (match) => ({ count: Number(match[1]) }),
    },
    {
      pattern: /^Analyzing (\d+) text documents$/,
      key: 'Analyzing {{count}} text documents',
      values: (match) => ({ count: Number(match[1]) }),
    },
    {
      pattern: /^Updating (\d+) entity vectors$/,
      key: 'Updating {{count}} entity vectors',
      values: (match) => ({ count: Number(match[1]) }),
    },
    {
      pattern: /^Relating (\d+) property nodes$/,
      key: 'Relating {{count}} property nodes',
      values: (match) => ({ count: Number(match[1]) }),
    },
    {
      pattern: /^Analyzing (.+)$/,
      key: 'Analyzing {{filename}}',
      values: (match) => ({ filename: match[1] }),
    },
  ]

  for (const rule of rules) {
    const match = detail.match(rule.pattern)
    if (match) return { key: rule.key, values: rule.values(match) }
  }
  return { key: detail, values: undefined }
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
