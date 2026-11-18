import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { CheckCircle2, Loader2, Mail, Plus, Trash2, X } from 'lucide-react';
import { ApiError } from '../../api';
import { BLUE, BORDER } from '../../constants/theme';
import {
  ExecutiveReportService,
  pdfFileName,
  type EmailResult,
  type ExecutiveReport,
  type ReportFilters,
} from '../../services/executiveReport.service';

type Row = {
  id: string;
  role: string;
  roleLabel: string;
  name: string;
  email: string;
  include: boolean;
  custom: boolean;
};

type Step = 'edit' | 'confirm' | 'sending' | 'sent';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

const input: CSSProperties = {
  width: '100%',
  height: 32,
  border: `1px solid ${BORDER}`,
  borderRadius: 6,
  padding: '0 8px',
  fontSize: '0.8125rem',
  boxSizing: 'border-box',
};

const button: CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  height: 34,
  padding: '0 14px',
  borderRadius: 6,
  fontSize: '0.8125rem',
  fontWeight: 600,
  cursor: 'pointer',
  background: 'white',
  color: '#374151',
  border: `1px solid ${BORDER}`,
};

const primary: CSSProperties = { ...button, background: BLUE, color: 'white', border: 'none' };

function newKey(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export function EmailReportDialog({
  report,
  filters,
  onClose,
}: {
  report: ExecutiveReport;
  filters: ReportFilters;
  onClose: () => void;
}) {
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [step, setStep] = useState<Step>('edit');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [savingDefaults, setSavingDefaults] = useState(false);
  const [sendKey, setSendKey] = useState<string>('');
  const [result, setResult] = useState<EmailResult | null>(null);

  useEffect(() => {
    ExecutiveReportService.getRecipients()
      .then(list =>
        setRows(
          list.map(r => ({
            id: r.role,
            role: r.role,
            roleLabel: `${r.role} — ${r.role_label}`,
            name: r.name || '',
            email: r.email || '',
            include: Boolean(r.email),
            custom: false,
          })),
        ),
      )
      .catch(err => setError(err instanceof ApiError ? err.message : 'Could not load management recipients.'))
      .finally(() => setLoading(false));
  }, []);

  const update = (id: string, patch: Partial<Row>) =>
    setRows(prev => prev.map(r => (r.id === id ? { ...r, ...patch } : r)));

  const selected = useMemo(() => rows.filter(r => r.include), [rows]);
  const invalid = useMemo(
    () => selected.filter(r => !EMAIL_RE.test(r.email.trim())).map(r => r.id),
    [selected],
  );
  const missingConfig = rows.filter(r => !r.custom && !r.email.trim()).map(r => r.role);
  const canReview = selected.length > 0 && invalid.length === 0;

  const addCustom = () =>
    setRows(prev => [
      ...prev,
      { id: newKey(), role: 'CUSTOM', roleLabel: 'Additional recipient', name: '', email: '', include: true, custom: true },
    ]);

  const saveDefaults = async () => {
    const management = rows.filter(r => !r.custom);
    const bad = management.filter(r => r.email.trim() && !EMAIL_RE.test(r.email.trim()));
    if (bad.length) {
      setError(`Fix the email address for ${bad.map(r => r.role).join(', ')} before saving.`);
      return;
    }
    setSavingDefaults(true);
    setError(null);
    setNotice(null);
    try {
      await ExecutiveReportService.saveRecipients(
        management.map(r => ({ role: r.role, name: r.name.trim() || null, email: r.email.trim() || null })),
      );
      setNotice('Management recipients saved as defaults.');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save recipients.');
    } finally {
      setSavingDefaults(false);
    }
  };

  const review = () => {
    setError(null);
    setNotice(null);
    setSendKey(newKey());
    setStep('confirm');
  };

  const send = async () => {
    setStep('sending');
    setError(null);
    try {
      const res = await ExecutiveReportService.sendEmail(report.id, {
        filters,
        idempotency_key: sendKey,
        recipients: selected.map(r => ({ role: r.role, name: r.name.trim() || null, email: r.email.trim() })),
      });
      if (res.status !== 'SENT') throw new ApiError('The email service did not confirm the send.', 502);
      setResult(res);
      setStep('sent');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The email could not be sent.');
      setStep('confirm');
      setSendKey(newKey());
    }
  };

  const busy = step === 'sending' || savingDefaults;

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(17,24,39,0.45)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}
      onClick={() => !busy && onClose()}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{ background: 'white', borderRadius: 12, width: 'min(860px, 100%)', maxHeight: '92vh', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 50px rgba(0,0,0,0.2)' }}
      >
        <div style={{ padding: '16px 20px', borderBottom: `1px solid ${BORDER}`, display: 'flex', justifyContent: 'space-between', gap: 12 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 700, color: BLUE }}>
              Send Secondary Sales Report via Email
            </h2>
            <div style={{ fontSize: '0.75rem', color: '#6B7280', marginTop: 4 }}>Reporting period: {report.period_label}</div>
          </div>
          <button type="button" onClick={onClose} disabled={busy} style={{ border: 'none', background: 'transparent', cursor: 'pointer' }} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: '16px 20px', overflowY: 'auto', flex: 1 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', gap: '6px 12px', fontSize: '0.8125rem', marginBottom: 16 }}>
            <span style={{ color: '#6B7280' }}>Subject</span>
            <strong style={{ color: '#111827' }}>{report.email_subject}</strong>
            <span style={{ color: '#6B7280' }}>Attachment</span>
            <span>
              {pdfFileName(report.report_title || 'Secondary Sales Report', report.period_label)} (dashboard charts +
              executive summary)
            </span>
          </div>

          {error && (
            <div style={{ background: '#FEF2F2', border: '1px solid #FCA5A5', color: '#991B1B', borderRadius: 8, padding: '8px 12px', fontSize: '0.8125rem', marginBottom: 12 }}>
              {error}
            </div>
          )}
          {notice && (
            <div style={{ background: '#ECFDF5', border: '1px solid #6EE7B7', color: '#065F46', borderRadius: 8, padding: '8px 12px', fontSize: '0.8125rem', marginBottom: 12 }}>
              {notice}
            </div>
          )}

          {step === 'sent' && result ? (
            <div style={{ textAlign: 'center', padding: '24px 0' }}>
              <CheckCircle2 size={36} color="#059669" />
              <div style={{ fontWeight: 700, color: '#065F46', margin: '8px 0 4px' }}>Email sent</div>
              <div style={{ fontSize: '0.8125rem', color: '#374151' }}>
                The mail service accepted the report for delivery to {result.recipients.map(r => r.email).join(', ')}.
              </div>
            </div>
          ) : step === 'confirm' || step === 'sending' ? (
            <div>
              <div style={{ fontSize: '0.875rem', color: '#111827', marginBottom: 8 }}>
                The report PDF for <strong>{report.period_label}</strong> will be emailed to {selected.length} recipient
                {selected.length === 1 ? '' : 's'}:
              </div>
              <ul style={{ margin: '0 0 12px', paddingLeft: 20, fontSize: '0.8125rem' }}>
                {selected.map(r => (
                  <li key={r.id}>
                    {r.custom ? r.name || 'Additional recipient' : r.role}
                    {!r.custom && r.name ? ` (${r.name})` : ''} — {r.email.trim()}
                  </li>
                ))}
              </ul>
              <div style={{ fontSize: '0.75rem', color: '#6B7280' }}>Please confirm. The email cannot be recalled once sent.</div>
            </div>
          ) : loading ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#6B7280', fontSize: '0.8125rem' }}>
              <Loader2 size={16} className="animate-spin" /> Loading management recipients…
            </div>
          ) : (
            <>
              {missingConfig.length > 0 && (
                <div style={{ background: '#FFFBEB', border: '1px solid #FCD34D', color: '#92400E', borderRadius: 8, padding: '8px 12px', fontSize: '0.8125rem', marginBottom: 12 }}>
                  No email address is configured for {missingConfig.join(', ')}. Enter one below, or use Save as default to store it.
                </div>
              )}
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
                <thead>
                  <tr style={{ background: '#F9FAFB', borderBottom: `1px solid ${BORDER}` }}>
                    {['Recipient', 'Name', 'Email Address', 'Include', ''].map(h => (
                      <th key={h} style={{ textAlign: 'left', padding: '8px 8px', fontSize: '0.6875rem', color: '#6B7280', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => {
                    const bad = r.include && invalid.includes(r.id);
                    return (
                      <tr key={r.id} style={{ borderBottom: `1px solid ${BORDER}` }}>
                        <td style={{ padding: '8px', fontWeight: 600, color: '#111827', width: 190 }}>{r.roleLabel}</td>
                        <td style={{ padding: '8px', width: 190 }}>
                          <input style={input} value={r.name} placeholder="Name" onChange={e => update(r.id, { name: e.target.value })} />
                        </td>
                        <td style={{ padding: '8px' }}>
                          <input
                            style={{ ...input, borderColor: bad ? '#F87171' : BORDER }}
                            value={r.email}
                            type="email"
                            placeholder="name@company.com"
                            onChange={e => update(r.id, { email: e.target.value, include: r.include || Boolean(e.target.value.trim()) })}
                          />
                          {bad && <div style={{ color: '#B91C1C', fontSize: '0.6875rem', marginTop: 2 }}>Enter a valid email address</div>}
                        </td>
                        <td style={{ padding: '8px', textAlign: 'center', width: 60 }}>
                          <input type="checkbox" checked={r.include} onChange={e => update(r.id, { include: e.target.checked })} />
                        </td>
                        <td style={{ padding: '8px', width: 36 }}>
                          {r.custom && (
                            <button
                              type="button"
                              onClick={() => setRows(prev => prev.filter(x => x.id !== r.id))}
                              style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: '#B91C1C' }}
                              aria-label="Remove recipient"
                            >
                              <Trash2 size={15} />
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                <button type="button" style={button} onClick={addCustom}>
                  <Plus size={14} /> Add Recipient
                </button>
                <button type="button" style={button} onClick={() => void saveDefaults()} disabled={savingDefaults}>
                  {savingDefaults ? <Loader2 size={14} className="animate-spin" /> : null}
                  Save MD / CFO / CMO / CIO as default
                </button>
              </div>
            </>
          )}
        </div>

        <div style={{ padding: '12px 20px', borderTop: `1px solid ${BORDER}`, display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          {step === 'sent' ? (
            <button type="button" style={primary} onClick={onClose}>
              Close
            </button>
          ) : step === 'confirm' || step === 'sending' ? (
            <>
              <button type="button" style={button} onClick={() => setStep('edit')} disabled={step === 'sending'}>
                Back
              </button>
              <button type="button" style={{ ...primary, opacity: step === 'sending' ? 0.7 : 1 }} onClick={() => void send()} disabled={step === 'sending'}>
                {step === 'sending' ? <Loader2 size={14} className="animate-spin" /> : <Mail size={14} />}
                {step === 'sending' ? 'Sending…' : 'Confirm & Send'}
              </button>
            </>
          ) : (
            <>
              <button type="button" style={button} onClick={onClose}>
                Cancel
              </button>
              <button type="button" style={{ ...primary, opacity: canReview ? 1 : 0.5 }} onClick={review} disabled={!canReview || loading}>
                Review & Send
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
