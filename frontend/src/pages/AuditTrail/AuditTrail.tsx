import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react';
import { Search, Download, Eye, Filter, ChevronLeft, ChevronRight, X, Trash2 } from 'lucide-react';
import { Navigate } from 'react-router';
import { useLayoutContext } from '../../hooks/useLayoutContext';
import { StatusBanner } from '../../components/common/StatusBanner';
import { AuditTrailService } from '../../services/auditTrail.service';
import { ApiError } from '../../api';
import { BLUE, BORDER, RED } from '../../constants/theme';
import { isAdminRole } from '../../utils/rbac';
import type { AuditTrailQuery, AuditTrailRow } from '../../types';
import { formatPeriodsInText } from '../../utils/quarter';

const PAGE_SIZE = 25;
const MODULES = [
  'All',
  'Emails',
  'Consolidated Data',
  'Visualization',
  'Master Data',
  'Settings',
  'Authentication',
  'Dashboard',
  'User Management',
  'System',
];
const STATUSES = ['All', 'Success', 'Warning', 'Failed', 'Info'];
const ROLES = ['All', 'super_admin', 'admin', 'user'];
const DATE_PRESETS = [
  { key: 'all', label: 'All time' },
  { key: 'today', label: 'Today' },
  { key: '7d', label: 'Last 7 Days' },
  { key: '30d', label: 'Last 30 Days' },
  { key: 'custom', label: 'Custom Range' },
] as const;

function isoDate(d: Date) {
  return d.toISOString().slice(0, 10);
}

function rangeFromPreset(preset: string): { date_from?: string; date_to?: string } {
  const today = new Date();
  if (preset === 'today') {
    const t = isoDate(today);
    return { date_from: t, date_to: t };
  }
  if (preset === '7d') {
    const from = new Date(today);
    from.setDate(from.getDate() - 6);
    return { date_from: isoDate(from), date_to: isoDate(today) };
  }
  if (preset === '30d') {
    const from = new Date(today);
    from.setDate(from.getDate() - 29);
    return { date_from: isoDate(from), date_to: isoDate(today) };
  }
  return {};
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { color: string; bg: string }> = {
    Success: { color: '#059669', bg: 'rgba(5,150,105,0.1)' },
    Warning: { color: '#D97706', bg: 'rgba(217,119,6,0.1)' },
    Failed: { color: '#DC2626', bg: 'rgba(220,38,38,0.1)' },
    Info: { color: '#2563EB', bg: 'rgba(37,99,235,0.1)' },
  };
  const style = map[status] || { color: '#6B7280', bg: 'rgba(107,114,128,0.1)' };
  return (
    <span
      style={{
        display: 'inline-flex',
        padding: '3px 10px',
        borderRadius: 999,
        fontSize: '0.6875rem',
        fontWeight: 700,
        color: style.color,
        background: style.bg,
        textTransform: 'uppercase',
        letterSpacing: '0.04em',
      }}
    >
      {status}
    </span>
  );
}

export function AuditTrail() {
  const { userRole } = useLayoutContext();
  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [role, setRole] = useState('All');
  const [module, setModule] = useState('All');
  const [status, setStatus] = useState('All');
  const [datePreset, setDatePreset] = useState<(typeof DATE_PRESETS)[number]['key']>('all');
  const [customFrom, setCustomFrom] = useState('');
  const [customTo, setCustomTo] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<AuditTrailRow[]>([]);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(0);
  const [selected, setSelected] = useState<AuditTrailRow | null>(null);
  const [exporting, setExporting] = useState(false);
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [purgeBefore, setPurgeBefore] = useState(() => {
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - 90);
    return isoDate(cutoff);
  });
  const [purgeCount, setPurgeCount] = useState<number | null>(null);
  const [purgeBusy, setPurgeBusy] = useState(false);
  const [purgeError, setPurgeError] = useState<string | null>(null);
  const [purgeSuccess, setPurgeSuccess] = useState<string | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => {
      setSearch(searchInput.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(t);
  }, [searchInput]);

  const query: AuditTrailQuery = useMemo(() => {
    const dates =
      datePreset === 'custom'
        ? {
            date_from: customFrom || undefined,
            date_to: customTo || undefined,
          }
        : rangeFromPreset(datePreset);
    return {
      page,
      page_size: PAGE_SIZE,
      search: search || undefined,
      role: role !== 'All' ? role : undefined,
      module: module !== 'All' ? module : undefined,
      status: status !== 'All' ? status : undefined,
      ...dates,
    };
  }, [page, search, role, module, status, datePreset, customFrom, customTo]);

  const load = useCallback(async () => {
    if (!isAdminRole(userRole)) return;
    setLoading(true);
    setError(null);
    try {
      const data = await AuditTrailService.list(query);
      setRows(data.data);
      setTotal(data.total);
      setTotalPages(data.totalPages);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load audit trail');
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [query, userRole]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!purgeOpen) return undefined;
    if (!purgeBefore) {
      setPurgeCount(null);
      setPurgeError(null);
      return undefined;
    }
    let cancelled = false;
    setPurgeCount(null);
    setPurgeError(null);
    AuditTrailService.purgePreview(purgeBefore)
      .then(data => {
        if (!cancelled) setPurgeCount(data.count);
      })
      .catch(err => {
        if (!cancelled) {
          setPurgeError(err instanceof ApiError ? err.message : 'Could not count old logs');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [purgeOpen, purgeBefore]);

  if (!isAdminRole(userRole)) {
    return <Navigate to="/" replace />;
  }

  const onExport = async () => {
    setExporting(true);
    try {
      await AuditTrailService.exportExcel(query);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export failed');
    } finally {
      setExporting(false);
    }
  };

  const purgeConfirmDisabled = purgeBusy || !purgeBefore || !purgeCount;

  const onPurge = async () => {
    if (purgeConfirmDisabled) return;
    setPurgeBusy(true);
    setPurgeError(null);
    try {
      const result = await AuditTrailService.purge(purgeBefore);
      setPurgeOpen(false);
      setPurgeSuccess(
        `Deleted ${result.deleted} audit log entr${result.deleted === 1 ? 'y' : 'ies'} older than ${result.before}.`,
      );
      setPage(1);
      await load();
    } catch (err) {
      setPurgeError(err instanceof ApiError ? err.message : 'Delete failed');
    } finally {
      setPurgeBusy(false);
    }
  };

  const todayIso = isoDate(new Date());

  return (
    <div style={{ padding: '24px', maxWidth: 1280, margin: '0 auto' }}>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 700, color: '#111827', margin: '0 0 6px' }}>
          Audit Trail
        </h1>
        <p style={{ margin: 0, color: '#6B7280', fontSize: '0.875rem' }}>
          Immutable enterprise activity log — Admin only. Newest events first.
        </p>
      </div>

      <StatusBanner loading={loading && rows.length === 0} error={error} onRetry={load} />

      <div
        style={{
          display: 'flex',
          gap: 12,
          marginBottom: 16,
          flexWrap: 'wrap',
          alignItems: 'flex-end',
          background: 'white',
          border: `1px solid ${BORDER}`,
          borderRadius: 10,
          padding: '14px 16px',
        }}
      >
        <div style={{ flex: '1 1 240px', position: 'relative' }}>
          <div style={labelStyle}>Search</div>
          <Search size={14} style={{ position: 'absolute', left: 10, bottom: 10, color: '#9CA3AF' }} />
          <input
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            placeholder="User, action, module, description…"
            style={{ ...inputStyle, paddingLeft: 32 }}
          />
        </div>
        <FilterSelect label="Role" value={role} options={ROLES} onChange={v => { setRole(v); setPage(1); }} />
        <FilterSelect label="Module" value={module} options={MODULES} onChange={v => { setModule(v); setPage(1); }} />
        <FilterSelect label="Status" value={status} options={STATUSES} onChange={v => { setStatus(v); setPage(1); }} />
        <div>
          <div style={labelStyle}>Date Range</div>
          <select
            value={datePreset}
            onChange={e => {
              setDatePreset(e.target.value as typeof datePreset);
              setPage(1);
            }}
            style={inputStyle}
          >
            {DATE_PRESETS.map(p => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
        {datePreset === 'custom' && (
          <>
            <div>
              <div style={labelStyle}>From</div>
              <input
                type="date"
                value={customFrom}
                onChange={e => { setCustomFrom(e.target.value); setPage(1); }}
                style={inputStyle}
              />
            </div>
            <div>
              <div style={labelStyle}>To</div>
              <input
                type="date"
                value={customTo}
                onChange={e => { setCustomTo(e.target.value); setPage(1); }}
                style={inputStyle}
              />
            </div>
          </>
        )}
        <button type="button" onClick={onExport} disabled={exporting} style={primaryBtn}>
          <Download size={14} />
          {exporting ? 'Exporting…' : 'Export Excel'}
        </button>
        <button
          type="button"
          onClick={() => {
            setPurgeSuccess(null);
            setPurgeOpen(true);
          }}
          style={{ ...primaryBtn, background: 'white', color: RED, border: `1px solid ${RED}` }}
        >
          <Trash2 size={14} />
          Delete Old Logs
        </button>
      </div>

      {purgeSuccess && (
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
          {purgeSuccess}
        </div>
      )}

      <div
        style={{
          background: 'white',
          border: `1px solid ${BORDER}`,
          borderRadius: 10,
          overflow: 'hidden',
        }}
      >
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 960 }}>
            <thead>
              <tr style={{ background: '#F3F4F6', borderBottom: `1px solid ${BORDER}` }}>
                {['Timestamp', 'User', 'Role', 'Module', 'Action', 'Description', 'Status', 'Details'].map(
                  h => (
                    <th key={h} style={thStyle}>
                      {h}
                    </th>
                  )
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.id} style={{ borderBottom: `1px solid ${BORDER}` }}>
                  <td style={tdStyle}>{row.timestamp}</td>
                  <td style={{ ...tdStyle, fontWeight: 600 }}>{row.user}</td>
                  <td style={tdStyle}>{row.role || '—'}</td>
                  <td style={tdStyle}>{row.module || '—'}</td>
                  <td style={tdStyle}>{row.action}</td>
                  <td style={{ ...tdStyle, maxWidth: 320 }}>{formatPeriodsInText(row.description)}</td>
                  <td style={tdStyle}>
                    <StatusBadge status={row.status} />
                  </td>
                  <td style={tdStyle}>
                    <button type="button" onClick={() => setSelected(row)} style={viewBtn}>
                      <Eye size={14} />
                      View
                    </button>
                  </td>
                </tr>
              ))}
              {!loading && rows.length === 0 && (
                <tr>
                  <td colSpan={8} style={{ ...tdStyle, textAlign: 'center', color: '#9CA3AF', padding: 40 }}>
                    No audit events match the current filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {total > 0 && (
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '10px 16px',
              borderTop: `1px solid ${BORDER}`,
              background: '#F9FAFB',
              fontSize: '0.8125rem',
              color: '#6B7280',
              gap: 12,
              flexWrap: 'wrap',
            }}
          >
            <span>
              Showing {(page - 1) * PAGE_SIZE + 1}–
              {Math.min(page * PAGE_SIZE, total)} of {total.toLocaleString()}
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <button
                type="button"
                disabled={page <= 1 || loading}
                onClick={() => setPage(p => Math.max(1, p - 1))}
                style={pagerBtn(page <= 1)}
              >
                <ChevronLeft size={14} /> Previous
              </button>
              <span>
                Page {page} / {Math.max(totalPages, 1)}
              </span>
              <button
                type="button"
                disabled={page >= totalPages || loading}
                onClick={() => setPage(p => p + 1)}
                style={pagerBtn(page >= totalPages)}
              >
                Next <ChevronRight size={14} />
              </button>
            </div>
          </div>
        )}
      </div>

      {selected && (
        <DetailDrawer
          row={selected}
          onClose={() => setSelected(null)}
        />
      )}

      {purgeOpen && (
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
            if (!purgeBusy) setPurgeOpen(false);
          }}
        >
          <div
            style={{
              background: 'white',
              borderRadius: 12,
              width: '100%',
              maxWidth: 440,
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
                Delete Old Audit Logs
              </h2>
              <button
                type="button"
                style={{ border: 'none', background: 'transparent', cursor: 'pointer' }}
                disabled={purgeBusy}
                onClick={() => setPurgeOpen(false)}
              >
                <X size={18} />
              </button>
            </div>
            <p style={{ margin: '0 0 14px', fontSize: '0.8125rem', color: '#6B7280', lineHeight: 1.5 }}>
              Permanently deletes every audit log entry created before the selected date. This cannot be
              undone. The deletion itself is recorded in the audit trail.
            </p>
            <div style={labelStyle}>Delete logs older than</div>
            <input
              type="date"
              value={purgeBefore}
              max={todayIso}
              onChange={event => setPurgeBefore(event.target.value)}
              style={{ ...inputStyle, marginBottom: 12 }}
            />
            <div style={{ fontSize: '0.875rem', color: '#111827', fontWeight: 600, marginBottom: 14 }}>
              {purgeCount == null && !purgeError
                ? 'Counting…'
                : purgeCount != null
                  ? `${purgeCount} entr${purgeCount === 1 ? 'y' : 'ies'} will be deleted.`
                  : null}
            </div>
            {purgeError && (
              <div
                style={{
                  padding: 10,
                  marginBottom: 12,
                  borderRadius: 8,
                  background: 'rgba(217,58,47,0.08)',
                  color: RED,
                  fontSize: '0.8125rem',
                }}
              >
                {purgeError}
              </div>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button
                type="button"
                style={{ ...primaryBtn, background: 'white', color: '#374151', border: `1px solid ${BORDER}` }}
                disabled={purgeBusy}
                onClick={() => setPurgeOpen(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                style={{
                  ...primaryBtn,
                  background: RED,
                  opacity: purgeConfirmDisabled ? 0.6 : 1,
                }}
                disabled={purgeConfirmDisabled}
                onClick={() => void onPurge()}
              >
                <Trash2 size={14} />
                {purgeBusy ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <div>
      <div style={labelStyle}>
        <Filter size={10} style={{ marginRight: 4 }} />
        {label}
      </div>
      <select value={value} onChange={e => onChange(e.target.value)} style={inputStyle}>
        {options.map(o => (
          <option key={o} value={o}>
            {o === 'admin' ? 'Admin' : o === 'user' ? 'User' : o}
          </option>
        ))}
      </select>
    </div>
  );
}

function DetailDrawer({ row, onClose }: { row: AuditTrailRow; onClose: () => void }) {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(15,23,42,0.45)',
        zIndex: 1000,
        display: 'flex',
        justifyContent: 'flex-end',
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: 'min(440px, 100%)',
          height: '100%',
          background: 'white',
          boxShadow: '-8px 0 24px rgba(0,0,0,0.12)',
          padding: 24,
          overflowY: 'auto',
        }}
        onClick={e => e.stopPropagation()}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <h2 style={{ margin: 0, fontSize: '1.125rem', fontWeight: 700 }}>Audit Details</h2>
          <button type="button" onClick={onClose} style={{ border: 'none', background: 'transparent', cursor: 'pointer' }}>
            <X size={18} color="#6B7280" />
          </button>
        </div>
        <Detail label="Timestamp" value={row.timestamp} />
        <Detail label="User" value={row.user} />
        <Detail label="Role" value={row.role || '—'} />
        <Detail label="Module" value={row.module || '—'} />
        <Detail label="Action" value={row.action} />
        <div style={{ marginBottom: 14 }}>
          <div style={detailLabel}>Status</div>
          <StatusBadge status={row.status} />
        </div>
        <Detail label="Description" value={formatPeriodsInText(row.description)} />
        <Detail label="Entity Type" value={row.entityType || '—'} />
        <Detail label="Entity ID" value={row.entityId || '—'} />
        <Detail label="Report Name" value={row.reportName || '—'} />
        <Detail label="IP Address" value={row.ipAddress || '—'} />
        <Detail label="Browser / Device" value={row.userAgent || '—'} />
        {row.metadata && Object.keys(row.metadata).length > 0 && (
          <div style={{ marginTop: 8 }}>
            <div style={detailLabel}>Metadata</div>
            <pre
              style={{
                margin: 0,
                padding: 12,
                background: '#F8FAFC',
                border: `1px solid ${BORDER}`,
                borderRadius: 8,
                fontSize: '0.75rem',
                overflow: 'auto',
                maxHeight: 220,
              }}
            >
              {JSON.stringify(row.metadata, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={detailLabel}>{label}</div>
      <div style={{ fontSize: '0.875rem', color: '#111827', lineHeight: 1.45 }}>{value}</div>
    </div>
  );
}

const labelStyle: CSSProperties = {
  fontSize: '0.65rem',
  color: '#6B7280',
  fontWeight: 600,
  marginBottom: 3,
  display: 'flex',
  alignItems: 'center',
};

const detailLabel: CSSProperties = {
  fontSize: '0.65rem',
  color: '#6B7280',
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  marginBottom: 4,
};

const inputStyle: CSSProperties = {
  height: 34,
  padding: '0 10px',
  border: `1px solid ${BORDER}`,
  borderRadius: 6,
  fontSize: '0.8125rem',
  color: '#374151',
  background: 'white',
  fontFamily: 'inherit',
  minWidth: 130,
  width: '100%',
};

const thStyle: CSSProperties = {
  padding: '10px 12px',
  textAlign: 'left',
  fontSize: '0.6875rem',
  fontWeight: 700,
  color: '#6B7280',
  textTransform: 'uppercase',
  letterSpacing: '0.03em',
  whiteSpace: 'nowrap',
};

const tdStyle: CSSProperties = {
  padding: '11px 12px',
  fontSize: '0.8125rem',
  color: '#374151',
  verticalAlign: 'top',
};

const primaryBtn: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  height: 34,
  padding: '0 14px',
  background: BLUE,
  color: 'white',
  border: 'none',
  borderRadius: 6,
  fontSize: '0.8125rem',
  fontWeight: 600,
  cursor: 'pointer',
  fontFamily: 'inherit',
};

const viewBtn: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 4,
  padding: '5px 8px',
  border: `1px solid ${BORDER}`,
  borderRadius: 5,
  background: 'white',
  color: '#4B5563',
  fontSize: '0.75rem',
  fontWeight: 600,
  cursor: 'pointer',
  fontFamily: 'inherit',
};

function pagerBtn(disabled: boolean): CSSProperties {
  return {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    height: 32,
    padding: '0 10px',
    border: `1px solid ${BORDER}`,
    borderRadius: 6,
    background: 'white',
    color: disabled ? '#9CA3AF' : '#374151',
    fontSize: '0.75rem',
    fontWeight: 600,
    cursor: disabled ? 'not-allowed' : 'pointer',
    fontFamily: 'inherit',
  };
}
