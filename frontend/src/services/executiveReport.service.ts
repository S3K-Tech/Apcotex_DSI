import { ApiError, apiRequest, getApiBaseUrl } from '../api';
import { triggerBrowserDownload } from '../utils/download';

export type ReportFilters = {
  distributor_id: number | null;
  distributor: string | null;
  customer: string | null;
  product: string | null;
  location: string | null;
  segment: string | null;
  fiscal_year_start: number | null;
  period: string;
  start_month: string | null;
  end_month: string | null;
};

export type SummarySection = {
  key: string;
  title: string;
  paragraphs: string[];
  bullets: string[];
};

export type ExecutiveReport = {
  id: number;
  filter_key: string;
  filters: ReportFilters;
  period_label: string;
  report_title: string;
  report_kind?: string;
  filter_lines: Array<{ label: string; value: string }>;
  generated_at: string | null;
  generated_by: string | null;
  model: string | null;
  kpis: {
    total_sales_mt_display?: string;
    total_customers?: number;
    total_products?: number;
    active_distributors?: number;
  };
  comparisons: Array<{
    key: string;
    description: string;
    period: string;
    available: boolean;
    total_mt_display?: string;
    change_mt?: number;
    change_pct?: number | null;
  }>;
  summary: {
    sections: SummarySection[];
    word_count: number;
    verification: { removed_statements: number };
  };
  email_subject: string;
  can_email: boolean;
};

export type ManagementRecipient = {
  role: string;
  role_label: string;
  name: string | null;
  email: string | null;
};

export type RecipientInput = { role: string; name?: string | null; email: string };

export type EmailResult = {
  status: string;
  recipients: Array<{ role: string; name: string; email: string }>;
  subject: string;
  attachment_name: string | null;
  sent_at: string | null;
};

type Query = {
  distributor_id?: number | null;
  distributor?: string | null;
  customer?: string | null;
  product?: string | null;
  location?: string | null;
  segment?: string | null;
  fiscal_year_start?: number | null;
  period?: string;
  start_month?: string | null;
  end_month?: string | null;
};

/** The report-relevant part of the Visualizations query, in a stable key order. */
export function reportFiltersFrom(q: Query): ReportFilters {
  return {
    distributor_id: q.distributor_id || null,
    distributor: q.distributor || null,
    customer: q.customer || null,
    product: q.product || null,
    location: q.location || null,
    segment: q.segment || null,
    fiscal_year_start: q.fiscal_year_start ?? null,
    period: q.period || 'full_year',
    start_month: q.start_month || null,
    end_month: q.end_month || null,
  };
}

export function reportFilterKey(filters: ReportFilters): string {
  return JSON.stringify(filters);
}

export function pdfFileName(reportTitle: string, periodLabel: string): string {
  const kind = reportTitle.replace(/[^A-Za-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'Secondary_Sales_Report';
  const period = periodLabel.replace(/[^A-Za-z0-9]+/g, '_').replace(/^_|_$/g, '');
  return `APCOTEX_${kind}_${period}.pdf`;
}

export const ExecutiveReportService = {
  generate: async (filters: ReportFilters): Promise<ExecutiveReport> => {
    const res = await apiRequest<{ data: ExecutiveReport }>('/executive-reports/generate', {
      method: 'POST',
      body: { filters },
    });
    return res.data;
  },

  downloadPdf: async (report: ExecutiveReport, filters: ReportFilters): Promise<void> => {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => {
      if (v !== null && v !== undefined && v !== '') params.set(k, String(v));
    });
    let response: Response;
    try {
      response = await fetch(`${getApiBaseUrl()}/executive-reports/${report.id}/pdf?${params}`, {
        credentials: 'include',
        cache: 'no-store',
      });
    } catch (err) {
      throw new ApiError('Unable to connect to the server.', 0, 'NETWORK_ERROR', err);
    }
    if (!response.ok) {
      let message = `PDF download failed (${response.status})`;
      try {
        const body = await response.json();
        message = body?.error?.message || body?.detail || message;
      } catch {
        // keep the status message
      }
      throw new ApiError(message, response.status);
    }
    const blob = await response.blob();
    if (!blob.size) throw new ApiError('The PDF was empty. Please try again.', 500);
    triggerBrowserDownload(blob, pdfFileName(report.report_title || 'Secondary_Sales_Report', report.period_label));
  },

  getRecipients: async (): Promise<ManagementRecipient[]> => {
    const res = await apiRequest<{ data: { recipients: ManagementRecipient[] } }>('/executive-reports/recipients');
    return res.data.recipients;
  },

  saveRecipients: async (
    recipients: Array<{ role: string; name: string | null; email: string | null }>,
  ): Promise<ManagementRecipient[]> => {
    const res = await apiRequest<{ data: { recipients: ManagementRecipient[] } }>('/executive-reports/recipients', {
      method: 'PUT',
      body: { recipients },
    });
    return res.data.recipients;
  },

  sendEmail: async (
    reportId: number,
    body: { filters: ReportFilters; recipients: RecipientInput[]; idempotency_key: string },
  ): Promise<EmailResult> => {
    const res = await apiRequest<{ data: EmailResult }>(`/executive-reports/${reportId}/email`, {
      method: 'POST',
      body,
    });
    return res.data;
  },
};
