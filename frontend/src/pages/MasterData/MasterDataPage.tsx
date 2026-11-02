import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { Navigate } from 'react-router';
import { Database, Upload, CheckCircle2, AlertTriangle, X } from 'lucide-react';
import { useLayoutContext } from '../../hooks/useLayoutContext';
import { StatusBanner } from '../../components/common/StatusBanner';
import { ApiError } from '../../api';
import { isSuperAdminRole } from '../../utils/rbac';
import { BLUE, BORDER, GREEN, RED, TEAL } from '../../constants/theme';
import {
  ProductStandardizationService,
  type FGMasterDiff,
  type FGMasterStatus,
  type FGMasterUploadResult,
  type FGMasterVersion,
} from '../../services/productStandardization.service';

const MAX_FILE_MB = 10;

const cardStyle: CSSProperties = {
  border: `1px solid ${BORDER}`,
  borderRadius: 12,
  background: 'white',
  padding: '16px 18px',
};

const cellStyle: CSSProperties = {
  padding: '9px 12px',
  borderBottom: `1px solid ${BORDER}`,
  fontSize: '0.8125rem',
  color: '#1F2937',
  textAlign: 'left',
};

const buttonStyle = (bg: string, disabled = false): CSSProperties => ({
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '8px 16px',
  border: 'none',
  borderRadius: 8,
  background: disabled ? '#9CA3AF' : bg,
  color: 'white',
  fontSize: '0.875rem',
  fontWeight: 600,
  cursor: disabled ? 'not-allowed' : 'pointer',
});

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return 'Only a Super Admin can manage the FG Master.';
    if (err.status === 401) return 'Your session has expired. Please sign in again.';
    return err.message || fallback;
  }
  return fallback;
}

function formatDate(value: string | null): string {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

function Stat({ label, value, color }: { label: string; value: ReactNode; color?: string }) {
  return (
    <div style={{ ...cardStyle, padding: '12px 14px', minWidth: 130, flex: 1 }}>
      <div style={{ fontSize: '0.75rem', color: '#6B7280', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: '1.25rem', fontWeight: 700, color: color || '#111827', marginTop: 4 }}>{value}</div>
    </div>
  );
}

function VersionSummary({ title, version }: { title: string; version: FGMasterVersion }) {
  return (
    <div style={cardStyle}>
      <div style={{ fontWeight: 700, color: BLUE, marginBottom: 8 }}>
        {title} · v{version.version_number}{' '}
        <span style={{ fontSize: '0.75rem', fontWeight: 600, color: '#6B7280' }}>({version.status})</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 6, fontSize: '0.8125rem', color: '#374151' }}>
        <div>File: <strong>{version.file_name}</strong></div>
        <div>Sheet: {version.sheet_name || '—'}</div>
        <div>Products: <strong>{version.valid_count}</strong></div>
        <div>Duplicates removed: {version.duplicate_count}</div>
        <div>Invalid rows: {version.invalid_count}</div>
        <div>Uploaded: {formatDate(version.uploaded_at)} by {version.uploaded_by_name || '—'}</div>
        {version.published_at && (
          <div>Published: {formatDate(version.published_at)} by {version.published_by_name || '—'}</div>
        )}
      </div>
    </div>
  );
}

function DiffPanel({ diff }: { diff: FGMasterDiff }) {
  return (
    <div style={{ ...cardStyle, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ fontWeight: 700, color: BLUE }}>
        Replace preview {diff.against_version ? `(compared with active v${diff.against_version})` : '(no active master yet)'}
      </div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <Stat label="New descriptions" value={diff.new_count} color={GREEN} />
        <Stat label="Removed descriptions" value={diff.removed_count} color={diff.removed_count ? RED : undefined} />
        <Stat label="Unchanged" value={diff.unchanged_count} />
        <Stat
          label="Mappings using removed descriptions"
          value={diff.affected_mappings_count}
          color={diff.affected_mappings_count ? RED : undefined}
        />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }}>
        <SampleList title="New (sample)" items={diff.new_sample} total={diff.new_count} />
        <SampleList title="Removed (sample)" items={diff.removed_sample} total={diff.removed_count} />
      </div>
      {diff.affected_mappings.length > 0 && (
        <div>
          <div style={{ fontWeight: 600, fontSize: '0.8125rem', marginBottom: 6, color: '#374151' }}>
            Approved mappings that point to a removed description (they keep working; review them in the workbench)
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: '#F9FAFB' }}>
                {['Scope', 'Raw product', 'Mapped to'].map(h => (
                  <th key={h} style={{ ...cellStyle, fontWeight: 600 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {diff.affected_mappings.map(m => (
                <tr key={m.mapping_id}>
                  <td style={cellStyle}>{m.scope}</td>
                  <td style={cellStyle}>{m.original_product_name}</td>
                  <td style={cellStyle}>{m.standard_product}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SampleList({ title, items, total }: { title: string; items: string[]; total: number }) {
  return (
    <div>
      <div style={{ fontWeight: 600, fontSize: '0.8125rem', marginBottom: 6, color: '#374151' }}>
        {title} {total > items.length ? `· showing ${items.length} of ${total}` : ''}
      </div>
      <div style={{ maxHeight: 180, overflowY: 'auto', border: `1px solid ${BORDER}`, borderRadius: 8, padding: '6px 10px', fontSize: '0.8125rem' }}>
        {items.length ? items.map(i => <div key={i}>{i}</div>) : <span style={{ color: '#9CA3AF' }}>None</span>}
      </div>
    </div>
  );
}

export function MasterDataPage() {
  const { userRole } = useLayoutContext();
  const isSuperAdmin = isSuperAdminRole(userRole);
  const fileRef = useRef<HTMLInputElement>(null);

  const [status, setStatus] = useState<FGMasterStatus | null>(null);
  const [versions, setVersions] = useState<FGMasterVersion[]>([]);
  const [upload, setUpload] = useState<FGMasterUploadResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, v] = await Promise.all([
        ProductStandardizationService.masterStatus(),
        ProductStandardizationService.masterVersions(),
      ]);
      setStatus(s);
      setVersions(v);
    } catch (err) {
      setError(errorMessage(err, 'Unable to load FG Master status.'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isSuperAdmin) void load();
  }, [isSuperAdmin, load]);

  if (!isSuperAdmin) return <Navigate to="/" replace />;

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    setMessage(null);
    setUpload(null);
    if (!/\.(xlsx|xlsm)$/i.test(file.name)) {
      setError('Upload the FG Master as an .xlsx or .xlsm workbook.');
      return;
    }
    if (file.size > MAX_FILE_MB * 1024 * 1024) {
      setError(`The file is larger than ${MAX_FILE_MB} MB.`);
      return;
    }
    setBusy(true);
    setProgress(0);
    try {
      const result = await ProductStandardizationService.uploadMaster(file, setProgress);
      setUpload(result);
      setMessage(`Draft v${result.version.version_number} validated. Review the changes below, then publish.`);
      await load();
    } catch (err) {
      setError(errorMessage(err, 'The FG Master could not be validated.'));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const draft = status?.draft ?? null;
  const draftDiff = upload?.diff ?? status?.draft_diff ?? null;

  const publish = async () => {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      const res = await ProductStandardizationService.publishMaster(draft.id);
      setMessage(`FG Master v${res.version.version_number} is now active. It applies to new imports only.`);
      setUpload(null);
      setConfirming(false);
      await load();
    } catch (err) {
      setError(errorMessage(err, 'The FG Master could not be published.'));
    } finally {
      setBusy(false);
    }
  };

  const discard = async () => {
    if (!draft) return;
    setBusy(true);
    setError(null);
    try {
      await ProductStandardizationService.discardMaster(draft.id);
      setUpload(null);
      setMessage(`Draft v${draft.version_number} discarded.`);
      await load();
    } catch (err) {
      setError(errorMessage(err, 'The draft could not be discarded.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ padding: '24px 28px', display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14 }}>
        <div style={{ width: 42, height: 42, borderRadius: 10, background: 'rgba(31,95,168,0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
          <Database size={22} color={BLUE} />
        </div>
        <div>
          <h1 style={{ margin: 0, fontSize: '1.25rem', fontWeight: 700, color: BLUE }}>Master Data</h1>
          <p style={{ margin: '6px 0 0', color: '#6B7280', fontSize: '0.875rem', lineHeight: 1.5 }}>
            FG Master used to standardize distributor product names. Only the <strong>Material Description</strong>{' '}
            column is read. Each environment keeps its own master; publishing affects new imports only and never
            changes existing sales data.
          </p>
        </div>
      </div>

      {error && <StatusBanner error={error} onClose={() => setError(null)} />}
      {message && <StatusBanner message={message} type="success" onClose={() => setMessage(null)} />}

      {loading ? (
        <StatusBanner loading loadingText="Loading FG Master…" />
      ) : (
        <>
          {!status?.configured && (
            <div style={{ ...cardStyle, display: 'flex', gap: 10, alignItems: 'center', borderColor: '#F59E0B', background: '#FFFBEB' }}>
              <AlertTriangle size={18} color="#B45309" />
              <span style={{ fontSize: '0.875rem', color: '#92400E' }}>
                FG Master is not configured in this environment. Imports continue with the original product names
                until a master is uploaded and published.
              </span>
            </div>
          )}

          {status?.active && <VersionSummary title="Active master" version={status.active} />}

          <div style={{ ...cardStyle, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <input
              ref={fileRef}
              type="file"
              accept=".xlsx,.xlsm"
              style={{ display: 'none' }}
              onChange={e => void onFile(e.target.files?.[0])}
            />
            <button type="button" style={buttonStyle(BLUE, busy)} disabled={busy} onClick={() => fileRef.current?.click()}>
              <Upload size={15} /> {status?.active ? 'Upload replacement FG Master' : 'Upload FG Master'}
            </button>
            <span style={{ fontSize: '0.8125rem', color: '#6B7280' }}>
              .xlsx / .xlsm, up to {MAX_FILE_MB} MB. Columns Material, Plant and Valuation Type are ignored.
            </span>
            {busy && progress > 0 && progress < 100 && (
              <span style={{ fontSize: '0.8125rem', color: BLUE }}>Uploading… {progress}%</span>
            )}
          </div>

          {draft && (
            <>
              <VersionSummary title="Draft awaiting publish" version={draft} />
              {upload && (upload.invalid_sample.length > 0 || upload.duplicates_sample.length > 0) && (
                <div style={{ ...cardStyle, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12 }}>
                  <SampleList
                    title="Invalid rows (sample)"
                    items={upload.invalid_sample.map(i => `Row ${i.row}: ${i.value || '(empty)'} — ${i.reason}`)}
                    total={draft.invalid_count}
                  />
                  <SampleList
                    title="Duplicates removed (sample)"
                    items={upload.duplicates_sample.map(d => `Row ${d.row}: ${d.value} (same as row ${d.first_row})`)}
                    total={draft.duplicate_count}
                  />
                </div>
              )}
              {draftDiff && <DiffPanel diff={draftDiff} />}
              <div style={{ display: 'flex', gap: 10 }}>
                <button type="button" style={buttonStyle(TEAL, busy)} disabled={busy} onClick={() => setConfirming(true)}>
                  <CheckCircle2 size={15} /> Publish v{draft.version_number}
                </button>
                <button type="button" style={buttonStyle('#6B7280', busy)} disabled={busy} onClick={() => void discard()}>
                  <X size={15} /> Discard draft
                </button>
              </div>
            </>
          )}

          <div style={{ ...cardStyle, padding: 0, overflow: 'hidden' }}>
            <div style={{ padding: '12px 14px', fontWeight: 700, color: BLUE }}>Upload history</div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }} aria-label="FG Master versions">
              <thead>
                <tr style={{ background: '#F9FAFB' }}>
                  {['Version', 'Status', 'File', 'Products', 'Duplicates', 'Invalid', 'Uploaded', 'Published'].map(h => (
                    <th key={h} style={{ ...cellStyle, fontWeight: 600 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {versions.length === 0 ? (
                  <tr>
                    <td colSpan={8} style={{ ...cellStyle, textAlign: 'center', color: '#6B7280', padding: '24px 12px' }}>
                      No FG Master uploaded in this environment yet.
                    </td>
                  </tr>
                ) : (
                  versions.map(v => (
                    <tr key={v.id}>
                      <td style={cellStyle}>v{v.version_number}</td>
                      <td style={{ ...cellStyle, fontWeight: 600, color: v.status === 'active' ? GREEN : '#374151' }}>{v.status}</td>
                      <td style={cellStyle}>{v.file_name}</td>
                      <td style={cellStyle}>{v.valid_count}</td>
                      <td style={cellStyle}>{v.duplicate_count}</td>
                      <td style={cellStyle}>{v.invalid_count}</td>
                      <td style={cellStyle}>{formatDate(v.uploaded_at)} · {v.uploaded_by_name || '—'}</td>
                      <td style={cellStyle}>
                        {v.published_at ? `${formatDate(v.published_at)} · ${v.published_by_name || '—'}` : '—'}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {confirming && draft && (
        <div
          role="dialog"
          aria-modal="true"
          style={{ position: 'fixed', inset: 0, background: 'rgba(17,24,39,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}
        >
          <div style={{ ...cardStyle, width: 'min(520px, 92vw)', padding: 22 }}>
            <h2 style={{ margin: 0, fontSize: '1.05rem', color: BLUE }}>Publish FG Master v{draft.version_number}?</h2>
            <ul style={{ fontSize: '0.875rem', color: '#374151', lineHeight: 1.6, paddingLeft: 18 }}>
              <li>{draft.valid_count} products become the active master{status?.active ? `, replacing v${status.active.version_number}` : ''}.</li>
              {draftDiff && (
                <li>
                  {draftDiff.new_count} new · {draftDiff.removed_count} removed · {draftDiff.affected_mappings_count} approved
                  mapping(s) reference removed descriptions.
                </li>
              )}
              <li>Only future imports are standardized. Existing sales records are not changed.</li>
            </ul>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button type="button" style={buttonStyle('#6B7280', busy)} disabled={busy} onClick={() => setConfirming(false)}>
                Cancel
              </button>
              <button type="button" style={buttonStyle(TEAL, busy)} disabled={busy} onClick={() => void publish()}>
                Confirm publish
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
