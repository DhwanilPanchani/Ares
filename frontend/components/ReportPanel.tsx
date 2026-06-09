'use client';

import ReactMarkdown from 'react-markdown';

interface Props {
  markdown: string;
}

export function ReportPanel({ markdown }: Props) {
  if (!markdown) return null;

  return (
    <div className="border-t border-zinc-700 bg-zinc-950 px-6 py-6 overflow-y-auto max-h-[50vh]">
      <div className="mb-4 flex items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-widest text-zinc-400">
          Final Report
        </span>
        <div className="h-px flex-1 bg-zinc-700" />
      </div>
      <div className="prose prose-invert prose-sm max-w-none
        prose-headings:text-zinc-100 prose-headings:font-bold
        prose-h1:text-2xl prose-h2:text-xl prose-h2:border-b prose-h2:border-zinc-700 prose-h2:pb-1
        prose-h3:text-base prose-h3:text-zinc-200
        prose-p:text-zinc-300 prose-p:leading-relaxed
        prose-strong:text-zinc-100
        prose-code:text-blue-300 prose-code:bg-zinc-800 prose-code:px-1 prose-code:rounded
        prose-hr:border-zinc-700
        prose-li:text-zinc-300
        prose-blockquote:border-zinc-600 prose-blockquote:text-zinc-400">
        <ReactMarkdown>{markdown}</ReactMarkdown>
      </div>
    </div>
  );
}
