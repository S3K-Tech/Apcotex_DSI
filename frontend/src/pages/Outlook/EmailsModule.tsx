import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import {
  Play,
  Loader2,
  Inbox,
  ArrowRight,
  Trash2,
  X,
  Package,
  Info as InfoIcon,
  UserRound,
} from 'lucide-react';
import { useNavigate } from 'react-router';
import { CustomerWorkbenchModal } from './CustomerWorkbenchModal';
import { ProductWorkbenchModal } from './ProductWorkbenchModal';
import { StandardizedDataModal } from './StandardizedDataModal';
import { BLUE, BORDER, RED, TEAL } from '../../constants/theme';
import type { EmailRecord } from '../../types';
import {
  EmailsService,
  type ERPPreviewResponse,
  type ReviewSummary,
} from '../../services/emails.service';
import { ApiError, getSession } from '../../api';
import { isAdminRole } from '../../utils/rbac';
import { formatPeriodDisplay } from '../../utils/quarter';

/** Max unread emails per Sync Outlook click; max ready emails per Proceed batch. */
const BATCH_LIMIT = 5;
const MIN_IMPORT_ACCURACY = 75;

type BatchItemResult = {
  emailId: number;
  subject: string;
  status: 'ok' | 'failed' | 'skipped';
  detail: string;
  rows?: number;
};

function formatSyncClock(iso?: string | null): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString('en-IN', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    });
  } catch {
    return '—';
  }
}

function formatSyncTimeOnly(iso?: string | null): string {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString('en-IN', {
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    });
  } catch {
    return '—';
  }
}

function isScoreRunning(email: EmailRecord): boolean {
  const status = (email.processStatus || email.scoringStatus || '').toLowerCase();
  return status === 'scoring' || status === 'queued';
}

function isHumanReview(email: EmailRecord): boolean {
  const label = (email.statusLabel || '').toLowerCase();
  return label === 'human review required' || label === 'human review';
}

/** human_review = low parser confidence; incremental_review = data already exists for this subject. */
function humanReviewReason(email: EmailRecord): string {
  return (email.processStatus || '').toLowerCase() === 'human_review'
    ? 'Human Review · Low confidence'
    : 'Human Review · Possible duplicate';
}

function reviewPeriodLabel(summary: ReviewSummary): string {
  const periods = summary.incoming.periods;
  if (periods && periods.length) {
    return periods.map(period => formatPeriodDisplay(period)).join(', ');
  }
  return formatPeriodDisplay(summary.incoming.period) || '-';
}

type HumanReviewState = {
  email: EmailRecord;
  summary: ReviewSummary | null;
  busy: boolean;
  error: string | null;
};

/** Single Accuracy badge. Scoring is only shown while the job is still running. */
function accuracyMeta(email: EmailRecord): {
  label: string;
  detail: string;
  bg: string;
  color: string;
} {
  const status = (email.processStatus || '').toLowerCase();
  const labelName = (email.statusLabel || '').toLowerCase();
  const failed = status === 'failed' || status === 'skipped' || labelName === 'failed';
  if (isScoreRunning(email)) {
    return {
      label: 'Scoring…',
      detail: 'Background job running',
      bg: 'rgba(107,114,128,0.12)',
      color: '#4B5563',
    };
  }
  if (failed) {
    return {
      label: '0%',
      detail: email.errorMessage || 'Could not detect structured sales table',
      bg: 'rgba(220,38,38,0.12)',
      color: RED,
    };
  }
  const value = email.confidenceScore ?? 0;
  if (value >= 90) {
    return {
      label: `${value}% Accuracy`,
      detail: 'Mapped correctly across the workbook',
      bg: 'rgba(5,150,105,0.12)',
      color: '#059669',
    };
  }
  if (value >= 75) {
    return {
      label: `${value}% Accuracy`,
      detail: 'Acceptable — review mappings if needed',
      bg: 'rgba(245,158,11,0.15)',
      color: '#B45309',
    };
  }
  return {
    label: `${value}% Accuracy`,
    detail: 'Low — fix column mappings before import',
    bg: 'rgba(220,38,38,0.12)',
    color: RED,
  };
}

const btnPrimary: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '8px 14px',
  border: 'none',
  borderRadius: 8,
  background: BLUE,
  color: 'white',
  fontSize: '0.8125rem',
  fontWeight: 600,
  cursor: 'pointer',
};

const btnSecondary: CSSProperties = {
  ...btnPrimary,
  background: 'white',
  color: '#374151',
  border: `1px solid ${BORDER}`,
};

export function EmailsModule() {
  const navigate = useNavigate();
  const isAdmin = isAdminRole(getSession()?.role);
  const [extracting, setExtracting] = useState(false);
  const [emails, setEmails] = useState<EmailRecord[]>([]);
  const [emailsLoaded, setEmailsLoaded] = useState(false);
  const [autoSyncStatus, setAutoSyncStatus] = useState<{
    status: string;
    frequency: string;
    last_successful_sync?: string | null;
    next_scheduled_sync?: string | null;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

  const [fiscalYearStart, setFiscalYearStart] = useState(2025);
  const [selectedEmailId, setSelectedEmailId] = useState<number | null>(null);
  const [standardization, setStandardization] = useState<{
    email: EmailRecord;
    mode: 'workbench' | 'customer' | 'data';
    attachmentId?: number | null;
  } | null>(null);
  const [consolidateOpen, setConsolidateOpen] = useState(false);
  const [consolidateBusy, setConsolidateBusy] = useState(false);
  const [consolidateError, setConsolidateError] = useState<string | null>(null);
  const [batchProgress, setBatchProgress] = useState<{
    current: number;
    total: number;
    label: string;
  } | null>(null);
  const [batchResults, setBatchResults] = useState<BatchItemResult[]>([]);
  const [review, setReview] = useState<HumanReviewState | null>(null);
  const reviewRequestId = useRef(0);

  const refreshEmails = async () => {
    const data = await EmailsService.getExtractedEmails();
    setEmails(data);
    setEmailsLoaded(true);
    setSelectedEmailId(prev =>
      prev != null && data.some(e => e.id === prev) ? prev : null,
    );
  };

  const refreshAutoSyncStatus = async () => {
    try {
      const status = await EmailsService.getAutoSyncStatus();
      setAutoSyncStatus(status);
    } catch {
      /* ignore status card errors */
    }
  };

  useEffect(() => {
    refreshEmails().catch(err => {
      setError(err instanceof ApiError ? err.message : 'Failed to load emails');
    });
    refreshAutoSyncStatus().catch(() => undefined);
  }, []);

  // Poll only while a scoring job is queued or running. Terminal states stop the timer.
  useEffect(() => {
    const pending = emails.some(isScoreRunning);
    if (!pending) return undefined;
    const id = window.setInterval(() => {
      refreshEmails().catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(id);
  }, [emails]);

  const openReview = (email: EmailRecord) => {
    const token = ++reviewRequestId.current;
    setReview({ email, summary: null, busy: true, error: null });
    void (async () => {
      try {
        const summary = await EmailsService.getReviewSummary(email.id);
        if (reviewRequestId.current !== token) return;
        setReview({ email, summary, busy: false, error: null });
      } catch (err) {
        if (reviewRequestId.current !== token) return;
        setReview({
          email,
          summary: null,
          busy: false,
          error: err instanceof ApiError ? err.message : 'Could not load review details',
        });
      }
    })();
  };

  const closeReview = () => {
    if (review?.busy) return;
    reviewRequestId.current += 1;
    setReview(null);
  };

  const submitReview = async (action: 'ADD' | 'DELETE') => {
    if (!review?.summary?.reviewable || review.busy) return;
    const token = reviewRequestId.current;
    const segment = review.summary.incoming.segment;
    const emailId = review.email.id;
    setReview({ ...review, busy: true, error: null });
    let result;
    try {
      result = await EmailsService.resolveReview(emailId, action);
    } catch (err) {
      if (reviewRequestId.current !== token) return;
      setReview({
        ...review,
        busy: false,
        error: err instanceof ApiError ? err.message : 'Review action failed',
      });
      return;
    }
    if (reviewRequestId.current !== token) return;
    reviewRequestId.current += 1;
    setReview(null);
    setError(null);
    const inserted = result.records_inserted ?? 0;
    const period = result.reporting_quarter ? ` (${formatPeriodDisplay(result.reporting_quarter)})` : '';
    setSyncMessage(
      action === 'DELETE'
        ? 'Incoming data rejected. Existing data kept.'
        : result.outcome === 'duplicate_upload'
          ? 'No records added. Every incoming row already exists, so identical rows were skipped. Existing data kept.'
          : inserted === 0
            ? 'ADD completed, but no new rows were inserted.'
            : `Added ${inserted} sales rows for ${segment || 'this segment'}${period}.`,
    );
    try {
      await refreshEmails();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to sync / load emails');
    }
  };

  const handleExtract = async () => {
    setExtracting(true);
    setError(null);
    setSyncMessage(null);
    try {
      const session = getSession();
      const sync = await EmailsService.triggerSync();
      const asEmail = session?.email ? ` for ${session.email}` : '';
      setSyncMessage(sync.message || `Outlook sync started${asEmail}…`);

      const jobId = sync.job?.id;
      if (jobId) {
        const finalJob = await EmailsService.waitForSyncJob(jobId);
        const details = (finalJob.details || {}) as Record<string, unknown>;
        const status = String(finalJob.status || '').toLowerCase();
        let resultMsg =
          (typeof details.result_message === 'string' && details.result_message) || null;
        if (!resultMsg) {
          if (status === 'started' || status === 'queued') {
            resultMsg =
              `Sync is still running (job #${finalJob.id}). Refresh the email list in a moment — ` +
              `do not assume 0 extracted until status is completed.`;
          } else if (finalJob.error_message) {
            resultMsg = `Outlook sync failed: ${finalJob.error_message}`;
          } else {
            resultMsg = `Sync ${finalJob.status}: found ${finalJob.emails_found}, processed ${finalJob.emails_processed}.`;
          }
        }
        if (status === 'failed' || status === 'partial') {
          setSyncMessage(null);
          setError(
            finalJob.error_message && !resultMsg.includes(finalJob.error_message)
              ? `${resultMsg} ${finalJob.error_message}`
              : resultMsg,
          );
        } else {
          setSyncMessage(resultMsg);
        }
      }

      await refreshEmails();
      await refreshAutoSyncStatus();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to sync / load emails');
    } finally {
      setExtracting(false);
    }
  };

  const readyEmailsForBatch = useMemo(() => {
    return emails
      .filter(
        e =>
          e.hasExcel &&
          e.subjectValid !== false &&
          (e.statusLabel || '').toLowerCase() !== 'imported' &&
          (e.statusLabel || '').toLowerCase() !== 'failed' &&
          (e.statusLabel || '').toLowerCase() !== 'invalid subject' &&
          (e.statusLabel || '').toLowerCase() !== 'human review required' &&
          (e.statusLabel || '').toLowerCase() !== 'duplicate upload' &&
          (e.confidenceScore || 0) >= MIN_IMPORT_ACCURACY &&
          Boolean(e.distributor || e.distributorName) &&
          Boolean(e.quarter),
      )
      .slice(0, BATCH_LIMIT);
  }, [emails]);

  const pendingLowAccuracyCount = useMemo(
    () =>
      emails.filter(
        e =>
          e.hasExcel &&
          (e.statusLabel || '').toLowerCase() !== 'imported' &&
          (e.confidenceScore || 0) > 0 &&
          (e.confidenceScore || 0) < MIN_IMPORT_ACCURACY,
      ).length,
    [emails],
  );

  const resolveDistributorId = (data: ERPPreviewResponse): number | null => {
    if (data.distributor_id != null) return Number(data.distributor_id);
    const matches = data.distributor_matches || [];
    if (matches.length === 1 && !data.distributor_unknown) return Number(matches[0].id);
    return null;
  };

  const runBatchConsolidate = async () => {
    setConsolidateError(null);
    setBatchResults([]);
    setError(null);

    const batch = readyEmailsForBatch;
    if (!batch.length) {
      if (pendingLowAccuracyCount > 0) {
        setError(
          `${pendingLowAccuracyCount} email(s) are below ${MIN_IMPORT_ACCURACY}% accuracy and were not consolidated.`,
        );
      } else {
        setError(
          `No ready emails to consolidate (need Excel + accuracy ≥ ${MIN_IMPORT_ACCURACY}%). Sync Outlook or wait for scoring.`,
        );
      }
      return;
    }

    setConsolidateOpen(true);
    setConsolidateBusy(true);
    setBatchProgress({ current: 0, total: batch.length, label: 'Starting…' });

    const results: BatchItemResult[] = [];
    let totalRows = 0;

    try {
      for (let i = 0; i < batch.length; i++) {
        const email = batch[i];
        setBatchProgress({
          current: i + 1,
          total: batch.length,
          label: email.subject || email.senderEmail || `Email #${email.id}`,
        });
        try {
          const data = await EmailsService.previewEmail(
            email.id,
            undefined,
            fiscalYearStart || undefined,
          );
          const accuracy = data.confidence?.overall ?? email.confidenceScore ?? 0;
          if (accuracy < MIN_IMPORT_ACCURACY) {
            results.push({
              emailId: email.id,
              subject: email.subject,
              status: 'skipped',
              detail: `Accuracy ${Math.round(accuracy)}% — below the consolidate threshold`,
            });
            setBatchResults([...results]);
            continue;
          }
          const distId = resolveDistributorId(data);
          if (distId == null) {
            results.push({
              emailId: email.id,
              subject: email.subject,
              status: 'skipped',
              detail: 'Distributor not matched',
            });
            setBatchResults([...results]);
            continue;
          }
          if (
            data.incremental_analysis?.status === 'confirm_add' ||
            data.incremental_analysis?.status === 'human_review' ||
            (data.incremental_analysis?.modified_count || 0) > 0
          ) {
            results.push({
              emailId: email.id,
              subject: email.subject,
              status: 'skipped',
              detail:
                'Confirmation required before adding records for this distributor/period.',
            });
            setBatchResults([...results]);
            continue;
          }
          if (data.fiscal_year_start) setFiscalYearStart(Number(data.fiscal_year_start));
          const result = await EmailsService.importErp({
            email_id: email.id,
            distributor_id: distId,
            fiscal_year_start: data.fiscal_year_start ?? undefined,
            rows: data.rows,
          });
          const skipNotes = (result.workbook_skips || []).map(
            s => `Skipped ${s.workbook ? `${s.workbook}: ` : ''}${s.reason || 'not imported'}`,
          );
          if (result.duplicate_upload) {
            results.push({
              emailId: email.id,
              subject: email.subject,
              status: 'skipped',
              detail: [
                'Duplicate upload. Every row already exists, so identical rows were skipped. No records inserted.',
                ...skipNotes,
              ].join('\n'),
              rows: 0,
            });
            setBatchResults([...results]);
            continue;
          }
          if (result.requires_review) {
            results.push({
              emailId: email.id,
              subject: email.subject,
              status: 'skipped',
              detail: [
                `Possible duplicate, sent to Human Review. ${
                  result.duplicate_review?.message ||
                  'Duplicate records detected for this Financial Year & Quarter. Review before proceeding.'
                }`,
                ...skipNotes,
              ].join('\n'),
            });
            setBatchResults([...results]);
            continue;
          }
          const inserted = result.records_inserted || 0;
          if (inserted === 0) {
            results.push({
              emailId: email.id,
              subject: email.subject,
              status: 'skipped',
              detail: ['No records inserted.', ...skipNotes].join('\n'),
              rows: 0,
            });
            setBatchResults([...results]);
            continue;
          }
          totalRows += inserted;
          const quarters = result.quarters_imported?.length
            ? result.quarters_imported
            : [result.reporting_quarter].filter(Boolean);
          const wbCount = result.workbooks_imported?.length || 1;
          results.push({
            emailId: email.id,
            subject: email.subject,
            status: 'ok',
            detail: [
              `${inserted} rows / ${wbCount} workbook(s)`,
              ...quarters.map(q => `Imported into ${formatPeriodDisplay(q)}`),
              ...skipNotes,
            ].join('\n'),
            rows: inserted,
          });
        } catch (err) {
          results.push({
            emailId: email.id,
            subject: email.subject,
            status: 'failed',
            detail: err instanceof ApiError ? err.message : 'Import failed',
          });
        }
        setBatchResults([...results]);
      }

      const refreshed = await EmailsService.getExtractedEmails();
      setEmails(refreshed);
      setSelectedEmailId(prev =>
        prev != null && refreshed.some(e => e.id === prev) ? prev : null,
      );

      const ok = results.filter(r => r.status === 'ok').length;
      const skipped = results.filter(r => r.status === 'skipped').length;
      const failed = results.filter(r => r.status === 'failed').length;
      const stillReady = refreshed.filter(
        e =>
          e.hasExcel &&
          (e.statusLabel || '').toLowerCase() !== 'imported' &&
          (e.statusLabel || '').toLowerCase() !== 'failed' &&
          (e.confidenceScore || 0) >= MIN_IMPORT_ACCURACY,
      ).length;

      setSyncMessage(
        `Batch consolidate: ${ok} imported (${totalRows} rows), ${skipped} skipped, ${failed} failed.` +
          (stillReady > 0
            ? ` ${stillReady} ready left — click Proceed again (max ${BATCH_LIMIT}).`
            : '') +
          (ok > 0 ? ' Opening Consolidated Data…' : ''),
      );
      setBatchProgress(null);
      if (ok > 0) {
        window.setTimeout(() => {
          setConsolidateOpen(false);
          navigate('/consolidated-data');
        }, 1100);
      } else {
        setConsolidateError(
          'No emails were imported. Skipped items stay in the list with their accuracy and status.',
        );
      }
    } finally {
      setConsolidateBusy(false);
      setBatchProgress(null);
    }
  };

  return (
    <div style={{ padding: '28px 32px', fontFamily: "'Inter', system-ui, sans-serif", maxWidth: 1200 }}>
      <div
        style={{
          marginBottom: 28,
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 16,
          flexWrap: 'wrap',
        }}
      >
        <div>
          <h1 style={{ fontSize: '1.375rem', fontWeight: 700, color: '#111827', margin: '0 0 4px' }}>
            Email Extraction
          </h1>
          <p style={{ fontSize: '0.875rem', color: '#6B7280', margin: 0 }}>
            Sync Outlook ERP attachments, then consolidate ready emails into Consolidated Data.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void runBatchConsolidate()}
          style={btnPrimary}
          disabled={consolidateBusy || (!readyEmailsForBatch.length && !emails.some(e => e.hasExcel))}
          title={
            readyEmailsForBatch.length
              ? `Import up to ${BATCH_LIMIT} ready emails (accuracy ≥ ${MIN_IMPORT_ACCURACY}%)`
              : `Need Excel emails with accuracy ≥ ${MIN_IMPORT_ACCURACY}%`
          }
        >
          {consolidateBusy ? <Loader2 size={15} className="spin" /> : null}
          Proceed to consolidation
          {readyEmailsForBatch.length > 0 ? ` (${Math.min(readyEmailsForBatch.length, BATCH_LIMIT)})` : ''}
          <ArrowRight size={15} />
        </button>
      </div>

      <div
        style={{
          background: 'white',
          border: `1px solid ${BORDER}`,
          borderRadius: 12,
          padding: '16px 20px',
          marginBottom: 16,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 16,
          flexWrap: 'wrap',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: '50%',
              background: autoSyncStatus?.status === 'Running' ? '#10B981' : '#9CA3AF',
              flexShrink: 0,
            }}
          />
          <div>
            <div style={{ fontWeight: 700, color: '#111827', fontSize: '0.875rem' }}>
              {autoSyncStatus?.status === 'Running' ? 'Auto Sync Running' : 'Auto Sync Stopped'}
            </div>
            <div style={{ fontSize: '0.75rem', color: '#6B7280', marginTop: 2 }}>
              {autoSyncStatus?.frequency || 'Every 30 Minutes'}
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap', fontSize: '0.8125rem' }}>
          <div>
            <div style={{ color: '#9CA3AF', fontSize: '0.6875rem', fontWeight: 600, textTransform: 'uppercase' }}>
              Last Sync
            </div>
            <div style={{ color: '#374151', fontWeight: 600, marginTop: 2 }}>
              {formatSyncClock(autoSyncStatus?.last_successful_sync)}
            </div>
          </div>
          <div>
            <div style={{ color: '#9CA3AF', fontSize: '0.6875rem', fontWeight: 600, textTransform: 'uppercase' }}>
              Next Sync
            </div>
            <div style={{ color: '#374151', fontWeight: 600, marginTop: 2 }}>
              {formatSyncTimeOnly(autoSyncStatus?.next_scheduled_sync)}
            </div>
          </div>
        </div>
      </div>

      <div
        style={{
          background: 'white',
          border: `1px solid ${BORDER}`,
          borderRadius: 12,
          padding: '24px 28px',
          marginBottom: 24,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 24,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div
            style={{
              width: 48,
              height: 48,
              borderRadius: 12,
              background: 'rgba(31,95,168,0.08)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Inbox size={24} color={BLUE} />
          </div>
          <div>
            <div style={{ fontWeight: 700, color: '#111827' }}>Outlook Sync</div>
            <div style={{ fontSize: '0.8125rem', color: '#6B7280', lineHeight: 1.55, maxWidth: 760 }}>
              {getSession()?.outlook_sync_permission === 'all' ? (
                <div>Syncs the complete shared Sales Insights mailbox (all unread business documents).</div>
              ) : (
                <div>
                  Syncs the shared Sales Insights mailbox for <strong>your sender email</strong> only
                  {getSession()?.email ? (
                    <>
                      {' '}
                      (<span style={{ color: BLUE, fontWeight: 600 }}>{getSession()?.email}</span>)
                    </>
                  ) : null}
                  .
                </div>
              )}
              <div style={{ marginTop: 10, fontWeight: 700, color: '#111827' }}>Required Subject Format</div>
              <code style={{ fontSize: '0.75rem', background: '#F3F4F6', padding: '1px 6px', borderRadius: 4 }}>
                DISTRIBUTOR | LOCATION | SEGMENT | PERIOD | UNIT
              </code>
              <div style={{ marginTop: 8 }}>Supported Period examples:</div>
              <div>APRIL 2026</div>
              <div>Q1 FY 2026-27</div>
              <div>Q1+Q2 FY 2026-27</div>
              <div>FY 2026-27</div>
              <div style={{ marginTop: 8 }}>
                Supported Units: <strong style={{ color: '#374151' }}>KG</strong>,{' '}
                <strong style={{ color: '#374151' }}>MT</strong>
              </div>
              <div style={{ marginTop: 8 }}>
                Example:{' '}
                <span style={{ color: '#374151' }}>
                  Purandar Investment Pvt. Ltd. | INDIA WEST | RUBBER | Q1+Q2 FY 2026-27 | KG
                </span>
              </div>
              <div style={{ marginTop: 8 }}>
                Supports Excel, PDF and DOCX documents. Extracts up to {BATCH_LIMIT} unread business documents
                per manual sync. Auto-sync runs every 30 minutes into the Email Queue (NEW) — Proceed to
                Consolidation remains required.
              </div>
            </div>
          </div>
        </div>
        <button type="button" onClick={handleExtract} disabled={extracting} style={btnPrimary}>
          {extracting ? <Loader2 size={16} className="spin" /> : <Play size={15} />}
          {extracting ? 'Syncing…' : 'Sync Outlook'}
        </button>
      </div>

      {error && (
        <div
          style={{
            padding: 12,
            marginBottom: 16,
            borderRadius: 8,
            background: 'rgba(217,58,47,0.06)',
            color: RED,
            fontSize: '0.875rem',
          }}
        >
          {error}
        </div>
      )}
      {syncMessage && !error && (
        <div
          style={{
            padding: 12,
            marginBottom: 16,
            borderRadius: 8,
            background: 'rgba(16,185,129,0.08)',
            color: '#065F46',
            fontSize: '0.875rem',
          }}
        >
          {syncMessage}
        </div>
      )}

      <div style={{ background: 'white', border: `1px solid ${BORDER}`, borderRadius: 12, overflow: 'hidden' }}>
        {emails.length === 0 ? (
          <div style={{ padding: 48, textAlign: 'center', color: '#9CA3AF' }}>
            {emailsLoaded
              ? 'No emails yet. Click Sync Outlook to download ERP attachments.'
              : error
                ? 'Emails could not be loaded. See the error above.'
                : 'Loading emails…'}
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
              <thead>
                <tr style={{ background: '#F9FAFB', borderBottom: `1px solid ${BORDER}` }}>
                  {[
                    'Distributor',
                    'Location',
                    'Segment',
                    'Period',
                    'Status',
                    'Extraction',
                    'Accuracy',
                    'Actions',
                  ].map(h => (
                    <th
                      key={h}
                      style={{
                        textAlign: 'left',
                        padding: '12px 14px',
                        fontSize: '0.6875rem',
                        fontWeight: 700,
                        color: '#6B7280',
                        textTransform: 'uppercase',
                        letterSpacing: '0.04em',
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {emails.map(email => {
                  const acc = accuracyMeta(email);
                  const extraction = email.extractionStatus || '—';
                  const selected = selectedEmailId === email.id;
                  return (
                    <tr
                      key={email.id}
                      onClick={() => setSelectedEmailId(email.id)}
                      style={{
                        borderBottom: `1px solid ${BORDER}`,
                        background: selected ? 'rgba(31,95,168,0.06)' : undefined,
                        cursor: 'pointer',
                      }}
                    >
                      <td style={{ padding: '12px 14px', color: '#374151' }}>
                        {email.distributor || email.distributorName || '—'}
                      </td>
                      <td style={{ padding: '12px 14px', color: '#374151' }}>
                        {email.location || '—'}
                      </td>
                      <td style={{ padding: '12px 14px', color: '#374151' }}>
                        {email.segment || '—'}
                      </td>
                      <td style={{ padding: '12px 14px', color: '#6B7280', whiteSpace: 'nowrap' }}>
                        {email.quarter ? formatPeriodDisplay(email.quarter) : '—'}
                      </td>
                      <td style={{ padding: '12px 14px', color: '#374151' }}>
                        {(email.statusLabel || '').toLowerCase() === 'invalid subject' ? (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxWidth: 280 }}>
                            <span
                              style={{
                                display: 'inline-flex',
                                alignSelf: 'flex-start',
                                padding: '3px 8px',
                                borderRadius: 6,
                                fontSize: '0.6875rem',
                                fontWeight: 700,
                                color: '#B45309',
                                background: 'rgba(245,158,11,0.12)',
                              }}
                            >
                              Invalid Subject
                            </span>
                            <span style={{ fontSize: '0.75rem', color: '#92400E', lineHeight: 1.4 }}>
                              {email.errorMessage ||
                                'Expected: DISTRIBUTOR | LOCATION | SEGMENT | PERIOD | UNIT'}
                            </span>
                            <span style={{ fontSize: '0.6875rem', color: '#6B7280' }}>
                              Subject: {email.subject || '—'}
                            </span>
                          </div>
                        ) : isHumanReview(email) ? (
                          <span
                            style={{
                              display: 'inline-flex',
                              padding: '3px 8px',
                              borderRadius: 6,
                              fontSize: '0.6875rem',
                              fontWeight: 700,
                              color: '#92400E',
                              background: 'rgba(245,158,11,0.16)',
                            }}
                          >
                            {humanReviewReason(email)}
                          </span>
                        ) : (email.statusLabel || '').toLowerCase() === 'duplicate upload' ? (
                          <span
                            style={{
                              display: 'inline-flex',
                              padding: '3px 8px',
                              borderRadius: 6,
                              fontSize: '0.6875rem',
                              fontWeight: 700,
                              color: '#374151',
                              background: 'rgba(107,114,128,0.16)',
                            }}
                          >
                            Duplicate Upload
                          </span>
                        ) : (email.statusLabel || '').toLowerCase() === 'failed' ? (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxWidth: 280 }}>
                            <span>Failed</span>
                            <span style={{ fontSize: '0.75rem', color: '#B91C1C', lineHeight: 1.4 }}>
                              {email.errorMessage || 'Could not detect structured sales table'}
                            </span>
                          </div>
                        ) : (
                          email.statusLabel || 'New'
                        )}
                      </td>
                      <td style={{ padding: '12px 14px', color: extraction === 'Failed' ? RED : '#374151' }}>
                        {extraction}
                      </td>
                      <td style={{ padding: '12px 14px' }}>
                        <div
                          title={acc.detail}
                          style={{
                            display: 'inline-flex',
                            flexDirection: 'column',
                            gap: 2,
                            padding: '5px 10px',
                            borderRadius: 8,
                            background: acc.bg,
                            color: acc.color,
                            maxWidth: 200,
                          }}
                        >
                          <span style={{ fontSize: '0.75rem', fontWeight: 700 }}>{acc.label}</span>
                          <span style={{ fontSize: '0.6875rem', fontWeight: 500, opacity: 0.9 }}>
                            {acc.detail}
                          </span>
                        </div>
                        {email.hasExcel !== false && (
                          <div style={{ display: 'inline-flex', gap: 4, marginLeft: 6, verticalAlign: 'top' }}>
                            <button
                              type="button"
                              title="Product Standardization Workbench"
                              aria-label="Open Product Standardization Workbench"
                              style={iconBtn}
                              onClick={event => {
                                event.stopPropagation();
                                setStandardization({ email, mode: 'workbench' });
                              }}
                            >
                              <Package size={15} />
                            </button>
                            <button
                              type="button"
                              title="Customer Standardization Workbench"
                              aria-label="Open Customer Standardization Workbench"
                              style={iconBtn}
                              onClick={event => {
                                event.stopPropagation();
                                setStandardization({ email, mode: 'customer' });
                              }}
                            >
                              <UserRound size={15} />
                            </button>
                            <button
                              type="button"
                              title="Extracted data with standardized products"
                              aria-label="Open extracted data with standardized products"
                              style={iconBtn}
                              onClick={event => {
                                event.stopPropagation();
                                setStandardization({ email, mode: 'data' });
                              }}
                            >
                              <InfoIcon size={15} />
                            </button>
                          </div>
                        )}
                      </td>
                      <td style={{ padding: '12px 14px' }}>
                        <div style={{ display: 'flex', gap: 6, flexWrap: 'nowrap' }}>
                          {isHumanReview(email) && (
                            <button
                              type="button"
                              style={{
                                ...btnSecondary,
                                padding: '5px 8px',
                                fontSize: '0.75rem',
                                color: BLUE,
                              }}
                              onClick={event => {
                                event.stopPropagation();
                                openReview(email);
                              }}
                            >
                              Review
                            </button>
                          )}
                          {isAdmin && (
                            <button
                              type="button"
                              style={{
                                ...btnSecondary,
                                padding: '5px 8px',
                                fontSize: '0.75rem',
                                color: RED,
                              }}
                              onClick={async () => {
                                await EmailsService.deleteEmailRecord(email.id);
                                await refreshEmails();
                              }}
                            >
                              <Trash2 size={12} />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {consolidateOpen && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(15,23,42,0.45)',
            zIndex: 1000,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 16,
          }}
          onClick={() => {
            if (!consolidateBusy) {
              setConsolidateOpen(false);
              setBatchResults([]);
              setConsolidateError(null);
            }
          }}
        >
          <div
            style={{
              background: 'white',
              borderRadius: 12,
              width: '100%',
              maxWidth: 520,
              padding: 24,
              boxShadow: '0 20px 50px rgba(0,0,0,0.2)',
            }}
            onClick={e => e.stopPropagation()}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: 12,
              }}
            >
              <h2 style={{ margin: 0, fontSize: '1.125rem', fontWeight: 700, color: '#111827' }}>
                Batch consolidation
              </h2>
              <button
                type="button"
                style={{ border: 'none', background: 'transparent', cursor: consolidateBusy ? 'default' : 'pointer' }}
                disabled={consolidateBusy}
                onClick={() => {
                  setConsolidateOpen(false);
                  setBatchResults([]);
                  setConsolidateError(null);
                }}
              >
                <X size={18} />
              </button>
            </div>
            <p style={{ margin: '0 0 16px', fontSize: '0.875rem', color: '#6B7280' }}>
              Importing up to {BATCH_LIMIT} ready emails (Excel + accuracy ≥ {MIN_IMPORT_ACCURACY}% +
              matched distributor). Emails that are not ready stay in the list.
            </p>

            {batchProgress && (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  color: BLUE,
                  marginBottom: 14,
                  fontSize: '0.875rem',
                  fontWeight: 600,
                }}
              >
                <Loader2 size={16} className="spin" />
                Importing {batchProgress.current}/{batchProgress.total}: {batchProgress.label}
              </div>
            )}

            {consolidateError && (
              <div
                style={{
                  padding: 12,
                  marginBottom: 12,
                  borderRadius: 8,
                  background: 'rgba(217,58,47,0.08)',
                  color: RED,
                  fontSize: '0.8125rem',
                }}
              >
                {consolidateError}
              </div>
            )}

            {batchResults.length > 0 && (
              <div
                style={{
                  maxHeight: 260,
                  overflow: 'auto',
                  border: `1px solid ${BORDER}`,
                  borderRadius: 8,
                  marginBottom: 14,
                }}
              >
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
                  <thead>
                    <tr style={{ background: '#F3F4F6' }}>
                      <th style={th}>Email</th>
                      <th style={th}>Status</th>
                      <th style={th}>Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {batchResults.map(r => (
                      <tr key={r.emailId} style={{ borderBottom: `1px solid ${BORDER}` }}>
                        <td style={{ ...td, maxWidth: 160 }}>{r.subject || `#${r.emailId}`}</td>
                        <td
                          style={{
                            ...td,
                            fontWeight: 700,
                            color:
                              r.status === 'ok'
                                ? '#059669'
                                : r.status === 'skipped'
                                  ? '#B45309'
                                  : RED,
                          }}
                        >
                          {r.status}
                        </td>
                        <td style={{ ...td, whiteSpace: 'pre-line' }}>{r.detail}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {!consolidateBusy && (
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                <button
                  type="button"
                  style={btnSecondary}
                  onClick={() => {
                    setConsolidateOpen(false);
                    setBatchResults([]);
                    setConsolidateError(null);
                  }}
                >
                  Close
                </button>
                {batchResults.some(r => r.status === 'ok') && (
                  <button
                    type="button"
                    style={btnPrimary}
                    onClick={() => {
                      setConsolidateOpen(false);
                      navigate('/consolidated-data');
                    }}
                  >
                    Open Consolidated Data
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {review && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(15,23,42,0.45)',
            zIndex: 1000,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 16,
          }}
          onClick={() => {
            if (!review.busy) closeReview();
          }}
        >
          <div
            style={{
              background: 'white',
              borderRadius: 12,
              width: '100%',
              maxWidth: 640,
              padding: 24,
              boxShadow: '0 20px 50px rgba(0,0,0,0.2)',
            }}
            onClick={event => event.stopPropagation()}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: 12,
              }}
            >
              <h2 style={{ margin: 0, fontSize: '1.125rem', fontWeight: 700, color: '#111827' }}>
                Human Review
              </h2>
              <button
                type="button"
                style={{ border: 'none', background: 'transparent', cursor: 'pointer' }}
                disabled={review.busy}
                onClick={closeReview}
              >
                <X size={18} />
              </button>
            </div>

            {review.busy && !review.summary && (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  color: BLUE,
                  fontSize: '0.875rem',
                }}
              >
                <Loader2 size={16} className="spin" /> Loading…
              </div>
            )}

            {review.summary && (
              <>
                <div
                  style={{
                    fontSize: '0.75rem',
                    fontWeight: 700,
                    color: '#6B7280',
                    textTransform: 'uppercase',
                    marginBottom: 8,
                  }}
                >
                  Incoming
                </div>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
                    gap: 12,
                    padding: 12,
                    border: `1px solid ${BORDER}`,
                    borderRadius: 8,
                    marginBottom: 16,
                  }}
                >
                  <Info label="Distributor" value={review.summary.incoming.distributor || '-'} />
                  <Info label="Location" value={review.summary.incoming.location || '-'} />
                  <Info
                    label="Segment"
                    value={review.summary.incoming.segment || '-'}
                    valueColor={BLUE}
                  />
                  <Info label="Period" value={reviewPeriodLabel(review.summary)} />
                </div>

                <div
                  style={{
                    fontSize: '0.75rem',
                    fontWeight: 700,
                    color: '#6B7280',
                    textTransform: 'uppercase',
                    marginBottom: 8,
                  }}
                >
                  Existing in Consolidated Data
                </div>
                {(review.summary.existing || []).length === 0 ? (
                  <div style={{ fontSize: '0.8125rem', color: '#6B7280', marginBottom: 16 }}>
                    No consolidated records for this distributor in these periods.
                  </div>
                ) : (
                  <div
                    style={{
                      border: `1px solid ${BORDER}`,
                      borderRadius: 8,
                      marginBottom: 16,
                      maxHeight: 220,
                      overflow: 'auto',
                    }}
                  >
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
                      <thead>
                        <tr style={{ background: '#F3F4F6' }}>
                          <th style={th}>Segment</th>
                          <th style={th}>Location</th>
                          <th style={th}>Period</th>
                          <th style={{ ...th, textAlign: 'right' }}>Records</th>
                          <th style={{ ...th, textAlign: 'right' }}>Qty (MT)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {review.summary.existing.map(row => (
                          <tr
                            key={`${row.segment}|${row.location}|${row.period}`}
                            style={{ borderBottom: `1px solid ${BORDER}` }}
                          >
                            <td style={{ ...td, fontWeight: 700 }}>{row.segment || '-'}</td>
                            <td style={td}>{row.location || '-'}</td>
                            <td style={td}>{formatPeriodDisplay(row.period) || '-'}</td>
                            <td style={{ ...td, textAlign: 'right' }}>{row.records}</td>
                            <td style={{ ...td, textAlign: 'right' }}>
                              {Number(row.quantity_mt).toFixed(3)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {review.summary.low_confidence && (
                  <p style={{ margin: '0 0 8px', fontSize: '0.8125rem', color: '#92400E', lineHeight: 1.5 }}>
                    Low confidence: the workbook mapping scored below 70%. Check the mapped data before
                    choosing ADD THIS.
                  </p>
                )}
                <p style={{ margin: '0 0 16px', fontSize: '0.8125rem', color: '#6B7280', lineHeight: 1.5 }}>
                  {review.summary.same_segment_exists
                    ? 'This segment already has records for this period. ADD THIS stores only rows that do not exist yet; existing rows are not overwritten.'
                    : 'A different segment exists for this period. ADD THIS stores the incoming segment separately without merging.'}{' '}
                  DELETE rejects the incoming data and keeps existing data.
                </p>
              </>
            )}

            {review.error && (
              <div
                style={{
                  padding: 12,
                  marginBottom: 12,
                  borderRadius: 8,
                  background: 'rgba(217,58,47,0.08)',
                  color: RED,
                  fontSize: '0.8125rem',
                }}
              >
                {review.error}
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button type="button" style={btnSecondary} disabled={review.busy} onClick={closeReview}>
                Cancel
              </button>
              <button
                type="button"
                style={{ ...btnSecondary, color: RED, borderColor: RED }}
                disabled={review.busy || !review.summary?.reviewable}
                onClick={() => void submitReview('DELETE')}
              >
                <Trash2 size={14} />
                DELETE
              </button>
              <button
                type="button"
                style={{ ...btnPrimary, background: TEAL }}
                disabled={review.busy || !review.summary?.reviewable}
                onClick={() => void submitReview('ADD')}
              >
                {review.busy && review.summary ? <Loader2 size={14} className="spin" /> : null}
                ADD THIS
              </button>
            </div>
          </div>
        </div>
      )}

      {standardization?.mode === 'workbench' && (
        <ProductWorkbenchModal
          emailId={standardization.email.id}
          title={standardization.email.subject}
          initialAttachmentId={standardization.attachmentId}
          onClose={() => setStandardization(null)}
          onSwitchToCustomers={attachmentId => setStandardization(s => (s ? { ...s, mode: 'customer', attachmentId } : s))}
        />
      )}
      {standardization?.mode === 'customer' && (
        <CustomerWorkbenchModal
          emailId={standardization.email.id}
          title={standardization.email.subject}
          initialAttachmentId={standardization.attachmentId}
          onClose={() => setStandardization(null)}
          onSwitchToProducts={attachmentId => setStandardization(s => (s ? { ...s, mode: 'workbench', attachmentId } : s))}
        />
      )}
      {standardization?.mode === 'data' && (
        <StandardizedDataModal
          emailId={standardization.email.id}
          title={standardization.email.subject}
          onClose={() => setStandardization(null)}
        />
      )}
    </div>
  );
}

function Info({
  label,
  value,
  valueColor,
}: {
  label: string;
  value: string;
  valueColor?: string;
}) {
  return (
    <div>
      <div style={{ fontSize: '0.6875rem', color: '#6B7280', fontWeight: 600, marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ fontSize: '0.875rem', fontWeight: 700, color: valueColor || '#111827' }}>{value}</div>
    </div>
  );
}

const th: CSSProperties = {
  textAlign: 'left',
  padding: '8px 10px',
  fontSize: '0.6875rem',
  fontWeight: 700,
  color: '#6B7280',
};

const iconBtn: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: 28,
  height: 28,
  border: `1px solid ${BORDER}`,
  borderRadius: 6,
  background: 'white',
  color: BLUE,
  cursor: 'pointer',
};

const td: CSSProperties = {
  padding: '8px 10px',
  color: '#374151',
  verticalAlign: 'middle',
};

const labelStyle: CSSProperties = {
  display: 'block',
  fontSize: '0.75rem',
  fontWeight: 600,
  color: '#374151',
  marginBottom: 6,
};

const selectStyle: CSSProperties = {
  width: '100%',
  padding: '8px 10px',
  borderRadius: 8,
  border: `1px solid ${BORDER}`,
  fontSize: '0.8125rem',
  fontFamily: 'inherit',
};
