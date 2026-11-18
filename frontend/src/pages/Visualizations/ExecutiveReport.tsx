import { useCallback, useMemo, useState, type CSSProperties } from 'react';
import { AlertTriangle, FileDown, FileText, Loader2, Mail, RefreshCw } from 'lucide-react';
import { ApiError } from '../../api';
import { BLUE, BORDER, TEAL } from '../../constants/theme';
import {
  ExecutiveReportService,
  reportFilterKey,
  type ExecutiveReport,
  type ReportFilters,
} from '../../services/executiveReport.service';

type GenerateStatus = 'idle' | 'generating' | 'ready' | 'error';
type PdfStatus = 'idle' | 'generating' | 'ready' | 'error';

export type ExecutiveReportState = {
  report: ExecutiveReport | null;
  status: GenerateStatus;
  error: string | null;
  stale: boolean;
  pdfStatus: PdfStatus;
  pdfError: string | null;
  currentFilters: ReportFilters;
  generate: () => Promise<void>;
  downloadPdf: () => Promise<void>;
};

/** One generated report bound to the filter selection it was generated for. */
export function useExecutiveReport(currentFilters: ReportFilters): ExecutiveReportState {
  const [report, setReport] = useState<ExecutiveReport | null>(null);
  const [reportKey, setReportKey] = useState<string | null>(null);
  const [status, setStatus] = useState<GenerateStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [pdfStatus, setPdfStatus] = useState<PdfStatus>('idle');
  const [pdfError, setPdfError] = useState<string | null>(null);

  const currentKey = useMemo(() => reportFilterKey(currentFilters), [currentFilters]);
  const stale = report !== null && reportKey !== currentKey;

  const generate = useCallback(async () => {
    if (status === 'generating') return;
    const requestedKey = currentKey;
    setStatus('generating');
    setError(null);
    setPdfStatus('idle');
    setPdfError(null);
    try {
      const next = await ExecutiveReportService.generate(currentFilters);
      setReport(next);
      setReportKey(requestedKey);
      setStatus('ready');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The executive summary could not be generated.');
      setStatus('error');
    }
  }, [status, currentKey, currentFilters]);

  const downloadPdf = useCallback(async () => {
    if (!report || stale || pdfStatus === 'generating') return;
    setPdfStatus('generating');
    setPdfError(null);
    try {
      await ExecutiveReportService.downloadPdf(report, currentFilters);
      setPdfStatus('ready');
    } catch (err) {
      setPdfError(err instanceof ApiError ? err.message : 'The PDF could not be generated.');
      setPdfStatus('error');
    }
  }, [report, stale, pdfStatus, currentFilters]);

  return { report, status, error, stale, pdfStatus, pdfError, currentFilters, generate, downloadPdf };
}

const baseBtn: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  height: 34,
  padding: '0 14px',
  borderRadius: 6,
  fontSize: '0.8125rem',
  fontWeight: 600,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
};

function btn(kind: 'primary' | 'secondary', disabled: boolean): CSSProperties {
  const look =
    kind === 'primary'
      ? { background: BLUE, color: 'white', border: 'none' }
      : { background: 'white', color: '#374151', border: `1px solid ${BORDER}` };
  return { ...baseBtn, ...look, opacity: disabled ? 0.5 : 1, cursor: disabled ? 'not-allowed' : 'pointer' };
}

function statusText(state: ExecutiveReportState): { text: string; color: string } {
  const { report, status, stale, pdfStatus } = state;
  if (status === 'generating') return { text: 'Generating executive summary…', color: BLUE };
  if (status === 'error') return { text: 'Executive summary failed — retry', color: '#B91C1C' };
  if (!report) return { text: 'Executive summary not generated', color: '#6B7280' };
  if (stale) return { text: `Summary is for ${report.period_label} — filters changed`, color: '#B45309' };
  if (pdfStatus === 'generating') return { text: 'Generating PDF…', color: BLUE };
  if (pdfStatus === 'ready') return { text: `PDF ready · ${report.period_label}`, color: '#047857' };
  if (pdfStatus === 'error') return { text: 'PDF generation failed', color: '#B91C1C' };
  return { text: `Executive summary generated · ${report.period_label}`, color: '#047857' };
}

export function ExecutiveReportToolbar({
  state,
  canGenerate,
  onSendEmail,
}: {
  state: ExecutiveReportState;
  canGenerate: boolean;
  onSendEmail: () => void;
}) {
  const generating = state.status === 'generating';
  const usable = state.report !== null && !state.stale && !generating;
  const generateDisabled = generating || !canGenerate;
  const pdfDisabled = !usable || state.pdfStatus === 'generating';
  const emailDisabled = !usable || !state.report?.can_email;
  const info = statusText(state);
  const staleHint = 'Generate the executive summary for the current filters first.';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
        <button
          type="button"
          style={btn('primary', generateDisabled)}
          disabled={generateDisabled}
          onClick={() => void state.generate()}
          title={canGenerate ? 'Generate an executive summary for the selected filters' : 'Wait for the analytics to load'}
        >
          {generating ? <Loader2 size={14} className="animate-spin" /> : <FileText size={14} />}
          {generating ? 'Generating…' : state.report ? 'Regenerate Executive Summary' : 'Generate Executive Summary'}
        </button>
        <button
          type="button"
          style={btn('secondary', pdfDisabled)}
          disabled={pdfDisabled}
          onClick={() => void state.downloadPdf()}
          title={usable ? 'Download the dashboard and executive summary as a PDF' : staleHint}
        >
          {state.pdfStatus === 'generating' ? <Loader2 size={14} className="animate-spin" /> : <FileDown size={14} />}
          {state.pdfStatus === 'generating' ? 'Preparing PDF…' : 'Download PDF'}
        </button>
        <button
          type="button"
          style={btn('secondary', emailDisabled)}
          disabled={emailDisabled}
          onClick={onSendEmail}
          title={
            !usable ? staleHint : state.report?.can_email ? 'Email the report to management' : 'Only administrators can email reports'
          }
        >
          <Mail size={14} />
          Send via Email
        </button>
      </div>
      <div style={{ fontSize: '0.75rem', fontWeight: 600, color: info.color }}>{info.text}</div>
      {state.pdfError && <div style={{ fontSize: '0.75rem', color: '#B91C1C' }}>{state.pdfError}</div>}
    </div>
  );
}

function formatStamp(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function ExecutiveSummarySection({ state }: { state: ExecutiveReportState }) {
  const { report, status, error, stale } = state;
  const card: CSSProperties = {
    background: 'white',
    border: `1px solid ${BORDER}`,
    borderRadius: 12,
    padding: '20px 24px',
    boxShadow: '0 1px 4px rgba(0,0,0,0.04)',
    marginBottom: 20,
  };

  if (status === 'generating') {
    return (
      <div style={{ ...card, display: 'flex', alignItems: 'center', gap: 10, color: BLUE, fontSize: '0.875rem' }}>
        <Loader2 size={18} className="animate-spin" />
        Generating the quarterly executive summary from the selected analytics. This can take up to a minute.
      </div>
    );
  }
  if (status === 'error' && !report) {
    return (
      <div style={{ ...card, borderColor: '#FCA5A5', background: '#FEF2F2' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#B91C1C', fontWeight: 700, fontSize: '0.875rem' }}>
          <AlertTriangle size={16} /> The executive summary could not be generated
        </div>
        <div style={{ fontSize: '0.8125rem', color: '#7F1D1D', margin: '6px 0 12px' }}>{error}</div>
        <button type="button" style={btn('secondary', false)} onClick={() => void state.generate()}>
          <RefreshCw size={14} /> Retry
        </button>
      </div>
    );
  }
  if (!report) return null;

  const removed = report.summary.verification?.removed_statements || 0;
  return (
    <div style={{ ...card, opacity: stale ? 0.65 : 1 }}>
      {stale && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            background: '#FFFBEB',
            border: '1px solid #FCD34D',
            color: '#92400E',
            borderRadius: 8,
            padding: '8px 12px',
            fontSize: '0.8125rem',
            marginBottom: 14,
          }}
        >
          <AlertTriangle size={15} />
          This summary belongs to the previous selection ({report.period_label}). The filters have changed — generate a
          new summary to download or email a report.
        </div>
      )}
      {status === 'error' && error && (
        <div style={{ fontSize: '0.8125rem', color: '#B91C1C', marginBottom: 10 }}>
          Regeneration failed: {error}
        </div>
      )}
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 6 }}>
        <div>
          <div style={{ fontSize: '1.05rem', fontWeight: 700, color: '#0F172A' }}>
            {report.report_title || 'Secondary Sales Report'} — Executive Summary
          </div>
          <div style={{ fontSize: '0.8125rem', color: BLUE, fontWeight: 600, marginTop: 2 }}>{report.period_label}</div>
        </div>
        <div style={{ fontSize: '0.75rem', color: '#6B7280', textAlign: 'right' }}>
          Generated {formatStamp(report.generated_at)}
          {report.generated_by ? ` by ${report.generated_by}` : ''}
          {report.model ? <div>Model: {report.model}</div> : null}
        </div>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, margin: '8px 0 16px' }}>
        {report.filter_lines.map(line => (
          <span
            key={line.label}
            style={{ fontSize: '0.6875rem', background: '#F3F6FA', border: `1px solid ${BORDER}`, borderRadius: 999, padding: '3px 9px', color: '#374151' }}
          >
            {line.label}: <strong>{line.value}</strong>
          </span>
        ))}
      </div>
      <div style={{ display: 'grid', gap: 12 }}>
        {report.summary.sections.map((section, idx) => {
          const accent = idx === 5 ? '#D93A2F' : idx === 6 ? '#10B981' : TEAL;
          return (
            <section
              key={section.key}
              style={{
                border: `1px solid ${BORDER}`,
                borderRadius: 10,
                background: '#FCFDFE',
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '10px 14px',
                  borderBottom: `1px solid ${BORDER}`,
                  background: '#F8FAFC',
                  borderLeft: `4px solid ${accent}`,
                }}
              >
                <span
                  style={{
                    width: 22,
                    height: 22,
                    borderRadius: 999,
                    background: accent,
                    color: 'white',
                    fontSize: '0.6875rem',
                    fontWeight: 700,
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexShrink: 0,
                  }}
                >
                  {idx + 1}
                </span>
                <h3 style={{ margin: 0, fontSize: '0.875rem', fontWeight: 700, color: '#111827' }}>
                  {section.title}
                </h3>
              </div>
              <div style={{ padding: '12px 14px 14px' }}>
                {section.paragraphs.map((p, i) => (
                  <p
                    key={i}
                    style={{
                      margin: section.bullets.length ? '0 0 10px' : 0,
                      fontSize: '0.8125rem',
                      lineHeight: 1.55,
                      color: '#1F2937',
                      fontWeight: 500,
                    }}
                  >
                    {p}
                  </p>
                ))}
                {section.bullets.length > 0 && (
                  <ul style={{ margin: 0, paddingLeft: 0, listStyle: 'none', display: 'grid', gap: 6 }}>
                    {section.bullets.map((b, i) => (
                      <li
                        key={i}
                        style={{
                          display: 'grid',
                          gridTemplateColumns: '8px 1fr',
                          gap: 8,
                          fontSize: '0.8125rem',
                          lineHeight: 1.5,
                          color: '#1F2937',
                          background: 'white',
                          border: `1px solid ${BORDER}`,
                          borderRadius: 8,
                          padding: '8px 10px',
                        }}
                      >
                        <span
                          style={{
                            width: 6,
                            height: 6,
                            borderRadius: 999,
                            background: accent,
                            marginTop: 6,
                          }}
                        />
                        <span>{b}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </section>
          );
        })}
      </div>
      <div style={{ fontSize: '0.75rem', color: '#6B7280', borderTop: `1px solid ${BORDER}`, paddingTop: 10, marginTop: 14 }}>
        Structured management brief · sales quantity in MT · every number checked against the report data
        {removed > 0 ? ` · ${removed} unverifiable statement${removed === 1 ? '' : 's'} removed` : ''}
        {report.summary.word_count ? ` · ${report.summary.word_count} words` : ''}.
      </div>
    </div>
  );
}
