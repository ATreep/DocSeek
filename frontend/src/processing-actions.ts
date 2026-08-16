type RequestLike = (path: string, options?: RequestInit) => Promise<unknown>

export function cancelProcessing(projectId: string, request: RequestLike): Promise<unknown> {
  return request(`/projects/${projectId}/processing/cancel`, { method: 'POST' })
}

export function retryProcessing(projectId: string, propertyId: string, request: RequestLike): Promise<unknown> {
  return request(`/projects/${projectId}/processing/retry`, { method: 'POST', body: JSON.stringify({ property_id: propertyId }) })
}
