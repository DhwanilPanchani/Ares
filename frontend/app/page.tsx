import { NewRunForm } from '@/components/NewRunForm';
import { RunList } from '@/components/RunList';

export default function HomePage() {
  return (
    <main className="max-w-4xl mx-auto px-8 py-14 space-y-10">
      <div className="pb-2">
        <h1 className="text-5xl font-extrabold tracking-tight text-white mb-3">
          Project Ares
        </h1>
        <p className="text-base text-zinc-500">
          Local-first multi-agent execution platform — submit a goal and watch a live DAG
          execute it entirely on your machine.
        </p>
      </div>

      <hr className="border-zinc-800" />

      <section className="space-y-4">
        <h2 className="text-xs font-semibold uppercase tracking-widest text-zinc-500">
          New run
        </h2>
        <NewRunForm />
      </section>

      <section className="space-y-4">
        <h2 className="text-xs font-semibold uppercase tracking-widest text-zinc-500">
          Run history
        </h2>
        <RunList />
      </section>
    </main>
  );
}
