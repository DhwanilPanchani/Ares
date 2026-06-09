'use client';

import { use, useEffect } from 'react';
import { useRunStore } from '@/store/runStore';
import { useRunData } from '@/hooks/useRunData';
import { RunHeader } from '@/components/RunHeader';
import { DagCanvas } from '@/components/DagCanvas';
import { TraceSidebar } from '@/components/TraceSidebar';
import { ReportPanel } from '@/components/ReportPanel';
import { CompanyCards } from '@/components/CompanyCard';

interface Props {
  params: Promise<{ id: string }>;
}

export default function RunPage({ params }: Props) {
  const { id } = use(params);
  const reset = useRunStore((s) => s.reset);
  const selectedNodeId = useRunStore((s) => s.selectedNodeId);
  const runStatus = useRunStore((s) => s.runStatus);
  const finalOutput = useRunStore((s) => s.finalOutput);

  // Reset store when navigating to a new run
  useEffect(() => {
    reset();
  }, [id, reset]);

  // Fetch initial run state and hydrate store
  const { loading, error } = useRunData(id);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen text-zinc-500 text-sm">
        Loading run…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-screen text-red-400 text-sm">
        {error.message}
      </div>
    );
  }

  const showOutput = runStatus === 'completed' && finalOutput;

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      <RunHeader />

      <div className="flex flex-1 overflow-hidden">
        {/* Main content area */}
        <div className="flex-1 overflow-hidden flex flex-col">
          {/* DAG canvas — always visible */}
          <div className={`overflow-hidden ${showOutput ? 'flex-none h-[40%]' : 'flex-1 h-full'}`}>
            <DagCanvas runId={id} />
          </div>

          {/* Output panel — shown only after completion */}
          {showOutput && (
            <div className="flex-1 overflow-y-auto border-t border-zinc-700">
              <CompanyCards markdown={finalOutput} />
              <ReportPanel markdown={finalOutput} />
            </div>
          )}
        </div>

        {/* Trace sidebar — slides in when a node is selected */}
        {selectedNodeId && <TraceSidebar runId={id} />}
      </div>
    </div>
  );
}
