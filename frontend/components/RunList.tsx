'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Trash2 } from 'lucide-react';
import { listRuns } from '@/lib/api';
import { RunResponse } from '@/lib/types';

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

function relativeTime(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function StatusBadge({ status }: { status: RunResponse['status'] }) {
  const color =
    status === 'completed'
      ? 'bg-green-900 text-green-300'
      : status === 'failed'
        ? 'bg-red-900 text-red-300'
        : status === 'running'
          ? 'bg-blue-900 text-blue-300'
          : 'bg-zinc-700 text-zinc-400';
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded font-mono ${color}`}>{status}</span>
  );
}

function TrustBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color =
    pct >= 75
      ? 'bg-green-700 text-green-100'
      : pct >= 51
        ? 'bg-yellow-400 text-yellow-950'
        : 'bg-red-700 text-red-100';
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded font-semibold ${color}`}>{pct}%</span>
  );
}

export function RunList() {
  const router = useRouter();
  const [runs, setRuns] = useState<RunResponse[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listRuns()
      .then(setRuns)
      .catch(() => setRuns([]))
      .finally(() => setLoading(false));
  }, []);

  async function handleDelete(e: React.MouseEvent, runId: string) {
    e.stopPropagation();
    if (!window.confirm('Delete this run?')) return;
    await fetch(`${BASE_URL}/api/runs/${runId}`, { method: 'DELETE' });
    setRuns((prev) => prev.filter((r) => r.id !== runId));
  }

  if (loading) {
    return <div className="text-sm text-zinc-500 py-8 text-center">Loading runs…</div>;
  }

  if (runs.length === 0) {
    return (
      <div className="text-sm text-zinc-500 py-8 text-center">
        No runs yet. Submit a goal above to get started.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-zinc-500 uppercase tracking-widest border-t border-b border-zinc-700">
            <th className="text-left py-2.5 pr-4 font-semibold">Goal</th>
            <th className="text-left py-2.5 pr-4 font-semibold w-24">Status</th>
            <th className="text-left py-2.5 pr-4 font-semibold w-20">Trust</th>
            <th className="text-left py-2.5 font-semibold w-24">When</th>
            <th className="w-8" />
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800/70">
          {runs.map((run) => (
            <tr
              key={run.id}
              onClick={() => router.push(`/runs/${run.id}`)}
              className="group cursor-pointer hover:bg-zinc-800/60 transition-colors duration-100"
            >
              <td className="py-3 pr-4">
                <span className="text-zinc-100 font-medium text-[0.9rem] leading-snug">
                  {run.goal.length > 80 ? run.goal.slice(0, 80) + '…' : run.goal}
                </span>
              </td>
              <td className="py-3 pr-4">
                <StatusBadge status={run.status} />
              </td>
              <td className="py-3 pr-4">
                {run.trust_score ? (
                  <TrustBadge score={run.trust_score.trust_score} />
                ) : (
                  <span className="text-zinc-600 text-xs">—</span>
                )}
              </td>
              <td className="py-3 text-zinc-600 text-xs whitespace-nowrap">
                {relativeTime(run.created_at)}
              </td>
              <td className="py-3 text-right">
                <button
                  onClick={(e) => handleDelete(e, run.id)}
                  className="opacity-0 group-hover:opacity-100 transition-opacity duration-100 p-1 rounded text-zinc-600 hover:text-red-400 hover:bg-red-950/40"
                  title="Delete run"
                >
                  <Trash2 size={14} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
