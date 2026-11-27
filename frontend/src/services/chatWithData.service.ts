import { apiRequest, getApiBaseUrl } from '../api';
import { getSession } from '../api/session';
import type { ChatDataAnswer, ChatDataAvailability, ChatQuerySpec } from '../types';
import { filenameFromContentDisposition, triggerBrowserDownload } from '../utils/download';

export const chatWithDataService = {
  async status(): Promise<ChatDataAvailability> {
    const res = await apiRequest<{ success: boolean; data: ChatDataAvailability }>(
      '/chat-data/status',
    );
    return res.data;
  },

  async ask(
    question: string,
    previousSpec: ChatQuerySpec | null,
    signal?: AbortSignal,
  ): Promise<ChatDataAnswer> {
    const res = await apiRequest<{ success: boolean; data: ChatDataAnswer }>('/chat-data/ask', {
      method: 'POST',
      body: { question, previous_spec: previousSpec },
      signal,
    });
    return res.data;
  },

  async page(
    spec: ChatQuerySpec,
    page: number,
    pageSize?: number,
    signal?: AbortSignal,
  ): Promise<ChatDataAnswer> {
    const res = await apiRequest<{ success: boolean; data: ChatDataAnswer }>('/chat-data/page', {
      method: 'POST',
      body: { spec, page, page_size: pageSize ?? null },
      signal,
    });
    return res.data;
  },

  async exportExcel(answer: ChatDataAnswer, question?: string): Promise<void> {
    const session = getSession();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (session?.name) headers['X-User-Name'] = session.name;
    if (session?.role) headers['X-User-Role'] = session.role;
    if (session?.user_id != null) headers['X-User-Id'] = String(session.user_id);
    if (session?.email) headers['X-User-Email'] = session.email;
    if (session?.segments?.length) headers['X-User-Segments'] = session.segments.join(',');

    const response = await fetch(`${getApiBaseUrl()}/chat-data/export`, {
      method: 'POST',
      headers,
      credentials: 'include',
      body: JSON.stringify({
        question: question ?? null,
        message: answer.message,
        periods: answer.periods ?? [],
        filters: answer.filters ?? {},
        facts: answer.facts ?? [],
        table: answer.table ?? null,
        chart: answer.chart ?? null,
        pagination: answer.pagination ?? null,
        spec: answer.spec ?? null,
      }),
    });
    if (!response.ok) {
      throw new Error('Could not export response to Excel.');
    }
    const blob = await response.blob();
    const fallbackName = `chat_data_export_${new Date().toISOString().slice(0, 10)}.xlsx`;
    const fileName =
      filenameFromContentDisposition(response.headers.get('Content-Disposition')) || fallbackName;
    triggerBrowserDownload(blob, fileName);
  },
};

