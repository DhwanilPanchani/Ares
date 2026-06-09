'use client';

import { useEffect, useRef } from 'react';
import { getStreamUrl } from '@/lib/api';
import { SSEEvent } from '@/lib/types';
import { useRunStore } from '@/store/runStore';

const SSE_EVENT_TYPES = [
  'run_started',
  'dag_compiled',
  'node_started',
  'token_chunk',
  'tool_called',
  'tool_result',
  'node_completed',
  'node_failed',
  'run_completed',
  'trust_scored',
  'run_failed',
] as const;

const TERMINAL_STATUSES = new Set(['completed', 'failed']);

export function useRunStream(runId: string) {
  const applyEvent = useRunStore((s) => s.applyEvent);
  const runStatus = useRunStore((s) => s.runStatus);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    // Don't open a new connection if the run is already terminal
    if (TERMINAL_STATUSES.has(runStatus)) return;
    // Don't create a second connection on re-renders
    if (esRef.current) return;

    const url = getStreamUrl(runId);
    const es = new EventSource(url);
    esRef.current = es;

    const handler = (e: MessageEvent) => {
      try {
        const event = JSON.parse(e.data as string) as SSEEvent;
        applyEvent(event);
      } catch {
        // ignore malformed events
      }
    };

    for (const type of SSE_EVENT_TYPES) {
      es.addEventListener(type, handler);
    }

    es.onerror = () => {
      // EventSource auto-reconnects on transient errors.
      // If the run is terminal we close permanently below on cleanup.
    };

    return () => {
      for (const type of SSE_EVENT_TYPES) {
        es.removeEventListener(type, handler);
      }
      es.close();
      esRef.current = null;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  // Close the connection once the run finishes
  useEffect(() => {
    if (TERMINAL_STATUSES.has(runStatus) && esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
  }, [runStatus]);
}
