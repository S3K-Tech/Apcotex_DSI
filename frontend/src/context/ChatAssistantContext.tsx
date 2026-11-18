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
};

const ChatAssistantContext = createContext<ChatAssistantContextValue | null>(null);

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

export function ChatAssistantProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ChatAssistantMode>('closed');
  const [position, setPosition] = useState({ x: -1, y: -1 });
  const [availability, setAvailability] = useState<ChatDataAvailability | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [thread, setThread] = useState<ChatThreadItem[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [lastSpec, setLastSpec] = useState<ChatQuerySpec | null>(null);
  const nextId = useRef(1);
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);

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

  const maxChars = availability?.max_question_chars ?? 500;
  const enabled = availability?.enabled === true;

  const send = useCallback(
    async (raw: string) => {
      const question = raw.trim();
      if (!question || loading || !enabled) return;
      if (question.length > maxChars) return;
      setThread(t => [...t, { id: nextId.current++, role: 'user', text: question }]);
      setInput('');
      setLoading(true);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const answer = await chatWithDataService.ask(question, lastSpec, controller.signal);
        if (answer.spec) setLastSpec(answer.spec);
        setThread(t => [...t, { id: nextId.current++, role: 'assistant', answer }]);
      } catch (err) {
        if (controller.signal.aborted) return;
        let message = 'Something went wrong while answering. Please try again.';
        if (err instanceof ApiError) {
          if (err.status === 401) message = 'Your session has expired. Please sign in again.';
          else if (err.status === 422) message = 'That question could not be processed. Please rephrase it.';
          else message = err.message || message;
        }
        setThread(t => [...t, { id: nextId.current++, role: 'assistant', answer: errorAnswer(message) }]);
      } finally {
        setLoading(false);
        abortRef.current = null;
        inputRef.current?.focus();
      }
    },
    [enabled, lastSpec, loading, maxChars],
  );

  const turnPage = useCallback(async (itemId: number, spec: ChatQuerySpec, isLatest: boolean, page: number) => {
    const update = (patch: Partial<Extract<ChatThreadItem, { role: 'assistant' }>>) =>
      setThread(t => t.map(i => (i.id === itemId && i.role === 'assistant' ? { ...i, ...patch } : i)));
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
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setThread([]);
    setLastSpec(null);
    setInput('');
    setLoading(false);
    inputRef.current?.focus();
  }, []);

  const openFloating = useCallback(() => setMode('floating'), []);
  const openFullscreen = useCallback(() => setMode('fullscreen'), []);
  const minimizeToFloating = useCallback(() => setMode('floating'), []);
  const close = useCallback(() => setMode('closed'), []);

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
    }),
    [
      mode,
      openFloating,
      openFullscreen,
      minimizeToFloating,
      close,
      position,
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
    ],
  );

  return <ChatAssistantContext.Provider value={value}>{children}</ChatAssistantContext.Provider>;
}

export function useChatAssistant() {
  const ctx = useContext(ChatAssistantContext);
  if (!ctx) throw new Error('useChatAssistant must be used within ChatAssistantProvider');
  return ctx;
}
