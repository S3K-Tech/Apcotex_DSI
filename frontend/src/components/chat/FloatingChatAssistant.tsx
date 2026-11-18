import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
} from 'react';
import { useNavigate } from 'react-router';
import { Bot, Maximize2, Minimize2, X } from 'lucide-react';
import { BLUE, BORDER, TEAL } from '../../constants/theme';
import { useChatAssistant } from '../../context/ChatAssistantContext';
import { ChatWithDataPanel } from '../../pages/ChatWithData/ChatWithData';

const FLOAT_WIDTH = 420;
const FLOAT_HEIGHT = 640;
const EDGE = 16;

function clampPosition(x: number, y: number, width: number, height: number) {
  const maxX = Math.max(EDGE, window.innerWidth - width - EDGE);
  const maxY = Math.max(EDGE, window.innerHeight - height - EDGE);
  return {
    x: Math.min(Math.max(EDGE, x), maxX),
    y: Math.min(Math.max(EDGE, y), maxY),
  };
}

function defaultPosition() {
  const width = Math.min(FLOAT_WIDTH, window.innerWidth - EDGE * 2);
  const height = Math.min(FLOAT_HEIGHT, window.innerHeight - EDGE * 2);
  return {
    x: Math.max(EDGE, window.innerWidth - width - EDGE),
    y: Math.max(EDGE, window.innerHeight - height - EDGE),
  };
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
  const dragRef = useRef<{ offsetX: number; offsetY: number } | null>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (mode === 'floating' && (position.x < 0 || position.y < 0)) {
      setPosition(defaultPosition());
    }
  }, [mode, position.x, position.y, setPosition]);

  useEffect(() => {
    const onResize = () => {
      if (mode !== 'floating' || position.x < 0) return;
      const width = Math.min(FLOAT_WIDTH, window.innerWidth - EDGE * 2);
      const height = Math.min(FLOAT_HEIGHT, window.innerHeight - EDGE * 2);
      setPosition(clampPosition(position.x, position.y, width, height));
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [mode, position.x, position.y, setPosition]);

  const onHeaderMouseDown = useCallback(
    (e: ReactMouseEvent) => {
      if (mode !== 'floating') return;
      const target = e.target as HTMLElement;
      if (target.closest('button, input, textarea, a, [data-no-drag]')) return;
      e.preventDefault();
      dragRef.current = { offsetX: e.clientX - position.x, offsetY: e.clientY - position.y };
      setDragging(true);
    },
    [mode, position.x, position.y],
  );

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: MouseEvent) => {
      if (!dragRef.current) return;
      const width = Math.min(FLOAT_WIDTH, window.innerWidth - EDGE * 2);
      const height = Math.min(FLOAT_HEIGHT, window.innerHeight - EDGE * 2);
      setPosition(
        clampPosition(e.clientX - dragRef.current.offsetX, e.clientY - dragRef.current.offsetY, width, height),
      );
    };
    const onUp = () => {
      dragRef.current = null;
      setDragging(false);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [dragging, setPosition]);

  if (mode === 'closed') {
    return (
      <button
        type="button"
        title="Chat with Data"
        aria-label="Chat with Data"
        onClick={openFloating}
        style={{
          position: 'fixed',
          right: 24,
          bottom: 24,
          zIndex: 1200,
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
      </button>
    );
  }

  const floating = mode === 'floating';
  const width = floating ? Math.min(FLOAT_WIDTH, window.innerWidth - EDGE * 2) : undefined;
  const height = floating ? Math.min(FLOAT_HEIGHT, window.innerHeight - EDGE * 2) : undefined;
  const pos = floating && position.x >= 0 ? position : defaultPosition();

  return (
    <div
      role="dialog"
      aria-label="Chat with Data"
      style={{
        position: 'fixed',
        zIndex: 1200,
        ...(floating
          ? {
              left: pos.x,
              top: pos.y,
              width,
              height,
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
      }}
    >
      <div
        onMouseDown={onHeaderMouseDown}
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
      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <ChatWithDataPanel compact={floating} hideChrome />
      </div>
    </div>
  );
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
