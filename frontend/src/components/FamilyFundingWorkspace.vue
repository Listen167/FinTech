<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import {
  ArrowRight,
  Check,
  CircleDollarSign,
  Clock3,
  Headphones,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from "lucide-vue-next";
import type {
  CreateFundingNegotiationPayload,
  FundingContext,
  FundingGoalConstraint,
  FundingNegotiation,
  FundingPlan,
} from "../api/client";

const props = defineProps<{
  context?: FundingContext;
  negotiations: FundingNegotiation[];
  loading: boolean;
  error: string;
}>();

const emit = defineEmits<{
  refresh: [];
  create: [payload: CreateFundingNegotiationPayload];
  select: [negotiationId: string, planId: string, reason: string];
  confirm: [negotiationId: string, reason: string];
  transition: [negotiationId: string, command: "reject" | "cancel" | "handoff" | "undo", reason: string];
}>();

interface GoalDraft {
  enabled: boolean;
  rigidity: "RIGID" | "FLEXIBLE";
  minimumAmount: string;
  canReduce: boolean;
  canDelay: boolean;
  canInstallments: boolean;
}

const drafts = reactive<Record<string, GoalDraft>>({});
const protectedBalance = ref("0.00");
const selectedNegotiationId = ref("");
const decisionReason = ref("我已比较截止日期、最低金额、备用金和资金缺口");

watch(
  () => props.context?.goals,
  (goals) => {
    for (const goal of goals ?? []) {
      if (drafts[goal.goal_node_id]) continue;
      drafts[goal.goal_node_id] = {
        enabled: true,
        rigidity: "FLEXIBLE",
        minimumAmount: goal.reserved_amount,
        canReduce: true,
        canDelay: true,
        canInstallments: true,
      };
    }
  },
  { immediate: true },
);

watch(
  () => props.negotiations,
  (items) => {
    if (!items.length) selectedNegotiationId.value = "";
    else if (!items.some((item) => item.negotiation_id === selectedNegotiationId.value)) {
      selectedNegotiationId.value = items[0].negotiation_id;
    }
  },
  { immediate: true },
);

const activeNegotiation = computed(() =>
  props.negotiations.find((item) => item.negotiation_id === selectedNegotiationId.value)
  ?? props.negotiations[0],
);
const enabledGoals = computed(() =>
  (props.context?.goals ?? []).filter((goal) => drafts[goal.goal_node_id]?.enabled),
);
const canCreate = computed(() =>
  enabledGoals.value.length >= 2
  && (props.context?.accounts.length ?? 0) > 0
  && Number(protectedBalance.value) >= 0,
);
const selectedPlan = computed(() =>
  activeNegotiation.value?.plans.find(
    (plan) => plan.plan_id === activeNegotiation.value?.selected_plan_id,
  ),
);

function setRigidity(goalId: string, value: "RIGID" | "FLEXIBLE", target: string) {
  const draft = drafts[goalId];
  draft.rigidity = value;
  if (value === "RIGID") {
    draft.minimumAmount = target;
    draft.canReduce = false;
    draft.canDelay = false;
    draft.canInstallments = false;
  }
}

function localDate(value = new Date()) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function planningEnd() {
  const dueDates = enabledGoals.value.map((goal) => new Date(goal.due_at as string));
  const latest = new Date(Math.max(...dueDates.map((value) => value.getTime())));
  latest.setDate(latest.getDate() + 90);
  return localDate(latest);
}

function userEvidence(fields: string[]): FundingGoalConstraint["evidence"] {
  return fields.map((field_name) => ({
    field_name,
    source: "USER_CONFIRMED",
    confidence_bp: 10000,
  }));
}

function submitNegotiation() {
  if (!canCreate.value || !props.context) return;
  const goals: FundingGoalConstraint[] = enabledGoals.value.map((goal) => {
    const draft = drafts[goal.goal_node_id];
    const minimum = draft.rigidity === "RIGID"
      ? goal.target_amount
      : String(Math.min(Number(goal.target_amount), Math.max(Number(goal.reserved_amount), Number(draft.minimumAmount) || 0)));
    return {
      goal_id: goal.goal_node_id,
      label: goal.label,
      target_amount: goal.target_amount,
      minimum_amount: minimum,
      reserved_amount: goal.reserved_amount,
      due_date: (goal.due_at as string).slice(0, 10),
      rigidity: draft.rigidity,
      can_reduce: draft.rigidity === "FLEXIBLE" && draft.canReduce,
      can_delay: draft.rigidity === "FLEXIBLE" && draft.canDelay,
      can_installments: draft.rigidity === "FLEXIBLE" && draft.canInstallments,
      evidence: userEvidence([
        "rigidity", "minimum_amount", "can_reduce", "can_delay", "can_installments",
      ]),
    };
  });
  const startingBalance = props.context.accounts.reduce(
    (sum, item) => sum + Number(item.available_balance),
    0,
  );
  emit("create", {
    request_text: `协商 ${goals.map((goal) => goal.label).join("、")} 的资金安排`,
    planning: {
      goals,
      constraints: {
        starting_balance: startingBalance.toFixed(2),
        protected_balance: Number(protectedBalance.value).toFixed(2),
        allowed_account_ids: props.context.accounts.map((item) => item.account_id),
        planning_start: localDate(),
        planning_end: planningEnd(),
        committed_expenses: [],
        incomes: [],
        evidence: userEvidence(["protected_balance", "allowed_account_ids"]),
      },
    },
  });
}

function money(value: string | number | undefined) {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    minimumFractionDigits: 2,
  }).format(Number(value ?? 0));
}

function shortDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" })
    .format(new Date(`${value}T00:00:00`));
}

function statusLabel(value: FundingNegotiation["status"]) {
  return {
    WAITING_INPUT: "待补充",
    WAITING_SELECTION: "待选择",
    PENDING_CONFIRMATION: "待确认",
    APPLIED: "已应用",
    REJECTED: "已拒绝",
    CANCELED: "已取消",
    STALE: "已失效",
    WAITING_HUMAN: "人工处理中",
    UNDONE: "已撤销",
  }[value];
}

function feasibilityLabel(value: FundingPlan["feasibility"]) {
  return {
    GUARANTEED: "保证方案",
    CONDITIONAL: "条件方案",
    INFEASIBLE: "不可行方案",
  }[value];
}
</script>

<template>
  <div class="funding-workspace">
    <div class="funding-toolbar">
      <div>
        <p>家庭目标协商</p>
        <h2>让价值选择留给用户，让可行性接受算法校验</h2>
      </div>
      <button type="button" class="icon-command" :disabled="loading" title="刷新资金协商" @click="emit('refresh')">
        <RefreshCw :size="17" :class="{ spinning: loading }" />
        <span>刷新</span>
      </button>
    </div>

    <p v-if="error" class="funding-error" role="alert">{{ error }}</p>

    <section class="constraint-section">
      <div class="funding-section-heading">
        <div><SlidersHorizontal :size="18" /><strong>约束确认</strong></div>
        <span>{{ enabledGoals.length }} 个目标</span>
      </div>
      <div v-if="!context?.goals.length" class="funding-empty">
        <CircleDollarSign :size="26" />
        <strong>尚无可协商的正式资金目标</strong>
        <span>先在生活图谱中确认至少两个带截止日期的人民币目标。</span>
      </div>
      <template v-else>
        <div class="constraint-table">
          <article v-for="goal in context.goals" :key="goal.goal_node_id" class="goal-constraint" :class="{ muted: !drafts[goal.goal_node_id]?.enabled }">
            <label class="goal-enabled">
              <input v-model="drafts[goal.goal_node_id].enabled" type="checkbox">
              <span><strong>{{ goal.label }}</strong><small>{{ shortDate((goal.due_at as string).slice(0, 10)) }} · {{ money(goal.target_amount) }}</small></span>
            </label>
            <div class="rigidity-control" aria-label="目标刚柔性">
              <button type="button" :class="{ active: drafts[goal.goal_node_id].rigidity === 'RIGID' }" @click="setRigidity(goal.goal_node_id, 'RIGID', goal.target_amount)">刚性</button>
              <button type="button" :class="{ active: drafts[goal.goal_node_id].rigidity === 'FLEXIBLE' }" @click="setRigidity(goal.goal_node_id, 'FLEXIBLE', goal.target_amount)">弹性</button>
            </div>
            <label class="minimum-field"><span>最低金额</span><input v-model="drafts[goal.goal_node_id].minimumAmount" type="number" min="0" :max="goal.target_amount" step="100" :disabled="drafts[goal.goal_node_id].rigidity === 'RIGID'"></label>
            <div class="flex-options">
              <label><input v-model="drafts[goal.goal_node_id].canReduce" type="checkbox" :disabled="drafts[goal.goal_node_id].rigidity === 'RIGID'">缩减</label>
              <label><input v-model="drafts[goal.goal_node_id].canDelay" type="checkbox" :disabled="drafts[goal.goal_node_id].rigidity === 'RIGID'">延期</label>
              <label><input v-model="drafts[goal.goal_node_id].canInstallments" type="checkbox" :disabled="drafts[goal.goal_node_id].rigidity === 'RIGID'">分期</label>
            </div>
          </article>
        </div>
        <div class="constraint-actions">
          <label><span>不可动用备用金</span><input v-model="protectedBalance" type="number" min="0" step="100"></label>
          <button type="button" class="primary-command" :disabled="loading || !canCreate" @click="submitNegotiation"><ArrowRight :size="17" />计算方案</button>
        </div>
      </template>
    </section>

    <section v-if="negotiations.length" class="negotiation-section">
      <div class="funding-section-heading">
        <div><Clock3 :size="18" /><strong>协商记录</strong></div>
        <select v-model="selectedNegotiationId" aria-label="选择协商记录">
          <option v-for="item in negotiations" :key="item.negotiation_id" :value="item.negotiation_id">
            {{ statusLabel(item.status) }} · {{ item.request_text }}
          </option>
        </select>
      </div>

      <div v-if="activeNegotiation" class="negotiation-summary">
        <div><strong>{{ activeNegotiation.request_text }}</strong><span>{{ activeNegotiation.workflow.engine }} · 检查点 {{ activeNegotiation.workflow.checkpoint_seq }}</span></div>
        <em :class="activeNegotiation.status.toLowerCase()">{{ statusLabel(activeNegotiation.status) }}</em>
      </div>

      <div v-if="activeNegotiation?.plans.length" class="plan-grid">
        <article v-for="plan in activeNegotiation.plans" :key="plan.plan_id" class="plan-card" :class="[plan.feasibility.toLowerCase(), { selected: activeNegotiation.selected_plan_id === plan.plan_id }]">
          <div class="plan-heading">
            <div><em>{{ feasibilityLabel(plan.feasibility) }}</em><strong>{{ plan.title }}</strong></div>
            <ShieldCheck v-if="plan.feasibility === 'GUARANTEED'" :size="19" />
            <Clock3 v-else-if="plan.feasibility === 'CONDITIONAL'" :size="19" />
            <X v-else :size="19" />
          </div>
          <p>{{ plan.explanation }}</p>
          <dl class="plan-metrics">
            <div><dt>保证缺口</dt><dd>{{ money(plan.shortfall) }}</dd></div>
            <div><dt>需确认收入</dt><dd>{{ money(plan.required_confirmed_income) }}</dd></div>
          </dl>
          <ul v-if="plan.adjustments.length" class="adjustment-list">
            <li v-for="item in plan.adjustments" :key="`${plan.plan_id}-${item.goal_id}`">{{ item.reason }}</li>
          </ul>
          <div v-if="plan.model_explanation" class="model-explanation"><strong>模型解释</strong><span>{{ plan.model_explanation }}</span></div>
          <ol class="cash-timeline">
            <li v-for="point in plan.timeline" :key="`${plan.plan_id}-${point.date}`">
              <span class="cash-mark"></span>
              <div><strong>{{ shortDate(point.date) }}</strong><small>确认余额 {{ money(point.closing_guaranteed_balance) }}</small></div>
              <em :class="{ gap: Number(point.guaranteed_gap) > 0 }">缺口 {{ money(point.guaranteed_gap) }}</em>
            </li>
          </ol>
          <small class="formula">{{ plan.formula }}</small>
          <button
            v-if="activeNegotiation.status === 'WAITING_SELECTION'"
            type="button"
            class="select-command"
            :disabled="loading || plan.feasibility === 'INFEASIBLE' || decisionReason.trim().length < 2"
            @click="emit('select', activeNegotiation.negotiation_id, plan.plan_id, decisionReason.trim())"
          ><Check :size="16" />选择此方案</button>
        </article>
      </div>

      <div v-if="activeNegotiation && ['WAITING_SELECTION', 'PENDING_CONFIRMATION', 'APPLIED'].includes(activeNegotiation.status)" class="decision-band">
        <label><span>选择理由</span><input v-model="decisionReason" maxlength="500"></label>
        <div v-if="activeNegotiation.status === 'PENDING_CONFIRMATION'" class="decision-buttons">
          <button type="button" class="icon-only" title="拒绝方案" @click="emit('transition', activeNegotiation.negotiation_id, 'reject', decisionReason)"><X :size="18" /></button>
          <button type="button" class="secondary-command" @click="emit('transition', activeNegotiation.negotiation_id, 'handoff', decisionReason)"><Headphones :size="16" />转人工</button>
          <button type="button" class="primary-command" :disabled="!selectedPlan" @click="emit('confirm', activeNegotiation.negotiation_id, decisionReason)"><Check :size="17" />二次确认</button>
        </div>
        <button v-else-if="activeNegotiation.status === 'APPLIED'" type="button" class="secondary-command" @click="emit('transition', activeNegotiation.negotiation_id, 'undo', decisionReason)"><RotateCcw :size="16" />撤销目标变更</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.funding-workspace { min-height: 100%; color: #252a26; }
.funding-toolbar, .funding-section-heading, .constraint-actions, .negotiation-summary, .plan-heading, .decision-band { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.funding-toolbar { padding-bottom: 18px; border-bottom: 1px solid #e2e5df; }
.funding-toolbar p { margin: 0 0 4px; color: #2a6b60; font-size: 11px; font-weight: 800; }
.funding-toolbar h2 { margin: 0; font-size: 18px; letter-spacing: 0; }
.icon-command, .primary-command, .secondary-command, .select-command, .icon-only { display: inline-flex; align-items: center; justify-content: center; gap: 7px; border-radius: 5px; font-weight: 750; }
.icon-command, .secondary-command { min-height: 36px; padding: 0 11px; border: 1px solid #cdd5cf; color: #3d554e; background: #fff; font-size: 11px; }
.primary-command, .select-command { min-height: 38px; padding: 0 13px; border: 0; color: #fff; background: #24675b; font-size: 11px; }
.icon-only { width: 36px; height: 36px; padding: 0; border: 1px solid #d5d9d4; color: #9a3f31; background: #fff; }
button:disabled { cursor: not-allowed; opacity: .5; }
.spinning { animation: spin 1s linear infinite; } @keyframes spin { to { transform: rotate(360deg); } }
.funding-error { margin: 14px 0 0; padding: 10px 12px; border-left: 3px solid #b94637; color: #8e3428; background: #fbefec; font-size: 12px; }
.constraint-section, .negotiation-section { padding: 22px 0; border-bottom: 1px solid #e2e5df; }
.funding-section-heading { margin-bottom: 14px; }
.funding-section-heading > div { display: flex; align-items: center; gap: 8px; color: #315f56; }
.funding-section-heading > span, .funding-section-heading select { color: #777f79; font-size: 10px; }
.funding-section-heading select { max-width: 330px; height: 34px; padding: 0 8px; border: 1px solid #ccd4ce; border-radius: 5px; background: #fff; }
.funding-empty { min-height: 140px; display: grid; place-items: center; align-content: center; gap: 7px; color: #737d76; text-align: center; }
.funding-empty strong { color: #465049; }.funding-empty span { font-size: 11px; }
.constraint-table { border-top: 1px solid #dfe4df; }
.goal-constraint { min-height: 76px; display: grid; grid-template-columns: minmax(170px, 1.4fr) 118px 110px minmax(160px, 1fr); align-items: center; gap: 12px; padding: 11px 4px; border-bottom: 1px solid #e4e7e2; }
.goal-constraint.muted { opacity: .55; }.goal-enabled { display: flex; align-items: center; gap: 9px; min-width: 0; }
.goal-enabled span, .goal-enabled strong, .goal-enabled small { display: block; min-width: 0; }.goal-enabled strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }.goal-enabled small { margin-top: 4px; color: #858c86; font-size: 9px; }
.rigidity-control { display: flex; padding: 2px; border: 1px solid #d5dad5; border-radius: 5px; background: #f2f4f1; }
.rigidity-control button { flex: 1; height: 29px; padding: 0 6px; border: 0; border-radius: 3px; color: #707871; background: transparent; font-size: 10px; }
.rigidity-control button.active { color: #285f55; background: #fff; box-shadow: 0 1px 2px rgb(31 54 46 / 10%); }
.minimum-field span, .constraint-actions label span, .decision-band label span { display: block; margin-bottom: 4px; color: #6a726b; font-size: 9px; }
.minimum-field input, .constraint-actions input, .decision-band input { width: 100%; height: 34px; padding: 0 8px; border: 1px solid #ccd4ce; border-radius: 5px; background: #fff; }
.flex-options { display: flex; flex-wrap: wrap; gap: 6px 12px; color: #626a64; font-size: 10px; }.flex-options label { display: flex; align-items: center; gap: 4px; }
.constraint-actions { align-items: flex-end; margin-top: 14px; }.constraint-actions label { width: 170px; }
.negotiation-summary { min-height: 54px; padding: 10px 12px; border-left: 4px solid #397568; background: #f2f7f5; }
.negotiation-summary strong, .negotiation-summary span { display: block; }.negotiation-summary strong { font-size: 12px; }.negotiation-summary span { margin-top: 4px; color: #7b837d; font-size: 9px; }
.negotiation-summary em { padding: 4px 6px; border-radius: 3px; color: #32675d; background: #dfece7; font-size: 9px; font-style: normal; }.negotiation-summary em.pending_confirmation { color: #7a591a; background: #f6e6c3; }
.plan-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 10px; margin-top: 14px; align-items: start; }
.plan-card { min-width: 0; padding: 13px; border: 1px solid #d9dfda; border-top: 4px solid #858d87; border-radius: 6px; background: #fff; }.plan-card.guaranteed { border-top-color: #2c8068; }.plan-card.conditional { border-top-color: #d09a31; }.plan-card.infeasible { border-top-color: #bc4938; }.plan-card.selected { outline: 2px solid #4e8b80; outline-offset: 1px; }
.plan-heading { align-items: flex-start; }.plan-heading > div em, .plan-heading > div strong { display: block; }.plan-heading em { color: #68706a; font-size: 9px; font-style: normal; }.plan-heading strong { margin-top: 4px; font-size: 13px; line-height: 1.35; }.plan-card > p { min-height: 48px; margin: 10px 0; color: #636b65; font-size: 10px; line-height: 1.6; }
.plan-metrics { display: grid; grid-template-columns: 1fr 1fr; margin: 0; background: #e1e5e1; gap: 1px; }.plan-metrics div { padding: 8px; background: #f7f8f6; }.plan-metrics dt { color: #858c86; font-size: 8px; }.plan-metrics dd { margin: 4px 0 0; font-size: 11px; font-weight: 750; }
.adjustment-list { min-height: 38px; margin: 10px 0; padding-left: 16px; color: #5e665f; font-size: 9px; line-height: 1.5; }
.model-explanation { margin: 10px 0; padding: 8px; border-left: 3px solid #4c7890; background: #f1f6f8; }.model-explanation strong, .model-explanation span { display: block; }.model-explanation strong { color: #426579; font-size: 9px; }.model-explanation span { margin-top: 4px; color: #626d73; font-size: 9px; line-height: 1.5; }
.cash-timeline { margin: 10px 0 0; padding: 0; list-style: none; }.cash-timeline li { min-height: 38px; display: grid; grid-template-columns: 9px minmax(0, 1fr) auto; align-items: center; gap: 7px; border-bottom: 1px dashed #e0e4df; }.cash-mark { width: 7px; height: 7px; border-radius: 50%; background: #4d8277; }.cash-timeline strong, .cash-timeline small { display: block; }.cash-timeline strong { font-size: 9px; }.cash-timeline small { margin-top: 2px; color: #7e867f; font-size: 8px; }.cash-timeline em { color: #557068; font-size: 8px; font-style: normal; }.cash-timeline em.gap { color: #a54435; }
.formula { display: block; min-height: 42px; margin: 10px 0; color: #858c86; font-size: 8px; line-height: 1.5; }.select-command { width: 100%; }
.decision-band { align-items: flex-end; margin-top: 14px; padding: 12px; border-left: 4px solid #d09a31; background: #fbf7eb; }.decision-band > label { flex: 1; }.decision-buttons { display: flex; gap: 7px; }
@media (max-width: 900px) { .goal-constraint { grid-template-columns: minmax(160px, 1fr) 118px; }.flex-options { justify-content: flex-end; } }
@media (max-width: 720px) {
  .funding-toolbar { align-items: flex-start; }.funding-toolbar h2 { font-size: 16px; }
  .goal-constraint { grid-template-columns: 1fr; gap: 8px; }.flex-options { justify-content: flex-start; }
  .constraint-actions, .decision-band { align-items: stretch; flex-direction: column; }.constraint-actions label { width: 100%; }
  .decision-buttons { display: grid; grid-template-columns: 36px 1fr 1fr; }.plan-grid { grid-template-columns: 1fr; }
  .funding-section-heading { align-items: flex-start; }.funding-section-heading select { max-width: 55%; }
}
</style>
