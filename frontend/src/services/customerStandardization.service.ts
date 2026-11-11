import { apiRequest } from '../api';

const BASE = '/customer-standardization';

type Envelope<T> = { success: boolean; data: T; message?: string | null };

export type CustomerSource = 'master' | 'approved' | 'sales';

export type CustomerBucket = 'approved' | 'auto_matched' | 'needs_review' | 'unmapped';

export type CustomerWorkbenchItem = {
  raw_customer: string;
  normalized_key: string;
  status: 'AUTO_MATCHED' | 'NEEDS_REVIEW' | 'UNMAPPED';
  ui_status: 'APPROVED' | 'AUTO_MATCHED' | 'NEEDS_REVIEW' | 'UNMAPPED';
  method: string;
  method_label: string;
  suggested_customer: string | null;
  suggested_source: CustomerSource | null;
  confidence: number;
  confidence_tier: 'EXACT' | 'HIGH' | 'MEDIUM' | 'LOW';
  mapping_id: number | null;
  mapping_scope: string | null;
  approved: boolean;
  alternatives: { name: string; confidence: number; source: CustomerSource }[];
  distributor: string | null;
  row_count: number;
  quantity: number;
  bucket: CustomerBucket;
};

export type CustomerWorkbenchData = {
  email_id: number;
  email_subject: string;
  attachment_id: number;
  attachment_name: string;
  attachments: { id: number; name: string }[];
  distributor: { id: number; name: string } | null;
  reference_count: number;
  extracted_at: string | null;
  row_count: number;
  blank_rows: number;
  metrics: {
    total_distinct: number;
    approved: number;
    auto_matched: number;
    needs_review: number;
    unmapped: number;
  };
  items: CustomerWorkbenchItem[];
};

export type CustomerApprovalItem = {
  raw_customer: string;
  standard_customer: string;
  confidence?: number | null;
  replace_existing?: boolean;
};

export type CustomerApprovalOutcome = {
  raw_customer: string;
  standard_customer: string;
  is_new_customer: boolean;
  distributor_action: 'created' | 'updated' | 'unchanged' | 'conflict';
  global_action?: string;
  mapping_id: number;
  existing_customer?: string;
};

export type CustomerApprovalResult = {
  approved: number;
  saved: number;
  global_conflicts: number;
  conflicts: CustomerApprovalOutcome[];
  results: CustomerApprovalOutcome[];
};

export const CustomerStandardizationService = {
  workbench: async (emailId: number, attachmentId?: number | null, refresh = false) =>
    (
      await apiRequest<Envelope<CustomerWorkbenchData>>(`${BASE}/emails/${emailId}/workbench`, {
        params: { attachment_id: attachmentId ?? undefined, refresh: refresh || undefined },
      })
    ).data,

  searchCustomers: async (q: string, limit = 20, signal?: AbortSignal) =>
    (
      await apiRequest<Envelope<{ items: { name: string; source: CustomerSource }[] }>>(`${BASE}/customers`, {
        params: { q, limit },
        signal,
      })
    ).data,

  approve: async (emailId: number, attachmentId: number | null, items: CustomerApprovalItem[]) =>
    (
      await apiRequest<Envelope<CustomerApprovalResult>>(`${BASE}/approve`, {
        method: 'POST',
        body: { email_id: emailId, attachment_id: attachmentId, items },
      })
    ).data,
};
