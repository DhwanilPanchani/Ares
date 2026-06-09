'use client';

import { useEffect, useState } from 'react';
import { getRun } from '@/lib/api';
import { ApiError, RunResponse } from '@/lib/types';
import { useRunStore } from '@/store/runStore';

interface UseRunDataResult {
  run: RunResponse | null;
  loading: boolean;
  error: ApiError | Error | null;
  refetch: () => void;
}

export function useRunData(runId: string): UseRunDataResult {
  const [run, setRun] = useState<RunResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [tick, setTick] = useState(0);
  const hydrateFromRun = useRunStore((s) => s.hydrateFromRun);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    getRun(runId)
      .then((data) => {
        if (cancelled) return;
        setRun(data);
        hydrateFromRun(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err : new Error(String(err)));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [runId, tick, hydrateFromRun]);

  return {
    run,
    loading,
    error,
    refetch: () => setTick((t) => t + 1),
  };
}
