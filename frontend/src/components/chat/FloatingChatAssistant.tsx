import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router';
import {
  Bot,
  ChevronLeft,
  ChevronRight,
  Maximize2,
  MessageSquarePlus,
  Minimize2,
  PanelLeft,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import { BLUE, BORDER, TEAL } from '../../constants/theme';
import { useChatAssistant, type ChatSessionSummary } from '../../context/ChatAssistantContext';
import { ChatWithDataPanel } from '../../pages/ChatWithData/ChatWithData';

const FLOAT_WIDTH = 420;
/** Keep shorter than typical viewports so vertical drag has real room. */
const FLOAT_HEIGHT = 520;
const EDGE = 8;
/** How much of the panel must stay on-screen (header grab area). */
const VISIBLE_HANDLE = 48;
const SIDEBAR_WIDTH = 280;
const SIDEBAR_COLLAPSED = 48;

function floatSize() {
  const width = Math.min(FLOAT_WIDTH, Math.max(280, window.innerWidth - EDGE * 2));
  const height = Math.min(FLOAT_HEIGHT, Math.max(320, Math.floor(window.innerHeight * 0.72)));
  return { width, height };
}

/** Allow free 360° movement; only keep a strip of the window visible. */
function clampPosition(x: number, y: number, width: number, height: number) {
  const minX = -(width - VISIBLE_HANDLE);
  const minY = 0;
  const maxX = window.innerWidth - VISIBLE_HANDLE;
  const maxY = window.innerHeight - VISIBLE_HANDLE;
  return {
    x: Math.min(Math.max(minX, x), maxX),
    y: Math.min(Math.max(minY, y), maxY),
  };
}

function defaultPosition() {
  const { width, height } = floatSize();
  return clampPosition(window.innerWidth - width - EDGE, window.innerHeight - height - EDGE, width, height);
}

function formatChatTime(ts: number): string {
  try {
    return new Date(ts).toLocaleString('en-IN', {
      day: '2-digit',
      month: 'short',
      hour: 'numeric',
      minute: '2-digit',
    });
  } catch {
    return '';
  }
}

function ChatHistorySidebar() {
  const {
    sessions,
    activeSessionId,
    historyCollapsed,
    setHistoryCollapsed,
    historyQuery,
    setHistoryQuery,
    selectSession,
    deleteSession,
    newChat,
  } = useChatAssistant();

  if (historyCollapsed) {
    return (
      <aside
        aria-label="Chat history"
        style={{
          width: SIDEBAR_COLLAPSED,
          flexShrink: 0,
          borderRight: `1px solid ${BORDER}`,
          background: '#0F172A',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          padding: '12px 0',
          gap: 10,
        }}
      >
        <button
          type="button"
          title="Expand chat history"
          aria-label="Expand chat history"
          onClick={() => setHistoryCollapsed(false)}
          style={sidebarIconBtn}
        >
          <PanelLeft size={16} />
        </button>
        <button type="button" title="New chat" aria-label="New chat" onClick={newChat} style={sidebarIconBtn}>
          <MessageSquarePlus size={16} />
        </button>
        <div style={{ flex: 1 }} />
        <button
          type="button"
          title="Expand chat history"
          aria-label="Expand chat history"
          onClick={() => setHistoryCollapsed(false)}
          style={sidebarIconBtn}
        >
          <ChevronRight size={16} />
        </button>
      </aside>
    );
  }

  return (
    <aside
      aria-label="Chat history"
      style={{
        width: SIDEBAR_WIDTH,
        flexShrink: 0,
        borderRight: `1px solid ${BORDER}`,
        background: '#0F172A',
        color: 'white',
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
      }}
    >
      <div style={{ padding: '12px 12px 8px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <div style={{ fontSize: '0.8125rem', fontWeight: 700, letterSpacing: 0.2 }}>Chat history</div>
          <button
            type="button"
            title="Collapse chat history"
            aria-label="Collapse chat history"
            onClick={() => setHistoryCollapsed(true)}
            style={sidebarIconBtn}
          >
            <ChevronLeft size={16} />
          </button>
        </div>
        <button
          type="button"
          onClick={newChat}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 6,
            width: '100%',
            padding: '8px 10px',
            borderRadius: 8,
            border: '1px solid rgba(255,255,255,0.18)',
            background: 'rgba(255,255,255,0.08)',
            color: 'white',
            fontSize: '0.8125rem',
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          <MessageSquarePlus size={15} />
          New chat
        </button>
        <label style={{ position: 'relative', display: 'block' }}>
          <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#94A3B8' }}>
            <Search size={14} aria-hidden />
          </span>
          <input
            value={historyQuery}
            onChange={e => setHistoryQuery(e.target.value)}
            placeholder="Search chats"
            aria-label="Search chats"
            style={{
              width: '100%',
              boxSizing: 'border-box',
              padding: '8px 10px 8px 30px',
              borderRadius: 8,
              border: '1px solid rgba(255,255,255,0.14)',
              background: 'rgba(15,23,42,0.65)',
              color: 'white',
              fontSize: '0.8125rem',
              outline: 'none',
            }}
          />
        </label>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '4px 8px 12px' }}>
        {sessions.length === 0 ? (
          <div style={{ padding: '16px 10px', fontSize: '0.75rem', color: '#94A3B8' }}>No chats match your search.</div>
        ) : (
          sessions.map(session => (
            <HistoryRow
              key={session.id}
              session={session}
              active={session.id === activeSessionId}
              onSelect={() => selectSession(session.id)}
              onDelete={() => deleteSession(session.id)}
            />
          ))
        )}
      </div>
    </aside>
  );
}

function HistoryRow({
  session,
  active,
  onSelect,
  onDelete,
}: {
  session: ChatSessionSummary;
  active: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'stretch',
        gap: 4,
        marginBottom: 4,
        borderRadius: 8,
        background: active ? 'rgba(45,212,191,0.16)' : 'transparent',
        border: active ? '1px solid rgba(45,212,191,0.35)' : '1px solid transparent',
      }}
    >
      <button
        type="button"
        onClick={onSelect}
        title={session.title}
        style={{
          flex: 1,
          minWidth: 0,
          textAlign: 'left',
          border: 'none',
          background: 'transparent',
          color: 'white',
          cursor: 'pointer',
          padding: '8px 8px 8px 10px',
          borderRadius: 8,
        }}
      >
        <div
          style={{
            fontSize: '0.8125rem',
            fontWeight: 600,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {session.title}
        </div>
        <div
          style={{
            fontSize: '0.6875rem',
            color: '#94A3B8',
            marginTop: 2,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {session.preview || 'Empty conversation'}
        </div>
        <div style={{ fontSize: '0.625rem', color: '#64748B', marginTop: 4 }}>{formatChatTime(session.updatedAt)}</div>
      </button>
      <button
        type="button"
        title="Delete chat"
        aria-label={`Delete chat ${session.title}`}
        onClick={e => {
          e.stopPropagation();
          if (window.confirm('Delete this chat? This cannot be undone.')) onDelete();
        }}
        style={{
          flexShrink: 0,
          width: 32,
          margin: 4,
          border: 'none',
          borderRadius: 6,
          background: 'transparent',
          color: '#F87171',
          cursor: 'pointer',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <Trash2 size={14} />
      </button>
    </div>
  );
}

export function FloatingChatAssistant() {
  const navigate = useNavigate();
  const {
    mode,
    openFloating,
    openFullscreen,
    minimizeToFloating,
    close,
    position,
    setPosition,
  } = useChatAssistant();
  const dragRef = useRef<{
    startX: number;
    startY: number;
    originX: number;
    originY: number;
  } | null>(null);
  const [dragging, setDragging] = useState(false);
  const positionRef = useRef(position);
  positionRef.current = position;

  const isUnsetPosition = (p: { x: number; y: number }) => p.x === -1 && p.y === -1;

  useEffect(() => {
    if (mode !== 'floating') return;
    if (isUnsetPosition(positionRef.current)) {
      setPosition(defaultPosition());
      return;
    }
    const { width, height } = floatSize();
    const cur = positionRef.current;
    const next = clampPosition(cur.x, cur.y, width, height);
    if (next.x !== cur.x || next.y !== cur.y) setPosition(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when entering floating
  }, [mode]);

  useEffect(() => {
    const onResize = () => {
      if (mode !== 'floating') return;
      const cur = positionRef.current;
      if (isUnsetPosition(cur)) return;
      const { width, height } = floatSize();
      setPosition(clampPosition(cur.x, cur.y, width, height));
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [mode, setPosition]);

  const onHeaderPointerDown = useCallback(
    (e: ReactPointerEvent<HTMLDivElement>) => {
      if (mode !== 'floating' || e.button !== 0) return;
      const target = e.target as HTMLElement;
      if (target.closest('button, input, textarea, a, [data-no-drag]')) return;
      e.preventDefault();
      e.stopPropagation();
      const { width, height } = floatSize();
      const cur = isUnsetPosition(positionRef.current) ? defaultPosition() : positionRef.current;
      const origin = clampPosition(cur.x, cur.y, width, height);
      dragRef.current = {
        startX: e.clientX,
        startY: e.clientY,
        originX: origin.x,
        originY: origin.y,
      };
      setDragging(true);
    },
    [mode],
  );

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      e.preventDefault();
      const { width, height } = floatSize();
      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;
      // Free 360°: apply both axes every move.
      setPosition(clampPosition(drag.originX + dx, drag.originY + dy, width, height));
    };
    const onUp = () => {
      dragRef.current = null;
      setDragging(false);
    };
    window.addEventListener('pointermove', onMove, { passive: false });
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
  }, [dragging, setPosition]);

  if (mode === 'closed') {
    return createPortal(
      <button
        type="button"
        title="Chat with Data"
        aria-label="Chat with Data"
        onClick={openFloating}
        style={{
          position: 'fixed',
          right: 24,
          bottom: 24,
          zIndex: 10000,
          width: 56,
          height: 56,
          borderRadius: '50%',
          border: 'none',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: `linear-gradient(135deg, ${TEAL} 0%, ${BLUE} 100%)`,
          color: 'white',
          boxShadow: '0 8px 24px rgba(15, 23, 42, 0.22)',
        }}
      >
        <Bot size={26} aria-hidden />
      </button>,
      document.body,
    );
  }

  const floating = mode === 'floating';
  const size = floating ? floatSize() : null;
  const pos =
    floating && !isUnsetPosition(position)
      ? clampPosition(position.x, position.y, size!.width, size!.height)
      : defaultPosition();

  const panel = (
    <div
      role="dialog"
      aria-label="Chat with Data"
      style={{
        position: 'fixed',
        zIndex: 10000,
        ...(floating
          ? {
              left: pos.x,
              top: pos.y,
              width: size!.width,
              height: size!.height,
              borderRadius: 14,
              boxShadow: '0 16px 48px rgba(15, 23, 42, 0.22)',
              border: `1px solid ${BORDER}`,
            }
          : {
              inset: 0,
              borderRadius: 0,
              border: 'none',
              boxShadow: 'none',
            }),
        background: '#F7FAFC',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        pointerEvents: 'auto',
      }}
    >
      <div
        onPointerDown={onHeaderPointerDown}
        style={{
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          padding: floating ? '10px 12px' : '12px 20px',
          background: 'white',
          borderBottom: `1px solid ${BORDER}`,
          cursor: floating ? (dragging ? 'grabbing' : 'grab') : 'default',
          userSelect: 'none',
          touchAction: floating ? 'none' : 'auto',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              background: `linear-gradient(135deg, ${TEAL} 0%, ${BLUE} 100%)`,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'white',
              flexShrink: 0,
            }}
          >
            <Bot size={18} aria-hidden />
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: '0.9375rem', fontWeight: 700, color: '#0F172A' }}>Chat with Data</div>
            {!floating && (
              <div style={{ fontSize: '0.75rem', color: '#64748B' }}>
                Ask questions about distributor sales quantities within your data access.
              </div>
            )}
            {floating && (
              <div style={{ fontSize: '0.6875rem', color: '#94A3B8' }}>Drag anywhere to reposition</div>
            )}
          </div>
        </div>
        <div data-no-drag style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          {floating ? (
            <button
              type="button"
              title="Expand to full screen"
              aria-label="Expand to full screen"
              onClick={() => {
                openFullscreen();
                navigate('/chat-with-data');
              }}
              style={iconBtnStyle}
            >
              <Maximize2 size={16} />
            </button>
          ) : (
            <button
              type="button"
              title="Return to floating window"
              aria-label="Return to floating window"
              onClick={() => {
                minimizeToFloating();
                navigate('/');
              }}
              style={iconBtnStyle}
            >
              <Minimize2 size={16} />
            </button>
          )}
          <button
            type="button"
            title="Close"
            aria-label="Close Chat with Data"
            onClick={() => {
              close();
              if (!floating) navigate('/');
            }}
            style={iconBtnStyle}
          >
            <X size={16} />
          </button>
        </div>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex' }}>
        {!floating && <ChatHistorySidebar />}
        <div style={{ flex: 1, minWidth: 0, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <ChatWithDataPanel compact={floating} hideChrome />
        </div>
      </div>
    </div>
  );

  return createPortal(panel, document.body);
}

const iconBtnStyle: CSSProperties = {
  border: `1px solid ${BORDER}`,
  background: 'white',
  borderRadius: 6,
  width: 30,
  height: 30,
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  cursor: 'pointer',
  color: '#475569',
};

const sidebarIconBtn: CSSProperties = {
  width: 32,
  height: 32,
  borderRadius: 8,
  border: '1px solid rgba(255,255,255,0.14)',
  background: 'rgba(255,255,255,0.08)',
  color: 'white',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  cursor: 'pointer',
};
