import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { Check, Loader2, Package, Pencil, RefreshCw, UserRound, X } from 'lucide-react';
import { ApiError } from '../../api';
import { StatusBanner } from '../../components/common/StatusBanner';
import { Pagination } from '../../components/ui/Pagination';
import { BLUE, BORDER, GREEN, RED, TEAL } from '../../constants/theme';
import {
  CustomerStandardizationService,
  type CustomerApprovalItem,
  type CustomerApprovalOutcome,
  type CustomerBucket,
  type CustomerSource,
  type CustomerWorkbenchData,
  type CustomerWorkbenchItem,
} from '../../services/customerStandardization.service';

type Props = {
  emailId: number;
  title: string;
  initialAttachmentId?: number | null;
  onClose: () => void;
  onSwitchToProducts?: (attachmentId: number | null) => void;
};

type Filter = 'all' | CustomerBucket;

const PAGE_SIZE = 100;
const MAX_BATCH = 500;

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'needs_review', label: 'Needs review' },
  { key: 'unmapped', label: 'Unmapped' },
  { key: 'auto_matched', label: 'Auto-matched' },
  { key: 'approved', label: 'Approved' },
];

const STATUS_STYLE: Record<CustomerBucket, { label: string; color: string; bg: string }> = {
  approved: { label: 'Approved', color: '#065F46', bg: 'rgba(16,185,129,0.14)' },
  auto_matched: { label: 'Auto-matched', color: '#1E3A8A', bg: 'rgba(59,130,246,0.12)' },
  needs_review: { label: 'Needs review', color: '#92400E', bg: 'rgba(245,158,11,0.16)' },
  unmapped: { label: 'Unmapped', color: '#991B1B', bg: 'rgba(239,68,68,0.12)' },
};

const SOURCE_LABEL: Record<CustomerSource, string> = {
  master: 'Customer master',
  approved: 'Approved name',
  sales: 'Existing customer',
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

const tabBtn = (active: boolean): CSSProperties => ({
  display: 'inline-flex',
  alignItems: 'center',
  gap: 5,
  padding: '5px 10px',
  borderRadius: 6,
  border: `1px solid ${active ? BLUE : BORDER}`,
  background: active ? 'rgba(31,95,168,0.08)' : 'white',
  color: active ? BLUE : '#374151',
  fontSize: '0.75rem',
  fontWeight: 600,
  cursor: active ? 'default' : 'pointer',
});

function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message || fallback;
  return fallback;
}

// Unmatched customers default to their own raw name; the status is not affected.
function defaultValue(item: CustomerWorkbenchItem): string {
  return item.suggested_customer || item.raw_customer;
}

function CustomerAutocomplete({
  value,
  onChange,
  alternatives,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  alternatives: CustomerWorkbenchItem['alternatives'];
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<{ name: string; source: CustomerSource }[]>([]);
  const [loading, setLoading] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const ctrl = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      CustomerStandardizationService.searchCustomers(value, 20, ctrl.signal)
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
    const out: { text: string; source: CustomerSource; hint?: string }[] = [];
    for (const a of alternatives) {
      const k = a.name.toLowerCase();
      if (seen.has(k)) continue;
      seen.add(k);
      out.push({ text: a.name, source: a.source, hint: `${Math.round(a.confidence)}%` });
    }
    for (const r of results) {
      const k = r.name.toLowerCase();
      if (seen.has(k)) continue;
      seen.add(k);
      out.push({ text: r.name, source: r.source });
    }
    return out;
  }, [alternatives, results]);

  const trimmed = value.trim();
  const existing = options.find(o => o.text.toLowerCase() === trimmed.toLowerCase());

  return (
    <div ref={boxRef} style={{ position: 'relative', minWidth: 260 }}>
      <input
        value={value}
        disabled={disabled}
        placeholder="Search existing customers or type a new name"
        aria-label="Standardized customer"
        onFocus={() => setOpen(true)}
        onChange={e => {
          onChange(e.target.value);
          setOpen(true);
        }}
        style={{
          width: '100%',
          padding: '6px 8px',
          fontSize: '0.8125rem',
          border: `1px solid ${trimmed ? BORDER : RED}`,
          borderRadius: 6,
          background: disabled ? '#F3F4F6' : 'white',
          boxSizing: 'border-box',
        }}
      />
      {!open && trimmed && (
        <div style={{ fontSize: '0.6875rem', marginTop: 2, color: existing ? '#065F46' : '#92400E' }}>
          {existing ? `Existing customer · ${SOURCE_LABEL[existing.source]}` : 'New customer name (will be saved as a new standardized customer)'}
        </div>
      )}
      {!trimmed && <div style={{ fontSize: '0.6875rem', marginTop: 2, color: RED }}>Enter a standardized customer name</div>}
      {open && !disabled && (
        <div
          role="listbox"
          style={{
            position: 'absolute',
            zIndex: 20,
            top: '100%',
            left: 0,
            right: 0,
            maxHeight: 240,
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
            <div style={{ padding: '6px 10px', fontSize: '0.75rem', color: '#6B7280' }}>No existing customer found. The typed name will be saved as a new customer.</div>
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
              <span style={{ color: '#6B7280', fontSize: '0.7rem', whiteSpace: 'nowrap' }}>
                {SOURCE_LABEL[o.source]}
                {o.hint ? ` · ${o.hint}` : ''}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function CustomerWorkbenchModal({ emailId, title, initialAttachmentId = null, onClose, onSwitchToProducts }: Props) {
  const [data, setData] = useState<CustomerWorkbenchData | null>(null);
  const [attachmentId, setAttachmentId] = useState<number | null>(initialAttachmentId);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [filter, setFilter] = useState<Filter>('all');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [conflicts, setConflicts] = useState<CustomerApprovalOutcome[]>([]);

  const load = useCallback(
    async (refresh = false) => {
      setLoading(true);
      setError(null);
      try {
        const res = await CustomerStandardizationService.workbench(emailId, attachmentId, refresh);
        setData(res);
        const next: Record<string, string> = {};
        res.items.forEach(i => {
          next[i.raw_customer] = defaultValue(i);
        });
        setValues(next);
        setEditing({});
        setSelected({});
      } catch (err) {
        setData(null);
        setError(errorText(err, 'The customer workbench could not be loaded.'));
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
  }, [filter, search, attachmentId]);

  const canApprove = Boolean(data?.distributor);
  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (data?.items || []).filter(
      i =>
        (filter === 'all' || i.bucket === filter) &&
        (!needle || i.raw_customer.toLowerCase().includes(needle) || (i.suggested_customer || '').toLowerCase().includes(needle))
    );
  }, [data, filter, search]);
  const pages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const pageItems = visible.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const isLocked = (i: CustomerWorkbenchItem) => i.bucket === 'approved' && !editing[i.raw_customer];
  const selectable = (i: CustomerWorkbenchItem) => canApprove && !isLocked(i);
  const allSelectable = visible.filter(selectable);
  const selectedItems = allSelectable.filter(i => selected[i.raw_customer]);
  const allChecked = allSelectable.length > 0 && allSelectable.every(i => selected[i.raw_customer]);

  const submit = async (payload: CustomerApprovalItem[]) => {
    if (!data || !payload.length) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      let approved = 0;
      let globalConflicts = 0;
      const found: CustomerApprovalOutcome[] = [];
      for (let start = 0; start < payload.length; start += MAX_BATCH) {
        const res = await CustomerStandardizationService.approve(data.email_id, data.attachment_id, payload.slice(start, start + MAX_BATCH));
        approved += res.approved;
        globalConflicts += res.global_conflicts;
        found.push(...res.conflicts);
      }
      setConflicts(found);
      const globalNote = globalConflicts
        ? ` ${globalConflicts} differ from an existing global mapping and were saved for this distributor only.`
        : '';
      const conflictNote = found.length ? ` ${found.length} already had a different approved mapping and need confirmation.` : '';
      setMessage(`${approved} customer mapping(s) approved.${globalNote}${conflictNote} Approvals apply to new imports only.`);
      await load();
    } catch (err) {
      setError(errorText(err, 'Approval failed.'));
    } finally {
      setBusy(false);
    }
  };

  const approve = (items: CustomerWorkbenchItem[]) => {
    const payload: CustomerApprovalItem[] = [];
    const empty: string[] = [];
    for (const item of items) {
      const value = (values[item.raw_customer] || '').trim();
      if (!value) empty.push(item.raw_customer);
      else payload.push({ raw_customer: item.raw_customer, standard_customer: value, confidence: item.confidence });
    }
    if (empty.length) {
      setError(`Enter a standardized customer before approving: ${empty.slice(0, 5).join(', ')}${empty.length > 5 ? '…' : ''}`);
      return;
    }
    void submit(payload);
  };

  const replaceConflicts = (items: CustomerApprovalOutcome[]) =>
    void submit(items.map(c => ({ raw_customer: c.raw_customer, standard_customer: c.standard_customer, replace_existing: true })));

  const m = data?.metrics;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Customer Standardization Workbench"
      style={{ position: 'fixed', inset: 0, background: 'rgba(17,24,39,0.45)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}
      onClick={() => !busy && onClose()}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{ background: 'white', borderRadius: 12, width: 'min(1200px, 100%)', maxHeight: '92vh', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 50px rgba(0,0,0,0.2)' }}
      >
        <div style={{ padding: '16px 20px', borderBottom: `1px solid ${BORDER}`, display: 'flex', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <h2 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 700, color: BLUE }}>Customer Standardization Workbench</h2>
            <div style={{ fontSize: '0.75rem', color: '#6B7280', marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {title}
              {data ? ` · ${data.attachment_name} · ${data.distributor?.name || 'Distributor not resolved'}` : ''}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {onSwitchToProducts && (
              <div style={{ display: 'inline-flex', gap: 4 }} role="tablist" aria-label="Standardization workbench">
                <button
                  type="button"
                  role="tab"
                  aria-selected={false}
                  style={tabBtn(false)}
                  disabled={busy}
                  onClick={() => onSwitchToProducts(data?.attachment_id ?? attachmentId)}
                >
                  <Package size={13} /> Products
                </button>
                <button type="button" role="tab" aria-selected style={tabBtn(true)}>
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
          {data && !data.distributor && (
            <StatusBanner type="error" message="The distributor for this email could not be resolved, so approvals are disabled. Fix the email subject first." />
          )}
          {data && data.reference_count === 0 && (
            <StatusBanner
              type="info"
              message="No customer master or approved customer names exist yet. Every customer starts as Unmapped; approved names are reused on future uploads."
            />
          )}
          {data && data.blank_rows > 0 && (
            <StatusBanner type="info" message={`${data.blank_rows} extracted row(s) have no customer name and are not listed.`} />
          )}

          {conflicts.length > 0 && (
            <div style={{ border: '1px solid rgba(245,158,11,0.5)', background: 'rgba(245,158,11,0.08)', borderRadius: 10, padding: '10px 12px' }}>
              <div style={{ fontWeight: 700, fontSize: '0.8125rem', color: '#92400E', marginBottom: 6 }}>
                These customers already have a different approved mapping for this distributor. Nothing was changed.
              </div>
              {conflicts.slice(0, 20).map(c => (
                <div key={c.raw_customer} style={{ fontSize: '0.75rem', color: '#374151', padding: '2px 0' }}>
                  <strong>{c.raw_customer}</strong>: approved as “{c.existing_customer}”, you chose “{c.standard_customer}”
                </div>
              ))}
              {conflicts.length > 20 && <div style={{ fontSize: '0.75rem', color: '#6B7280' }}>…and {conflicts.length - 20} more</div>}
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <button type="button" style={smallBtn('#B45309', busy)} disabled={busy} onClick={() => replaceConflicts(conflicts)}>
                  Replace existing mapping{conflicts.length > 1 ? 's' : ''}
                </button>
                <button
                  type="button"
                  style={{ ...smallBtn('white', busy), color: '#374151', border: `1px solid ${BORDER}` }}
                  disabled={busy}
                  onClick={() => setConflicts([])}
                >
                  Keep existing
                </button>
              </div>
            </div>
          )}

          {loading && !data ? (
            <StatusBanner loading loadingText="Extracting customers…" />
          ) : data && m ? (
            <>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {[
                  { label: 'Distinct customers', value: m.total_distinct, color: '#111827' },
                  { label: 'Auto-matched', value: m.auto_matched, color: BLUE },
                  { label: 'Needs review', value: m.needs_review, color: '#B45309' },
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
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
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
                  <input
                    value={search}
                    onChange={e => setSearch(e.target.value)}
                    placeholder="Search customers"
                    aria-label="Search customers"
                    style={{ padding: '4px 8px', fontSize: '0.75rem', border: `1px solid ${BORDER}`, borderRadius: 6, minWidth: 180 }}
                  />
                </div>
                <button
                  type="button"
                  style={smallBtn(TEAL, busy || selectedItems.length === 0)}
                  disabled={busy || selectedItems.length === 0}
                  onClick={() => approve(selectedItems)}
                >
                  {busy ? <Loader2 size={13} className="spin" /> : <Check size={13} />} Approve Selected ({selectedItems.length})
                </button>
              </div>

              <div style={{ border: `1px solid ${BORDER}`, borderRadius: 10, overflow: 'visible' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }} aria-label="Customers to standardize">
                  <thead>
                    <tr style={{ background: '#F9FAFB' }}>
                      <th style={{ ...cell, width: 32 }}>
                        <input
                          type="checkbox"
                          aria-label="Select all"
                          title={`Select all ${allSelectable.length} customer(s) in this view`}
                          checked={allChecked}
                          disabled={!allSelectable.length}
                          onChange={e => {
                            const next = { ...selected };
                            allSelectable.forEach(i => {
                              next[i.raw_customer] = e.target.checked;
                            });
                            setSelected(next);
                          }}
                        />
                      </th>
                      {['Distributor', 'Raw Customer Name', 'Standardized Customer Name', 'Confidence', 'Status', 'Actions'].map(h => (
                        <th key={h} style={{ ...cell, fontWeight: 600, color: '#374151' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {pageItems.length === 0 ? (
                      <tr>
                        <td colSpan={7} style={{ ...cell, textAlign: 'center', color: '#6B7280', padding: '24px 10px' }}>
                          {data.items.length === 0 ? 'No customer names were found in this attachment.' : 'No customers in this view.'}
                        </td>
                      </tr>
                    ) : (
                      pageItems.map(item => {
                        const locked = isLocked(item);
                        const isEditing = Boolean(editing[item.raw_customer]);
                        const showEditor = !locked && (isEditing || item.bucket === 'needs_review' || item.bucket === 'unmapped');
                        const st = STATUS_STYLE[item.bucket];
                        const value = values[item.raw_customer] || '';
                        return (
                          <tr key={item.raw_customer}>
                            <td style={cell}>
                              <input
                                type="checkbox"
                                aria-label={`Select ${item.raw_customer}`}
                                disabled={!selectable(item)}
                                checked={Boolean(selected[item.raw_customer]) && selectable(item)}
                                onChange={e => setSelected(s => ({ ...s, [item.raw_customer]: e.target.checked }))}
                              />
                            </td>
                            <td style={cell}>{item.distributor || '—'}</td>
                            <td style={{ ...cell, fontWeight: 600 }}>
                              <span style={{ whiteSpace: 'pre-wrap' }}>{item.raw_customer}</span>
                              <div style={{ fontSize: '0.6875rem', color: '#6B7280', fontWeight: 400 }}>{item.row_count} row(s)</div>
                            </td>
                            <td style={cell}>
                              {showEditor ? (
                                <CustomerAutocomplete
                                  value={value}
                                  alternatives={item.alternatives}
                                  disabled={!canApprove || busy}
                                  onChange={v => setValues(prev => ({ ...prev, [item.raw_customer]: v }))}
                                />
                              ) : (
                                <div>
                                  <div style={{ fontWeight: 600 }}>{item.suggested_customer || '—'}</div>
                                  {item.method_label && <div style={{ fontSize: '0.6875rem', color: '#6B7280' }}>{item.method_label}</div>}
                                </div>
                              )}
                            </td>
                            <td style={cell}>
                              {item.status === 'AUTO_MATCHED' ? '100%' : item.confidence ? `${Math.round(item.confidence)}% · ${item.confidence_tier}` : '—'}
                            </td>
                            <td style={cell}>
                              <span style={{ padding: '3px 8px', borderRadius: 6, fontSize: '0.6875rem', fontWeight: 700, color: st.color, background: st.bg }}>{st.label}</span>
                            </td>
                            <td style={cell}>
                              <div style={{ display: 'inline-flex', gap: 6 }}>
                                {!showEditor && (
                                  <button
                                    type="button"
                                    title="Edit standardized customer"
                                    aria-label={`Edit ${item.raw_customer}`}
                                    disabled={!canApprove || busy}
                                    onClick={() => setEditing(e => ({ ...e, [item.raw_customer]: true }))}
                                    style={{ border: `1px solid ${BORDER}`, background: 'white', borderRadius: 6, padding: 5, cursor: canApprove ? 'pointer' : 'not-allowed', color: BLUE, display: 'inline-flex' }}
                                  >
                                    <Pencil size={14} />
                                  </button>
                                )}
                                {!locked && (
                                  <button
                                    type="button"
                                    style={smallBtn(TEAL, !canApprove || busy || !value.trim())}
                                    disabled={!canApprove || busy || !value.trim()}
                                    onClick={() => approve([item])}
                                  >
                                    <Check size={13} /> Approve
                                  </button>
                                )}
                                {isEditing && (
                                  <button
                                    type="button"
                                    title="Cancel edit"
                                    aria-label={`Cancel edit ${item.raw_customer}`}
                                    disabled={busy}
                                    onClick={() => {
                                      setEditing(e => ({ ...e, [item.raw_customer]: false }));
                                      setValues(prev => ({ ...prev, [item.raw_customer]: defaultValue(item) }));
                                    }}
                                    style={{ border: `1px solid ${BORDER}`, background: 'white', borderRadius: 6, padding: 5, cursor: 'pointer', color: '#6B7280', display: 'inline-flex' }}
                                  >
                                    <X size={14} />
                                  </button>
                                )}
                              </div>
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
              {visible.length > PAGE_SIZE && (
                <Pagination
                  currentPage={page}
                  totalPages={pages}
                  totalRecords={visible.length}
                  firstRecord={(page - 1) * PAGE_SIZE + 1}
                  lastRecord={Math.min(page * PAGE_SIZE, visible.length)}
                  entity="customers"
                  disabled={busy}
                  onPageChange={setPage}
                />
              )}
              <div style={{ fontSize: '0.75rem', color: '#6B7280' }}>
                Approvals are saved for this distributor and, when no global mapping exists, learned globally. Existing approved
                mappings are never replaced without confirmation. Approvals affect future imports only; already-imported sales data
                and the original Excel file are not changed.
              </div>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
