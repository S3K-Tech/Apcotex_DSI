import { ChevronLeft, ChevronRight } from 'lucide-react';
import { BLUE, BORDER, TEXT, TEXT_SECONDARY } from '../../constants/theme';

export interface PaginationProps {
  currentPage: number;
  totalPages: number;
  totalRecords: number;
  firstRecord: number;
  lastRecord: number;
  /** Plural noun for the summary line, e.g. "customers". */
  entity?: string;
  disabled?: boolean;
  onPageChange: (page: number) => void;
}

const NUMBER_FORMAT = new Intl.NumberFormat('en-IN');

/** Page numbers to show: always first and last, a window around the current page, gaps as null. */
function pageWindow(current: number, total: number): (number | null)[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const pages = new Set([1, total, current - 1, current, current + 1]);
  const sorted = [...pages].filter(p => p >= 1 && p <= total).sort((a, b) => a - b);
  const out: (number | null)[] = [];
  sorted.forEach((p, i) => {
    if (i > 0 && p - sorted[i - 1] > 1) out.push(null);
    out.push(p);
  });
  return out;
}

export function Pagination({
  currentPage,
  totalPages,
  totalRecords,
  firstRecord,
  lastRecord,
  entity = 'records',
  disabled = false,
  onPageChange,
}: PaginationProps) {
  const go = (page: number) => {
    if (disabled || page < 1 || page > totalPages || page === currentPage) return;
    onPageChange(page);
  };
  const buttonStyle = (active: boolean, off: boolean) => ({
    minWidth: 32,
    height: 32,
    padding: '0 8px',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
    fontSize: '0.8125rem',
    fontWeight: active ? 600 : 400,
    color: active ? 'white' : off ? '#94A3B8' : TEXT,
    background: active ? BLUE : 'white',
    border: `1px solid ${active ? BLUE : BORDER}`,
    borderRadius: 6,
    cursor: off ? 'not-allowed' : 'pointer',
  });

  return (
    <nav
      aria-label="Pagination"
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 8,
        marginTop: 10,
      }}
    >
      <span style={{ fontSize: '0.75rem', color: TEXT_SECONDARY }} aria-live="polite">
        Showing {NUMBER_FORMAT.format(firstRecord)}–{NUMBER_FORMAT.format(lastRecord)} of{' '}
        {NUMBER_FORMAT.format(totalRecords)} {entity}
      </span>
      {totalPages > 1 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 4 }}>
          <button
            type="button"
            onClick={() => go(currentPage - 1)}
            disabled={disabled || currentPage <= 1}
            aria-label="Previous page"
            style={buttonStyle(false, disabled || currentPage <= 1)}
          >
            <ChevronLeft size={14} aria-hidden />
            <span>Previous</span>
          </button>
          {pageWindow(currentPage, totalPages).map((p, i) =>
            p === null ? (
              <span key={`gap-${i}`} style={{ padding: '0 4px', color: TEXT_SECONDARY }} aria-hidden>
                …
              </span>
            ) : (
              <button
                key={p}
                type="button"
                onClick={() => go(p)}
                disabled={disabled}
                aria-label={`Page ${p}`}
                aria-current={p === currentPage ? 'page' : undefined}
                style={buttonStyle(p === currentPage, disabled && p !== currentPage)}
              >
                {p}
              </button>
            ),
          )}
          <button
            type="button"
            onClick={() => go(currentPage + 1)}
            disabled={disabled || currentPage >= totalPages}
            aria-label="Next page"
            style={buttonStyle(false, disabled || currentPage >= totalPages)}
          >
            <span>Next</span>
            <ChevronRight size={14} aria-hidden />
          </button>
        </div>
      )}
    </nav>
  );
}
