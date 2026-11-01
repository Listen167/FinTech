<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  KeyRound,
  Link2,
  RefreshCw,
  ShieldCheck,
  ShieldX,
  UserRoundCheck,
  UsersRound,
  XCircle,
} from "lucide-vue-next";
import {
  ApiError,
  acceptGuardianInvitation,
  cancelGuardianInvitation,
  createGuardianInvitation,
  createGuardianRiskReview,
  decideGuardianRiskAsGuardian,
  decideGuardianRiskAsOwner,
  fetchGuardianConsents,
  fetchGuardianInvitations,
  fetchGuardianRiskReviews,
  rejectGuardianInvitation,
  revokeGuardianConsent,
  type GuardianConsent,
  type GuardianInvitation,
  type GuardianRiskReview,
  type GuardianScenario,
  type GuardianVisibleField,
} from "../api/client";

const props = defineProps<{ currentUserId: string }>();

const invitations = ref<GuardianInvitation[]>([]);
const consents = ref<GuardianConsent[]>([]);
const reviews = ref<GuardianRiskReview[]>([]);
const loading = ref(false);
const error = ref("");
const notice = ref("");
const inviteToken = ref("");
const issuedToken = ref("");
const guardianUserId = ref(props.currentUserId === "user_001" ? "user_002" : "user_001");
const maxAmount = ref("50000");
const validityDate = ref(defaultValidityDate());
const selectedScenarios = ref<GuardianScenario[]>([
  "LARGE_TRANSFER",
  "NEW_RECIPIENT",
  "SUSPECTED_FRAUD",
]);
const selectedFields = ref<GuardianVisibleField[]>([
  "RISK_LEVEL",
  "SCENARIO",
  "AMOUNT_BAND",
  "RECIPIENT_MASKED",
  "RISK_SUMMARY",
  "OCCURRED_AT",
]);
const reviewConsentId = ref("");
const reviewScenario = ref<GuardianScenario>("NEW_RECIPIENT");
const reviewAmount = ref("12000");
const reviewRecipient = ref("陌***户");
const reviewSummary = ref("新收款人且金额较高，请共同核实是否认识对方。");

const scenarioOptions: Array<{ value: GuardianScenario; label: string }> = [
  { value: "LARGE_TRANSFER", label: "大额转账" },
  { value: "NEW_RECIPIENT", label: "新收款人" },
  { value: "SUSPECTED_FRAUD", label: "疑似诈骗" },
];
const fieldOptions: Array<{ value: GuardianVisibleField; label: string }> = [
  { value: "RISK_LEVEL", label: "风险等级" },
  { value: "SCENARIO", label: "风险场景" },
  { value: "AMOUNT_BAND", label: "金额区间" },
  { value: "RECIPIENT_MASKED", label: "脱敏收款人" },
  { value: "RISK_SUMMARY", label: "风险摘要" },
  { value: "OCCURRED_AT", label: "发生时间" },
];

const ownerConsents = computed(() => consents.value.filter(
  (item) => item.status === "ACTIVE" && item.viewer_role === "OWNER",
));
const incomingPending = computed(() => invitations.value.filter(
  (item) => item.status === "PENDING" && item.guardian.user_id === props.currentUserId,
));

watch(ownerConsents, (items) => {
  if (!items.some((item) => item.consent_id === reviewConsentId.value)) {
    reviewConsentId.value = items[0]?.consent_id ?? "";
  }
}, { immediate: true });

watch(() => props.currentUserId, () => {
  guardianUserId.value = props.currentUserId === "user_001" ? "user_002" : "user_001";
  issuedToken.value = "";
  inviteToken.value = "";
  void refresh();
});

function defaultValidityDate() {
  const value = new Date();
  value.setDate(value.getDate() + 30);
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

function readableError(reason: unknown) {
  if (reason instanceof ApiError) return reason.message;
  if (reason instanceof TypeError) return "无法连接后端服务，请确认服务已启动。";
  return reason instanceof Error ? reason.message : "操作失败，请稍后重试。";
}

function beijingTime(value?: string) {
  if (!value) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function scenarioLabel(value?: string) {
  return scenarioOptions.find((item) => item.value === value)?.label ?? value ?? "未披露";
}

function statusLabel(value: string) {
  return {
    PENDING: "等待接受",
    ACCEPTED: "已接受",
    REJECTED: "已拒绝",
    CANCELED: "已取消",
    EXPIRED: "已过期",
    ACTIVE: "授权生效",
    REVOKED: "已撤销",
    AWAITING_OWNER: "等待本人确认",
    AWAITING_GUARDIAN: "等待守护人确认",
    ACKNOWLEDGED: "风险已知",
  }[value] ?? value;
}

async function run(action: () => Promise<void>) {
  if (loading.value) return;
  loading.value = true;
  error.value = "";
  notice.value = "";
  try {
    await action();
  } catch (reason) {
    error.value = readableError(reason);
  } finally {
    loading.value = false;
  }
}

async function refresh() {
  await run(async () => {
    const [invitationResult, consentResult, reviewResult] = await Promise.all([
      fetchGuardianInvitations(),
      fetchGuardianConsents(),
      fetchGuardianRiskReviews(),
    ]);
    invitations.value = invitationResult.items;
    consents.value = consentResult.items;
    reviews.value = reviewResult.items;
  });
}

async function issueInvitation() {
  await run(async () => {
    const result = await createGuardianInvitation({
      guardian_user_id: guardianUserId.value,
      scenarios: selectedScenarios.value,
      amount_lower_bound: "0",
      amount_upper_bound: maxAmount.value,
      visible_fields: selectedFields.value,
      consent_valid_until: new Date(`${validityDate.value}T23:59:59+08:00`).toISOString(),
    });
    issuedToken.value = result.invite_token ?? "";
    notice.value = "一次性邀请已生成。请通过可信渠道交给指定守护人；接受前不会授予任何权限。";
    const list = await fetchGuardianInvitations();
    invitations.value = list.items;
  });
}

async function respondInvitation(accepted: boolean) {
  const token = inviteToken.value.trim();
  if (!token) return;
  await run(async () => {
    if (accepted) {
      await acceptGuardianInvitation(token);
      notice.value = "守护关系已由双方确认，权限仅限风险摘要和风险已知确认。";
    } else {
      await rejectGuardianInvitation(token, "受邀用户拒绝建立守护关系");
      notice.value = "邀请已拒绝，没有建立任何授权。";
    }
    inviteToken.value = "";
    const [invitationResult, consentResult] = await Promise.all([
      fetchGuardianInvitations(), fetchGuardianConsents(),
    ]);
    invitations.value = invitationResult.items;
    consents.value = consentResult.items;
  });
}

async function cancelInvitation(item: GuardianInvitation) {
  await run(async () => {
    await cancelGuardianInvitation(item.invitation_id, "本人取消尚未接受的守护邀请");
    notice.value = "邀请已取消，旧令牌立即失效。";
    invitations.value = (await fetchGuardianInvitations()).items;
  });
}

async function revoke(item: GuardianConsent) {
  await run(async () => {
    await revokeGuardianConsent(item.consent_id, "用户即时撤销家庭守护授权");
    notice.value = "授权已即时撤销，旧风险链接和待确认请求均已失效。";
    const [consentResult, reviewResult] = await Promise.all([
      fetchGuardianConsents(), fetchGuardianRiskReviews(),
    ]);
    consents.value = consentResult.items;
    reviews.value = reviewResult.items;
  });
}

async function createReview() {
  if (!reviewConsentId.value) return;
  await run(async () => {
    await createGuardianRiskReview({
      consent_id: reviewConsentId.value,
      scenario: reviewScenario.value,
      amount: reviewAmount.value,
      risk_level: reviewScenario.value === "SUSPECTED_FRAUD" ? "CRITICAL" : "HIGH",
      recipient_masked: reviewRecipient.value,
      risk_summary: reviewSummary.value,
      expires_in_seconds: 900,
    });
    notice.value = "风险摘要已建立，必须先由本人确认，再等待守护人确认风险已知。";
    reviews.value = (await fetchGuardianRiskReviews()).items;
  });
}

async function ownerDecision(item: GuardianRiskReview, confirmed: boolean) {
  await run(async () => {
    await decideGuardianRiskAsOwner(
      item.review_id,
      confirmed,
      confirmed ? "本人确认将风险摘要交给守护人" : "本人取消本次风险协助",
    );
    notice.value = confirmed
      ? "本人已确认，正在等待守护人确认风险已知。"
      : "本人已拒绝，本次风险协助结束。";
    reviews.value = (await fetchGuardianRiskReviews()).items;
  });
}

async function guardianDecision(item: GuardianRiskReview, acknowledged: boolean) {
  await run(async () => {
    await decideGuardianRiskAsGuardian(
      item.review_id,
      acknowledged,
      acknowledged ? "风险已知，建议本人继续通过银行官方渠道核实" : "无法确认风险，建议停止操作",
    );
    notice.value = acknowledged
      ? "已记录风险已知；这不是 MFA，也不会授权或执行资金操作。"
      : "守护人未确认风险，本次协助结束。";
    reviews.value = (await fetchGuardianRiskReviews()).items;
  });
}

onMounted(refresh);
</script>

<template>
  <div class="guardian-workspace">
    <header class="guardian-intro">
      <div>
        <small>Guardian Consent Circle</small>
        <h2>家庭守护授权圈</h2>
        <p>生活图谱中的家庭关系不会自动获得权限。只有一次性邀请经双方确认后，守护人才能查看最小风险摘要。</p>
      </div>
      <button type="button" class="icon-command" title="刷新守护圈" aria-label="刷新守护圈" :disabled="loading" @click="refresh">
        <RefreshCw :size="17" :class="{ spinning: loading }" />
      </button>
    </header>

    <div class="guardian-boundary">
      <ShieldCheck :size="19" />
      <div><strong>守护不等于代操作</strong><span>只能确认“风险已知”，不能代替本人 MFA、查看完整账单余额或直接执行转账。</span></div>
    </div>

    <p v-if="notice" class="guardian-notice" role="status">{{ notice }}</p>
    <p v-if="error" class="guardian-error" role="alert">{{ error }}</p>

    <section class="guardian-band">
      <div class="band-title"><UsersRound :size="18" /><div><strong>建立守护关系</strong><span>邀请只可使用一次，且仅指定账号能够接受</span></div></div>
      <div class="invite-layout">
        <form class="scope-form" @submit.prevent="issueInvitation">
          <label>守护账号
            <select v-model="guardianUserId"><option value="user_001">林安 · user_001</option><option value="user_002">周宁 · user_002</option></select>
          </label>
          <label>单次金额上限<input v-model="maxAmount" type="number" min="0" step="100" required></label>
          <label>授权截止日期<input v-model="validityDate" type="date" required></label>
          <fieldset><legend>通知场景</legend><label v-for="item in scenarioOptions" :key="item.value"><input v-model="selectedScenarios" type="checkbox" :value="item.value">{{ item.label }}</label></fieldset>
          <fieldset><legend>守护人可见字段</legend><label v-for="item in fieldOptions" :key="item.value"><input v-model="selectedFields" type="checkbox" :value="item.value">{{ item.label }}</label></fieldset>
          <button type="submit" class="primary-command" :disabled="loading || guardianUserId === currentUserId || !selectedScenarios.length || !selectedFields.length"><Link2 :size="16" />生成一次性邀请</button>
        </form>
        <div class="accept-panel">
          <strong><KeyRound :size="16" />接受收到的邀请</strong>
          <p v-if="incomingPending.length">你有 {{ incomingPending.length }} 条待处理邀请。请向邀请人核对范围后输入一次性令牌。</p>
          <p v-else>令牌需要由邀请人通过可信渠道单独发送。</p>
          <textarea v-model="inviteToken" rows="3" maxlength="256" placeholder="粘贴一次性邀请令牌" aria-label="一次性守护邀请令牌"></textarea>
          <div class="command-row"><button type="button" class="primary-command" :disabled="loading || !inviteToken.trim()" @click="respondInvitation(true)"><UserRoundCheck :size="16" />接受范围</button><button type="button" class="secondary-command" :disabled="loading || !inviteToken.trim()" @click="respondInvitation(false)"><ShieldX :size="16" />拒绝</button></div>
          <div v-if="issuedToken" class="issued-token"><small>仅本次展示的一次性令牌</small><code>{{ issuedToken }}</code><span>数据库只保存摘要；刷新后无法再次查看。</span></div>
        </div>
      </div>
      <div v-if="invitations.length" class="invitation-list">
        <article v-for="item in invitations" :key="item.invitation_id">
          <div><strong>{{ item.owner.display_name }} → {{ item.guardian.display_name }}</strong><span>{{ item.scope.scenarios.map(scenarioLabel).join("、") }}</span></div>
          <em :class="item.status.toLowerCase()">{{ statusLabel(item.status) }}</em>
          <small>邀请有效至 {{ beijingTime(item.expires_at) }} · 授权截止 {{ beijingTime(item.scope.valid_until) }}</small>
          <button v-if="item.status === 'PENDING' && item.owner.user_id === currentUserId" type="button" class="text-command" @click="cancelInvitation(item)">取消邀请</button>
        </article>
      </div>
    </section>

    <section class="guardian-band">
      <div class="band-title"><ShieldCheck :size="18" /><div><strong>有效授权范围</strong><span>任一方均可即时撤销；撤销后旧链接和待确认立即失效</span></div></div>
      <div v-if="!consents.length" class="empty-state">尚无生效中的守护授权</div>
      <div v-else class="consent-list">
        <article v-for="item in consents" :key="item.consent_id">
          <div class="consent-heading"><div><strong>{{ item.viewer_role === "OWNER" ? item.guardian.display_name : item.owner.display_name }}</strong><span>{{ item.viewer_role === "OWNER" ? "我的守护人" : "我守护的用户" }}</span></div><em>{{ statusLabel(item.status) }}</em></div>
          <dl><div><dt>场景</dt><dd>{{ item.scope.scenarios.map(scenarioLabel).join("、") }}</dd></div><div><dt>金额范围</dt><dd>¥{{ item.scope.amount_lower_bound }} - ¥{{ item.scope.amount_upper_bound }}</dd></div><div><dt>可见字段</dt><dd>{{ item.scope.visible_fields.length }} 项最小字段</dd></div><div><dt>有效期</dt><dd>{{ beijingTime(item.scope.valid_until) }}</dd></div></dl>
          <div class="capability-line"><ShieldX :size="15" />不允许资金执行 · 不允许代替 MFA</div>
          <button v-if="item.status === 'ACTIVE'" type="button" class="secondary-command danger" :disabled="loading" @click="revoke(item)">即时撤销授权</button>
        </article>
      </div>
    </section>

    <section v-if="ownerConsents.length" class="guardian-band review-create">
      <div class="band-title"><AlertTriangle :size="18" /><div><strong>发起风险双确认</strong><span>先由本人确认，再把授权范围内的最小摘要发送给守护人</span></div></div>
      <form @submit.prevent="createReview">
        <select v-model="reviewConsentId" aria-label="守护授权"><option v-for="item in ownerConsents" :key="item.consent_id" :value="item.consent_id">守护人 {{ item.guardian.display_name }}</option></select>
        <select v-model="reviewScenario" aria-label="风险场景"><option v-for="item in scenarioOptions" :key="item.value" :value="item.value">{{ item.label }}</option></select>
        <input v-model="reviewAmount" type="number" min="0" step="100" placeholder="金额" aria-label="风险交易金额" required>
        <input v-model="reviewRecipient" maxlength="64" placeholder="脱敏收款人" aria-label="脱敏收款人" required>
        <textarea v-model="reviewSummary" rows="2" maxlength="240" aria-label="风险摘要"></textarea>
        <button type="submit" class="primary-command" :disabled="loading"><AlertTriangle :size="16" />建立双确认请求</button>
      </form>
    </section>

    <section class="guardian-band">
      <div class="band-title"><UserRoundCheck :size="18" /><div><strong>风险确认队列</strong><span>确认结果仅作为风险沟通证据</span></div></div>
      <div v-if="!reviews.length" class="empty-state">当前没有可见的风险确认请求</div>
      <div v-else class="review-list">
        <article v-for="item in reviews" :key="item.review_id">
          <header><div><strong>{{ scenarioLabel(item.visible_payload.scenario) }}</strong><span>{{ item.visible_payload.risk_level ?? "风险等级未披露" }} · {{ item.visible_payload.amount_band ?? "金额未披露" }}</span></div><em :class="item.status.toLowerCase()">{{ statusLabel(item.status) }}</em></header>
          <p>{{ item.visible_payload.risk_summary ?? "授权范围未包含风险摘要" }}</p>
          <small v-if="item.visible_payload.recipient_masked">关联收款人 {{ item.visible_payload.recipient_masked }}</small>
          <div v-if="item.status === 'AWAITING_OWNER' && item.viewer_role === 'OWNER'" class="command-row"><button type="button" class="primary-command" @click="ownerDecision(item, true)">本人确认发送</button><button type="button" class="secondary-command" @click="ownerDecision(item, false)">取消请求</button></div>
          <div v-else-if="item.status === 'AWAITING_GUARDIAN' && item.viewer_role === 'GUARDIAN'" class="command-row"><button type="button" class="primary-command" @click="guardianDecision(item, true)"><CheckCircle2 :size="16" />确认风险已知</button><button type="button" class="secondary-command" @click="guardianDecision(item, false)"><XCircle :size="16" />建议停止</button></div>
          <div class="review-boundary"><Clock3 :size="14" />{{ beijingTime(item.expires_at) }} 前有效 · 不满足 MFA · 不授权执行</div>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.guardian-workspace { padding: 22px 26px 110px; color: #242b27; }
.guardian-intro { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; padding-bottom: 18px; border-bottom: 1px solid #dde3de; }.guardian-intro small { color: #357366; font-size: 10px; font-weight: 800; }.guardian-intro h2 { margin: 4px 0 7px; font-size: 21px; letter-spacing: 0; }.guardian-intro p { max-width: 720px; margin: 0; color: #68706a; font-size: 12px; line-height: 1.65; }
.icon-command { width: 36px; height: 36px; display: grid; place-items: center; flex: 0 0 36px; border: 1px solid #cbd5ce; border-radius: 5px; color: #2c6d5f; background: #fff; }.spinning { animation: spin .8s linear infinite; }@keyframes spin { to { transform: rotate(360deg); } }
.guardian-boundary { display: flex; align-items: center; gap: 10px; margin: 16px 0; padding: 12px 14px; border-left: 4px solid #287464; background: #edf5f1; color: #2d675a; }.guardian-boundary div { min-width: 0; }.guardian-boundary strong, .guardian-boundary span { display: block; }.guardian-boundary span { margin-top: 3px; color: #566a62; font-size: 10px; line-height: 1.5; }
.guardian-notice, .guardian-error { margin: 0 0 14px; padding: 10px 12px; font-size: 11px; }.guardian-notice { color: #35695d; background: #eaf3ef; }.guardian-error { color: #973d31; background: #f8e8e4; }
.guardian-band { padding: 20px 0; border-top: 1px solid #dde3de; }.guardian-band:first-of-type { border-top: 0; }.band-title { display: flex; align-items: flex-start; gap: 9px; margin-bottom: 14px; color: #285f55; }.band-title div { min-width: 0; }.band-title strong, .band-title span { display: block; }.band-title strong { color: #222b27; font-size: 14px; }.band-title span { margin-top: 3px; color: #7a827c; font-size: 10px; }
.invite-layout { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(250px, .75fr); gap: 18px; }.scope-form { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }.scope-form label, .review-create form { color: #626b65; font-size: 10px; }.scope-form input, .scope-form select, .accept-panel textarea, .review-create input, .review-create select, .review-create textarea { width: 100%; min-width: 0; border: 1px solid #ccd5ce; border-radius: 5px; color: #263029; background: #fff; outline: none; }.scope-form > label input, .scope-form > label select, .review-create input, .review-create select { height: 38px; margin-top: 5px; padding: 0 9px; }.scope-form fieldset { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 8px 18px; margin: 0; padding: 10px 12px; border: 1px solid #d9dfda; }.scope-form legend { padding: 0 5px; color: #6d756f; font-size: 10px; }.scope-form fieldset label { display: inline-flex; align-items: center; gap: 6px; }.scope-form fieldset input { width: 14px; height: 14px; }.scope-form > button { grid-column: 1 / -1; justify-self: start; }
.accept-panel { padding-left: 18px; border-left: 1px solid #dde3de; }.accept-panel > strong { display: flex; align-items: center; gap: 7px; font-size: 12px; }.accept-panel p { margin: 8px 0; color: #737b75; font-size: 10px; line-height: 1.6; }.accept-panel textarea, .review-create textarea { padding: 9px; resize: vertical; }.command-row { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 8px; }.issued-token { margin-top: 12px; padding: 10px; background: #fbf6e9; }.issued-token small, .issued-token code, .issued-token span { display: block; }.issued-token code { margin: 6px 0; overflow-wrap: anywhere; color: #6b5423; font-size: 10px; }.issued-token span { color: #847967; font-size: 9px; }
.primary-command, .secondary-command { min-height: 36px; display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 0 12px; border-radius: 5px; font-size: 10px; font-weight: 750; }.primary-command { border: 0; color: #fff; background: #2f7465; }.secondary-command { border: 1px solid #c8d2ca; color: #315f55; background: #fff; }.secondary-command.danger { color: #9b3f34; border-color: #ddc0ba; }.primary-command:disabled, .secondary-command:disabled { cursor: not-allowed; opacity: .5; }
.invitation-list { margin-top: 16px; border-top: 1px solid #e1e5e1; }.invitation-list article { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 5px 12px; padding: 11px 0; border-bottom: 1px solid #e5e8e5; }.invitation-list strong, .invitation-list span { display: block; }.invitation-list span, .invitation-list small { color: #7b837d; font-size: 9px; }.invitation-list small { grid-column: 1; }.invitation-list em, .review-list em { align-self: start; padding: 4px 6px; border-radius: 4px; color: #68726b; background: #edf0ed; font-size: 8px; font-style: normal; font-weight: 800; }.invitation-list em.pending, .review-list em.awaiting_owner, .review-list em.awaiting_guardian { color: #84651d; background: #f6edcf; }.invitation-list em.accepted, .review-list em.acknowledged { color: #2e6b5d; background: #e3f0eb; }.text-command { justify-self: start; padding: 0; border: 0; color: #9b4438; background: transparent; font-size: 9px; }
.consent-list, .review-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }.consent-list article, .review-list article { min-width: 0; padding: 14px; border: 1px solid #d5ddd7; border-radius: 6px; background: #fff; }.consent-heading, .review-list header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }.consent-heading strong, .consent-heading span, .review-list header strong, .review-list header span { display: block; }.consent-heading span, .review-list header span { margin-top: 3px; color: #78817b; font-size: 9px; }.consent-heading em { color: #2d6d5f; font-size: 9px; font-style: normal; }.consent-list dl { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; margin: 13px 0; }.consent-list dl div { min-width: 0; }.consent-list dt { color: #8a908b; font-size: 8px; }.consent-list dd { margin: 3px 0 0; overflow-wrap: anywhere; font-size: 10px; }.capability-line, .review-boundary { display: flex; align-items: center; gap: 6px; margin: 10px 0; color: #8e463b; font-size: 9px; }
.review-create form { display: grid; grid-template-columns: 1fr 1fr 120px 1fr; gap: 8px; }.review-create textarea { grid-column: 1 / -1; }.review-create button { justify-self: start; grid-column: 1 / -1; }.review-list p { min-height: 34px; margin: 12px 0 6px; color: #555f58; font-size: 10px; line-height: 1.6; }.review-list > article > small { color: #818984; font-size: 9px; }.review-boundary { margin: 12px 0 0; padding-top: 10px; border-top: 1px solid #e2e6e2; color: #747d77; }.empty-state { padding: 24px 0; color: #929993; font-size: 11px; text-align: center; }
@media (max-width: 720px) { .guardian-workspace { padding: 18px 16px 100px; }.invite-layout, .consent-list, .review-list { grid-template-columns: 1fr; }.scope-form { grid-template-columns: 1fr; }.scope-form fieldset, .scope-form > button { grid-column: 1; }.accept-panel { padding: 16px 0 0; border-top: 1px solid #dde3de; border-left: 0; }.review-create form { grid-template-columns: 1fr 1fr; }.review-create textarea, .review-create button { grid-column: 1 / -1; }.guardian-intro p { font-size: 11px; } }
</style>
