type RequestLike = (path: string, options?: RequestInit) => Promise<unknown>

export function canLeaveProject(processing: boolean): boolean {
  return !processing
}

export async function closeMcpBeforeSwitch(projectId: string | undefined, mcpOpen: boolean, request: RequestLike): Promise<void> {
  if (projectId && mcpOpen) await request(`/projects/${projectId}/mcp/close`, { method: 'POST' })
}
