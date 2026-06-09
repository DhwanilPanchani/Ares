// Types mirror the backend Pydantic models and SSE event shapes.
// Field names match the backend wire format (snake_case) so fetch responses
// can be used directly without a transformation layer.

export type NodeStatus = 'pending' | 'running' | 'success' | 'failed';
export type RunStatus = 'pending' | 'compiling' | 'running' | 'completed' | 'failed';
export type SpanKind = 'llm' | 'tool' | 'agent' | 'system';

export interface ToolCallRecord {
  tool: string;
  args: Record<string, unknown>;
  result?: string;
  error?: string;
}

export interface DAGNodeDef {
  id: string;
  name: string;
  description: string;
  depends_on: string[];
  tool_hint?: string;
}

export interface NodeResponse {
  id: string;
  run_id: string;
  name: string;
  description: string;
  status: NodeStatus;
  depends_on: string[];
  prompt?: string;
  output?: string;
  tool_calls: ToolCallRecord[];
  started_at?: string;
  completed_at?: string;
  error?: string;
}

export interface TrustScore {
  factual_grounding: number;
  goal_completion: number;
  tool_error_rate: number;
  trust_score: number;
  critique_text: string;
  flagged_span_ids: string[];
}

export interface RunResponse {
  id: string;
  goal: string;
  status: RunStatus;
  dag_json?: {
    goal: string;
    nodes: DAGNodeDef[];
  };
  created_at: string;
  completed_at?: string;
  error?: string;
  trust_score?: TrustScore;
  nodes: NodeResponse[];
}

export interface SpanResponse {
  id: string;
  trace_id: string;
  parent_id?: string;
  run_id: string;
  node_id?: string;
  name: string;
  kind: SpanKind;
  attributes: Record<string, unknown>;
  started_at: string;
  ended_at?: string;
  status_code: 'OK' | 'ERROR';
}

export interface TraceResponse {
  run_id: string;
  spans: SpanResponse[];
}

// ---------------------------------------------------------------------------
// SSE events — discriminated union on the `event` field.
// The `data` field uses snake_case because these come over the wire from Python.
// ---------------------------------------------------------------------------

export type SSEEvent =
  | {
      event: 'run_started';
      run_id: string;
      data: { goal: string; node_count: number };
    }
  | {
      event: 'dag_compiled';
      run_id: string;
      data: { nodes: Array<{ id: string; name: string; depends_on: string[] }> };
    }
  | {
      event: 'node_started';
      run_id: string;
      data: { node_id: string; name: string };
    }
  | {
      event: 'token_chunk';
      run_id: string;
      data: { node_id: string; chunk: string };
    }
  | {
      event: 'tool_called';
      run_id: string;
      data: { node_id: string; tool: string; args: Record<string, unknown> };
    }
  | {
      event: 'tool_result';
      run_id: string;
      data: { node_id: string; tool: string; result: string };
    }
  | {
      event: 'node_completed';
      run_id: string;
      data: { node_id: string; output: string };
    }
  | {
      event: 'node_failed';
      run_id: string;
      data: { node_id: string; error: string };
    }
  | {
      event: 'run_completed';
      run_id: string;
      data: { output: string };
    }
  | {
      event: 'trust_scored';
      run_id: string;
      data: TrustScore;
    }
  | {
      event: 'run_failed';
      run_id: string;
      data: { error: string };
    };

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}
