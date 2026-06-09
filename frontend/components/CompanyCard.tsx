'use client';

import { useMemo } from 'react';

interface CompanyData {
  name: string;
  funding: string;
  description: string;
  threatScore: number | null;
}

/**
 * Parse the competitive intelligence markdown output into a list of companies.
 * Uses simple regex — falls back to an empty array if parsing fails.
 *
 * Expected markdown pattern per company:
 *   ## <Company Name>
 *   **Funding:** $XXX
 *   <description paragraph>
 *   **Competitive Threat Score:** 0.XX
 */
function parseCompanies(markdown: string): CompanyData[] {
  const companies: CompanyData[] = [];

  // Split on h2 headings that look like company names
  const sections = markdown.split(/^##\s+/m).slice(1);

  for (const section of sections) {
    const lines = section.trim().split('\n');
    if (lines.length === 0) continue;

    const name = lines[0].trim().replace(/[*_`]/g, '');
    if (!name) continue;

    // Extract funding
    const fundingMatch = section.match(/\*\*Funding[^*]*\*\*[:\s]+([^\n]+)/i);
    const funding = fundingMatch ? fundingMatch[1].trim() : 'Unknown';

    // Extract description — first non-empty non-heading non-bold paragraph
    const descLine = lines.find(
      (l) =>
        l.trim() &&
        !l.startsWith('#') &&
        !l.startsWith('**') &&
        !l.startsWith('-') &&
        !l.match(/^[*_]/) &&
        l !== lines[0],
    );
    const description = descLine ? descLine.trim().slice(0, 200) : '';

    // Extract competitive threat score
    const scoreMatch = section.match(
      /[Cc]ompetitive\s+[Tt]hreat\s+[Ss]core[:\s]+([0-9]*\.?[0-9]+)/,
    );
    const threatScore = scoreMatch ? parseFloat(scoreMatch[1]) : null;

    companies.push({ name, funding, description, threatScore });
  }

  return companies;
}

function ThreatBar({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color =
    score < 0.4
      ? 'bg-green-500'
      : score < 0.7
        ? 'bg-yellow-500'
        : 'bg-red-500';
  const label =
    score < 0.4 ? 'Low threat' : score < 0.7 ? 'Moderate threat' : 'High threat';

  return (
    <div className="mt-2">
      <div className="flex justify-between items-center mb-1">
        <span className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</span>
        <span className="text-[11px] font-mono text-zinc-300">{pct}%</span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-zinc-700 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-700 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

interface Props {
  markdown: string;
}

export function CompanyCards({ markdown }: Props) {
  const companies = useMemo(() => parseCompanies(markdown), [markdown]);

  if (companies.length === 0) return null;

  return (
    <div className="px-6 py-4 border-t border-zinc-700">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-widest text-zinc-400">
          Company Intelligence
        </span>
        <div className="h-px flex-1 bg-zinc-700" />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {companies.map((c) => (
          <div
            key={c.name}
            className="rounded-lg border border-zinc-700 bg-zinc-900 p-4 flex flex-col gap-2 hover:border-zinc-500 transition-colors"
          >
            <div className="flex items-start justify-between gap-2">
              <h3 className="text-sm font-bold text-zinc-100 leading-tight">{c.name}</h3>
              {c.funding && c.funding !== 'Unknown' && (
                <span className="shrink-0 text-[10px] px-2 py-0.5 rounded-full bg-blue-900/60 text-blue-300 font-mono border border-blue-700/40">
                  {c.funding}
                </span>
              )}
            </div>
            {c.description && (
              <p className="text-[11px] text-zinc-400 leading-snug line-clamp-3">
                {c.description}
              </p>
            )}
            {c.threatScore !== null && <ThreatBar score={c.threatScore} />}
          </div>
        ))}
      </div>
    </div>
  );
}
