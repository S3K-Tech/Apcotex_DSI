import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { Check, Loader2, Package, Pencil, RefreshCw, UserRound, X } from 'lucide-react';
import { ApiError } from '../../api';
import { StatusBanner } from '../../components/common/StatusBanner';
import { Pagination } from '../../components/ui/Pagination';
import { BLUE, BORDER, GREEN, RED, TEAL } from '../../constants/theme';
import {
  ProductStandardizationService,
  type ApprovalItem,
  type WorkbenchData,
  type WorkbenchItem,
} from '../../services/productStandardization.service';

const PAGE_SIZE = 100;

function cellText(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  return String(value);
}

type Props = {
  emailId: number;
  title: string;
  onClose: () => void;
  initialAttachmentId?: number | null;
  onSwitchToCustomers?: (attachmentId: number | null) => void;
};

type Filter = 'all' | WorkbenchItem['bucket'];

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'awaiting_review', label: 'Awaiting review' },
  { key: 'unmapped', label: 'Unmapped' },
  { key: 'auto_standardized', label: 'Auto-standardized' },
  { key: 'approved', label: 'Approved' },
];

const STATUS_STYLE: Record<WorkbenchItem['bucket'], { label: string; color: string; bg: string }> = {
  approved: { label: 'Approved', color: '#065F46', bg: 'rgba(16,185,129,0.14)' },
  auto_standardized: { label: 'Auto-matched', color: '#1E3A8A', bg: 'rgba(59,130,246,0.12)' },
  awaiting_review: { label: 'Needs review', color: '#92400E', bg: 'rgba(245,158,11,0.16)' },
  unmapped: { label: 'Unmapped', color: '#991B1B', bg: 'rgba(239,68,68,0.12)' },
};

const cell: CSSProperties = {
  padding: '8px 10px',
  borderBottom: `1px solid ${BORDER}`,
  fontSize: '0.8125rem',
  color: '#1F2937',
  textAlign: 'left',
  verticalAlign: 'middle',
};

const smallBtn = (bg: string, disabled = false): CSSProperties => ({
  display: 'inline-flex',
  alignItems: 'center',
  gap: 4,
  padding: '5px 10px',
  border: 'none',
  borderRadius: 6,
  background: disabled ? '#D1D5DB' : bg,
  color: 'white',
  fontSize: '0.75rem',
  fontWeight: 600,
  cursor: disabled ? 'not-allowed' : 'pointer',
});

function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message || fallback;
  return fallback;
}

function isLocked(item: WorkbenchItem): boolean {
  return item.bucket === 'approved' || item.bucket === 'auto_standardized';
}

function MasterAutocomplete({
  value,
  onChange,
  alternatives,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  alternatives: { description: string; confidence: number }[];
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const ctrl = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      ProductStandardizationService.searchMaster(value, 20, ctrl.signal)
        .then(res => setResults(res.items))
        .catch(() => setResults([]))
        .finally(() => setLoading(false));
    }, 200);
    return () => {
      window.clearTimeout(timer);
      ctrl.abort();
    };
  }, [value, open]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const options = useMemo(() => {
    const seen = new Set<string>();
    const out: { text: string; hint?: string }[] = [];
    for (const a of alternatives) {
      if (seen.has(a.description)) continue;
      seen.add(a.description);
      out.push({ text: a.description, hint: `${Math.round(a.confidence)}%` });
    }
    for (const r of results) {
      if (seen.has(r)) continue;
      seen.add(r);
      out.push({ text: r });
    }
    return out;
  }, [alternatives, results]);

  const isCustom = value.trim() !== '' && !options.some(o => o.text.toLowerCase() === value.trim().toLowerCase());

  return (
    <div ref={boxRef} style={{ position: 'relative', minWidth: 240 }}>
      <input
        value={value}
        disabled={disabled}
        placeholder="Search FG Master or type a custom value"
        onFocus={() => setOpen(true)}
        onChange={e => {
          onChange(e.target.value);
          setOpen(true);
        }}
        style={{
          width: '100%',
          padding: '6px 8px',
          fontSize: '0.8125rem',
          border: `1px solid ${BORDER}`,
          borderRadius: 6,
          background: disabled ? '#F3F4F6' : 'white',
          boxSizing: 'border-box',
        }}
      />
      {isCustom && !open && (
        <div style={{ fontSize: '0.6875rem', color: '#92400E', marginTop: 2 }}>Custom value (saved as a mapping, not added to the master)</div>
      )}
      {open && !disabled && (
        <div
          role="listbox"
          style={{
            position: 'absolute',
            zIndex: 20,
            top: '100%',
            left: 0,
            right: 0,
            maxHeight: 220,
            overflowY: 'auto',
            background: 'white',
            border: `1px solid ${BORDER}`,
            borderRadius: 6,
            boxShadow: '0 8px 20px rgba(0,0,0,0.12)',
            marginTop: 2,
          }}
        >
          {loading && options.length === 0 && (
            <div style={{ padding: '6px 10px', fontSize: '0.75rem', color: '#6B7280' }}>Searching…</div>
          )}
          {!loading && options.length === 0 && (
            <div style={{ padding: '6px 10px', fontSize: '0.75rem', color: '#6B7280' }}>No master match. The typed value will be saved as custom.</div>
          )}
          {options.map(o => (
            <div
              key={o.text}
              role="option"
              aria-selected={o.text === value}
              onMouseDown={e => {
                e.preventDefault();
                onChange(o.text);
                setOpen(false);
              }}
              style={{
                padding: '6px 10px',
                fontSize: '0.8125rem',
                cursor: 'pointer',
                display: 'flex',
                justifyContent: 'space-between',
                gap: 8,
                background: o.text === value ? 'rgba(31,95,168,0.08)' : undefined,
              }}
            >
              <span>{o.text}</span>
              {o.hint && <span style={{ color: '#6B7280', fontSize: '0.75rem' }}>{o.hint}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function ProductWorkbenchModal({ emailId, title, onClose, initialAttachmentId = null, onSwitchToCustomers }: Props) {
  const [data, setData] = useState<WorkbenchData | null>(null);
  const [attachmentId, setAttachmentId] = useState<number | null>(initialAttachmentId);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [filter, setFilter] = useState<Filter>('all');
  const [page, setPage] = useState(1);

  const load = useCallback(
    async (refresh = false) => {
      setLoading(true);
      setError(null);
      try {
        const res = await ProductStandardizationService.workbench(emailId, attachmentId, refresh);
        setData(res);
        const next: Record<string, string> = {};
        res.items.forEach(i => {
          next[i.raw_product] = i.suggested_product || '';
        });
        setValues(next);
        setEditing({});
        setSelected({});
      } catch (err) {
        setData(null);
        setError(errorText(err, 'The workbench could not be loaded.'));
      } finally {
        setLoading(false);
      }
    },
    [emailId, attachmentId]
  );

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setPage(1);
  }, [filter, attachmentId]);

  const canApprove = Boolean(data?.distributor);
  const itemsByRaw = useMemo(() => {
    const map = new Map<string, WorkbenchItem>();
    (data?.items || []).forEach(i => map.set(i.raw_product, i));
    return map;
  }, [data]);
  // One table row per source row, in source order; filters use the row's product status.
  const visibleRows = useMemo(
    () =>
      (data?.table?.rows || []).filter(r => {
        if (filter === 'all') return true;
        const item = itemsByRaw.get(r.raw_product);
        return item !== undefined && item.bucket === filter;
      }),
    [data, filter, itemsByRaw]
  );
  const visible = useMemo(() => {
    const seen = new Set<string>();
    const out: WorkbenchItem[] = [];
    visibleRows.forEach(r => {
      const item = itemsByRaw.get(r.raw_product);
      if (item && !seen.has(item.raw_product)) {
        seen.add(item.raw_product);
        out.push(item);
      }
    });
    return out;
  }, [visibleRows, itemsByRaw]);
  const pages = Math.max(1, Math.ceil(visibleRows.length / PAGE_SIZE));
  const pageRows = visibleRows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const selectable = (i: WorkbenchItem) => canApprove && (!isLocked(i) || editing[i.raw_product]);
  const selectedItems = visible.filter(i => selected[i.raw_product] && selectable(i));
  const allSelectable = visible.filter(selectable);
  const allChecked = allSelectable.length > 0 && allSelectable.every(i => selected[i.raw_product]);

  const approve = async (items: WorkbenchItem[]) => {
    if (!data) return;
    const payload: ApprovalItem[] = [];
    const empty: string[] = [];
    for (const item of items) {
      const value = (values[item.raw_product] || '').trim();
      if (!value) empty.push(item.raw_product);
      else payload.push({ raw_product: item.raw_product, standard_product: value, confidence: item.confidence });
    }
    if (empty.length) {
      setError(`Choose a standardized product before approving: ${empty.slice(0, 5).join(', ')}${empty.length > 5 ? '…' : ''}`);
      return;
    }
    if (!payload.length) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const res = await ProductStandardizationService.approve(data.email_id, data.attachment_id, payload);
      const conflicts = res.global_conflicts
        ? ` ${res.global_conflicts} differ from an existing global mapping and were saved for this distributor only.`
        : '';
      setMessage(`${res.approved} product mapping(s) approved.${conflicts} Approvals apply to new imports only.`);
      await load();
    } catch (err) {
      setError(errorText(err, 'Approval failed.'));
    } finally {
      setBusy(false);
    }
  };

  const m = data?.metrics;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Product Standardization Workbench"
      style={{ position: 'fixed', inset: 0, background: 'rgba(17,24,39,0.45)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}
      onClick={() => !busy && onClose()}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{ background: 'white', borderRadius: 12, width: 'min(1200px, 100%)', maxHeight: '92vh', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 50px rgba(0,0,0,0.2)' }}
      >
        <div style={{ padding: '16px 20px', borderBottom: `1px solid ${BORDER}`, display: 'flex', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <h2 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 700, color: BLUE }}>Product Standardization Workbench</h2>
            <div style={{ fontSize: '0.75rem', color: '#6B7280', marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {title}
              {data ? ` · ${data.attachment_name} · ${data.distributor?.name || 'Distributor not resolved'}` : ''}
              {data?.master.configured ? ` · FG Master v${data.master.version_number}` : ''}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {onSwitchToCustomers && (
              <div style={{ display: 'inline-flex', gap: 4 }} role="tablist" aria-label="Standardization workbench">
                <button
                  type="button"
                  role="tab"
                  aria-selected
                  style={{ ...smallBtn('white'), color: BLUE, border: `1px solid ${BLUE}`, background: 'rgba(31,95,168,0.08)', cursor: 'default' }}
                >
                  <Package size={13} /> Products
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={false}
                  disabled={busy}
                  style={{ ...smallBtn('white'), color: '#374151', border: `1px solid ${BORDER}` }}
                  onClick={() => onSwitchToCustomers(data?.attachment_id ?? attachmentId)}
                >
                  <UserRound size={13} /> Customers
                </button>
              </div>
            )}
            {data && data.attachments.length > 1 && (
              <select
                value={attachmentId ?? data.attachment_id}
                onChange={e => setAttachmentId(Number(e.target.value))}
                style={{ padding: '6px 8px', borderRadius: 6, border: `1px solid ${BORDER}`, fontSize: '0.8125rem' }}
                aria-label="Attachment"
              >
                {data.attachments.map(a => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            )}
            <button type="button" title="Re-extract attachment" style={{ ...smallBtn('#6B7280', loading || busy) }} disabled={loading || busy} onClick={() => void load(true)}>
              <RefreshCw size={13} /> Re-extract
            </button>
            <button type="button" aria-label="Close" style={{ border: 'none', background: 'transparent', cursor: 'pointer' }} onClick={onClose} disabled={busy}>
              <X size={18} />
            </button>
          </div>
        </div>

        <div style={{ padding: '14px 20px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
          {error && <StatusBanner error={error} onClose={() => setError(null)} />}
          {message && <StatusBanner message={message} type="success" onClose={() => setMessage(null)} />}
          {data && !data.master.configured && (
            <StatusBanner
              type="info"
              message="FG Master is not configured in this environment. Products keep their original names; approved mappings still apply. A Super Admin can upload the master from Master Data."
            />
          )}
          {data && !data.distributor && (
            <StatusBanner type="error" message="The distributor for this email could not be resolved, so approvals are disabled. Fix the email subject first." />
          )}

          {loading && !data ? (
            <StatusBanner loading loadingText="Extracting products…" />
          ) : data && m ? (
            <>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {[
                  { label: 'Distinct products', value: m.total_distinct, color: '#111827' },
                  { label: 'Auto-standardized', value: m.auto_standardized, color: BLUE },
                  { label: 'Awaiting review', value: m.awaiting_review, color: '#B45309' },
                  { label: 'Unmapped', value: m.unmapped, color: RED },
                  { label: 'Approved', value: m.approved, color: GREEN },
                ].map(s => (
                  <div key={s.label} style={{ flex: 1, minWidth: 130, border: `1px solid ${BORDER}`, borderRadius: 10, padding: '10px 12px' }}>
                    <div style={{ fontSize: '0.7rem', color: '#6B7280', fontWeight: 600 }}>{s.label}</div>
                    <div style={{ fontSize: '1.2rem', fontWeight: 700, color: s.color }}>{s.value}</div>
                  </div>
                ))}
              </div>

              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {FILTERS.map(f => (
                    <button
                      key={f.key}
                      type="button"
                      onClick={() => setFilter(f.key)}
                      style={{
                        padding: '4px 10px',
                        borderRadius: 999,
                        border: `1px solid ${filter === f.key ? BLUE : BORDER}`,
                        background: filter === f.key ? 'rgba(31,95,168,0.08)' : 'white',
                        color: filter === f.key ? BLUE : '#374151',
                        fontSize: '0.75rem',
                        fontWeight: 600,
                        cursor: 'pointer',
                      }}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  style={smallBtn(TEAL, busy || selectedItems.length === 0)}
                  disabled={busy || selectedItems.length === 0}
                  onClick={() => void approve(selectedItems)}
                >
                  {busy ? <Loader2 size={13} className="spin" /> : <Check size={13} />} Approve Selected ({selectedItems.length})
                </button>
              </div>

              <div style={{ border: `1px solid ${BORDER}`, borderRadius: 10, overflow: 'visible' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }} aria-label="Products to standardize">
                  <thead>
                    <tr style={{ background: '#F9FAFB' }}>
                      <th style={{ ...cell, width: 32 }}>
                        <input
                          type="checkbox"
                          aria-label="Select all"
                          checked={allChecked}
                          disabled={!allSelectable.length}
                          onChange={e => {
                            const next = { ...selected };
                            allSelectable.forEach(i => {
                              next[i.raw_product] = e.target.checked;
                            });
                            setSelected(next);
                          }}
                        />
                      </th>
                      {['Customer', 'Raw Product Entry', 'Sales Quantity', 'Suggested Master Product', 'Confidence', 'Workflow Status', 'Action'].map(h => (
                        <th key={h} style={{ ...cell, fontWeight: 600, color: '#374151', whiteSpace: 'nowrap' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {pageRows.length === 0 ? (
                      <tr>
                        <td colSpan={8} style={{ ...cell, textAlign: 'center', color: '#6B7280', padding: '24px 10px' }}>
                          No products in this view.
                        </td>
                      </tr>
                    ) : (
                      pageRows.map(row => {
                        const item = itemsByRaw.get(row.raw_product);
                        const originalCells = (
                          <>
                            <td style={cell}>
                              <span style={{ whiteSpace: 'pre-wrap' }}>{row.customer_display || '—'}</span>
                              {row.customer_standardized && (
                                <div style={{ fontSize: '0.6875rem', color: '#6B7280' }}>Original: {row.customer_raw}</div>
                              )}
                            </td>
                            <td style={{ ...cell, fontWeight: 600, whiteSpace: 'pre-wrap' }}>{row.raw_product || '—'}</td>
                            <td style={{ ...cell, textAlign: 'right' }}>{cellText(row.quantity)}</td>
                          </>
                        );
                        const rowKey = String(row.source_row);
                        if (!item) {
                          return (
                            <tr key={rowKey}>
                              <td style={cell} />
                              {originalCells}
                              <td colSpan={4} style={{ ...cell, color: '#9CA3AF' }}>—</td>
                            </tr>
                          );
                        }
                        const locked = isLocked(item) && !editing[item.raw_product];
                        const st = STATUS_STYLE[item.bucket];
                        const value = values[item.raw_product] || '';
                        return (
                          <tr key={rowKey}>
                            <td style={cell}>
                              <input
                                type="checkbox"
                                aria-label={`Select ${item.raw_product}`}
                                disabled={!selectable(item)}
                                checked={Boolean(selected[item.raw_product]) && selectable(item)}
                                onChange={e => setSelected(s => ({ ...s, [item.raw_product]: e.target.checked }))}
                              />
                            </td>
                            {originalCells}
                            <td style={cell}>
                              {locked ? (
                                <div>
                                  <div style={{ fontWeight: 600 }}>{item.suggested_product}</div>
                                  {item.method_label && <div style={{ fontSize: '0.6875rem', color: '#6B7280' }}>{item.method_label}</div>}
                                </div>
                              ) : (
                                <MasterAutocomplete
                                  value={value}
                                  alternatives={item.alternatives}
                                  disabled={!canApprove || busy}
                                  onChange={v => setValues(prev => ({ ...prev, [item.raw_product]: v }))}
                                />
                              )}
                            </td>
                            <td style={cell}>
                              {item.status === 'AUTO_MATCHED' ? '100%' : item.confidence ? `${Math.round(item.confidence)}% · ${item.confidence_tier}` : '—'}
                            </td>
                            <td style={cell}>
                              <span style={{ padding: '3px 8px', borderRadius: 6, fontSize: '0.6875rem', fontWeight: 700, color: st.color, background: st.bg }}>{st.label}</span>
                            </td>
                            <td style={cell}>
                              {locked ? (
                                <button
                                  type="button"
                                  title="Edit standardized product"
                                  aria-label={`Edit ${item.raw_product}`}
                                  disabled={!canApprove || busy}
                                  onClick={() => setEditing(e => ({ ...e, [item.raw_product]: true }))}
                                  style={{ border: `1px solid ${BORDER}`, background: 'white', borderRadius: 6, padding: 5, cursor: canApprove ? 'pointer' : 'not-allowed', color: BLUE, display: 'inline-flex' }}
                                >
                                  <Pencil size={14} />
                                </button>
                              ) : (
                                <button
                                  type="button"
                                  style={smallBtn(TEAL, !canApprove || busy || !value.trim())}
                                  disabled={!canApprove || busy || !value.trim()}
                                  onClick={() => void approve([item])}
                                >
                                  <Check size={13} /> Approve
                                </button>
                              )}
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
              {visibleRows.length > PAGE_SIZE && (
                <Pagination
                  currentPage={page}
                  totalPages={pages}
                  totalRecords={visibleRows.length}
                  firstRecord={(page - 1) * PAGE_SIZE + 1}
                  lastRecord={Math.min(page * PAGE_SIZE, visibleRows.length)}
                  entity="rows"
                  disabled={busy}
                  onPageChange={setPage}
                />
              )}
              <div style={{ fontSize: '0.75rem', color: '#6B7280' }}>
                Repeated products share one decision: approving a product applies to every row where it appears.{' '}
                Approvals are saved for this distributor and, when no global mapping exists, learned globally. An existing global
                mapping is never overwritten. Approvals affect future imports only; already-imported sales data is not changed.
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
