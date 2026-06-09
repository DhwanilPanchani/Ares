import { create } from 'zustand';
import { NodeStatus, RunStatus, SSEEvent, ToolCallRecord, TrustScore } from '@/lib/types';

export interface NodeState {
  id: string;
  name: string;
  description: string;
  depends_on: string[];
  status: NodeStatus;
  output?: string;
  prompt?: string;
  tool_calls: ToolCallRecord[];
  error?: string;
  started_at?: string;
  completed_at?: string;
}

interface RunStoreState {
  runId: string | null;
  runGoal: string;
  runStatus: RunStatus;
  nodes: Record<string, NodeState>;
  selectedNodeId: string | null;
  trustScore: TrustScore | null;
  tokenChunks: Record<string, string>;
  finalOutput: string;
  // pending tool call data keyed by node_id + tool name
  pendingToolCalls: Record<string, { tool: string; args: Record<string, unknown> }>;
  startedAt: number | null; // epoch ms

  setRunId: (id: string) => void;
  setSelectedNode: (id: string | null) => void;
  applyEvent: (event: SSEEvent) => void;
  hydrateFromRun: (run: import('@/lib/types').RunResponse) => void;
  reset: () => void;
}

const initialState = {
  runId: null,
  runGoal: '',
  runStatus: 'pending' as RunStatus,
  nodes: {},
  selectedNodeId: null,
  trustScore: null,
  tokenChunks: {},
  finalOutput: '',
  pendingToolCalls: {},
  startedAt: null,
};

export const useRunStore = create<RunStoreState>((set) => ({
  ...initialState,

  setRunId: (id) => set({ runId: id }),

  setSelectedNode: (id) => set({ selectedNodeId: id }),

  reset: () => set(initialState),

  hydrateFromRun: (run) => {
    const nodes: Record<string, NodeState> = {};
    for (const n of run.nodes) {
      nodes[n.id] = {
        id: n.id,
        name: n.name,
        description: n.description,
        depends_on: n.depends_on,
        status: n.status,
        output: n.output,
        prompt: n.prompt,
        tool_calls: n.tool_calls ?? [],
        error: n.error,
        started_at: n.started_at,
        completed_at: n.completed_at,
      };
    }
    // Also seed node stubs from dag_json if nodes array is empty (run still compiling)
    if (run.dag_json && run.nodes.length === 0) {
      for (const n of run.dag_json.nodes) {
        nodes[n.id] = {
          id: n.id,
          name: n.name,
          description: n.description,
          depends_on: n.depends_on,
          status: 'pending',
          tool_calls: [],
        };
      }
    }
    // Extract final output from terminal node outputs if the run is completed
    let finalOutput = '';
    if (run.status === 'completed' && run.nodes.length > 0) {
      // Use the output of the last node (terminal node) as the final output
      // Find nodes that are not dependencies of any other node
      const depSet = new Set(run.nodes.flatMap((n) => n.depends_on));
      const terminalNodes = run.nodes.filter((n) => !depSet.has(n.id));
      finalOutput = terminalNodes
        .map((n) => n.output || '')
        .filter(Boolean)
        .join('\n\n---\n\n');
    }
    set({
      runId: run.id,
      runGoal: run.goal,
      runStatus: run.status,
      nodes,
      trustScore: run.trust_score ?? null,
      finalOutput,
      startedAt: run.created_at ? new Date(run.created_at).getTime() : null,
    });
  },

  applyEvent: (event) => {
    switch (event.event) {
      case 'run_started':
        set({ runGoal: event.data.goal, runStatus: 'running', startedAt: Date.now() });
        break;

      case 'dag_compiled':
        set((s) => {
          const nodes = { ...s.nodes };
          for (const n of event.data.nodes) {
            nodes[n.id] = {
              id: n.id,
              name: n.name,
              description: '',
              depends_on: n.depends_on,
              status: 'pending',
              tool_calls: [],
            };
          }
          return { nodes, runStatus: 'running' };
        });
        break;

      case 'node_started':
        set((s) => ({
          nodes: {
            ...s.nodes,
            [event.data.node_id]: {
              ...s.nodes[event.data.node_id],
              status: 'running',
              name: event.data.name,
            },
          },
        }));
        break;

      case 'token_chunk':
        set((s) => ({
          tokenChunks: {
            ...s.tokenChunks,
            [event.data.node_id]:
              (s.tokenChunks[event.data.node_id] ?? '') + event.data.chunk,
          },
        }));
        break;

      case 'tool_called':
        set((s) => ({
          pendingToolCalls: {
            ...s.pendingToolCalls,
            [event.data.node_id]: { tool: event.data.tool, args: event.data.args },
          },
        }));
        break;

      case 'tool_result': {
        const nodeId = event.data.node_id;
        set((s) => {
          const pending = s.pendingToolCalls[nodeId];
          const existing = s.nodes[nodeId];
          const newToolCall: ToolCallRecord = {
            tool: event.data.tool,
            args: pending?.args ?? {},
            result: event.data.result,
          };
          const { [nodeId]: _removed, ...remainingPending } = s.pendingToolCalls;
          return {
            nodes: {
              ...s.nodes,
              [nodeId]: {
                ...existing,
                tool_calls: [...(existing?.tool_calls ?? []), newToolCall],
              },
            },
            pendingToolCalls: remainingPending,
          };
        });
        break;
      }

      case 'node_completed':
        set((s) => ({
          nodes: {
            ...s.nodes,
            [event.data.node_id]: {
              ...s.nodes[event.data.node_id],
              status: 'success',
              output: event.data.output,
            },
          },
          tokenChunks: (() => {
            const tc = { ...s.tokenChunks };
            delete tc[event.data.node_id];
            return tc;
          })(),
        }));
        break;

      case 'node_failed':
        set((s) => ({
          nodes: {
            ...s.nodes,
            [event.data.node_id]: {
              ...s.nodes[event.data.node_id],
              status: 'failed',
              error: event.data.error,
            },
          },
        }));
        break;

      case 'run_completed':
        set({ runStatus: 'completed', finalOutput: event.data.output || '' });
        break;

      case 'trust_scored':
        set({ trustScore: event.data });
        break;

      case 'run_failed':
        set({ runStatus: 'failed' });
        break;
    }
  },
}));
