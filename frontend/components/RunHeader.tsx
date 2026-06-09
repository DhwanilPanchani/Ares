'use client';

import { useEffect, useRef, useState } from 'react';
import { useRunStore } from '@/store/runStore';
import { Badge } from '@/components/ui/badge';

const STATUS_COLORS: Record<string, string> = {
  pending: 'bg-zinc-600 text-zinc-200',
  compiling: 'bg-yellow-800 text-yellow-200',
  running: 'bg-blue-800 text-blue-200',
  completed: 'bg-green-800 text-green-200',
  failed: 'bg-red-800 text-red-200',
};

function TrustBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color =
    score >= 0.8
      ? 'bg-green-800 text-green-200'
      : score >= 0.6
        ? 'bg-yellow-800 text-yellow-200'
        : 'bg-red-800 text-red-200';
  return (
    <span className={`text-xs px-2 py-0.5 rounded font-semibold ${color}`}>
      Trust {pct}%
    </span>
  );
}

export function RunHeader() {
  const runGoal = useRunStore((s) => s.runGoal);
  const runStatus = useRunStore((s) => s.runStatus);
  const trustScore = useRunStore((s) => s.trustScore);
  const startedAt = useRunStore((s) => s.startedAt);
  const tokenChunks = useRunStore((s) => s.tokenChunks);

  const [elapsed, setElapsed] = useState(0);
  const [tps, setTps] = useState(0);
  const tokenCountRef = useRef(0);
  const prevTokenRef = useRef(0);
  const prevTimeRef = useRef(Date.now());

  // Elapsed timer
  useEffect(() => {
    if (!startedAt || runStatus === 'completed' || runStatus === 'failed') return;
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 500);
    return () => clearInterval(id);
  }, [startedAt, runStatus]);

  // Token throughput
  useEffect(() => {
    const totalChars = Object.values(tokenChunks).reduce((s, c) => s + c.length, 0);
    tokenCountRef.current = totalChars;
  }, [tokenChunks]);

  useEffect(() => {
    const id = setInterval(() => {
      const now = Date.now();
      const dt = (now - prevTimeRef.current) / 1000;
      const dtokens = tokenCountRef.current - prevTokenRef.current;
      setTps(dt > 0 ? Math.round(dtokens / dt) : 0);
      prevTokenRef.current = tokenCountRef.current;
      prevTimeRef.current = now;
    }, 1000);
    return () => clearInterval(id);
  }, []);

  const elapsedStr =
    elapsed >= 60
      ? `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`
      : `${elapsed}s`;

  return (
    <header className="border-b border-zinc-700 bg-zinc-900 px-6 py-4">
      <div className="flex items-start gap-3 flex-wrap">
        <div className="flex-1 min-w-0">
          <p className="text-xs text-zinc-500 mb-1 font-semibold uppercase tracking-wide">Goal</p>
          <p className="text-sm text-zinc-100 leading-snug break-words">{runGoal || '—'}</p>
        </div>

        <div className="flex items-center gap-2 flex-wrap shrink-0 pt-5">
          <span
            className={`text-xs px-2 py-0.5 rounded font-semibold ${STATUS_COLORS[runStatus] ?? 'bg-zinc-600 text-zinc-200'}`}
          >
            {runStatus}
          </span>

          {trustScore ? (
            <TrustBadge score={trustScore.trust_score} />
          ) : runStatus === 'completed' ? (
            <span className="text-xs text-zinc-500 italic">scoring…</span>
          ) : null}

          {runStatus === 'running' && (
            <>
              {tps > 0 && (
                <span className="text-xs text-zinc-400 font-mono">{tps} tok/s</span>
              )}
              <span className="text-xs text-zinc-400 font-mono">{elapsedStr}</span>
            </>
          )}

          {(runStatus === 'completed' || runStatus === 'failed') && startedAt && (
            <span className="text-xs text-zinc-400 font-mono">{elapsedStr}</span>
          )}
        </div>
      </div>

      {trustScore && (
        <div className="mt-2 text-xs text-zinc-400 leading-snug">
          {trustScore.critique_text}
        </div>
      )}
    </header>
  );
}
