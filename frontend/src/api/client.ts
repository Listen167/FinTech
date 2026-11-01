const DEFAULT_API_HOST = typeof window === "undefined" ? "localhost" : window.location.hostname;
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? `http://${DEFAULT_API_HOST}:8000`;
const CSRF_COOKIE_NAME = import.meta.env.VITE_AUTH_CSRF_COOKIE_NAME ?? "safebank_csrf";

let accessToken: string | undefined;
let refreshRequest: Promise<LoginResponse> | undefined;
let authenticationExpiredHandler: (() => void) | undefined;

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly retryable = false,
    public readonly details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface RequestOptions extends RequestInit {
  authenticated?: boolean;
  retryAfterRefresh?: boolean;
  timeoutMs?: number;
}

function readCookie(name: string) {
  if (typeof document === "undefined") return undefined;
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie.split("; ").find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : undefined;
}

async function responseError(response: Response) {
  let code = "REQUEST_FAILED";
  let message = `请求失败（${response.status}）`;
  try {
    const body = await response.json() as {
      detail?: string | Array<{ msg?: string; loc?: Array<string | number> }> | {
        code?: string;
        message?: string;
        retryable?: boolean;
        details?: Record<string, unknown>;
      };
    };
    if (typeof body.detail === "string") message = body.detail;
    else if (Array.isArray(body.detail)) {
      code = "INVALID_REQUEST";
      message = body.detail
        .map((item) => `${item.loc?.join(".") ?? "参数"}：${item.msg ?? "格式错误"}`)
        .join("；");
    } else if (body.detail) {
      code = body.detail.code ?? code;
      message = body.detail.message ?? message;
      return new ApiError(
        response.status,
        code,
        message,
        body.detail.retryable ?? response.status >= 500,
        body.detail.details,
      );
    }
  } catch {
    // 非 JSON 错误响应使用通用信息。
  }
  return new ApiError(response.status, code, message, response.status >= 500);
}

function clearAccessToken(notify = false) {
  accessToken = undefined;
  if (notify) authenticationExpiredHandler?.();
}

async function sendRequest(path: string, options: RequestOptions) {
  const authenticated = options.authenticated ?? true;
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (authenticated && accessToken) headers.set("Authorization", `Bearer ${accessToken}`);

  const timeoutMs = options.timeoutMs ?? 15000;
  const controller = options.signal ? undefined : new AbortController();
  const timeoutId = controller
    ? window.setTimeout(() => controller.abort(), timeoutMs)
    : undefined;
  try {
    return await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers,
      signal: options.signal ?? controller?.signal,
      credentials: "include",
    });
  } catch (reason) {
    if (reason instanceof DOMException && reason.name === "AbortError") {
      throw new ApiError(408, "REQUEST_TIMEOUT", "请求超时，请稍后重试。", true);
    }
    throw reason;
  } finally {
    if (timeoutId !== undefined) window.clearTimeout(timeoutId);
  }
}

export async function requestJson<TResponse>(
  path: string,
  options: RequestOptions = {},
): Promise<TResponse> {
  let response = await sendRequest(path, options);

  const authenticated = options.authenticated ?? true;
  const mayRefresh = options.retryAfterRefresh ?? true;
  if (response.status === 401 && authenticated && mayRefresh) {
    try {
      await restoreAuthentication();
    } catch (reason) {
      clearAccessToken(true);
      throw reason;
    }
    response = await sendRequest(path, { ...options, retryAfterRefresh: false });
    if (response.status === 401) clearAccessToken(true);
  }

  if (!response.ok) {
    throw await responseError(response);
  }

  if (response.status === 204) return undefined as TResponse;
  return response.json() as Promise<TResponse>;
}

export function onAuthenticationExpired(handler: () => void) {
  authenticationExpiredHandler = handler;
}

export async function login(username: string, password: string) {
  const result = await requestJson<LoginResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
    authenticated: false,
    retryAfterRefresh: false,
  });
  accessToken = result.access_token;
  return result;
}

export async function restoreAuthentication() {
  if (refreshRequest) return refreshRequest;
  refreshRequest = (async () => {
    const csrfToken = readCookie(CSRF_COOKIE_NAME);
    if (!csrfToken) {
      clearAccessToken();
      throw new ApiError(401, "REFRESH_CREDENTIALS_MISSING", "登录状态不存在，请重新登录。");
    }

    const response = await sendRequest("/api/auth/refresh", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      authenticated: false,
      retryAfterRefresh: false,
    });
    if (!response.ok) {
      clearAccessToken(true);
      throw await responseError(response);
    }
    const result = await response.json() as LoginResponse;
    accessToken = result.access_token;
    return result;
  })();

  try {
    return await refreshRequest;
  } finally {
    refreshRequest = undefined;
  }
}

export async function logout(allDevices = false) {
  await requestJson<{ success: boolean }>(
    allDevices ? "/api/auth/logout-all" : "/api/auth/logout",
    { method: "POST" },
  );
  clearAccessToken();
}

export function createAgentRun(sessionId: string, message: string) {
  return requestJson<AgentRun>("/api/agent/runs", {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify({ session_id: sessionId, message }),
  });
}

export function createAgentSession() {
  return requestJson<AgentSession>("/api/agent/sessions", {
    method: "POST" });
}

export function fetchAgentSessions() {
  return requestJson<{ items: AgentSessionSummary[]; total: number }>("/api/agent/sessions");
}

export function requestHumanHandoff(runId: string, reason: string) {
  return requestJson<AgentRun>(
    `/api/agent/runs/${encodeURIComponent(runId)}/handoff`,
    {
      method: "POST",
      body: JSON.stringify({ reason }),
    },
  );
}

export function fetchSession(sessionId: string) {
  return requestJson<AgentSession>(`/api/agent/sessions/${encodeURIComponent(sessionId)}`);
}

export function fetchAgentRun(runId: string) {
  return requestJson<AgentRun>(`/api/agent/runs/${encodeURIComponent(runId)}`);
}

export function fetchModelStatus() {
  return requestJson<ModelStatus>("/api/agent/model-status");
}

export function decideConfirmation(confirmationId: string, confirmed: boolean) {
  return requestJson<AgentRun>(`/api/agent/confirmations/${confirmationId}/decide`, {
    method: "POST",
    body: JSON.stringify({ confirmed }),
  });
}

export function verifyOperation(verificationId: string, code: string) {
  return requestJson<AgentRun>(`/api/agent/verifications/${verificationId}/verify`, {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export function resendVerification(verificationId: string) {
  return requestJson<VerificationChallenge>(
    `/api/agent/verifications/${verificationId}/resend`,
    { method: "POST" },
  );
}

export function fetchDemoVerificationCode(verificationId: string) {
  return requestJson<{
    verification_id: string;
    code: string;
    delivery_channel: "MOCK_SMS";
    expires_at: string;
  }>(`/api/demo/verifications/${verificationId}/code`);
}

export function fetchAuditLogs(runId?: string) {
  const query = runId ? `?run_id=${encodeURIComponent(runId)}` : "";
  return requestJson<{ items: AuditLog[]; total: number }>(`/api/audit-logs${query}`);
}

export function fetchOperationPassport(runId: string) {
  return requestJson<OperationPassport>(
    `/api/operation-passports/runs/${encodeURIComponent(runId)}`,
  );
}

export async function downloadOperationPassport(runId: string) {
  const passport = await requestJson<OperationPassport>(
    `/api/operation-passports/runs/${encodeURIComponent(runId)}/export`,
  );
  const blob = new Blob([JSON.stringify(passport, null, 2)], {
    type: "application/json;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `operation-passport-${runId}.json`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function resetDemo() {
  return requestJson<{ success: boolean; message: string }>("/api/demo/reset", { method: "POST" });
}

export function fetchLifeGraph() {
  return requestJson<LifeGraph>("/api/life-graph");
}

export function fetchLifeGraphChangeRequests(status?: LifeGraphChangeStatus) {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return requestJson<{ items: LifeGraphChangeRequest[]; total: number }>(
    `/api/life-graph/change-requests${query}`,
  );
}

export function decideLifeGraphChange(requestId: string, approved: boolean, reason?: string) {
  return requestJson<LifeGraphChangeRequest>(
    `/api/life-graph/change-requests/${encodeURIComponent(requestId)}/decide`,
    {
      method: "POST",
      body: JSON.stringify({ approved, reason }),
    },
  );
}

export function fetchGoalConflictAnalysis(requestId?: string, protectedBalance = "0.00") {
  const base = requestId
    ? `/api/life-graph/change-requests/${encodeURIComponent(requestId)}/goal-conflicts`
    : "/api/life-graph/goal-conflicts";
  return requestJson<GoalConflictAnalysis>(
    `${base}?protected_balance=${encodeURIComponent(protectedBalance)}`,
  );
}

export function correctLifeGraphNode(
  nodeId: string,
  correction: LifeGraphNodeCorrection,
) {
  return requestJson<{ correction_id: string; graph: LifeGraph }>(
    `/api/life-graph/nodes/${encodeURIComponent(nodeId)}`,
    {
      method: "PATCH",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify(correction),
    },
  );
}

export function fetchLifeRadarStatus() {
  return requestJson<LifeRadarStatus>("/api/life-radar/status");
}

export function controlLifeRadarAction(
  actionId: string,
  command: "pause" | "resume" | "cancel",
) {
  return requestJson<LifeEventAction>(
    `/api/life-radar/actions/${encodeURIComponent(actionId)}/${command}`,
    { method: "POST" },
  );
}

export function simulateLifeRadar(advanceDays = 30) {
  return requestJson<LifeRadarScanResult>("/api/life-radar/demo/scan", {
    method: "POST",
    body: JSON.stringify({ advance_days: advanceDays }),
  });
}

export interface PlanNode {
  id: string;
  type: "VALIDATION" | "TOOL" | "POLICY" | "HUMAN_CONFIRM" | "RESPONSE";
  label: string;
  tool?: string;
  input: Record<string, unknown>;
  depends_on: string[];
  runtime: {
    status: "PENDING" | "READY" | "RUNNING" | "WAITING" | "SUCCEEDED" | "FAILED" | "SKIPPED";
    revision: number;
    reason?: string;
    attempt: number;
    input_digest?: string;
    output_receipt?: {
      kind: "TOOL_RESULT" | "POLICY_DECISION" | "USER_DECISION" | "SYSTEM_RESULT";
      output_digest: string;
      summary: string;
      reference_id?: string;
    };
    started_at?: string;
    finished_at?: string;
    error_code?: string;
  };
}

export interface AgentRun {
  run_id: string;
  session_id: string;
  state: string;
  message: string;
  user_message: string;
  intent?: {
    name: string;
    confidence: number;
    risk_hints: string[];
    parser?: string;
    model?: string;
    missing_slots?: string[];
  };
  model_trace?: {
    provider: string;
    requested_model: string;
    response_model?: string;
    status: "DISABLED" | "SUCCEEDED" | "TIMEOUT" | "API_ERROR" | "INVALID_OUTPUT";
    latency_ms: number;
    fallback_used: boolean;
    error_type?: string;
  };
  plan?: {
    schema_version: number;
    execution_mode: "DECLARATIVE" | "EXECUTABLE";
    plan_id: string;
    nodes: PlanNode[];
  };
  policy?: {
    level: "GREEN" | "YELLOW" | "RED" | "BLOCKED";
    action: string;
    reason: string;
    risk_score: number;
    risk_factors: string[];
  };
  pending_action: {
    type: "NONE" | "CONFIRMATION" | "VERIFICATION" | "LIFE_GRAPH_CHANGE_REQUEST";
    confirmation?: { confirmation_id: string; summary: string; operation_snapshot_hash: string };
    verification?: VerificationChallenge;
    life_graph_change_request?: Pick<
      LifeGraphChangeRequest,
      "request_id" | "summary" | "status" | "expires_at" | "snapshot_hash"
    >;
  };
  data: Record<string, any>;
  workflow?: {
    engine: "langgraph";
    version: number;
    stage: string;
    checkpoint_seq: number;
    trace: string[];
    resumable: boolean;
    recovered: boolean;
    missing_slots: string[];
    change_request_id?: string;
    conflict_status?: string;
    error_code?: string;
    updated_at: string;
  };
  audit_id?: string;
}

export interface VerificationChallenge {
  verification_id: string;
  status: string;
  failed_attempts: number;
  delivery_channel: "SMS";
  delivery_hint: string;
  expires_at: string;
  resend_available_at: string;
}

export interface AgentSession {
  session_id: string;
  title: string;
  channel: string;
  run_count: number;
  last_message?: string;
  last_state?: string;
  has_pending_input: boolean;
  created_at: string;
  runs: AgentRun[];
  pending: { run_id?: string; missing_slots: string[]; expires_at?: string };
  last_recipient?: string;
  updated_at: string;
}

export type AgentSessionSummary = Omit<AgentSession, "runs" | "pending" | "last_recipient">;

export interface ModelStatus {
  provider: string;
  enabled: boolean;
  configured: boolean;
  ready: boolean;
  base_url: string;
  model: string;
  timeout_seconds: number;
}

export interface AuditLog {
  audit_id: string;
  user_id: string;
  run_id: string;
  action: string;
  result: string;
  created_at: string;
}

export interface PassportToolReceipt {
  node_id?: string;
  tool?: string;
  kind: "TOOL_RESULT";
  summary?: string;
  reference_id?: string;
  output_digest?: string;
  finished_at?: string;
}

export interface OperationPassport {
  schema_version: string;
  passport_id: string;
  run_id: string;
  generated_at: string;
  passport_digest: string;
  request: {
    submitted_at?: string;
    raw_message: string;
    structured_intent?: Record<string, any>;
  };
  versions: {
    model?: {
      provider?: string;
      requested_model?: string;
      response_model?: string;
      status?: string;
    };
    prompt?: { version?: string; digest?: string };
    planner?: string;
    executor?: string;
    policy?: string;
    passport_schema?: string;
  };
  model_interpretation: {
    evidence_type: "MODEL_INTERPRETATION";
    status: string;
    parser?: string;
    confidence?: number;
    model_trace?: Record<string, unknown>;
    notice: string;
  };
  task_graph?: AgentRun["plan"];
  risk_evidence: {
    policy?: AgentRun["policy"];
    input_safety?: Record<string, unknown>;
  };
  user_authorization: {
    evidence_type: "USER_AUTHORIZATION";
    method: string;
    status: string;
    reference_id?: string;
    snapshot_digest?: string;
    decided_at?: string;
    notice: string;
  };
  operation_snapshot: {
    digest?: string;
    verified?: boolean;
    view?: Record<string, unknown>;
  };
  tool_receipts: PassportToolReceipt[];
  bank_sandbox_result: {
    evidence_type: "BANK_SANDBOX_RESULT";
    status: string;
    success_claim_allowed: boolean;
    authorization_status: string;
    decisive_receipts: PassportToolReceipt[];
    notice: string;
  };
  audit_timeline: Array<{
    audit_id: string;
    chain_index?: number;
    previous_digest?: string;
    record_digest?: string;
    action: string;
    result: string;
    details: Record<string, unknown>;
    beijing_time?: string;
  }>;
  integrity: {
    audit_chain: {
      status: "VERIFIED" | "TAMPERED" | "EMPTY" | "LEGACY_UNVERIFIED" | "PARTIAL_UNVERIFIED";
      record_count: number;
      verified_count: number;
      head_digest?: string;
      failed_audit_id?: string;
    };
    snapshot_verified?: boolean;
    claim: "DETECTS_ABNORMAL_MODIFICATION_ONLY";
    disclaimer: string;
  };
}

export interface AuthenticatedUser {
  id: string;
  display_name: string;
  phone_masked?: string;
  role: string;
  risk_level: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
  user: AuthenticatedUser;
}

export type LifeGraphNodeType = "PERSON" | "EVENT" | "GOAL";
export type LifeGraphChangeStatus = "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED" | "CANCELED";

export interface LifeGraphNode {
  node_id: string;
  node_type: LifeGraphNodeType;
  display_label: string;
  sensitivity_level: string;
  status: string;
  attributes: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface LifeGraphEdge {
  edge_id: string;
  source_node_id: string;
  target_node_id: string;
  relation_type: string;
  consent_scope: { allowed_uses?: string[] };
  provenance: string;
  confidence_bp: number;
  status: string;
}

export interface LifeEvent {
  event_node_id: string;
  event_type: string;
  occurs_at: string;
  timezone: string;
  recurrence_rule?: string;
  status: string;
  details: Record<string, unknown>;
}

export interface FinancialGoal {
  goal_node_id: string;
  target_amount: string;
  reserved_amount: string;
  currency: string;
  due_at?: string;
  funding_policy: Record<string, unknown>;
  status: string;
}

export interface LifeEventAction {
  action_id: string;
  event_node_id?: string;
  goal_node_id?: string;
  action_type: string;
  scheduled_at: string;
  status: string;
  idempotency_key: string;
  payload: Record<string, unknown>;
  attempt_count: number;
  max_attempts: number;
  next_attempt_at?: string;
  sent_at?: string;
  failed_at?: string;
  paused_at?: string;
  canceled_at?: string;
  last_error_code?: string;
  delivery_receipt: {
    channel?: string;
    delivered_at?: string;
    provider_message_id?: string;
    status?: string;
  };
}

export interface LifeRadarStatus {
  configured: boolean;
  simulation_enabled: boolean;
  clock_mode: "REAL_TIME";
  supported_event_types: string[];
  delivery_channel: "FEISHU_PRIVATE_CHAT";
  funds_execution_allowed: false;
}

export interface LifeRadarScanResult {
  clock: {
    mode: "REAL_TIME" | "SANDBOX_SIMULATED_TIME";
    effective_now: string;
  };
  claimed: number;
  sent: number;
  failed: number;
  canceled: number;
  items: LifeEventAction[];
}

export interface LifeGraph {
  nodes: LifeGraphNode[];
  edges: LifeGraphEdge[];
  events: LifeEvent[];
  goals: FinancialGoal[];
  actions: LifeEventAction[];
}

export interface LifeGraphChangeRequest {
  request_id: string;
  source_run_id?: string;
  source: string;
  status: LifeGraphChangeStatus;
  schema_version: number;
  idempotency_key: string;
  snapshot_hash: string;
  summary: string;
  change_set: Record<string, unknown>;
  decision_reason?: string;
  expires_at: string;
  decided_at?: string;
  created_at: string;
  updated_at: string;
}

export interface GoalConflictGoal {
  goal_node_id: string;
  label: string;
  related_person?: string;
  target_amount: string;
  reserved_amount: string;
  remaining_amount: string;
  currency: string;
  due_date?: string;
  source: "FORMAL" | "CANDIDATE";
}

export interface GoalConflictWindow {
  period: string;
  goals: GoalConflictGoal[];
  period_goal_total: string;
  cumulative_goal_total: string;
  liquidity_gap: string;
  cash_flow_gap?: string;
  status: "CONFLICT" | "PRESSURE" | "CLEAR";
}

export interface GoalConflictAnalysis {
  status: "CONFLICT" | "PRESSURE" | "CLEAR" | "UNKNOWN";
  severity: "HIGH" | "MEDIUM" | "NONE" | "UNKNOWN";
  as_of: string;
  currency: string;
  priority_decision: "USER_REQUIRED";
  evidence: Record<string, string | number | boolean | null | GoalConflictGoal[]>;
  windows: GoalConflictWindow[];
  suggestions: Array<{
    code: string;
    description: string;
    affected_goal_ids: string[];
    requires_user_confirmation: boolean;
    auto_apply: false;
  }>;
  explanation: string;
  disclaimer: string;
}

export interface LifeGraphNodeCorrection {
  display_label?: string;
  event_date?: string;
  target_amount?: string;
  reason: string;
}

