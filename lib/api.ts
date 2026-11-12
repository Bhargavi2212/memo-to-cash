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
  reply: string | null;
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
  paypal_setup: PaypalSetupState; // P3 §M M9: not READY → can't be paid yet
}

/** Whether a buyer's invoices are on PayPal yet (P3 §M M9). */
export type PaypalSetupState = "READY" | "SETTING_UP" | "FAILED";
export type SetupResult = { paypal_setup: PaypalSetupState };
export type WhoAmI = {
  statement_token: string | null; // the copy's Northside (landing / back link)
  buyers: { id: number; name: string; statement_token: string }[]; // all three (P3 §M M4)
};

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
  status: "CAPTURED" | "CAPTURE_PENDING" | "RECORDED";
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

// ---------- supplier side (P3a) ----------

export interface ProposalCard {
  id: number;
  kind: "ADJUSTMENT" | "RETURN_CHECK" | "REMINDER" | "REPLY" | "PROMISE_NOTE";
  status: "PENDING" | "APPROVED" | "REJECTED" | "DONE" | "FAILED";
  source: "agent" | "seed" | "fallback";
  buyer: { id: number; name: string };
  summary: string;
  checks: string[];
  message_text: string | null;
  payload: Record<string, unknown> & {
    amount_cents?: number;
    mode?: "APPLY" | "CREDIT";
    credit_cents?: number;
    reason?: string;
    items?: { text: string }[];
  };
  due_cents: number | null;
  suggested: "APPLY" | "CREDIT" | null;
  result: (Record<string, unknown> & {
    paypal_due_cents?: number;
    error?: string | null;
    reply_text?: string;
  }) | null;
  invoice_no: string | null;
  paypal_url: string | null;
}

export interface SupplierOverview {
  totals: { open_cents: number; past_due_cents: number; paid_this_week_cents: number };
  buyers: {
    id: number;
    name: string;
    out: number;
    kept: number;
    returned: number;
    owed_cents: number;
    oldest_age_days: number;
    past_due_cents: number;
    credit_cents: number;
    last_payment: { amount_cents: number; received_at: string | null } | null;
    pending_proposals: number;
    promise: string | null;
    paypal_setup: PaypalSetupState;
  }[];
  pending: ProposalCard[];
  recent_payments: {
    id: number;
    buyer: { id: number; name: string };
    amount_cents: number;
    source: string;
    method: string | null;
    received_at: string | null;
    split: { invoice_no: string; amount_cents: number }[];
    recorded_all: boolean;
  }[];
}

export interface SupplierInvoice extends Invoice {
  paypal_status: string | null;
  paypal_due_cents: number | null;
  paypal_synced_at: string | null;
  mismatch: string | null;
  adjustments: { name: string; amount_cents: number }[];
  reminder: "AVAILABLE" | "PENDING" | null; // P3b F10: "Draft a reminder" button state
}

export interface BuyerDetail {
  buyer: { id: number; name: string; is_demo: boolean };
  statement_url: string;
  statement_token: string;
  owed_cents: number;
  past_due_cents: number;
  oldest_age_days: number;
  credit_cents: number;
  paypal_setup: PaypalSetupState;
  memos: Memo[];
  invoices: SupplierInvoice[];
  messages: { id: number; kind: string; text: string; label: string | null; created_at: string }[];
  proposals: ProposalCard[];
  payments: {
    id: number;
    source: string;
    status: string;
    method: string | null;
    reference: string | null;
    amount_cents: number;
    unapplied_cents: number;
    received_at: string | null;
    allocations: { invoice_no: string; amount_cents: number; record_state: string; error: string | null }[];
  }[];
}

export interface PaymentDetail {
  payment: {
    id: number;
    source: string;
    status: string;
    method: string | null;
    reference: string | null;
    amount_cents: number;
    unapplied_cents: number;
    received_at: string | null;
    paypal_order_id: string | null;
    paypal_capture_id: string | null;
  };
  buyer: { id: number; name: string };
  allocations: {
    invoice_id: number;
    invoice_no: string;
    amount_cents: number;
    record_state: string;
    paypal_transaction_id: string | null;
    error: string | null;
    paypal_status: string | null;
    paypal_due_cents: number | null;
    paypal_synced_at: string | null;
    mismatch: string | null;
    paypal_url: string | null;
  }[];
  timeline: { at: string; actor: string; action: string; invoice_no: string | null }[];
}

export interface DecideBody {
  decision: "APPROVE" | "REJECT";
  mode?: "APPLY" | "CREDIT";
  edits?: { amount_cents?: number; reason?: string; text?: string };
}

export interface RecordBody {
  buyer_id: number;
  client_uid: string;
  method: "CHECK" | "WIRE_TRANSFER" | "OTHER";
  reference: string;
  amount_cents?: number;
  invoice_ids?: number[];
  received_on?: string;
}

export interface RecordResult {
  payment_id: number;
  method: string;
  reference: string | null;
  amount_cents: number;
  received_at: string | null;
  allocations: { invoice_no: string; amount_cents: number; record_state: string }[];
  repeat: boolean;
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
  setup: (token: string) => call<SetupResult>(`/statement/${enc(token)}/setup`, post({})),
  whoami: () => call<WhoAmI>("/demo/whoami"),
  supplier: {
    overview: () => call<SupplierOverview>("/supplier/overview"),
    buyer: (id: number) => call<BuyerDetail>(`/supplier/buyers/${id}`),
    payment: (id: number) => call<PaymentDetail>(`/supplier/payments/${id}`),
    decide: (id: number, body: DecideBody) =>
      call<ProposalCard>(`/supplier/proposals/${id}/decide`, post(body)),
    record: (body: RecordBody) => call<RecordResult>("/supplier/payments/record", post(body)),
    setup: (id: number) => call<SetupResult>(`/supplier/buyers/${id}/setup`, post({})),
    draftReminder: (invoiceId: number) =>
      call<ProposalCard>(`/supplier/invoices/${invoiceId}/reminder`, post({})),
  },
};
