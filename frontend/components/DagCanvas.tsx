'use client';

import { useMemo } from 'react';
import {
  Background,
  Controls,
  Edge,
  NodeTypes,
  ReactFlow,
  MarkerType,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { useRunStore } from '@/store/runStore';
import { useRunStream } from '@/hooks/useRunStream';
import { NodeCard, DagNode } from './NodeCard';
import { NodeState } from '@/store/runStore';

const nodeTypes: NodeTypes = { dagNode: NodeCard as NodeTypes[string] };

const NODE_W = 220;
const NODE_H = 110;
const H_GAP = 80;
const V_GAP = 30;

function computePositions(nodes: NodeState[]): Record<string, { x: number; y: number }> {
  if (nodes.length === 0) return {};

  const depth = new Map<string, number>();
  for (const n of nodes) depth.set(n.id, 0);

  let changed = true;
  while (changed) {
    changed = false;
    for (const n of nodes) {
      for (const depId of n.depends_on) {
        const d = (depth.get(depId) ?? 0) + 1;
        if (d > (depth.get(n.id) ?? 0)) {
          depth.set(n.id, d);
          changed = true;
        }
      }
    }
  }

  const byDepth = new Map<number, string[]>();
  depth.forEach((d, id) => {
    if (!byDepth.has(d)) byDepth.set(d, []);
    byDepth.get(d)!.push(id);
  });

  const positions: Record<string, { x: number; y: number }> = {};
  byDepth.forEach((ids, d) => {
    ids.forEach((id, i) => {
      positions[id] = {
        x: d * (NODE_W + H_GAP),
        y: i * (NODE_H + V_GAP),
      };
    });
  });
  return positions;
}

interface Props {
  runId: string;
}

export function DagCanvas({ runId }: Props) {
  useRunStream(runId);

  const storeNodes = useRunStore((s) => s.nodes);
  const runStatus = useRunStore((s) => s.runStatus);

  const nodeList = useMemo(() => Object.values(storeNodes), [storeNodes]);
  const positions = useMemo(() => computePositions(nodeList), [nodeList]);

  const rfNodes: DagNode[] = useMemo(
    () =>
      nodeList.map((n) => ({
        id: n.id,
        type: 'dagNode' as const,
        position: positions[n.id] ?? { x: 0, y: 0 },
        data: {
          nodeId: n.id,
          label: n.name || n.id,
          description: n.description,
          status: n.status,
        },
      })),
    [nodeList, positions],
  );

  const rfEdges: Edge[] = useMemo(() => {
    const edges: Edge[] = [];
    for (const n of nodeList) {
      for (const dep of n.depends_on) {
        edges.push({
          id: `${dep}->${n.id}`,
          source: dep,
          target: n.id,
          animated: runStatus === 'running',
          style: { stroke: '#6b7280' },
          markerEnd: { type: MarkerType.ArrowClosed, color: '#6b7280' },
        });
      }
    }
    return edges;
  }, [nodeList, runStatus]);

  if (nodeList.length === 0) {
    return (
      <div className="w-full h-full flex items-center justify-center text-zinc-500 text-sm">
        {runStatus === 'compiling' || runStatus === 'pending'
          ? 'Compiling DAG…'
          : 'No nodes yet'}
      </div>
    );
  }

  return (
    <div className="w-full h-full">
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        proOptions={{ hideAttribution: true }}
        style={{ width: '100%', height: '100%' }}
      >
        <Background color="#374151" gap={20} />
        <Controls />
      </ReactFlow>
    </div>
  );
}
