'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { createRun } from '@/lib/api';
import { ApiError } from '@/lib/types';

export function NewRunForm() {
  const router = useRouter();
  const [goal, setGoal] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = goal.trim();
    if (trimmed.length < 10) {
      setError('Goal must be at least 10 characters.');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const run = await createRun(trimmed);
      router.push(`/runs/${run.id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`Error ${err.status}: ${err.message}`);
      } else {
        setError('Failed to create run. Is the backend running?');
      }
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <textarea
        value={goal}
        onChange={(e) => setGoal(e.target.value)}
        placeholder="Describe a goal for the agent… (e.g. 'Research the latest AI papers and write a summary report')"
        rows={5}
        style={{ minHeight: '120px' }}
        className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-4 py-3 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/40 transition-all duration-150 resize-none"
        disabled={loading}
      />
      {error && <p className="text-xs text-red-400">{error}</p>}
      <button
        type="submit"
        disabled={loading || goal.trim().length < 10}
        className="w-full rounded-lg bg-blue-600 hover:bg-blue-500 active:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-semibold py-2.5 transition-colors duration-150"
      >
        {loading ? 'Starting…' : 'Run goal'}
      </button>
    </form>
  );
}
