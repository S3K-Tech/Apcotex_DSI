import { useEffect, useState, type CSSProperties, type FormEvent } from 'react';
import { Search, X } from 'lucide-react';
import { ApiError } from '../../api';
import { StatusBanner } from '../../components/common/StatusBanner';
import { BLUE, BORDER } from '../../constants/theme';
import {
  ProductStandardizationService,
  type MatchStatus,
  type StandardizedPreview,
} from '../../services/productStandardization.service';

type Props = { emailId: number; title: string; onClose: () => void };

const PAGE_SIZE = 50;

const COLUMN_LABELS: Record<string, string> = {
  original_product: 'Original Product',
  standardized_product: 'Standardized Product',
  standardization_status: 'Standardization Status',
  suggested_product: 'Suggestion (not approved)',
  standardization_source: 'Standardization Source',
  customer_name: 'Customer',
  sales_quantity: 'Quantity',
  sales_quantity_mt: 'Quantity (MT)',
  original_unit: 'Unit',
  source_month: 'Month',
  period: 'Period',
};

const STATUS_LABEL: Record<string, { text: string; color: string }> = {
  AUTO_MATCHED: { text: 'Standardized', color: '#065F46' },
  NEEDS_REVIEW: { text: 'Needs review', color: '#92400E' },
  UNMAPPED: { text: 'Unmapped', color: '#991B1B' },
};

const cell: CSSProperties = {
  padding: '7px 10px',
  borderBottom: `1px solid ${BORDER}`,
  fontSize: '0.8125rem',
  color: '#1F2937',
  textAlign: 'left',
  whiteSpace: 'nowrap',
};

function label(col: string): string {
  return COLUMN_LABELS[col] || col.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3);
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export function StandardizedDataModal({ emailId, title, onClose }: Props) {
  const [data, setData] = useState<StandardizedPreview | null>(null);
  const [attachmentId, setAttachmentId] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<MatchStatus | ''>('');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    ProductStandardizationService.preview(emailId, {
      attachment_id: attachmentId,
      page,
      page_size: PAGE_SIZE,
      status,
      search: query,
    })
      .then(res => {
        if (!cancelled) setData(res);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setData(null);
        setError(err instanceof ApiError ? err.message : 'Extracted data could not be loaded.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [emailId, attachmentId, page, status, query]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setPage(1);
    setQuery(search.trim());
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Extracted data with standardized products"
      style={{ position: 'fixed', inset: 0, background: 'rgba(17,24,39,0.45)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{ background: 'white', borderRadius: 12, width: 'min(1300px, 100%)', maxHeight: '92vh', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 50px rgba(0,0,0,0.2)' }}
      >
        <div style={{ padding: '16px 20px', borderBottom: `1px solid ${BORDER}`, display: 'flex', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <h2 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 700, color: BLUE }}>Extracted Data</h2>
            <div style={{ fontSize: '0.75rem', color: '#6B7280', marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {title}
              {data ? ` · ${data.attachment_name} · ${data.distributor?.name || 'Distributor not resolved'}` : ''}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {data && data.attachments.length > 1 && (
              <select
                value={attachmentId ?? data.attachment_id}
                onChange={e => {
                  setAttachmentId(Number(e.target.value));
                  setPage(1);
                }}
                style={{ padding: '6px 8px', borderRadius: 6, border: `1px solid ${BORDER}`, fontSize: '0.8125rem' }}
                aria-label="Attachment"
              >
                {data.attachments.map(a => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            )}
            <button type="button" aria-label="Close" style={{ border: 'none', background: 'transparent', cursor: 'pointer' }} onClick={onClose}>
              <X size={18} />
            </button>
          </div>
        </div>

        <div style={{ padding: '12px 20px', display: 'flex', flexDirection: 'column', gap: 10, overflow: 'hidden' }}>
          {data && !data.master.configured && (
            <StatusBanner type="info" message="FG Master is not configured in this environment. Only approved mappings are shown as standardized." />
          )}
          <form onSubmit={submit} style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <select
              value={status}
              onChange={e => {
                setStatus(e.target.value as MatchStatus | '');
                setPage(1);
              }}
              style={{ padding: '6px 8px', borderRadius: 6, border: `1px solid ${BORDER}`, fontSize: '0.8125rem' }}
              aria-label="Standardization status"
            >
              <option value="">All statuses</option>
              <option value="AUTO_MATCHED">Standardized</option>
              <option value="NEEDS_REVIEW">Needs review</option>
              <option value="UNMAPPED">Unmapped</option>
            </select>
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search rows"
              style={{ padding: '6px 10px', borderRadius: 6, border: `1px solid ${BORDER}`, fontSize: '0.8125rem', minWidth: 220 }}
            />
            <button type="submit" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '6px 12px', border: 'none', borderRadius: 6, background: BLUE, color: 'white', fontSize: '0.8125rem', fontWeight: 600, cursor: 'pointer' }}>
              <Search size={13} /> Search
            </button>
            <span style={{ fontSize: '0.75rem', color: '#6B7280' }}>
              "Standardized Product" shows only approved mappings and exact FG Master matches.
            </span>
          </form>

          {error && <StatusBanner error={error} />}
          {loading && !data ? (
            <StatusBanner loading loadingText="Loading extracted rows…" />
          ) : data ? (
            <>
              <div style={{ overflow: 'auto', border: `1px solid ${BORDER}`, borderRadius: 10, maxHeight: '58vh', opacity: loading ? 0.6 : 1 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }} aria-label="Extracted rows">
                  <thead style={{ position: 'sticky', top: 0, background: '#F9FAFB' }}>
                    <tr>
                      <th style={{ ...cell, fontWeight: 600 }}>#</th>
                      {data.columns.map(c => (
                        <th key={c} style={{ ...cell, fontWeight: 600, color: '#374151' }}>{label(c)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.length === 0 ? (
                      <tr>
                        <td colSpan={data.columns.length + 1} style={{ ...cell, textAlign: 'center', color: '#6B7280', padding: '24px 10px' }}>
                          No rows match.
                        </td>
                      </tr>
                    ) : (
                      data.rows.map((row, idx) => (
                        <tr key={idx}>
                          <td style={{ ...cell, color: '#9CA3AF' }}>{(data.page - 1) * data.page_size + idx + 1}</td>
                          {data.columns.map(c => {
                            if (c === 'standardization_status') {
                              const s = STATUS_LABEL[String(row[c])] || { text: display(row[c]), color: '#374151' };
                              return (
                                <td key={c} style={{ ...cell, fontWeight: 600, color: s.color }}>{s.text}</td>
                              );
                            }
                            return (
                              <td key={c} style={{ ...cell, fontWeight: c === 'standardized_product' ? 600 : undefined }}>
                                {display(row[c])}
                              </td>
                            );
                          })}
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: '0.8125rem', color: '#6B7280' }}>
                <button type="button" disabled={data.page <= 1 || loading} onClick={() => setPage(p => p - 1)}>
                  Previous
                </button>
                <span>
                  Page {data.page} of {data.pages} · {data.total} row{data.total === 1 ? '' : 's'}
                </span>
                <button type="button" disabled={data.page >= data.pages || loading} onClick={() => setPage(p => p + 1)}>
                  Next
                </button>
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
