<script setup lang="ts">
import { computed, ref } from "vue";
import {
  CalendarDays,
  BellRing,
  Check,
  Clock3,
  FastForward,
  Network,
  Pause,
  Pencil,
  Play,
  RefreshCw,
  Target,
  UserRound,
  WalletCards,
  X,
} from "lucide-vue-next";
import type {
  FinancialGoal,
  GoalConflictAnalysis,
  LifeEvent,
  LifeGraph,
  LifeGraphChangeRequest,
  LifeGraphNode,
  LifeGraphNodeCorrection,
  LifeRadarStatus,
} from "../api/client";

const props = defineProps<{
  graph?: LifeGraph;
  requests: LifeGraphChangeRequest[];
  conflict?: GoalConflictAnalysis;
  loading: boolean;
  error: string;
  radarStatus?: LifeRadarStatus;
}>();

const emit = defineEmits<{
  refresh: [];
  decide: [requestId: string, approved: boolean];
  correct: [nodeId: string, correction: LifeGraphNodeCorrection];
  controlAction: [actionId: string, command: "pause" | "resume" | "cancel"];
  simulateRadar: [advanceDays: number];
}>();

type CorrectionKind = "PERSON" | "EVENT" | "GOAL";

const editingNode = ref<LifeGraphNode>();
const correctionKind = ref<CorrectionKind>();
const correctionLabel = ref("");
const correctionDate = ref("");
const correctionAmount = ref("");
const correctionReason = ref("");

const nodesById = computed(() => new Map((props.graph?.nodes ?? []).map((node) => [node.node_id, node])));
const eventsById = computed(() => new Map((props.graph?.events ?? []).map((event) => [event.event_node_id, event])));
const goalsById = computed(() => new Map((props.graph?.goals ?? []).map((goal) => [goal.goal_node_id, goal])));
const selfNode = computed(() => (props.graph?.nodes ?? []).find((node) => node.attributes.is_account_owner === true));
const pendingRequests = computed(() => props.requests.filter((item) => item.status === "PENDING"));
const hasCorrectionChange = computed(() => {
  if (!editingNode.value || !correctionKind.value) return false;
  if (correctionLabel.value.trim() !== editingNode.value.display_label) return true;
  if (correctionKind.value === "EVENT") return Boolean(correctionDate.value);
  if (correctionKind.value === "GOAL") return Boolean(correctionAmount.value);
  return false;
});

const relationships = computed(() => {
  const graph = props.graph;
  if (!graph || !selfNode.value) return [];
  return graph.edges
    .filter((edge) => edge.source_node_id === selfNode.value?.node_id && ["SPOUSE_OF", "PARENT_OF", "CHILD_OF", "FAMILY_OF"].includes(edge.relation_type))
    .map((edge) => ({ edge, person: nodesById.value.get(edge.target_node_id) }))
    .filter((item): item is typeof item & { person: LifeGraphNode } => Boolean(item.person));
});

const timeline = computed(() => {
  const graph = props.graph;
  if (!graph) return [];
  return [...graph.events]
    .sort((left, right) => left.occurs_at.localeCompare(right.occurs_at))
    .map((event) => {
      const personEdge = graph.edges.find((edge) => edge.target_node_id === event.event_node_id && edge.relation_type === "HAS_EVENT");
      const goalEdge = graph.edges.find((edge) => edge.target_node_id === event.event_node_id && edge.relation_type === "TRIGGERED_BY");
      const action = graph.actions.find((item) => item.event_node_id === event.event_node_id);
      return {
        event,
        eventNode: nodesById.value.get(event.event_node_id),
        person: personEdge ? nodesById.value.get(personEdge.source_node_id) : undefined,
        goal: goalEdge ? goalsById.value.get(goalEdge.source_node_id) : undefined,
        goalNode: goalEdge ? nodesById.value.get(goalEdge.source_node_id) : undefined,
        action,
      };
    });
});

function relationLabel(value: string) {
  return {
    SPOUSE_OF: "配偶",
    PARENT_OF: "子女",
    CHILD_OF: "父母",
    FAMILY_OF: "家人",
  }[value] ?? value;
}

function formatDate(value?: string) {
  if (!value) return "日期待补充";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

function formatMoney(value?: string) {
  if (!value) return "-";
  return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" }).format(Number(value));
}

function actionStatusLabel(status: string) {
  return {
    PENDING: "待提醒",
    PROCESSING: "处理中",
    SENT: "已发送",
    FAILED: "等待重试",
    PAUSED: "已暂停",
    CANCELED: "已取消",
    MANUAL_REVIEW: "人工处理",
  }[status] ?? status;
}

function startCorrection(node: LifeGraphNode | undefined, kind: CorrectionKind, detail?: LifeEvent | FinancialGoal) {
  if (!node) return;
  editingNode.value = node;
  correctionKind.value = kind;
  correctionLabel.value = node.display_label;
  correctionDate.value = kind === "EVENT" && detail && "occurs_at" in detail
    ? detail.occurs_at.slice(0, 10)
    : "";
  correctionAmount.value = kind === "GOAL" && detail && "target_amount" in detail
    ? detail.target_amount
    : "";
  correctionReason.value = "";
}

function cancelCorrection() {
  editingNode.value = undefined;
  correctionKind.value = undefined;
  correctionReason.value = "";
}

function submitCorrection() {
  if (!editingNode.value || !correctionKind.value || !hasCorrectionChange.value || correctionReason.value.trim().length < 2) return;
  const correction: LifeGraphNodeCorrection = { reason: correctionReason.value.trim() };
  if (correctionLabel.value.trim() !== editingNode.value.display_label) {
    correction.display_label = correctionLabel.value.trim();
  }
  if (correctionKind.value === "EVENT" && correctionDate.value) correction.event_date = correctionDate.value;
  if (correctionKind.value === "GOAL" && correctionAmount.value) correction.target_amount = correctionAmount.value;
  emit("correct", editingNode.value.node_id, correction);
  cancelCorrection();
}
</script>

<template>
  <div class="life-workspace">
    <div class="life-toolbar">
      <div>
        <p>生活关系与事件</p>
        <h2>在重要日子到来前看见资金安排</h2>
      </div>
      <button type="button" class="icon-command" :disabled="loading" title="刷新生活图谱" @click="emit('refresh')">
        <RefreshCw :size="17" :class="{ spinning: loading }" />
        <span>刷新</span>
      </button>
    </div>

    <div class="radar-band" :class="{ unavailable: !radarStatus?.configured }">
      <BellRing :size="17" />
      <div>
        <strong>{{ radarStatus?.configured ? "飞书生活事件雷达已就绪" : "飞书生活事件雷达尚未就绪" }}</strong>
        <span>仅发送私聊提醒，不执行资金操作</span>
      </div>
      <button
        v-if="radarStatus?.simulation_enabled"
        type="button"
        class="simulation-command"
        :disabled="loading || !radarStatus.configured"
        title="使用沙箱模拟时间推进 30 天"
        @click="emit('simulateRadar', 30)"
      >
        <FastForward :size="16" />
        <span>沙箱时间 +30 天</span>
      </button>
    </div>

    <p v-if="error" class="life-error" role="alert">{{ error }}</p>
    <div v-if="loading && !graph" class="life-loading" role="status">正在读取关系、事件与目标...</div>

    <template v-else>
      <section v-if="pendingRequests.length" class="pending-band" aria-labelledby="pending-title">
        <div class="pending-band-title">
          <Clock3 :size="18" />
          <div><strong id="pending-title">等待确认</strong><span>{{ pendingRequests.length }} 项候选方案尚未写入正式图谱</span></div>
        </div>
        <div v-for="request in pendingRequests" :key="request.request_id" class="pending-request">
          <div><strong>{{ request.summary }}</strong><small>{{ request.request_id }}</small></div>
          <div class="pending-actions">
            <button type="button" class="icon-only secondary" title="拒绝候选方案" @click="emit('decide', request.request_id, false)"><X :size="18" /></button>
            <button type="button" class="confirm-command" @click="emit('decide', request.request_id, true)"><Check :size="17" />确认写入</button>
          </div>
        </div>
      </section>

      <section class="relationship-section">
        <div class="life-section-heading">
          <div><Network :size="18" /><strong>关系视图</strong></div>
          <span>{{ relationships.length }} 条家庭关系</span>
        </div>
        <div v-if="!selfNode" class="life-empty">
          <UserRound :size="26" />
          <strong>尚未建立生活关系</strong>
          <span>在对话中描述“我爱人生日”或“女儿学费”即可生成待确认方案。</span>
        </div>
        <div v-else class="relationship-map">
          <div class="self-node">
            <span><UserRound :size="18" /></span>
            <div><strong>{{ selfNode.display_label }}</strong><small>账户本人</small></div>
            <button type="button" class="icon-only" title="纠正显示名称" @click="startCorrection(selfNode, 'PERSON')"><Pencil :size="15" /></button>
          </div>
          <div class="relationship-lines">
            <div v-for="item in relationships" :key="item.edge.edge_id" class="relationship-row">
              <span class="relation-connector"></span>
              <em>{{ relationLabel(item.edge.relation_type) }}</em>
              <div class="person-node">
                <span><UserRound :size="17" /></span>
                <div><strong>{{ item.person.display_label }}</strong><small>{{ item.edge.consent_scope.allowed_uses?.join(" · ") ?? "已授权关系" }}</small></div>
                <button type="button" class="icon-only" title="纠正关系人名称" @click="startCorrection(item.person, 'PERSON')"><Pencil :size="15" /></button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="timeline-section">
        <div class="life-section-heading">
          <div><CalendarDays :size="18" /><strong>事件时间线</strong></div>
          <span>{{ timeline.length }} 个事件</span>
        </div>
        <div v-if="!timeline.length" class="life-empty compact">
          <CalendarDays :size="24" />
          <strong>暂无已确认事件</strong>
        </div>
        <ol v-else class="event-timeline">
          <li v-for="item in timeline" :key="item.event.event_node_id">
            <div class="event-date"><strong>{{ formatDate(item.event.occurs_at) }}</strong><span>{{ item.event.recurrence_rule ? "每年重复" : "单次事件" }}</span></div>
            <span class="timeline-mark"></span>
            <div class="event-detail">
              <div class="event-title">
                <div><strong>{{ item.eventNode?.display_label ?? item.event.event_type }}</strong><small>{{ item.person?.display_label ?? "未关联人物" }}</small></div>
                <button type="button" class="icon-only" title="纠正事件日期" @click="startCorrection(item.eventNode, 'EVENT', item.event)"><Pencil :size="15" /></button>
              </div>
              <div v-if="item.goal" class="goal-line">
                <Target :size="15" />
                <span>{{ item.goalNode?.display_label }}</span>
                <strong>{{ formatMoney(item.goal.target_amount) }}</strong>
                <em>{{ item.goal.status }}</em>
                <button type="button" class="icon-only" title="纠正目标金额" @click="startCorrection(item.goalNode, 'GOAL', item.goal)"><Pencil :size="14" /></button>
              </div>
              <div v-if="item.action" class="reminder-line">
                <Clock3 :size="14" />
                <span>{{ formatDate(item.action.scheduled_at) }} 提醒</span>
                <em :class="item.action.status.toLowerCase()">{{ actionStatusLabel(item.action.status) }}</em>
                <small v-if="item.action.attempt_count">{{ item.action.attempt_count }}/{{ item.action.max_attempts }} 次</small>
                <div class="reminder-actions">
                  <button
                    v-if="['PENDING', 'FAILED'].includes(item.action.status)"
                    type="button"
                    class="icon-only"
                    :disabled="loading"
                    title="暂停提醒"
                    @click="emit('controlAction', item.action.action_id, 'pause')"
                  ><Pause :size="14" /></button>
                  <button
                    v-if="item.action.status === 'PAUSED'"
                    type="button"
                    class="icon-only"
                    :disabled="loading"
                    title="恢复提醒"
                    @click="emit('controlAction', item.action.action_id, 'resume')"
                  ><Play :size="14" /></button>
                  <button
                    v-if="['PENDING', 'FAILED', 'PAUSED'].includes(item.action.status)"
                    type="button"
                    class="icon-only cancel-action"
                    :disabled="loading"
                    title="取消提醒"
                    @click="emit('controlAction', item.action.action_id, 'cancel')"
                  ><X :size="14" /></button>
                </div>
              </div>
            </div>
          </li>
        </ol>
      </section>

      <section v-if="conflict" class="conflict-summary" :class="conflict.status.toLowerCase()">
        <WalletCards :size="19" />
        <div><strong>{{ conflict.status === "CLEAR" ? "资金安排暂时清晰" : conflict.status === "UNKNOWN" ? "等待更多资金证据" : "检测到目标资金压力" }}</strong><p>{{ conflict.explanation }}</p></div>
      </section>
    </template>

    <form v-if="editingNode" class="correction-panel" @submit.prevent="submitCorrection">
      <div class="correction-heading"><div><Pencil :size="17" /><strong>纠正资料</strong></div><button type="button" class="icon-only" title="关闭纠正表单" @click="cancelCorrection"><X :size="18" /></button></div>
      <label><span>显示名称</span><input v-model="correctionLabel" maxlength="100"></label>
      <label v-if="correctionKind === 'EVENT'"><span>事件日期</span><input v-model="correctionDate" type="date"></label>
      <label v-if="correctionKind === 'GOAL'"><span>目标金额</span><input v-model="correctionAmount" type="number" min="0" step="0.01"></label>
      <label><span>纠正原因</span><input v-model="correctionReason" maxlength="255" placeholder="例如：日期录入错误" required></label>
      <button type="submit" class="confirm-command" :disabled="!hasCorrectionChange || correctionReason.trim().length < 2"><Check :size="17" />保存纠正</button>
    </form>
  </div>
</template>

<style scoped>
.life-workspace { min-height: 100%; color: #252a26; }
.life-toolbar, .life-section-heading, .pending-request, .pending-band-title, .event-title, .correction-heading { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.life-toolbar { padding-bottom: 18px; border-bottom: 1px solid #e2e5df; }
.life-toolbar p { margin: 0 0 4px; color: #2a6b60; font-size: 11px; font-weight: 800; }
.life-toolbar h2 { margin: 0; font-size: 18px; letter-spacing: 0; }
.radar-band { min-height: 54px; display: grid; grid-template-columns: 20px minmax(0, 1fr) auto; align-items: center; gap: 10px; margin-top: 14px; padding: 10px 12px; border-left: 4px solid #2f796a; background: #eef5f2; color: #315f56; }
.radar-band.unavailable { border-color: #88918b; background: #f2f3f1; color: #666d68; }
.radar-band strong, .radar-band span { display: block; }.radar-band strong { font-size: 11px; }.radar-band span { margin-top: 3px; color: #737b75; font-size: 9px; }
.simulation-command { min-height: 34px; display: inline-flex; align-items: center; gap: 6px; padding: 0 9px; border: 1px solid #c7d5cf; border-radius: 5px; color: #2c6258; background: #fff; font-size: 10px; font-weight: 750; }
.simulation-command:disabled { cursor: not-allowed; opacity: .5; }
.icon-command, .confirm-command, .icon-only { display: inline-flex; align-items: center; justify-content: center; border-radius: 5px; }
.icon-command { gap: 7px; min-height: 36px; padding: 0 11px; border: 1px solid #cdd5cf; color: #3d554e; background: #fff; font-size: 12px; font-weight: 700; }
.icon-command:disabled { opacity: .5; cursor: not-allowed; }
.icon-only { width: 32px; height: 32px; padding: 0; border: 1px solid transparent; color: #68716a; background: transparent; }
.icon-only:hover { border-color: #cfd6d0; background: #fff; }
.icon-only.secondary { border-color: #d5d9d4; background: #fff; }
.confirm-command { gap: 6px; min-height: 36px; padding: 0 12px; border: 0; color: #fff; background: #24675b; font-size: 12px; font-weight: 750; }
.confirm-command:disabled { cursor: not-allowed; opacity: .5; }
.spinning { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.life-error { margin: 14px 0 0; padding: 10px 12px; border-left: 3px solid #b94637; color: #8e3428; background: #fbefec; font-size: 12px; }
.life-loading { min-height: 300px; display: grid; place-items: center; color: #7b827c; font-size: 13px; }
.pending-band { margin: 18px 0 0; padding: 14px 16px; border-left: 4px solid #d29a31; background: #fbf6e8; }
.pending-band-title { justify-content: flex-start; color: #79591c; }
.pending-band-title div strong, .pending-band-title div span, .pending-request div strong, .pending-request div small { display: block; }
.pending-band-title span { margin-top: 3px; color: #786f60; font-size: 11px; }
.pending-request { margin-top: 12px; padding-top: 12px; border-top: 1px solid #eadbb9; }
.pending-request div > strong { font-size: 12px; }
.pending-request small { margin-top: 4px; color: #93856d; font-family: ui-monospace, monospace; font-size: 9px; }
.pending-actions { display: flex; gap: 7px; }
.relationship-section, .timeline-section { padding: 22px 0; border-bottom: 1px solid #e2e5df; }
.life-section-heading { margin-bottom: 15px; }
.life-section-heading > div { display: flex; align-items: center; gap: 8px; color: #315f56; }
.life-section-heading span { color: #858c86; font-size: 11px; }
.relationship-map { display: grid; grid-template-columns: minmax(150px, 190px) 1fr; align-items: center; gap: 22px; }
.self-node, .person-node { min-height: 64px; display: grid; grid-template-columns: 34px 1fr 32px; align-items: center; gap: 9px; padding: 10px 11px; border: 1px solid #d4ddd7; border-radius: 6px; background: #f8fbf9; }
.self-node > span, .person-node > span { width: 34px; height: 34px; display: grid; place-items: center; border-radius: 5px; color: #22675a; background: #dfece7; }
.self-node strong, .self-node small, .person-node strong, .person-node small { display: block; }
.self-node small, .person-node small { margin-top: 3px; color: #7d857e; font-size: 9px; overflow-wrap: anywhere; }
.relationship-lines { display: grid; gap: 9px; }
.relationship-row { min-height: 64px; display: grid; grid-template-columns: 30px 58px minmax(180px, 1fr); align-items: center; }
.relation-connector { height: 1px; background: #94aaa2; }
.relationship-row > em { color: #547169; font-size: 10px; font-style: normal; text-align: center; }
.event-timeline { margin: 0; padding: 0; list-style: none; }
.event-timeline li { display: grid; grid-template-columns: 132px 18px 1fr; gap: 12px; min-height: 98px; }
.event-date { padding-top: 2px; text-align: right; }
.event-date strong, .event-date span { display: block; }
.event-date strong { font-size: 12px; }.event-date span { margin-top: 5px; color: #89908a; font-size: 9px; }
.timeline-mark { position: relative; width: 10px; height: 10px; margin-top: 3px; border-radius: 50%; background: #2d7668; box-shadow: 0 0 0 4px #e3eeea; }
.timeline-mark::after { content: ""; position: absolute; top: 13px; left: 4px; width: 1px; height: 76px; background: #ccd8d2; }
.event-timeline li:last-child .timeline-mark::after { display: none; }
.event-detail { padding: 0 0 18px; }
.event-title > div strong, .event-title > div small { display: block; }
.event-title > div small { margin-top: 4px; color: #7b827c; font-size: 10px; }
.goal-line { min-height: 36px; display: grid; grid-template-columns: 18px 1fr auto auto 30px; align-items: center; gap: 7px; margin-top: 10px; padding: 6px 8px; background: #f3f6f2; font-size: 11px; }
.goal-line strong { font-variant-numeric: tabular-nums; }.goal-line em { color: #5b736b; font-size: 9px; font-style: normal; }
.reminder-line { min-height: 32px; display: flex; flex-wrap: wrap; align-items: center; gap: 7px; margin-top: 7px; color: #747c75; font-size: 10px; }
.reminder-line em { padding: 3px 5px; border-radius: 3px; color: #4c615a; background: #e7ece9; font-size: 8px; font-style: normal; font-weight: 750; }
.reminder-line em.sent { color: #286858; background: #dfede7; }.reminder-line em.failed, .reminder-line em.manual_review { color: #963e31; background: #f7dfda; }.reminder-line em.paused { color: #795a1d; background: #f6e8c9; }.reminder-line em.canceled { color: #727873; background: #eceeeb; }
.reminder-line small { color: #8e948f; font-size: 8px; }
.reminder-actions { display: flex; align-items: center; gap: 2px; margin-left: auto; }
.reminder-actions .icon-only { width: 28px; height: 28px; }.reminder-actions .cancel-action { color: #a14739; }
.life-empty { min-height: 120px; display: grid; place-items: center; align-content: center; gap: 7px; color: #7b837c; text-align: center; }
.life-empty strong { color: #4b554e; font-size: 13px; }.life-empty span { max-width: 380px; font-size: 11px; line-height: 1.6; }.life-empty.compact { min-height: 90px; }
.conflict-summary { display: flex; align-items: flex-start; gap: 10px; margin: 20px 0 4px; padding: 13px 14px; border-left: 4px solid #2f7d69; background: #edf5f1; }
.conflict-summary.pressure { border-color: #cf9329; background: #fbf5e7; }.conflict-summary.conflict { border-color: #b84636; background: #fbefec; }.conflict-summary.unknown { border-color: #7d8580; background: #f2f3f1; }
.conflict-summary strong, .conflict-summary p { margin: 0; }.conflict-summary p { margin-top: 4px; color: #646d66; font-size: 11px; line-height: 1.6; }
.correction-panel { margin: 20px 0 4px; padding: 15px; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; border: 1px solid #cdd8d1; background: #f8faf8; }
.correction-heading { grid-column: 1 / -1; }.correction-heading > div { display: flex; align-items: center; gap: 7px; }
.correction-panel label span { display: block; margin-bottom: 5px; color: #59625b; font-size: 10px; font-weight: 700; }
.correction-panel input { width: 100%; height: 38px; padding: 0 9px; border: 1px solid #cad2cc; border-radius: 5px; background: #fff; }
.correction-panel > .confirm-command { align-self: end; justify-self: end; }
@media (max-width: 720px) {
  .life-toolbar { align-items: flex-start; }.life-toolbar h2 { font-size: 16px; }
  .relationship-map { grid-template-columns: 1fr; gap: 10px; }
  .relationship-row { grid-template-columns: 20px 52px 1fr; }
  .event-timeline li { grid-template-columns: 1fr; gap: 7px; padding-left: 18px; border-left: 1px solid #ccd8d2; }
  .event-date { text-align: left; }.timeline-mark { display: none; }
  .pending-request { align-items: flex-start; flex-direction: column; }.pending-actions { width: 100%; justify-content: flex-end; }
  .correction-panel { grid-template-columns: 1fr; }.correction-heading { grid-column: auto; }
  .radar-band { grid-template-columns: 20px minmax(0, 1fr); }.simulation-command { grid-column: 1 / -1; width: 100%; justify-content: center; }
  .reminder-actions { width: 100%; margin-left: 20px; justify-content: flex-end; }
}
</style>
