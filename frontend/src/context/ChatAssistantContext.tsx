import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import type { ChatDataAnswer, ChatDataAvailability, ChatQuerySpec } from '../types';
import { ApiError } from '../api/client';
import { chatWithDataService } from '../services/chatWithData.service';

export type ChatAssistantMode = 'closed' | 'floating' | 'fullscreen';

export type ChatThreadItem =
  | { id: number; role: 'user'; text: string }
  | { id: number; role: 'assistant'; answer: ChatDataAnswer; paging?: boolean; pageError?: string | null };

export type ChatSessionSummary = {
  id: string;
  title: string;
  updatedAt: number;
  preview: string;
  messageCount: number;
};

type ChatSessionRecord = {
  id: string;
  title: string;
  updatedAt: number;
  createdAt: number;
  thread: ChatThreadItem[];
  lastSpec: ChatQuerySpec | null;
  nextId: number;
};

type ChatAssistantContextValue = {
  mode: ChatAssistantMode;
  openFloating: () => void;
  openFullscreen: () => void;
  minimizeToFloating: () => void;
  close: () => void;
  position: { x: number; y: number };
  setPosition: (pos: { x: number; y: number }) => void;
  availability: ChatDataAvailability | null;
  statusError: string | null;
  thread: ChatThreadItem[];
  input: string;
  setInput: (value: string) => void;
  loading: boolean;
  lastSpec: ChatQuerySpec | null;
  send: (raw: string) => Promise<void>;
  turnPage: (itemId: number, spec: ChatQuerySpec, isLatest: boolean, page: number) => Promise<void>;
  reset: () => void;
  maxChars: number;
  enabled: boolean;
  inputRef: React.RefObject<HTMLTextAreaElement | null>;
  endRef: React.RefObject<HTMLDivElement | null>;
  activeSessionId: string;
  sessions: ChatSessionSummary[];
  historyCollapsed: boolean;
  setHistoryCollapsed: (collapsed: boolean) => void;
  historyQuery: string;
  setHistoryQuery: (q: string) => void;
  selectSession: (id: string) => void;
  deleteSession: (id: string) => void;
  newChat: () => void;
};

const ChatAssistantContext = createContext<ChatAssistantContextValue | null>(null);

const SESSIONS_KEY = 'apcotex.chat.sessions.v1';
const ACTIVE_KEY = 'apcotex.chat.activeSession.v1';
const SIDEBAR_KEY = 'apcotex.chat.historyCollapsed.v1';
const POSITION_KEY = 'apcotex.chat.floatPosition.v1';
const MAX_STORED_SESSIONS = 40;

function errorAnswer(message: string): ChatDataAnswer {
  return {
    status: 'error',
    message,
    narrative_source: 'template',
    facts: [],
    table: null,
    periods: [],
    filters: {},
    caveats: [],
    interpreted_by: 'none',
  };
}

function newSessionId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `chat-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function titleFromThread(thread: ChatThreadItem[]): string {
  const first = thread.find(i => i.role === 'user');
  if (!first || first.role !== 'user') return 'New chat';
  const text = first.text.trim().replace(/\s+/g, ' ');
  return text.length > 48 ? `${text.slice(0, 48)}…` : text || 'New chat';
}

function previewFromThread(thread: ChatThreadItem[]): string {
  for (let i = thread.length - 1; i >= 0; i -= 1) {
    const item = thread[i];
    if (item.role === 'user') return item.text.trim().replace(/\s+/g, ' ').slice(0, 80);
    if (item.role === 'assistant') return (item.answer.message || '').trim().replace(/\s+/g, ' ').slice(0, 80);
  }
  return 'Empty conversation';
}

function emptySession(id = newSessionId()): ChatSessionRecord {
  const now = Date.now();
  return {
    id,
    title: 'New chat',
    updatedAt: now,
    createdAt: now,
    thread: [],
    lastSpec: null,
    nextId: 1,
  };
}

function loadSessions(): ChatSessionRecord[] {
  try {
    const raw = localStorage.getItem(SESSIONS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as ChatSessionRecord[];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(s => s && typeof s.id === 'string' && Array.isArray(s.thread))
      .map(s => ({
        ...s,
        title: s.title || titleFromThread(s.thread),
        updatedAt: Number(s.updatedAt) || Date.now(),
        createdAt: Number(s.createdAt) || Date.now(),
        lastSpec: s.lastSpec ?? null,
        nextId: Math.max(1, Number(s.nextId) || 1),
      }))
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, MAX_STORED_SESSIONS);
  } catch {
    return [];
  }
}

function persistSessions(sessions: ChatSessionRecord[]) {
  try {
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions.slice(0, MAX_STORED_SESSIONS)));
  } catch {
    /* quota / private mode — ignore */
  }
}

function loadActiveId(sessions: ChatSessionRecord[]): string {
  try {
    const id = localStorage.getItem(ACTIVE_KEY);
    if (id && sessions.some(s => s.id === id)) return id;
  } catch {
    /* ignore */
  }
  return sessions[0]?.id || '';
}

function loadCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === '1';
  } catch {
    return false;
  }
}

function loadPosition(): { x: number; y: number } {
  try {
    const raw = localStorage.getItem(POSITION_KEY);
    if (!raw) return { x: -1, y: -1 };
    const parsed = JSON.parse(raw) as { x?: number; y?: number };
    if (typeof parsed.x === 'number' && typeof parsed.y === 'number') return { x: parsed.x, y: parsed.y };
  } catch {
    /* ignore */
  }
  return { x: -1, y: -1 };
}

export function ChatAssistantProvider({ children }: { children: ReactNode }) {
  const initialSessions = useMemo(() => {
    const stored = loadSessions();
    if (stored.length) return stored;
    return [emptySession()];
  }, []);
  const initialActive = useMemo(() => loadActiveId(initialSessions) || initialSessions[0].id, [initialSessions]);
  const initial = useMemo(
    () => initialSessions.find(s => s.id === initialActive) || initialSessions[0],
    [initialSessions, initialActive],
  );

  const [mode, setMode] = useState<ChatAssistantMode>('closed');
  const [position, setPositionState] = useState(loadPosition);
  const [availability, setAvailability] = useState<ChatDataAvailability | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [sessions, setSessions] = useState<ChatSessionRecord[]>(initialSessions);
  const [activeSessionId, setActiveSessionId] = useState(initial.id);
  const [thread, setThread] = useState<ChatThreadItem[]>(initial.thread);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [lastSpec, setLastSpec] = useState<ChatQuerySpec | null>(initial.lastSpec);
  const [historyCollapsed, setHistoryCollapsedState] = useState(loadCollapsed);
  const [historyQuery, setHistoryQuery] = useState('');
  const nextId = useRef(initial.nextId);
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const activeSessionIdRef = useRef(activeSessionId);
  activeSessionIdRef.current = activeSessionId;
  const sessionsRef = useRef(sessions);
  sessionsRef.current = sessions;

  const setPosition = useCallback((pos: { x: number; y: number }) => {
    setPositionState(pos);
    try {
      localStorage.setItem(POSITION_KEY, JSON.stringify(pos));
    } catch {
      /* ignore */
    }
  }, []);

  const setHistoryCollapsed = useCallback((collapsed: boolean) => {
    setHistoryCollapsedState(collapsed);
    try {
      localStorage.setItem(SIDEBAR_KEY, collapsed ? '1' : '0');
    } catch {
      /* ignore */
    }
  }, []);

  const syncActiveSession = useCallback(
    (patch: Partial<Pick<ChatSessionRecord, 'thread' | 'lastSpec' | 'nextId' | 'title'>>) => {
      const id = activeSessionIdRef.current;
      setSessions(prev => {
        const now = Date.now();
        const next = prev.map(s => {
          if (s.id !== id) return s;
          const thread = patch.thread ?? s.thread;
          return {
            ...s,
            thread,
            title: patch.title ?? (thread.length ? titleFromThread(thread) : s.title),
            updatedAt: now,
            nextId: patch.nextId ?? s.nextId,
            lastSpec: patch.lastSpec !== undefined ? patch.lastSpec : s.lastSpec,
          };
        });
        const ordered = [...next].sort((a, b) => b.updatedAt - a.updatedAt);
        persistSessions(ordered);
        return ordered;
      });
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;
    chatWithDataService
      .status()
      .then(data => {
        if (!cancelled) setAvailability(data);
      })
      .catch(err => {
        if (!cancelled) setStatusError(err instanceof ApiError ? err.message : 'Could not reach the server.');
      });
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (mode === 'closed') return;
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [thread.length, loading, mode]);

  useEffect(() => {
    try {
      localStorage.setItem(ACTIVE_KEY, activeSessionId);
    } catch {
      /* ignore */
    }
  }, [activeSessionId]);

  const maxChars = availability?.max_question_chars ?? 500;
  const enabled = availability?.enabled === true;

  const send = useCallback(
    async (raw: string) => {
      const question = raw.trim();
      if (!question || loading || !enabled) return;
      if (question.length > maxChars) return;
      const userId = nextId.current++;
      setThread(t => {
        const next = [...t, { id: userId, role: 'user' as const, text: question }];
        syncActiveSession({ thread: next, nextId: nextId.current });
        return next;
      });
      setInput('');
      setLoading(true);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const answer = await chatWithDataService.ask(question, lastSpec, controller.signal);
        if (answer.spec) setLastSpec(answer.spec);
        const assistantId = nextId.current++;
        setThread(t => {
          const next = [...t, { id: assistantId, role: 'assistant' as const, answer }];
          syncActiveSession({
            thread: next,
            lastSpec: answer.spec ?? lastSpec,
            nextId: nextId.current,
          });
          return next;
        });
      } catch (err) {
        if (controller.signal.aborted) return;
        let message = 'Something went wrong while answering. Please try again.';
        if (err instanceof ApiError) {
          if (err.status === 401) message = 'Your session has expired. Please sign in again.';
          else if (err.status === 422) message = 'That question could not be processed. Please rephrase it.';
          else message = err.message || message;
        }
        const assistantId = nextId.current++;
        setThread(t => {
          const next = [...t, { id: assistantId, role: 'assistant' as const, answer: errorAnswer(message) }];
          syncActiveSession({ thread: next, nextId: nextId.current });
          return next;
        });
      } finally {
        setLoading(false);
        abortRef.current = null;
        inputRef.current?.focus();
      }
    },
    [enabled, lastSpec, loading, maxChars, syncActiveSession],
  );

  const turnPage = useCallback(
    async (itemId: number, spec: ChatQuerySpec, isLatest: boolean, page: number) => {
      const update = (patch: Partial<Extract<ChatThreadItem, { role: 'assistant' }>>) =>
        setThread(t => {
          const next = t.map(i => (i.id === itemId && i.role === 'assistant' ? { ...i, ...patch } : i));
          syncActiveSession({
            thread: next,
            lastSpec: isLatest && patch.answer?.spec ? patch.answer.spec : undefined,
            nextId: nextId.current,
          });
          return next;
        });
      update({ paging: true, pageError: null });
      try {
        const answer = await chatWithDataService.page(spec, page);
        if (answer.status !== 'answer') {
          update({ paging: false, pageError: answer.message || 'This page could not be loaded.' });
          return;
        }
        update({ answer, paging: false, pageError: null });
        if (isLatest && answer.spec) setLastSpec(answer.spec);
      } catch (err) {
        let message = 'This page could not be loaded. Please try again.';
        if (err instanceof ApiError && err.status === 401) message = 'Your session has expired. Please sign in again.';
        else if (err instanceof ApiError && err.message) message = err.message;
        update({ paging: false, pageError: message });
      }
    },
    [syncActiveSession],
  );

  const applySession = useCallback((session: ChatSessionRecord) => {
    abortRef.current?.abort();
    setLoading(false);
    setActiveSessionId(session.id);
    setThread(session.thread);
    setLastSpec(session.lastSpec);
    setInput('');
    nextId.current = session.nextId || 1;
    inputRef.current?.focus();
  }, []);

  const newChat = useCallback(() => {
    abortRef.current?.abort();
    setLoading(false);
    const created = emptySession();
    setSessions(prev => {
      // Drop empty "New chat" drafts that never got a message.
      const kept = prev.filter(s => s.thread.length > 0 || s.id === activeSessionIdRef.current);
      const withoutEmptyActive =
        kept.find(s => s.id === activeSessionIdRef.current)?.thread.length === 0
          ? kept.filter(s => s.id !== activeSessionIdRef.current)
          : kept;
      const next = [created, ...withoutEmptyActive].slice(0, MAX_STORED_SESSIONS);
      persistSessions(next);
      return next;
    });
    applySession(created);
  }, [applySession]);

  const reset = useCallback(() => {
    newChat();
  }, [newChat]);

  const selectSession = useCallback(
    (id: string) => {
      if (id === activeSessionIdRef.current) return;
      const target = sessionsRef.current.find(s => s.id === id);
      if (target) applySession(target);
    },
    [applySession],
  );

  const deleteSession = useCallback(
    (id: string) => {
      setSessions(prev => {
        const remaining = prev.filter(s => s.id !== id);
        let next = remaining;
        if (!next.length) next = [emptySession()];
        persistSessions(next);
        if (id === activeSessionIdRef.current) {
          applySession(next[0]);
        }
        return next;
      });
    },
    [applySession],
  );

  const openFloating = useCallback(() => setMode('floating'), []);
  const openFullscreen = useCallback(() => setMode('fullscreen'), []);
  const minimizeToFloating = useCallback(() => setMode('floating'), []);
  const close = useCallback(() => setMode('closed'), []);

  const sessionSummaries = useMemo<ChatSessionSummary[]>(() => {
    const q = historyQuery.trim().toLowerCase();
    return sessions
      .filter(s => {
        if (!q) return true;
        const hay = `${s.title} ${previewFromThread(s.thread)}`.toLowerCase();
        return hay.includes(q);
      })
      .map(s => ({
        id: s.id,
        title: s.title || titleFromThread(s.thread),
        updatedAt: s.updatedAt,
        preview: previewFromThread(s.thread),
        messageCount: s.thread.length,
      }));
  }, [sessions, historyQuery]);

  const value = useMemo(
    () => ({
      mode,
      openFloating,
      openFullscreen,
      minimizeToFloating,
      close,
      position,
      setPosition,
      availability,
      statusError,
      thread,
      input,
      setInput,
      loading,
      lastSpec,
      send,
      turnPage,
      reset,
      maxChars,
      enabled,
      inputRef,
      endRef,
      activeSessionId,
      sessions: sessionSummaries,
      historyCollapsed,
      setHistoryCollapsed,
      historyQuery,
      setHistoryQuery,
      selectSession,
      deleteSession,
      newChat,
    }),
    [
      mode,
      openFloating,
      openFullscreen,
      minimizeToFloating,
      close,
      position,
      setPosition,
      availability,
      statusError,
      thread,
      input,
      loading,
      lastSpec,
      send,
      turnPage,
      reset,
      maxChars,
      enabled,
      activeSessionId,
      sessionSummaries,
      historyCollapsed,
      setHistoryCollapsed,
      historyQuery,
      selectSession,
      deleteSession,
      newChat,
    ],
  );

  return <ChatAssistantContext.Provider value={value}>{children}</ChatAssistantContext.Provider>;
}

export function useChatAssistant() {
  const ctx = useContext(ChatAssistantContext);
  if (!ctx) throw new Error('useChatAssistant must be used within ChatAssistantProvider');
  return ctx;
}
