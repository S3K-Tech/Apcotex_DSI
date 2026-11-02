import { apiRequest, apiUpload } from '../api';

const BASE = '/product-standardization';

type Envelope<T> = { success: boolean; data: T; message?: string | null };

export type FGMasterVersion = {
  id: number;
  version_number: number;
  status: 'draft' | 'active' | 'superseded' | 'discarded';
  file_name: string;
  file_size: number;
  sheet_name: string | null;
  total_rows: number;
  valid_count: number;
  duplicate_count: number;
  invalid_count: number;
  uploaded_by_name: string | null;
  uploaded_at: string | null;
  published_by_name: string | null;
  published_at: string | null;
};

export type AffectedMapping = {
  mapping_id: number;
  original_product_name: string;
  standard_product: string;
  scope: string;
  distributor_id: number | null;
};

export type FGMasterDiff = {
  against_version: number | null;
  new_count: number;
  removed_count: number;
  unchanged_count: number;
  new_sample: string[];
  removed_sample: string[];
  affected_mappings_count: number;
  affected_mappings: AffectedMapping[];
};

export type FGMasterStatus = {
  configured: boolean;
  active: FGMasterVersion | null;
  draft: FGMasterVersion | null;
  draft_diff: FGMasterDiff | null;
};

export type FGMasterUploadResult = {
  version: FGMasterVersion;
  duplicates_sample: { row: number; value: string; first_row: number }[];
  invalid_sample: { row: number; value: string; reason: string }[];
  diff: FGMasterDiff;
};

export type MatchStatus = 'AUTO_MATCHED' | 'NEEDS_REVIEW' | 'UNMAPPED';

export type WorkbenchItem = {
  raw_product: string;
  normalized_key: string;
  status: MatchStatus;
  method: string;
  method_label: string;
  suggested_product: string | null;
  confidence: number;
  confidence_tier: 'EXACT' | 'HIGH' | 'MEDIUM' | 'LOW';
  mapping_id: number | null;
  mapping_scope: string | null;
  approved: boolean;
  alternatives: { description: string; confidence: number }[];
  distributor: string | null;
  row_count: number;
  quantity: number;
  bucket: 'approved' | 'auto_standardized' | 'awaiting_review' | 'unmapped';
};

export type WorkbenchContext = {
  email_id: number;
  email_subject: string;
  attachment_id: number;
  attachment_name: string;
  attachments: { id: number; name: string }[];
  distributor: { id: number; name: string } | null;
  master: { configured: boolean; version_number: number | null; product_count: number };
  extracted_at: string | null;
  row_count: number;
};

export type WorkbenchData = WorkbenchContext & {
  metrics: {
    total_distinct: number;
    auto_standardized: number;
    awaiting_review: number;
    unmapped: number;
    approved: number;
  };
  items: WorkbenchItem[];
};

export type StandardizedPreview = WorkbenchContext & {
  columns: string[];
  rows: Record<string, unknown>[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
};

export type ApprovalItem = { raw_product: string; standard_product: string; confidence?: number | null };

export type ApprovalResult = {
  approved: number;
  saved: number;
  global_conflicts: number;
  results: {
    raw_product: string;
    standard_product: string;
    distributor_action: string;
    global_action: string;
    mapping_id: number;
  }[];
};

export const ProductStandardizationService = {
  masterStatus: async () => (await apiRequest<Envelope<FGMasterStatus>>(`${BASE}/master/status`)).data,

  masterVersions: async () => (await apiRequest<Envelope<FGMasterVersion[]>>(`${BASE}/master/versions`)).data,

  uploadMaster: async (file: File, onProgress?: (p: number) => void) =>
    (await apiUpload<Envelope<FGMasterUploadResult>>(`${BASE}/master/upload`, file, 'file', onProgress)).data,

  publishMaster: async (versionId: number) =>
    (
      await apiRequest<Envelope<{ version: FGMasterVersion; diff: FGMasterDiff }>>(
        `${BASE}/master/versions/${versionId}/publish`,
        { method: 'POST', body: { confirm: true } }
      )
    ).data,

  discardMaster: async (versionId: number) =>
    (await apiRequest<Envelope<{ version: FGMasterVersion }>>(`${BASE}/master/versions/${versionId}/discard`, {
      method: 'POST',
    })).data,

  searchMaster: async (q: string, limit = 20, signal?: AbortSignal) =>
    (
      await apiRequest<Envelope<{ configured: boolean; items: string[] }>>(`${BASE}/master/products`, {
        params: { q, limit },
        signal,
      })
    ).data,

  workbench: async (emailId: number, attachmentId?: number | null, refresh = false) =>
    (
      await apiRequest<Envelope<WorkbenchData>>(`${BASE}/emails/${emailId}/workbench`, {
        params: { attachment_id: attachmentId ?? undefined, refresh: refresh || undefined },
      })
    ).data,

  preview: async (
    emailId: number,
    query: { attachment_id?: number | null; page?: number; page_size?: number; status?: MatchStatus | ''; search?: string }
  ) =>
    (
      await apiRequest<Envelope<StandardizedPreview>>(`${BASE}/emails/${emailId}/preview`, {
        params: {
          attachment_id: query.attachment_id ?? undefined,
          page: query.page,
          page_size: query.page_size,
          status: query.status || undefined,
          search: query.search || undefined,
        },
      })
    ).data,

  approve: async (emailId: number, attachmentId: number | null, items: ApprovalItem[]) =>
    (
      await apiRequest<Envelope<ApprovalResult>>(`${BASE}/approve`, {
        method: 'POST',
        body: { email_id: emailId, attachment_id: attachmentId, items },
      })
    ).data,
};
