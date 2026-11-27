import { useEffect, useState, type KeyboardEvent, type ReactNode } from 'react';
import {
  AlertTriangle,
  Download,
  HelpCircle,
  Info,
  Lock,
  MessageSquare,
  RotateCcw,
  SearchX,
  Send,
} from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Pagination } from '../../components/ui/Pagination';
import { BG, BLUE, BORDER, RED, TEAL, TEXT, TEXT_SECONDARY } from '../../constants/theme';
import { useChatAssistant } from '../../context/ChatAssistantContext';
import { chatWithDataService } from '../../services/chatWithData.service';
import type { ChatDataAnswer, ChatDataChart, ChatDataTable, ChatDataTableColumn } from '../../types';

const CHART_COLORS = ['#0F766E', '#1D4ED8', '#B45309', '#BE123C', '#7C3AED', '#0E7490', '#4D7C0F', '#C2410C'];

const STATUS_STYLE: Record<
  ChatDataAnswer['status'],
  { label: string; color: string; background: string; Icon: typeof Info } | null
> = {
  answer: null,
  clarification: { label: 'Needs clarification', color: '#92400E', background: '#FFFBEB', Icon: HelpCircle },
  restricted: { label: 'Restricted', color: RED, background: '#FEF2F2', Icon: Lock },
  no_data: { label: 'No data', color: '#475569', background: '#F1F5F9', Icon: SearchX },
  unsupported: { label: 'Not supported', color: '#475569', background: '#F1F5F9', Icon: Info },
  error: { label: 'Error', color: RED, background: '#FEF2F2', Icon: AlertTriangle },
};

const MT_FORMAT = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 3, maximumFractionDigits: 3 });
const INT_FORMAT = new Intl.NumberFormat('en-IN');

function formatCell(
  column: ChatDataTableColumn,
  row: Record<string, string | number | null>,
  value: string | number | null | undefined,
): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value !== 'number') return String(value);
  if (column.key === 'share_pct') return `${value.toFixed(1)}%`;
  if (column.key === 'rank') return String(value);
  const mtLike = column.label.includes('MT') || String(row.metric ?? '').includes('MT');
  const sign = column.key === 'change' && value > 0 ? '+' : '';
  return sign + (mtLike ? MT_FORMAT.format(value) : INT_FORMAT.format(value));
}

function ResultTable({ table }: { table: ChatDataTable }) {
  if (!table.rows.length) return null;
  return (
    <div style={{ overflowX: 'auto', marginTop: 12, border: `1px solid ${BORDER}`, borderRadius: 8 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
        <thead>
          <tr style={{ background: BG }}>
            {table.columns.map(col => (
              <th
                key={col.key}
                scope="col"
                style={{
                  padding: '8px 12px',
                  textAlign: col.numeric ? 'right' : 'left',
                  fontWeight: 600,
                  color: TEXT_SECONDARY,
                  whiteSpace: 'nowrap',
                  borderBottom: `1px solid ${BORDER}`,
                }}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, i) => (
            <tr key={i} style={{ borderTop: i ? `1px solid ${BORDER}` : undefined }}>
              {table.columns.map(col => (
                <td
                  key={col.key}
                  style={{
                    padding: '8px 12px',
                    textAlign: col.numeric ? 'right' : 'left',
                    color: TEXT,
                    fontVariantNumeric: col.numeric ? 'tabular-nums' : undefined,
                    whiteSpace: col.numeric ? 'nowrap' : 'normal',
                  }}
                >
                  {formatCell(col, row, row[col.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ResultChart({ chart }: { chart: ChatDataChart }) {
  if (!chart.points?.length) return null;
  const data = chart.points.map(p => ({ name: p.name, value: Number(p.value) || 0 }));
  const unit = chart.unit || 'MT';
  return (
    <div
      style={{
        marginTop: 14,
        border: `1px solid ${BORDER}`,
        borderRadius: 10,
        background: 'white',
        padding: '10px 12px 6px',
      }}
    >
      <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: TEXT, marginBottom: 8 }}>{chart.title}</div>
      <div style={{ width: '100%', height: 260 }}>
        <ResponsiveContainer>
          {chart.type === 'pie' ? (
            <PieChart>
              <Pie data={data} dataKey="value" nameKey="name" outerRadius={90} label={({ name }) => name}>
                {data.map((_, i) => (
                  <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip formatter={(v: number) => [`${v} ${unit}`, 'Quantity']} />
              <Legend />
            </PieChart>
          ) : chart.type === 'line' ? (
            <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} interval={0} angle={data.length > 6 ? -30 : 0} textAnchor={data.length > 6 ? 'end' : 'middle'} height={data.length > 6 ? 60 : 30} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip formatter={(v: number) => [`${v} ${unit}`, 'Quantity']} />
              <Line type="monotone" dataKey="value" stroke={TEAL} strokeWidth={2} dot={{ r: 3 }} />
            </LineChart>
          ) : (
            <BarChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} interval={0} angle={data.length > 5 ? -30 : 0} textAnchor={data.length > 5 ? 'end' : 'middle'} height={data.length > 5 ? 70 : 30} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip formatter={(v: number) => [`${v} ${unit}`, 'Quantity']} />
              <Bar dataKey="value" fill={BLUE} radius={[4, 4, 0, 0]} />
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function Chip({ children }: { children: ReactNode }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '2px 10px',
        borderRadius: 999,
        fontSize: '0.75rem',
        color: BLUE,
        background: '#EFF6FF',
        border: '1px solid #DBEAFE',
      }}
    >
      {children}
    </span>
  );
}

function AnswerCard({
  answer,
  question,
  onOption,
  disabled,
  latest,
  onPage,
  paging = false,
  pageError = null,
}: {
  answer: ChatDataAnswer;
  question?: string;
  onOption: (text: string) => void;
  disabled: boolean;
  latest: boolean;
  onPage?: (page: number) => void;
  paging?: boolean;
  pageError?: string | null;
}) {
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const pagination = answer.status === 'answer' ? answer.pagination : null;
  const badge = STATUS_STYLE[answer.status];
  const showFacts = answer.status === 'answer' && answer.facts.length > 0;
  const filterEntries = Object.entries(answer.filters || {});
  const canExport =
    answer.status === 'answer' &&
    Boolean(
      (answer.table && answer.table.rows.length > 0) ||
        answer.facts.length > 0 ||
        (answer.chart && answer.chart.points.length > 0),
    );

  const handleExport = async () => {
    if (exporting) return;
    setExporting(true);
    setExportError(null);
    try {
      await chatWithDataService.exportExcel(answer, question);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : 'Excel export failed.');
    } finally {
      setExporting(false);
    }
  };

  return (
    <div
      style={{
        background: 'white',
        border: `1px solid ${BORDER}`,
        borderRadius: 12,
        padding: '14px 16px',
        maxWidth: 860,
        width: '100%',
        boxShadow: '0 1px 2px rgba(15,23,42,0.04)',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: 12,
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          {badge && (
            <div
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                padding: '2px 10px',
                borderRadius: 999,
                fontSize: '0.75rem',
                fontWeight: 600,
                color: badge.color,
                background: badge.background,
                marginBottom: 8,
              }}
            >
              <badge.Icon size={14} aria-hidden />
              {badge.label}
            </div>
          )}
          <p style={{ margin: 0, fontSize: '0.9rem', lineHeight: 1.6, color: TEXT, whiteSpace: 'pre-line' }}>
            {answer.message}
          </p>
        </div>

        {canExport && (
          <button
            type="button"
            onClick={() => void handleExport()}
            disabled={exporting}
            title="Download this response as an Excel (.xlsx) file"
            aria-label="Download Excel"
            style={{
              flexShrink: 0,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 5,
              padding: '5px 10px',
              borderRadius: 8,
              border: `1px solid ${BORDER}`,
              background: exporting ? '#F8FAFC' : 'white',
              color: exporting ? TEXT_SECONDARY : TEAL,
              fontSize: '0.75rem',
              fontWeight: 600,
              cursor: exporting ? 'wait' : 'pointer',
              whiteSpace: 'nowrap',
            }}
          >
            <Download size={13} aria-hidden />
            {exporting ? 'Exporting…' : 'Excel'}
          </button>
        )}
      </div>

      {exportError && (
        <div role="alert" style={{ marginTop: 6, fontSize: '0.75rem', color: RED }}>
          {exportError}
        </div>
      )}

      {(answer.periods.length > 0 || filterEntries.length > 0) && answer.status === 'answer' && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
          {answer.periods.map(p => (
            <Chip key={p}>{p}</Chip>
          ))}
          {filterEntries.map(([k, v]) => (
            <Chip key={k}>
              {k}: {v}
            </Chip>
          ))}
        </div>
      )}

      {showFacts && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
            gap: 8,
            marginTop: 12,
          }}
        >
          {answer.facts.map(f => (
            <div key={f.key} style={{ background: BG, borderRadius: 8, padding: '8px 12px' }}>
              <div style={{ fontSize: '0.7rem', color: TEXT_SECONDARY, textTransform: 'uppercase', letterSpacing: 0.3 }}>
                {f.label}
              </div>
              <div style={{ fontSize: '0.95rem', fontWeight: 600, color: TEXT, marginTop: 2 }}>{f.display}</div>
            </div>
          ))}
        </div>
      )}

      {answer.chart && <ResultChart chart={answer.chart} />}

      {answer.table && (
        <div style={{ opacity: paging ? 0.55 : 1, transition: 'opacity 0.15s' }} aria-busy={paging}>
          <ResultTable table={answer.table} />
        </div>
      )}

      {pagination && onPage && (
        <Pagination
          currentPage={pagination.current_page}
          totalPages={pagination.total_pages}
          totalRecords={pagination.total_records}
          firstRecord={pagination.first_record}
          lastRecord={pagination.last_record}
          entity={pagination.entity}
          disabled={paging || disabled}
          onPageChange={onPage}
        />
      )}
      {paging && (
        <div role="status" style={{ marginTop: 6, fontSize: '0.75rem', color: TEXT_SECONDARY }}>
          Loading page…
        </div>
      )}
      {pageError && (
        <div role="alert" style={{ marginTop: 6, fontSize: '0.75rem', color: RED }}>
          {pageError}
        </div>
      )}

      {answer.clarification && answer.clarification.options.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }} role="group" aria-label="Suggested answers">
          {answer.clarification.options.map(opt => (
            <button
              key={opt}
              type="button"
              disabled={disabled}
              onClick={() => onOption(opt)}
              style={{
                padding: '6px 12px',
                fontSize: '0.8125rem',
                color: BLUE,
                background: 'white',
                border: `1px solid ${TEAL}`,
                borderRadius: 8,
                cursor: disabled ? 'not-allowed' : 'pointer',
              }}
            >
              {opt}
            </button>
          ))}
        </div>
      )}

      {latest && answer.status === 'answer' && answer.refinements && answer.refinements.length > 0 && (
        <div
          style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginTop: 12 }}
          role="group"
          aria-label="Optional quarter filter"
        >
          <span style={{ fontSize: '0.75rem', color: TEXT_SECONDARY }}>Narrow to a quarter (optional):</span>
          {answer.refinements.map(opt => (
            <button
              key={opt}
              type="button"
              disabled={disabled}
              onClick={() => onOption(opt)}
              style={{
                padding: '4px 10px',
                fontSize: '0.75rem',
                color: BLUE,
                background: 'white',
                border: `1px solid ${TEAL}`,
                borderRadius: 8,
                cursor: disabled ? 'not-allowed' : 'pointer',
              }}
            >
              {opt}
            </button>
          ))}
        </div>
      )}

      {answer.caveats.length > 0 && (
        <ul style={{ margin: '12px 0 0', paddingLeft: 18, fontSize: '0.75rem', color: TEXT_SECONDARY, lineHeight: 1.5 }}>
          {answer.caveats.map(c => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      )}

      {answer.scope_note && answer.status !== 'clarification' && (
        <div style={{ marginTop: 10, fontSize: '0.7rem', color: '#94A3B8' }}>
          {answer.scope_note}
          {answer.narrative_source === 'llm_verified' ? ' · Wording checked against the figures.' : ''}
        </div>
      )}
    </div>
  );
}

export function ChatWithDataPanel({
  compact = false,
  hideChrome = false,
}: {
  compact?: boolean;
  hideChrome?: boolean;
}) {
  const {
    availability,
    statusError,
    thread,
    input,
    setInput,
    loading,
    send,
    turnPage,
    newChat,
    maxChars,
    enabled,
    inputRef,
    endRef,
  } = useChatAssistant();

  const tooLong = input.trim().length > maxChars;

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send(input);
    }
  };

  return (
    <div
      style={{
        padding: hideChrome ? (compact ? '12px' : '16px 20px') : '24px 28px',
        maxWidth: hideChrome ? 'none' : 1280,
        height: '100%',
        boxSizing: 'border-box',
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
      }}
    >
      <style>
        {`
          @keyframes cwdPulse { 0%, 100% { opacity: 0.3; transform: scale(0.8); } 50% { opacity: 1; transform: scale(1.2); } }
          @media (prefers-reduced-motion: reduce) { .cwd-dot { animation: none !important; } }
        `}
      </style>
      {!hideChrome && (
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, marginBottom: 16, flexWrap: 'wrap' }}>
          <div>
            <h1 style={{ margin: 0, fontSize: '1.35rem', fontWeight: 700, color: '#0F172A' }}>Chat with Data</h1>
            <p style={{ margin: '6px 0 0', fontSize: '0.875rem', color: '#64748B' }}>
              Ask questions about distributor sales quantities (MT) within your data access. Financial years run April–March.
            </p>
          </div>
          <button
            type="button"
            onClick={newChat}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '8px 14px',
              fontSize: '0.8125rem',
              color: BLUE,
              background: 'white',
              border: `1px solid ${BORDER}`,
              borderRadius: 8,
              cursor: 'pointer',
            }}
          >
            <RotateCcw size={14} aria-hidden />
            New conversation
          </button>
        </div>
      )}

      {hideChrome && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
          <button
            type="button"
            onClick={newChat}
            title="Start a new chat (current chat is saved)"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '6px 10px',
              fontSize: '0.75rem',
              color: BLUE,
              background: 'white',
              border: `1px solid ${BORDER}`,
              borderRadius: 8,
              cursor: 'pointer',
            }}
          >
            <RotateCcw size={12} aria-hidden />
            New conversation
          </button>
        </div>
      )}

      {statusError && (
        <div role="alert" style={{ padding: 16, borderRadius: 10, background: '#FEF2F2', color: RED, fontSize: '0.875rem' }}>
          {statusError}
        </div>
      )}

      {availability && !availability.enabled && (
        <div
          role="status"
          style={{ padding: 20, borderRadius: 12, border: `1px solid ${BORDER}`, background: 'white', color: TEXT_SECONDARY, fontSize: '0.875rem' }}
        >
          <strong style={{ color: TEXT }}>Chat with Data is not enabled on this server.</strong>
          <div style={{ marginTop: 6 }}>Please ask an administrator to enable it.</div>
        </div>
      )}

      {enabled && (
        <div
          style={{
            flex: 1,
            minHeight: compact ? 200 : 360,
            display: 'flex',
            flexDirection: 'column',
            border: `1px solid ${BORDER}`,
            borderRadius: 12,
            background: BG,
            overflow: 'hidden',
          }}
        >
          <div
            role="log"
            aria-live="polite"
            aria-label="Conversation"
            style={{ flex: 1, overflowY: 'auto', padding: compact ? 12 : 20, display: 'flex', flexDirection: 'column', gap: 14 }}
          >
            {thread.length === 0 && (
              <div style={{ margin: 'auto 0', textAlign: 'center', padding: compact ? '16px 4px' : '24px 8px' }}>
                <div
                  style={{
                    width: 52,
                    height: 52,
                    borderRadius: '50%',
                    margin: '0 auto 12px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    background: `linear-gradient(135deg, ${TEAL} 0%, ${BLUE} 100%)`,
                  }}
                >
                  <MessageSquare size={24} color="white" aria-hidden />
                </div>
                <div style={{ fontSize: '1rem', fontWeight: 600, color: TEXT }}>What would you like to know?</div>
                <div style={{ fontSize: '0.8125rem', color: TEXT_SECONDARY, marginTop: 4 }}>
                  Answers are computed from consolidated sales data you have access to. Always name a financial year.
                </div>
                {availability && availability.suggestions.length > 0 && (
                  <div
                    style={{
                      display: 'flex',
                      flexWrap: 'wrap',
                      gap: 8,
                      justifyContent: 'center',
                      marginTop: 18,
                      maxWidth: 760,
                      marginInline: 'auto',
                    }}
                    role="group"
                    aria-label="Suggested questions"
                  >
                    {availability.suggestions.map(s => (
                      <button
                        key={s}
                        type="button"
                        onClick={() => void send(s)}
                        style={{
                          padding: '8px 14px',
                          fontSize: '0.8125rem',
                          color: BLUE,
                          background: 'white',
                          border: `1px solid ${BORDER}`,
                          borderRadius: 8,
                          cursor: 'pointer',
                          textAlign: 'left',
                        }}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {thread.map((item, index) =>
              item.role === 'user' ? (
                <div key={item.id} style={{ display: 'flex', justifyContent: 'flex-end' }}>
                  <div
                    style={{
                      maxWidth: '75%',
                      padding: '10px 14px',
                      borderRadius: 12,
                      background: TEAL,
                      color: 'white',
                      fontSize: '0.875rem',
                      lineHeight: 1.5,
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                    }}
                  >
                    {item.text}
                  </div>
                </div>
              ) : (
                <div key={item.id} style={{ display: 'flex', justifyContent: 'flex-start' }}>
                  <AnswerCard
                    answer={item.answer}
                    question={
                      index > 0 && thread[index - 1]?.role === 'user'
                        ? (thread[index - 1] as { role: 'user'; text: string }).text
                        : undefined
                    }
                    onOption={opt => void send(opt)}
                    disabled={loading}
                    latest={index === thread.length - 1}
                    onPage={
                      item.answer.spec
                        ? page => void turnPage(item.id, item.answer.spec!, index === thread.length - 1, page)
                        : undefined
                    }
                    paging={item.paging}
                    pageError={item.pageError}
                  />
                </div>
              ),
            )}

            {loading && (
              <div
                role="status"
                style={{
                  alignSelf: 'flex-start',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '10px 14px',
                  borderRadius: 12,
                  background: 'white',
                  border: `1px solid ${BORDER}`,
                  fontSize: '0.8125rem',
                  color: TEXT_SECONDARY,
                }}
              >
                {[0, 0.2, 0.4].map(delay => (
                  <span
                    key={delay}
                    className="cwd-dot"
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: '50%',
                      background: TEAL,
                      animation: `cwdPulse 1.4s ease-in-out ${delay}s infinite`,
                    }}
                  />
                ))}
                <span style={{ marginLeft: 6 }}>Analysing your data…</span>
              </div>
            )}
            <div ref={endRef} />
          </div>

          <form
            onSubmit={e => {
              e.preventDefault();
              void send(input);
            }}
            style={{ borderTop: `1px solid ${BORDER}`, background: 'white', padding: compact ? '10px 12px' : '12px 16px' }}
          >
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end' }}>
              <label htmlFor="chat-with-data-input" style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>
                Ask a question about your sales data
              </label>
              <textarea
                id="chat-with-data-input"
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                rows={compact ? 2 : 2}
                placeholder="e.g. Top 10 customers of a distributor in FY 2026-27"
                disabled={loading}
                aria-describedby="chat-with-data-hint"
                aria-invalid={tooLong}
                style={{
                  flex: 1,
                  resize: 'vertical',
                  minHeight: 44,
                  maxHeight: compact ? 120 : 200,
                  padding: '10px 14px',
                  fontSize: '0.875rem',
                  fontFamily: 'inherit',
                  border: `1px solid ${tooLong ? RED : BORDER}`,
                  borderRadius: 8,
                  outline: 'none',
                  background: loading ? BG : 'white',
                }}
              />
              <button
                type="submit"
                disabled={loading || !input.trim() || tooLong}
                aria-label="Send question"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '10px 16px',
                  fontSize: '0.875rem',
                  fontWeight: 600,
                  color: 'white',
                  background: loading || !input.trim() || tooLong ? '#94A3B8' : BLUE,
                  border: 'none',
                  borderRadius: 8,
                  cursor: loading || !input.trim() || tooLong ? 'not-allowed' : 'pointer',
                }}
              >
                <Send size={16} aria-hidden />
                Send
              </button>
            </div>
            <div
              id="chat-with-data-hint"
              style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6, fontSize: '0.7rem', color: tooLong ? RED : '#94A3B8' }}
            >
              <span>Enter to send · Shift+Enter for a new line</span>
              <span>
                {input.trim().length}/{maxChars}
              </span>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

/** Route entry: opens the shared assistant in full-screen mode. */
export function ChatWithData() {
  const { openFullscreen, mode } = useChatAssistant();
  useEffect(() => {
    openFullscreen();
  }, [openFullscreen]);

  // Panel is rendered by FloatingChatAssistant; keep route for deep links / expand.
  if (mode === 'fullscreen') return null;
  return <ChatWithDataPanel />;
}
