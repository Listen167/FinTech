<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  RefreshCw,
  ShieldCheck,
} from "lucide-vue-next";
import {
  ApiError,
  fetchEvaluationSummary,
  fetchRuntimeMetrics,
  type EvaluationSummary,
  type RuntimeMetrics,
} from "../api/client";

const summary = ref<EvaluationSummary>();
const runtime = ref<RuntimeMetrics>();
const loading = ref(false);
const error = ref("");

const metricItems = computed(() => {
  if (!summary.value) return [];
  const metrics = summary.value.metrics;
  return [
    { label: "意图准确率", value: `${metrics.intent_accuracy_percent.toFixed(2)}%`, tone: "green" },
    { label: "槽位完整率", value: `${metrics.slot_completeness_percent.toFixed(2)}%`, tone: "green" },
    { label: "模型回退率", value: `${metrics.fallback_rate_percent.toFixed(2)}%`, tone: "yellow" },
    { label: "误执行率", value: `${metrics.false_execution_rate_percent.toFixed(2)}%`, tone: "red" },
    { label: "越权成功率", value: `${metrics.privilege_escalation_success_rate_percent.toFixed(2)}%`, tone: "red" },
    { label: "P95 延迟", value: `${metrics.p95_latency_ms.toFixed(2)} ms`, tone: "neutral" },
  ];
});

const categoryLabels: Record<string, string> = {
  INTENT: "金融意图",
  AMBIGUOUS: "模糊表达",
  MULTI_TURN: "多轮补槽",
  ATTACK: "攻击与越权",
};

const failureLabels: Record<string, string> = {
  SLOT_ASSERTION_MISMATCH: "槽位契约未满足",
  INTENT_MISMATCH: "意图判断不一致",
  SAFETY_DECISION_MISMATCH: "安全决策不一致",
};

const modeLabel = computed(() => (
  summary.value?.evaluation_mode === "LIVE_DEEPSEEK"
    ? "DeepSeek 在线评测"
    : "离线确定性基线"
));

function readableError(reason: unknown) {
  if (reason instanceof ApiError) return reason.message;
  if (reason instanceof TypeError) return "页面处理响应时发生异常，请刷新后重试。";
  return reason instanceof Error ? reason.message : "评测数据加载失败。";
}

function shortDigest(value?: string) {
  if (!value) return "-";
  return `${value.slice(0, 10)}...${value.slice(-6)}`;
}

function formatTime(value?: string) {
  if (!value) return "尚未生成";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

async function refresh() {
  if (loading.value) return;
  loading.value = true;
  error.value = "";
  try {
    const [evaluation, metrics] = await Promise.all([
      fetchEvaluationSummary(),
      fetchRuntimeMetrics(),
    ]);
    summary.value = evaluation;
    runtime.value = metrics;
  } catch (reason) {
    error.value = readableError(reason);
  } finally {
    loading.value = false;
  }
}

onMounted(refresh);
</script>

<template>
  <div class="evaluation-dashboard">
    <header class="evaluation-toolbar">
      <div>
        <span :class="['mode-badge', summary?.evaluation_mode === 'LIVE_DEEPSEEK' ? 'live' : 'offline']">
          {{ modeLabel }}
        </span>
        <small v-if="summary">{{ summary.dataset.case_count }} 条样本 · {{ formatTime(summary.generated_at) }}</small>
      </div>
      <button type="button" :disabled="loading" title="刷新评测与运行指标" @click="refresh">
        <RefreshCw :size="17" :class="{ spinning: loading }" />
        <span>{{ loading ? "刷新中" : "刷新" }}</span>
      </button>
    </header>

    <p v-if="error" class="evaluation-error" role="alert"><AlertTriangle :size="18" />{{ error }}</p>
    <div v-else-if="!summary || !runtime" class="evaluation-loading"><Activity :size="20" />正在读取聚合评测结果...</div>

    <template v-else>
      <section class="metric-section" aria-labelledby="evaluation-core-metrics">
        <div class="evaluation-heading">
          <div><p>评测基线</p><h2 id="evaluation-core-metrics">六项核心指标</h2></div>
          <span>{{ summary.dataset.version }}</span>
        </div>
        <div class="metric-grid">
          <article v-for="item in metricItems" :key="item.label" :class="item.tone">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
          </article>
        </div>
        <p v-if="summary.evaluation_mode === 'OFFLINE_DETERMINISTIC'" class="mode-note">
          当前为不调用外部模型的离线回归，因此模型回退率为 100%；该结果不代表 DeepSeek 在线准确率。
        </p>
      </section>

      <section class="security-gate" :class="{ passed: summary.security_gate.passed }">
        <ShieldCheck :size="24" />
        <div>
          <span>红队发布门禁</span>
          <strong>{{ summary.security_gate.passed ? "通过" : "未通过" }}</strong>
          <p>
            {{ summary.security_gate.high_risk_cases ?? 0 }} 条高风险样本，越权成功
            {{ summary.security_gate.privilege_escalation_successes ?? 0 }} 次；误执行
            {{ summary.security_gate.false_execution_successes ?? 0 }} 次；重复执行
            {{ summary.security_gate.duplicate_execution_successes ?? 0 }} 次。
          </p>
        </div>
        <CheckCircle2 v-if="summary.security_gate.passed" :size="22" />
        <AlertTriangle v-else :size="22" />
      </section>

      <div class="evaluation-columns">
        <section>
          <div class="evaluation-heading compact"><div><p>数据覆盖</p><h2>样本分层</h2></div></div>
          <div class="coverage-list">
            <div v-for="(count, category) in summary.dataset.categories" :key="category">
              <span>{{ categoryLabels[category] ?? category }}</span>
              <div><i :style="{ width: `${count / summary.dataset.case_count * 100}%` }"></i></div>
              <strong>{{ count }}</strong>
            </div>
          </div>
        </section>

        <section>
          <div class="evaluation-heading compact"><div><p>实时聚合</p><h2>运行健康</h2></div><span>{{ runtime.telemetry_status }}</span></div>
          <dl class="runtime-grid">
            <div><dt>Run 总数</dt><dd>{{ runtime.totals.runs }}</dd></div>
            <div><dt>节点失败</dt><dd>{{ runtime.totals.failed_nodes }}</dd></div>
            <div><dt>模型回退</dt><dd>{{ runtime.totals.model_fallbacks }}</dd></div>
            <div><dt>渠道失败</dt><dd>{{ runtime.totals.channel_failures }}</dd></div>
            <div><dt>渠道拒绝</dt><dd>{{ runtime.totals.channel_rejections }}</dd></div>
            <div><dt>安全熔断</dt><dd>{{ runtime.totals.circuit_breakers }}</dd></div>
          </dl>
        </section>
      </div>

      <div class="evaluation-columns lower">
        <section>
          <div class="evaluation-heading compact"><div><p>状态分布</p><h2>Run 状态</h2></div></div>
          <div v-if="runtime.run_states.length" class="state-list">
            <span v-for="item in runtime.run_states" :key="item.state"><b>{{ item.count }}</b>{{ item.state }}</span>
          </div>
          <p v-else class="empty-copy">尚无 Run 数据。</p>
        </section>

        <section>
          <div class="evaluation-heading compact"><div><p>脱敏抽样</p><h2>待改进案例</h2></div></div>
          <ul v-if="summary.redacted_failures.length" class="failure-list">
            <li v-for="item in summary.redacted_failures.slice(0, 5)" :key="item.case_id">
              <span>{{ item.case_id }}</span>
              <strong>{{ failureLabels[item.reason] ?? item.reason }}</strong>
              <small>{{ categoryLabels[item.category] ?? item.category }}</small>
            </li>
          </ul>
          <p v-else class="empty-copy">当前基线没有失败案例。</p>
        </section>
      </div>

      <footer class="version-strip">
        <Clock3 :size="17" />
        <span>模型 <b>{{ summary.versions.model.requested_model }}</b></span>
        <span>Prompt <b>{{ summary.versions.prompt.version }}</b></span>
        <span>Policy <b>{{ summary.versions.policy.version }}</b></span>
        <span>数据摘要 <b>{{ shortDigest(summary.dataset.sha256) }}</b></span>
      </footer>
    </template>
  </div>
</template>

<style scoped>
.evaluation-dashboard { min-height: 100%; padding: 24px 26px 34px; color: #28312d; background: #f7f8f6; }
.evaluation-toolbar { min-height: 42px; display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 22px; }
.evaluation-toolbar > div { display: flex; align-items: center; flex-wrap: wrap; gap: 9px; }
.evaluation-toolbar small { color: #7a827d; font-size: 11px; }
.evaluation-toolbar button { height: 36px; display: inline-flex; align-items: center; gap: 7px; padding: 0 11px; border: 1px solid #ccd3ce; border-radius: 5px; color: #315e55; background: #fff; font-weight: 750; }
.evaluation-toolbar button:disabled { cursor: not-allowed; opacity: .55; }
.mode-badge { padding: 5px 7px; border-radius: 4px; font-size: 10px; font-weight: 800; }
.mode-badge.offline { color: #77591e; background: #f6e9c9; }
.mode-badge.live { color: #246354; background: #dfeee8; }
.evaluation-error, .evaluation-loading { min-height: 160px; display: flex; align-items: center; justify-content: center; gap: 9px; color: #7a817c; }
.evaluation-error { color: #a34235; }
.metric-section, .evaluation-columns > section { padding-bottom: 23px; border-bottom: 1px solid #dce1dc; }
.evaluation-heading { display: flex; align-items: end; justify-content: space-between; gap: 16px; margin-bottom: 13px; }
.evaluation-heading p, .evaluation-heading h2 { margin: 0; }
.evaluation-heading p { margin-bottom: 3px; color: #397367; font-size: 10px; font-weight: 800; }
.evaluation-heading h2 { font-size: 18px; }
.evaluation-heading > span { color: #7c847e; font-family: ui-monospace, monospace; font-size: 10px; }
.evaluation-heading.compact h2 { font-size: 14px; }
.metric-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; }
.metric-grid article { min-height: 82px; padding: 13px; border: 1px solid #d8ded9; border-left: 4px solid #7f8982; border-radius: 6px; background: #fff; }
.metric-grid article.green { border-left-color: #2e7d67; }
.metric-grid article.yellow { border-left-color: #cf9830; }
.metric-grid article.red { border-left-color: #b84b3c; }
.metric-grid span, .metric-grid strong { display: block; }
.metric-grid span { color: #747c77; font-size: 10px; }
.metric-grid strong { margin-top: 10px; font-size: 20px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.mode-note { margin: 11px 0 0; color: #74684d; font-size: 10px; line-height: 1.6; }
.security-gate { min-height: 84px; display: grid; grid-template-columns: 28px minmax(0, 1fr) 24px; align-items: center; gap: 12px; margin: 19px 0 24px; padding: 13px 15px; border-left: 5px solid #ba493b; background: #fff4f2; }
.security-gate.passed { border-left-color: #2d7c66; background: #edf6f2; }
.security-gate span, .security-gate strong { display: block; }
.security-gate span { color: #6d756f; font-size: 9px; }
.security-gate strong { margin-top: 2px; font-size: 15px; }
.security-gate p { margin: 5px 0 0; color: #646c67; font-size: 10px; line-height: 1.5; }
.evaluation-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 26px; }
.evaluation-columns.lower { margin-top: 23px; }
.coverage-list > div { display: grid; grid-template-columns: 76px minmax(40px, 1fr) 32px; align-items: center; gap: 9px; min-height: 31px; }
.coverage-list span, .coverage-list strong { font-size: 10px; }
.coverage-list strong { text-align: right; font-variant-numeric: tabular-nums; }
.coverage-list div > div { height: 6px; overflow: hidden; background: #e5e9e5; }
.coverage-list i { height: 100%; display: block; min-width: 2px; background: #3a796c; }
.runtime-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px; margin: 0; background: #dfe3df; }
.runtime-grid div { min-width: 0; padding: 10px; background: #fff; }
.runtime-grid dt { color: #7b827d; font-size: 9px; }
.runtime-grid dd { margin: 4px 0 0; font-size: 16px; font-weight: 800; font-variant-numeric: tabular-nums; }
.state-list { display: flex; flex-wrap: wrap; gap: 7px; }
.state-list span { min-width: 88px; display: inline-flex; align-items: center; gap: 6px; padding: 7px 8px; border: 1px solid #d8ddd9; border-radius: 4px; color: #66706a; background: #fff; font-size: 9px; }
.state-list b { color: #2e665b; font-size: 12px; }
.failure-list { margin: 0; padding: 0; list-style: none; }
.failure-list li { min-height: 35px; display: grid; grid-template-columns: minmax(86px, auto) 1fr auto; align-items: center; gap: 8px; border-bottom: 1px solid #e1e5e1; }
.failure-list span { color: #707972; font-family: ui-monospace, monospace; font-size: 8px; }
.failure-list strong { font-size: 10px; }
.failure-list small { color: #8a918c; font-size: 8px; }
.empty-copy { color: #8a918c; font-size: 10px; }
.version-strip { display: flex; align-items: center; flex-wrap: wrap; gap: 9px 16px; margin-top: 23px; padding: 12px 0; color: #68716b; font-size: 9px; }
.version-strip b { color: #38453f; font-family: ui-monospace, monospace; }
.spinning { animation: evaluation-spin 1s linear infinite; }
@keyframes evaluation-spin { to { transform: rotate(360deg); } }
@media (max-width: 720px) {
  .evaluation-dashboard { padding: 18px 16px 28px; }
  .evaluation-toolbar { align-items: flex-start; }
  .evaluation-toolbar button span { display: none; }
  .evaluation-toolbar button { width: 36px; justify-content: center; padding: 0; }
  .metric-grid { grid-template-columns: 1fr 1fr; }
  .metric-grid article { min-height: 76px; }
  .metric-grid strong { font-size: 16px; }
  .evaluation-columns { grid-template-columns: 1fr; gap: 23px; }
  .failure-list li { grid-template-columns: 1fr auto; padding: 7px 0; }
  .failure-list strong { grid-row: 2; grid-column: 1 / -1; }
}
</style>
