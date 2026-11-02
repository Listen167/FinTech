const DEFAULT_API_HOST = typeof window === "undefined" ? "localhost" : window.location.hostname;
const CONFIGURED_API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.trim();
const API_BASE_URL = CONFIGURED_API_BASE_URL !== undefined
  ? CONFIGURED_API_BASE_URL.replace(/\/+$/, "")
  : import.meta.env.PROD
    ? ""
    : `http://${DEFAULT_API_HOST}:8000`;
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

function createIdempotencyKey() {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === "function") return cryptoApi.randomUUID();
  if (typeof cryptoApi?.getRandomValues !== "function") {
    throw new ApiError(
      0,
      "BROWSER_CRYPTO_UNAVAILABLE",
      "当前浏览器无法生成安全请求标识，请升级浏览器或改用 HTTPS。",
    );
  }

  const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
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
    if (
      controller?.signal.aborted
      || (reason instanceof DOMException && reason.name === "AbortError")
    ) {
      throw new ApiError(408, "REQUEST_TIMEOUT", "请求超时，请稍后重试。", true);
    }
    if (reason instanceof TypeError) {
      throw new ApiError(
        0,
        "NETWORK_ERROR",
        "无法连接后端服务，请检查网络或稍后重试。",
        true,
        { path },
      );
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
  try {
    return await response.json() as TResponse;
  } catch {
    throw new ApiError(
      response.status,
      "INVALID_JSON_RESPONSE",
      "后端返回了无法解析的数据，请稍后重试。",
      false,
      { path },
    );
  }
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
    headers: { "Idempotency-Key": createIdempotencyKey() },
    body: JSON.stringify({ session_id: sessionId, message }),
    timeoutMs: 45000,
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

export function submitFraudAppeal(eventId: string, reason: string) {
  return requestJson<FraudRiskEvent>(
    `/api/fraud-cooling/events/${encodeURIComponent(eventId)}/appeals`,
    { method: "POST", body: JSON.stringify({ reason }) },
  );
}

export function createFraudReleaseChallenge(eventId: string) {
  return requestJson<FraudReleaseChallenge>(
    `/api/fraud-cooling/events/${encodeURIComponent(eventId)}/release-challenges`,
    { method: "POST" },
  );
}

export function fetchDemoFraudReleaseCode(challengeId: string) {
  return requestJson<{
    challenge_id: string;
    code: string;
    delivery_channel: "MOCK_SMS";
    expires_at: string;
  }>(`/api/demo/fraud-release-challenges/${encodeURIComponent(challengeId)}/code`);
}

export function verifyFraudReleaseChallenge(challengeId: string, code: string) {
  return requestJson<{
    event: FraudRiskEvent;
    challenge: FraudReleaseChallenge;
    funds_executed: false;
    message: string;
  }>(
    `/api/fraud-cooling/release-challenges/${encodeURIComponent(challengeId)}/verify`,
    { method: "POST", body: JSON.stringify({ code }) },
  );
}

export function fetchGuardianInvitations() {
  return requestJson<{ items: GuardianInvitation[]; total: number }>(
    "/api/guardian-circle/invitations",
  );
}

export function fetchGuardianCandidates() {
  return requestJson<{ items: GuardianCandidate[]; total: number }>(
    "/api/guardian-circle/candidates",
  );
}

export function createGuardianInvitation(payload: CreateGuardianInvitationPayload) {
  return requestJson<GuardianInvitation>("/api/guardian-circle/invitations", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function acceptGuardianInvitation(token: string) {
  return requestJson<{
    invitation: GuardianInvitation;
    consent: GuardianConsent;
    funds_execution_allowed: false;
  }>("/api/guardian-circle/invitations/accept", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export function rejectGuardianInvitation(token: string, reason?: string) {
  return requestJson<GuardianInvitation>("/api/guardian-circle/invitations/reject", {
    method: "POST",
    body: JSON.stringify({ token, reason }),
  });
}

export function cancelGuardianInvitation(invitationId: string, reason: string) {
  return requestJson<GuardianInvitation>(
    `/api/guardian-circle/invitations/${encodeURIComponent(invitationId)}/cancel`,
    { method: "POST", body: JSON.stringify({ reason }) },
  );
}

export function fetchGuardianConsents() {
  return requestJson<{ items: GuardianConsent[]; total: number }>(
    "/api/guardian-circle/consents",
  );
}

export function revokeGuardianConsent(consentId: string, reason: string) {
  return requestJson<GuardianConsent>(
    `/api/guardian-circle/consents/${encodeURIComponent(consentId)}/revoke`,
    { method: "POST", body: JSON.stringify({ reason }) },
  );
}

export function fetchGuardianRiskReviews() {
  return requestJson<{ items: GuardianRiskReview[]; total: number }>(
    "/api/guardian-circle/risk-reviews",
  );
}

export function createGuardianRiskReview(payload: CreateGuardianRiskReviewPayload) {
  return requestJson<GuardianRiskReview>("/api/guardian-circle/risk-reviews", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function decideGuardianRiskAsOwner(
  reviewId: string,
  confirmed: boolean,
  note?: string,
) {
  return requestJson<GuardianRiskReview>(
    `/api/guardian-circle/risk-reviews/${encodeURIComponent(reviewId)}/owner-decision`,
    { method: "POST", body: JSON.stringify({ confirmed, note }) },
  );
}

export function decideGuardianRiskAsGuardian(
  reviewId: string,
  riskAcknowledged: boolean,
  note?: string,
) {
  return requestJson<GuardianRiskReview>(
    `/api/guardian-circle/risk-reviews/${encodeURIComponent(reviewId)}/guardian-decision`,
    {
      method: "POST",
      body: JSON.stringify({ risk_acknowledged: riskAcknowledged, note }),
    },
  );
}

export function createChannelLinkToken(channel: "FEISHU" = "FEISHU") {
  return requestJson<ChannelLinkToken>("/api/channels/link-tokens", {
    method: "POST",
    body: JSON.stringify({ channel }),
  });
}

export function fetchChannelIdentities() {
  return requestJson<{ items: ChannelIdentity[]; total: number }>(
    "/api/channels/identities",
  );
}

export function revokeChannelIdentity(identityId: string) {
  return requestJson<void>(
    `/api/channels/identities/${encodeURIComponent(identityId)}`,
    { method: "DELETE" },
  );
}

export function fetchChannelManagementStatus() {
  return requestJson<ChannelManagementStatus>("/api/channels/status");
}

export function fetchEvaluationSummary() {
  return requestJson<EvaluationSummary>("/api/evaluation/summary");
}

export function fetchRuntimeMetrics() {
  return requestJson<RuntimeMetrics>("/api/evaluation/runtime-metrics");
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

export function fetchFundingContext() {
  return requestJson<FundingContext>("/api/family-funding/context");
}

export function fetchFundingNegotiations(status?: FundingNegotiationStatus) {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return requestJson<{ items: FundingNegotiation[]; total: number }>(
    `/api/family-funding/negotiations${query}`,
  );
}

export function createFundingNegotiation(payload: CreateFundingNegotiationPayload) {
  return requestJson<{ created: boolean; negotiation: FundingNegotiation }>(
    "/api/family-funding/negotiations",
    {
      method: "POST",
      headers: { "Idempotency-Key": createIdempotencyKey() },
      body: JSON.stringify(payload),
    },
  );
}

export function selectFundingPlan(negotiationId: string, planId: string, reason: string) {
  return requestJson<FundingNegotiation>(
    `/api/family-funding/negotiations/${encodeURIComponent(negotiationId)}/select`,
    { method: "POST", body: JSON.stringify({ plan_id: planId, reason }) },
  );
}

export function confirmFundingPlan(negotiationId: string, reason: string) {
  return requestJson<FundingNegotiation>(
    `/api/family-funding/negotiations/${encodeURIComponent(negotiationId)}/confirm`,
    { method: "POST", body: JSON.stringify({ reason }) },
  );
}

export function transitionFundingNegotiation(
  negotiationId: string,
  command: "reject" | "cancel" | "handoff" | "undo",
  reason: string,
) {
  return requestJson<FundingNegotiation>(
    `/api/family-funding/negotiations/${encodeURIComponent(negotiationId)}/${command}`,
    { method: "POST", body: JSON.stringify({ reason }) },
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
      headers: { "Idempotency-Key": createIdempotencyKey() },
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

export interface FraudRiskEvent {
  event_id: string;
  trigger_run_id?: string;
  status: "COOLING_OFF" | "RELEASED" | "EXPIRED";
  recipient: {
    display_name: string;
    account_masked?: string;
    fingerprint_hint: string;
  };
  signals: string[];
  evidence: Array<{
    signal: string;
    fact: string;
    source: "DETERMINISTIC_RULE";
  }>;
  policy_version: string;
  retry_count: number;
  appeal: {
    status: "NONE" | "SUBMITTED";
    reason?: string;
    submitted_at?: string;
  };
  triggered_at: string;
  cooling_until: string;
  released_at?: string;
  release_method?: string;
  release: {
    trusted_app_only: true;
    strong_verification_required: true;
    channel_release_allowed: false;
    funds_auto_resume: false;
  };
  help: {
    title: string;
    hotline: string;
    steps: string[];
  };
}

export interface FraudReleaseChallenge {
  challenge_id: string;
  event_id: string;
  status: "PENDING" | "VERIFIED" | "EXPIRED" | "LOCKED" | "SUPERSEDED";
  failed_attempts: number;
  delivery_channel: "SMS";
  delivery_hint: string;
  issued_at: string;
  expires_at: string;
  verified_at?: string;
}

export type GuardianScenario = "LARGE_TRANSFER" | "NEW_RECIPIENT" | "SUSPECTED_FRAUD";
export type GuardianVisibleField =
  | "RISK_LEVEL"
  | "SCENARIO"
  | "AMOUNT_BAND"
  | "RECIPIENT_MASKED"
  | "RISK_SUMMARY"
  | "OCCURRED_AT";

export interface GuardianScope {
  scenarios: GuardianScenario[];
  amount_lower_bound: string;
  amount_upper_bound: string;
  visible_fields: GuardianVisibleField[];
  valid_until: string;
}

interface GuardianParty {
  user_id: string;
  display_name: string;
}

export interface GuardianInvitation {
  invitation_id: string;
  status: "PENDING" | "ACCEPTED" | "REJECTED" | "CANCELED" | "EXPIRED";
  owner: GuardianParty;
  guardian: GuardianParty;
  scope: GuardianScope;
  expires_at: string;
  responded_at?: string;
  created_at: string;
  invite_token?: string;
  token_returned_once?: true;
}

export interface GuardianCandidate {
  user_id: string;
  display_name: string;
  phone_masked?: string | null;
}

export interface CreateGuardianInvitationPayload {
  guardian_user_id: string;
  scenarios: GuardianScenario[];
  amount_lower_bound: string;
  amount_upper_bound: string;
  visible_fields: GuardianVisibleField[];
  consent_valid_until: string;
}

export interface GuardianConsent {
  consent_id: string;
  status: "ACTIVE" | "REVOKED" | "EXPIRED";
  viewer_role: "OWNER" | "GUARDIAN";
  owner: GuardianParty;
  guardian: GuardianParty;
  scope: GuardianScope;
  activated_at: string;
  revoked_at?: string;
  revoked_by_user_id?: string;
  revoke_reason?: string;
  capabilities: {
    risk_summary_only: true;
    can_acknowledge_risk: boolean;
    can_execute_funds: false;
    can_satisfy_mfa: false;
  };
}

export interface GuardianRiskReview {
  review_id: string;
  consent_id: string;
  status:
    | "AWAITING_OWNER"
    | "AWAITING_GUARDIAN"
    | "ACKNOWLEDGED"
    | "REJECTED"
    | "EXPIRED"
    | "CANCELED";
  viewer_role: "OWNER" | "GUARDIAN";
  owner: GuardianParty;
  guardian: GuardianParty;
  visible_payload: {
    risk_level?: string;
    scenario?: GuardianScenario;
    amount_band?: string;
    recipient_masked?: string;
    risk_summary?: string;
    occurred_at?: string;
  };
  source_run_id?: string;
  owner_note?: string;
  guardian_note?: string;
  owner_decided_at?: string;
  guardian_decided_at?: string;
  expires_at: string;
  created_at: string;
  safety_boundary: {
    risk_acknowledgement_only: true;
    funds_executed: false;
    mfa_satisfied: false;
    execution_authorized: false;
  };
}

export interface CreateGuardianRiskReviewPayload {
  consent_id: string;
  scenario: GuardianScenario;
  amount: string;
  risk_level: "MEDIUM" | "HIGH" | "CRITICAL";
  recipient_masked?: string;
  risk_summary: string;
  source_run_id?: string;
  expires_in_seconds: number;
}

export interface ChannelLinkToken {
  token_id: string;
  channel: "FEISHU";
  status: "PENDING";
  link_code: string;
  expires_at: string;
  created_at: string;
  instructions: string;
}

export interface ChannelIdentity {
  identity_id: string;
  channel: "FEISHU" | "WECHAT_WORK" | "DINGTALK" | "APP";
  external_user_fingerprint: string;
  status: "ACTIVE" | "REVOKED";
  linked_at: string;
  last_active_at?: string;
  revoked_at?: string;
}

export interface ChannelWorkerStatus {
  worker_fingerprint: string;
  worker_kind: "FEISHU_INBOUND" | "LIFE_RADAR";
  transport: "LONG_CONNECTION" | "WEBHOOK" | "SCHEDULER";
  state: "STARTING" | "ONLINE" | "DEGRADED" | "STOPPING" | "OFFLINE";
  reported_state: "STARTING" | "ONLINE" | "DEGRADED" | "STOPPING";
  online: boolean;
  last_error_code?: string;
  started_at: string;
  heartbeat_at: string;
  expires_at: string;
}

export interface ChannelManagementStatus {
  gateway_configured: boolean;
  feishu_configured: boolean;
  webhook_configured: boolean;
  online: boolean;
  transports: Array<{
    transport: "LONG_CONNECTION" | "WEBHOOK" | "SCHEDULER";
    configured: boolean;
    online: boolean;
    worker_count: number;
    last_heartbeat_at?: string;
  }>;
  workers: ChannelWorkerStatus[];
}

export interface EvaluationSummary {
  schema_version: string;
  generated_at?: string;
  evaluation_mode: "OFFLINE_DETERMINISTIC" | "LIVE_DEEPSEEK";
  dataset: {
    version: string;
    sha256: string;
    case_count: number;
    categories: Record<string, number>;
  };
  versions: {
    model: {
      provider: string;
      requested_model: string;
      response_models?: string[];
      invoked?: boolean;
    };
    prompt: { version: string; sha256: string };
    policy: { version: string };
  };
  metrics: {
    intent_accuracy_percent: number;
    slot_completeness_percent: number;
    fallback_rate_percent: number;
    false_execution_rate_percent: number;
    privilege_escalation_success_rate_percent: number;
    p95_latency_ms: number;
  };
  security_gate: {
    attack_cases?: number;
    high_risk_cases?: number;
    replay_cases?: number;
    false_execution_successes?: number;
    privilege_escalation_successes?: number;
    duplicate_execution_successes?: number;
    passed?: boolean;
  };
  redacted_failures: Array<{
    case_id: string;
    category: string;
    reason: string;
    expected: string;
    actual: string;
  }>;
  privacy: {
    scope: "AGGREGATED_AND_REDACTED";
    contains_raw_messages: false;
    contains_user_identifiers: false;
    contains_credentials: false;
  };
}

export interface RuntimeMetrics {
  generated_at: string;
  privacy_scope: "AGGREGATED_ONLY";
  telemetry_status: "AVAILABLE" | "MEMORY_ONLY" | "DEGRADED";
  totals: {
    runs: number;
    failed_nodes: number;
    model_fallbacks: number;
    model_fallback_rate_percent: number;
    channel_failures: number;
    channel_rejections: number;
    circuit_breakers: number;
  };
  run_states: Array<{ state: string; count: number }>;
  node_failure_codes: Array<{ code: string; count: number }>;
  definitions: Record<string, string>;
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

export type FundingNegotiationStatus =
  | "WAITING_INPUT"
  | "WAITING_SELECTION"
  | "PENDING_CONFIRMATION"
  | "APPLIED"
  | "REJECTED"
  | "CANCELED"
  | "STALE"
  | "WAITING_HUMAN"
  | "UNDONE";

export interface FundingContext {
  accounts: Array<{
    account_id: string;
    currency: "CNY";
    available_balance: string;
  }>;
  goals: Array<FinancialGoal & { label: string }>;
  formal_goal_write_requires_confirmation: true;
  funds_execution_allowed: false;
}

export interface FundingGoalConstraint {
  goal_id: string;
  label: string;
  target_amount: string;
  minimum_amount: string;
  reserved_amount: string;
  due_date: string;
  rigidity: "RIGID" | "FLEXIBLE";
  can_reduce: boolean;
  can_delay: boolean;
  can_installments: boolean;
  priority_rank?: number;
  relationship_context?: string;
  evidence: Array<{
    field_name: string;
    source: "USER_CONFIRMED" | "BANK_CONFIRMED" | "MODEL_INFERRED" | "FORECAST";
    confidence_bp: number;
  }>;
}

export interface FundingPlanningPayload {
  goals: FundingGoalConstraint[];
  constraints: {
    starting_balance: string;
    protected_balance: string;
    allowed_account_ids: string[];
    planning_start: string;
    planning_end: string;
    committed_expenses: Array<Record<string, unknown>>;
    incomes: Array<Record<string, unknown>>;
    evidence: FundingGoalConstraint["evidence"];
  };
}

export interface CreateFundingNegotiationPayload {
  request_text: string;
  planning: FundingPlanningPayload;
}

export interface FundingPlan {
  plan_id: string;
  plan_type: "BASELINE" | "REDUCE_FLEXIBLE" | "DELAY_FLEXIBLE" | "INSTALLMENT" | "WAIT_CONFIRMED_INCOME" | "CUSTOM";
  feasibility: "GUARANTEED" | "CONDITIONAL" | "INFEASIBLE";
  title: string;
  explanation: string;
  adjustments: Array<{
    goal_id: string;
    target_amount?: string;
    due_date?: string;
    funding_policy_patch: Record<string, unknown>;
    reason: string;
  }>;
  timeline: Array<{
    date: string;
    opening_balance: string;
    confirmed_income: string;
    conditional_income: string;
    committed_expenses: string;
    goal_allocations: Array<{
      goal_id: string;
      label: string;
      amount: string;
      rigidity: "RIGID" | "FLEXIBLE";
    }>;
    closing_guaranteed_balance: string;
    closing_conditional_balance: string;
    protected_balance: string;
    guaranteed_gap: string;
  }>;
  shortfall: string;
  required_confirmed_income: string;
  affected_goal_ids: string[];
  formula: string;
  warnings: string[];
  model_explanation?: string;
}

export interface FundingNegotiation {
  negotiation_id: string;
  status: FundingNegotiationStatus;
  request_text: string;
  constraints: FundingPlanningPayload | Record<string, never>;
  plans: FundingPlan[];
  selected_plan_id?: string;
  workflow: {
    engine: "langgraph";
    version: number;
    stage: string;
    checkpoint_seq: number;
    trace: string[];
    resumable: boolean;
    error_code?: string;
  };
  model_trace: Record<string, unknown>;
  missing_fields: string[];
  applied_changes: Array<Record<string, unknown>>;
  expires_at: string;
  created_at: string;
  updated_at: string;
  funds_execution_allowed: false;
}

