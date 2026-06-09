'use client';

import { useEffect, useState } from 'react';
import { useRunStore } from '@/store/runStore';
import { getTrace, retryNode } from '@/lib/api';
import { SpanResponse } from '@/lib/types';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { ChevronDown, ChevronRight, RefreshCw, X } from 'lucide-react';

interface Props {
  runId: string;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex items-center gap-1 w-full text-left py-1.5 text-xs font-semibold uppercase tracking-wide text-zinc-400 hover:text-zinc-200 transition-colors">
        {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        {title}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1 mb-3">{children}</div>
      </CollapsibleContent>
    </Collapsible>
  );
}

export function TraceSidebar({ runId }: Props) {
  const selectedNodeId = useRunStore((s) => s.selectedNodeId);
  const setSelectedNode = useRunStore((s) => s.setSelectedNode);
  const nodes = useRunStore((s) => s.nodes);
  const [spans, setSpans] = useState<SpanResponse[]>([]);
  const [retrying, setRetrying] = useState(false);

  const node = selectedNodeId ? nodes[selectedNodeId] : null;

  useEffect(() => {
    if (!selectedNodeId) return;
    getTrace(runId)
      .then((t) => setSpans(t.spans.filter((s) => s.node_id === selectedNodeId)))
      .catch(() => setSpans([]));
  }, [runId, selectedNodeId]);

  if (!node) return null;

  const spanDuration =
    spans.length > 0 && spans[0].started_at && spans[0].ended_at
      ? Math.round(
          (new Date(spans[0].ended_at).getTime() - new Date(spans[0].started_at).getTime()),
        )
      : null;

  async function handleRetry() {
    if (!selectedNodeId) return;
    setRetrying(true);
    try {
      await retryNode(runId, selectedNodeId);
    } finally {
      setRetrying(false);
    }
  }

  return (
    <aside className="w-80 flex-shrink-0 bg-zinc-900 border-l border-zinc-700 flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-zinc-700">
        <div>
          <div className="text-sm font-bold text-zinc-100">{node.name || node.id}</div>
          <span
            className={`text-xs px-1.5 py-0.5 rounded font-mono mt-1 inline-block ${
              node.status === 'success'
                ? 'bg-green-900 text-green-300'
                : node.status === 'failed'
                  ? 'bg-red-900 text-red-300'
                  : node.status === 'running'
                    ? 'bg-blue-900 text-blue-300'
                    : 'bg-zinc-700 text-zinc-400'
            }`}
          >
            {node.status}
          </span>
        </div>
        <button onClick={() => setSelectedNode(null)} className="text-zinc-500 hover:text-zinc-200">
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-1">
        {node.prompt && (
          <Section title="Prompt">
            <pre className="text-[11px] font-mono text-zinc-400 whitespace-pre-wrap break-words leading-snug">
              {node.prompt}
            </pre>
          </Section>
        )}

        {node.output && (
          <Section title="Output">
            <pre className="text-[11px] font-mono text-zinc-300 whitespace-pre-wrap break-words leading-snug">
              {node.output}
            </pre>
          </Section>
        )}

        {node.tool_calls.length > 0 && (
          <Section title={`Tool Calls (${node.tool_calls.length})`}>
            <div className="space-y-2">
              {node.tool_calls.map((tc, i) => (
                <div key={i} className="rounded border border-zinc-700 p-2 text-[11px]">
                  <div className="font-semibold text-blue-400 mb-1">{tc.tool}</div>
                  <div className="text-zinc-500 mb-1">
                    args: <span className="text-zinc-300 font-mono">{JSON.stringify(tc.args)}</span>
                  </div>
                  {tc.result && (
                    <div className="text-zinc-400">
                      result:{' '}
                      <span className="text-zinc-200 font-mono break-all">
                        {tc.result.slice(0, 300)}
                        {tc.result.length > 300 ? '…' : ''}
                      </span>
                    </div>
                  )}
                  {tc.error && (
                    <div className="text-red-400">
                      error: <span className="font-mono">{tc.error}</span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </Section>
        )}

        {spans.length > 0 && (
          <Section title="Span">
            <div className="text-[11px] text-zinc-400 space-y-1">
              {spanDuration !== null && (
                <div>
                  Duration: <span className="text-zinc-200">{spanDuration} ms</span>
                </div>
              )}
              <div>
                Status:{' '}
                <span
                  className={spans[0].status_code === 'OK' ? 'text-green-400' : 'text-red-400'}
                >
                  {spans[0].status_code}
                </span>
              </div>
              <div>
                Kind: <span className="text-zinc-200">{spans[0].kind}</span>
              </div>
              <div>
                Span ID: <span className="text-zinc-500 font-mono">{spans[0].id.slice(0, 16)}</span>
              </div>
            </div>
          </Section>
        )}

        {node.error && (
          <div className="rounded border border-red-800 bg-red-950 p-2 text-[11px] text-red-300">
            <div className="font-semibold mb-1">Error</div>
            <pre className="whitespace-pre-wrap break-words">{node.error}</pre>
          </div>
        )}
      </div>

      {/* Footer */}
      {node.status === 'failed' && (
        <div className="p-4 border-t border-zinc-700">
          <Button
            variant="destructive"
            size="sm"
            className="w-full gap-2"
            onClick={handleRetry}
            disabled={retrying}
          >
            <RefreshCw className={`w-3 h-3 ${retrying ? 'animate-spin' : ''}`} />
            Retry from here
          </Button>
        </div>
      )}
    </aside>
  );
}
