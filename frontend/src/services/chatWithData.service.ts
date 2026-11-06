import { apiRequest } from '../api';
import type { ChatDataAnswer, ChatDataAvailability, ChatQuerySpec } from '../types';

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
};
