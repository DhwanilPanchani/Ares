import { ApiError, RunResponse, TraceResponse } from './types';

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      message = body.detail ?? body.message ?? message;
    } catch {
      // ignore parse error — use the status text
    }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

export async function createRun(goal: string): Promise<RunResponse> {
  return apiFetch<RunResponse>('/api/runs', {
    method: 'POST',
    body: JSON.stringify({ goal }),
  });
}

export async function listRuns(): Promise<RunResponse[]> {
  return apiFetch<RunResponse[]>('/api/runs');
}

export async function getRun(id: string): Promise<RunResponse> {
  return apiFetch<RunResponse>(`/api/runs/${id}`);
}

export async function getTrace(id: string): Promise<TraceResponse> {
  return apiFetch<TraceResponse>(`/api/runs/${id}/trace`);
}

export async function retryNode(runId: string, nodeId: string): Promise<void> {
  return apiFetch<void>(`/api/runs/${runId}/retry-node`, {
    method: 'POST',
    body: JSON.stringify({ node_id: nodeId }),
  });
}

export function getStreamUrl(runId: string): string {
  return `${BASE_URL}/api/runs/${runId}/stream`;
}
