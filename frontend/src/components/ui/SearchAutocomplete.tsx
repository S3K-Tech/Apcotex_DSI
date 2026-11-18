import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { Search, X } from 'lucide-react';
import { BORDER } from '../../constants/theme';

type Props = {
  label: string;
  options: string[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** Value meaning "no filter" */
  allValue?: string;
  width?: number | string;
};

/**
 * Search-with-autocomplete filter (case-insensitive partial match).
 * Dropdown is portaled so parent overflow never hides options.
 */
export function SearchAutocomplete({
  label,
  options,
  value,
  onChange,
  placeholder = 'Search…',
  allValue = 'All',
  width = 220,
}: Props) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number; width: number } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const inputWrapRef = useRef<HTMLDivElement>(null);

  const displayValue = value && value !== allValue ? value : '';

  useEffect(() => {
    if (!open) {
      setQuery(displayValue);
    }
  }, [displayValue, open]);

  const updateMenuPos = () => {
    const el = inputWrapRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setMenuPos({
      top: rect.bottom + 4,
      left: rect.left,
      width: Math.max(rect.width, 220),
    });
  };

  useEffect(() => {
    if (!open) return;
    updateMenuPos();
    const onScroll = () => updateMenuPos();
    window.addEventListener('resize', updateMenuPos);
    window.addEventListener('scroll', onScroll, true);
    return () => {
      window.removeEventListener('resize', updateMenuPos);
      window.removeEventListener('scroll', onScroll, true);
    };
  }, [open]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (rootRef.current?.contains(target)) return;
      const menu = document.getElementById(`sac-menu-${label}`);
      if (menu?.contains(target)) return;
      setOpen(false);
      setQuery(displayValue);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [displayValue, label]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const norm = (s: string) => s.toLowerCase().replace(/-/g, '').replace(/\s+/g, ' ').trim();
    const base = options.filter(o => o && o !== allValue);
    if (!q) return base; // show every DB value when browsing
    const nq = norm(q);
    return base.filter(o => {
      const ol = o.toLowerCase();
      if (ol.includes(q)) return true;
      return norm(o).includes(nq);
    });
  }, [options, query, allValue]);

  const select = (next: string) => {
    onChange(next);
    setQuery(next === allValue ? '' : next);
    setOpen(false);
  };

  const menu =
    open && menuPos
      ? createPortal(
          <div
            id={`sac-menu-${label}`}
            role="listbox"
            aria-label={`${label} options`}
            style={{
              position: 'fixed',
              zIndex: 5000,
              top: menuPos.top,
              left: menuPos.left,
              width: menuPos.width,
              maxHeight: 320,
              overflowY: 'auto',
              background: 'white',
              border: `1px solid ${BORDER}`,
              borderRadius: 8,
              boxShadow: '0 12px 32px rgba(15, 23, 42, 0.18)',
            }}
          >
            <button type="button" onClick={() => select(allValue)} style={optionStyle(value === allValue || !value)}>
              All {label}s
            </button>
            {options.length === 0 ? (
              <div style={{ padding: '10px 12px', fontSize: '0.75rem', color: '#9CA3AF' }}>
                No values loaded from the database yet.
              </div>
            ) : filtered.length === 0 ? (
              <div style={{ padding: '10px 12px', fontSize: '0.75rem', color: '#9CA3AF' }}>No matches</div>
            ) : (
              filtered.map(opt => (
                <button key={opt} type="button" onClick={() => select(opt)} style={optionStyle(value === opt)} title={opt}>
                  {opt}
                </button>
              ))
            )}
          </div>,
          document.body,
        )
      : null;

  return (
    <div ref={rootRef} style={{ position: 'relative', width, minWidth: 160 }}>
      <div style={{ fontSize: '0.65rem', color: '#6B7280', fontWeight: 600, marginBottom: 3 }}>{label}</div>
      <div
        ref={inputWrapRef}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          height: 32,
          padding: '0 8px',
          border: `1px solid ${BORDER}`,
          borderRadius: 6,
          background: 'white',
        }}
      >
        <Search size={13} color="#9CA3AF" />
        <input
          value={query}
          placeholder={placeholder}
          onFocus={() => {
            setOpen(true);
            // next frame so layout is ready
            requestAnimationFrame(updateMenuPos);
          }}
          onClick={() => {
            setOpen(true);
            requestAnimationFrame(updateMenuPos);
          }}
          onChange={e => {
            setQuery(e.target.value);
            setOpen(true);
            requestAnimationFrame(updateMenuPos);
          }}
          onKeyDown={e => {
            if (e.key === 'Enter' && filtered[0]) {
              e.preventDefault();
              select(filtered[0]);
            }
            if (e.key === 'Escape') {
              setOpen(false);
              setQuery(displayValue);
            }
          }}
          style={{
            flex: 1,
            border: 'none',
            outline: 'none',
            fontSize: '0.8125rem',
            color: '#374151',
            background: 'transparent',
            minWidth: 0,
          }}
        />
        {(displayValue || query) && (
          <button
            type="button"
            title="Clear"
            onClick={() => select(allValue)}
            style={{
              border: 'none',
              background: 'transparent',
              padding: 0,
              cursor: 'pointer',
              display: 'flex',
              color: '#9CA3AF',
            }}
          >
            <X size={13} />
          </button>
        )}
      </div>
      {menu}
    </div>
  );
}

function optionStyle(active: boolean): CSSProperties {
  return {
    display: 'block',
    width: '100%',
    textAlign: 'left',
    padding: '8px 12px',
    border: 'none',
    background: active ? '#EFF6FF' : 'transparent',
    color: active ? '#1D4ED8' : '#374151',
    fontSize: '0.8125rem',
    cursor: 'pointer',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  };
}
