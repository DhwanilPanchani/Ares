'use client';

import { memo } from 'react';
import { Handle, Node, NodeProps, Position } from '@xyflow/react';
import { useRunStore } from '@/store/runStore';
import { NodeStatus } from '@/lib/types';
import { cn } from '@/lib/utils';

export interface NodeCardData extends Record<string, unknown> {
  nodeId: string;
  label: string;
  description: string;
  status: NodeStatus;
}

export type DagNode = Node<NodeCardData, 'dagNode'>;

const statusStyles: Record<NodeStatus, string> = {
  pending: 'border-zinc-400 bg-zinc-800 text-zinc-300',
  running: 'border-blue-500 bg-blue-950 text-blue-100 animate-pulse',
  success: 'border-green-500 bg-green-950 text-green-100',
  failed: 'border-red-500 bg-red-950 text-red-100',
};

const statusLabel: Record<NodeStatus, string> = {
  pending: 'pending',
  running: 'running…',
  success: 'done',
  failed: 'failed',
};

function NodeCardInner({ data }: NodeProps<DagNode>) {
  const setSelectedNode = useRunStore((s) => s.setSelectedNode);
  const tokenChunk = useRunStore((s) => s.tokenChunks[data.nodeId] ?? '');

  return (
    <div
      className={cn(
        'w-52 rounded-lg border-2 px-3 py-2 cursor-pointer select-none shadow-md transition-all',
        statusStyles[data.status],
      )}
      onClick={() => setSelectedNode(data.nodeId)}
    >
      <Handle type="target" position={Position.Left} className="!bg-zinc-400" />

      <div className="text-xs font-semibold uppercase tracking-wide opacity-60 mb-0.5">
        {statusLabel[data.status]}
      </div>
      <div className="text-sm font-bold leading-snug truncate">{data.label}</div>
      <div className="text-xs opacity-70 mt-0.5 line-clamp-2 leading-snug">
        {data.description}
      </div>

      {data.status === 'running' && tokenChunk && (
        <div className="mt-1 text-[10px] font-mono opacity-60 line-clamp-2 leading-snug">
          {tokenChunk.slice(-120)}
        </div>
      )}

      <Handle type="source" position={Position.Right} className="!bg-zinc-400" />
    </div>
  );
}

export const NodeCard = memo(NodeCardInner);
