// Tiny typed client for our API (CLAUDE.md §6). Same origin: the web service's proxy route
// forwards /api/* to the api service. Money fields are integer cents (invariant 1).

export type LineStatus = "OUT" | "RETURNED" | "KEPT";

export interface Line {
  sku: string;
  description: string;
  price_cents: number;
  status: LineStatus;
  returned_on: string | null;
  return_ref: string | null;
  due_back_on: string | null;
  invoice_no: string | null;
}

export interface Memo {
  memo_no: string;
  sent_on: string;
  lines: Line[];
}

export interface Invoice {
  id: number;
  invoice_no: string;
  memo_no: string;
  total_cents: number;
  paid_cents: number;
  due_cents: number;
  issued_on: string;
  due_on: string;
  ledger_status: string;
  days_overdue: number;
  paypal_url: string | null;
}

export interface Payment {
  id: number;
  source: string;
  status: string;
  method: string | null;
  reference: string | null;
  amount_cents: number;
  received_at: string | null;
  applied: { invoice_no: string; amount_cents: number }[];
}

export type NoticeKind = "PAYMENT_VOIDED" | "PAYMENT_CREDITED" | "RECORDING_PENDING";

export interface Notice {
  kind: NoticeKind;
  payment_id: number;
  at: string;
  amount_cents: number | null;
}

export type RequestStatus = "WAITING" | "APPROVED" | "DECLINED" | "ANSWERED";

/** What the buyer sent and where it stands (P4 D17); summary written by code, not the model. */
export interface BuyerRequest {
  id: number;
  kind: string;
  summary: string;
  status: RequestStatus;
  created_at: string | null;
}

/** POST /ask (P4 D10). `looked_up` = cited records that exist ("Looked up" trace). */
export interface AskResult {
  answer: string;
  cited_records: string[];
  looked_up: string[];
  confidence: "low" | "med" | "high";
  fallback: boolean;
  proposal_id: number | null;
}

/** POST /message (P4 D10). `checks` are code-written ("$20.00 ≤ price $1,420.00"). */
export interface MessageResult {
  received: boolean;
  label: string | null;
  summary: string;
  checks: string[];
  proposal_id: number | null;
  proposal_ids: number[];
  answer: string | null;
  fallback: boolean;
}

export interface Statement {
  buyer: { name: string };
  supplier: { name: string };
  owed_cents: number;
  oldest_age_days: number;
  past_due_cents: number;
  memos: Memo[];
  invoices: Invoice[];
  payments: Payment[];
  credit_cents: number;
  notices: Notice[];
  pending_proposals: number;
  requests: BuyerRequest[];
  demo_login: { email: string; password: string } | null;
}

export interface Preview {
  allocations: { invoice_id: number; invoice_no: string; amount_cents: number }[];
  total_cents: number;
  remaining_after: { invoice_no: string; due_cents: number }[];
}

export interface ReceiptAllocation {
  invoice_no: string;
  amount_cents: number;
  recorded: boolean;
  record_state: string;
  due_after_cents: number;
  paypal_url: string | null;
}

export interface Receipt {
  payment_id: number;
  status: "CAPTURED" | "CAPTURE_PENDING";
  paypal_capture_id: string | null;
  amount_cents: number;
  received_at: string | null;
  allocations: ReceiptAllocation[];
  new_owed_cents: number;
  credit_cents: number;
  resplit: boolean;
}

export interface PublicConfig {
  paypal_client_id: string | null;
  demo_mode: boolean;
}

export interface DemoStart {
  statement_token: string;
  buyer_id: number;
  ms: number;
}

export interface PayRequest {
  invoice_ids?: number[];
  amount_cents?: number;
}

/** {error:{code,message,paypal_debug_id?}} from the API (BACKEND_SCHEMA §2), or a network failure. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public debugId?: string,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "NETWORK", "We couldn't reach the server. Check your connection and try again.");
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const err = body?.error;
    throw new ApiError(
      res.status,
      err?.code ?? "UNKNOWN",
      err?.message ?? "Something went wrong. Please try again.",
      err?.paypal_debug_id,
    );
  }
  return body as T;
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });
const enc = encodeURIComponent;

export const api = {
  config: () => call<PublicConfig>("/config"),
  startDemo: () => call<DemoStart>("/demo/start", post({})),
  statement: (token: string) => call<Statement>(`/statement/${enc(token)}`),
  preview: (token: string, req: PayRequest) =>
    call<Preview>(`/statement/${enc(token)}/preview`, post(req)),
  order: (token: string, req: PayRequest & { funding_source?: string }) =>
    call<{ paypal_order_id: string }>(`/statement/${enc(token)}/order`, post(req)),
  capture: (token: string, paypalOrderId: string) =>
    call<Receipt>(`/statement/${enc(token)}/capture`, post({ paypal_order_id: paypalOrderId })),
  receipt: (token: string, paymentId: number) =>
    call<Receipt>(`/statement/${enc(token)}/payments/${paymentId}`),
  refresh: (token: string, paymentId: number) =>
    call<Receipt>(`/statement/${enc(token)}/payments/${paymentId}/refresh`, post({})),
  ask: (token: string, question: string) =>
    call<AskResult>(`/statement/${enc(token)}/ask`, post({ question })),
  message: (token: string, text: string) =>
    call<MessageResult>(`/statement/${enc(token)}/message`, post({ text })),
};
