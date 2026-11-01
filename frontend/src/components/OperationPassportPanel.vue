<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from "vue";
import {
  BrainCircuit,
  Clock3,
  Download,
  Fingerprint,
  Landmark,
  ShieldCheck,
  UserCheck,
  X,
} from "lucide-vue-next";
import type { OperationPassport } from "../api/client";

const props = defineProps<{
  open: boolean;
  passport?: OperationPassport;
  loading?: boolean;
  error?: string;
  exporting?: boolean;
}>();

const emit = defineEmits<{
  close: [];
  export: [];
}>();

const integrityLabel = computed(() => {
  const labels: Record<string, string> = {
    VERIFIED: "摘要链验证通过",
    TAMPERED: "检测到记录异常",
    EMPTY: "暂无审计记录",
    LEGACY_UNVERIFIED: "旧记录未建立摘要链",
    PARTIAL_UNVERIFIED: "部分记录无法验证",
  };
  return labels[props.passport?.integrity.audit_chain.status ?? ""] ?? "等待验证";
});

function shortDigest(value?: string) {
  if (!value) return "-";
  return value.length > 18 ? `${value.slice(0, 10)}...${value.slice(-6)}` : value;
}

function authorizationLabel(value?: string) {
  const labels: Record<string, string> = {
    CONFIRMATION: "用户明确确认",
    MFA: "多因子强验证",
    LIFE_GRAPH_CONFIRMATION: "生活图谱候选确认",
    NOT_REQUIRED_READ_ONLY: "绿色只读自动执行",
    NONE: "未授权",
  };
  return labels[value ?? ""] ?? value ?? "-";
}

function resultLabel(value?: string) {
  const labels: Record<string, string> = {
    SUCCEEDED: "银行沙箱已回执",
    PENDING_AUTHORIZATION: "等待用户授权",
    NOT_SUCCEEDED: "未成功执行",
    UNVERIFIED_NO_TOOL_RECEIPT: "缺少可信工具回执",
    UNVERIFIED_INTEGRITY: "完整性异常，不能声明成功",
    NOT_EXECUTED: "尚未执行",
  };
  return labels[value ?? ""] ?? value ?? "-";
}

function closeOnEscape(event: KeyboardEvent) {
  if (event.key === "Escape" && props.open) emit("close");
}

watch(
  () => props.open,
  (open) => {
    document.body.style.overflow = open ? "hidden" : "";
  },
);

onMounted(() => document.addEventListener("keydown", closeOnEscape));
onBeforeUnmount(() => {
  document.removeEventListener("keydown", closeOnEscape);
  document.body.style.overflow = "";
});
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="passport-overlay" @click.self="emit('close')">
      <section class="passport-dialog" role="dialog" aria-modal="true" aria-labelledby="passport-title">
        <header class="passport-header">
          <div>
            <span>OPERATION PASSPORT</span>
            <h2 id="passport-title">操作护照</h2>
            <p>{{ passport?.run_id ?? "正在读取任务证据" }}</p>
          </div>
          <div class="passport-actions">
            <button
              type="button"
              title="导出脱敏 JSON"
              aria-label="导出脱敏 JSON"
              :disabled="!passport || exporting"
              @click="emit('export')"
            >
              <Download :size="18" />
            </button>
            <button type="button" title="关闭操作护照" aria-label="关闭操作护照" @click="emit('close')">
              <X :size="19" />
            </button>
          </div>
        </header>

        <div v-if="loading" class="passport-state" role="status">正在重建操作证据...</div>
        <div v-else-if="error" class="passport-state passport-error" role="alert">{{ error }}</div>

        <div v-else-if="passport" class="passport-body">
          <div class="integrity-strip" :class="passport.integrity.audit_chain.status.toLowerCase()">
            <ShieldCheck :size="20" />
            <div>
              <strong>{{ integrityLabel }}</strong>
              <span>{{ passport.integrity.audit_chain.verified_count }}/{{ passport.integrity.audit_chain.record_count }} 条记录可验证</span>
            </div>
            <code>{{ shortDigest(passport.passport_digest) }}</code>
          </div>

          <section class="passport-section request-section">
            <div class="passport-section-title"><Fingerprint :size="18" /><div><strong>请求与版本</strong><span>{{ passport.request.submitted_at }}</span></div></div>
            <blockquote>{{ passport.request.raw_message }}</blockquote>
            <dl class="passport-facts">
              <div><dt>结构化意图</dt><dd>{{ passport.request.structured_intent?.name ?? "未形成" }}</dd></div>
              <div><dt>解析器</dt><dd>{{ passport.model_interpretation.parser ?? "-" }}</dd></div>
              <div><dt>模型</dt><dd>{{ passport.versions.model?.response_model ?? passport.versions.model?.requested_model ?? "本地稳定模式" }}</dd></div>
              <div><dt>Prompt</dt><dd>{{ passport.versions.prompt?.version ?? "-" }}</dd></div>
              <div><dt>Planner</dt><dd>{{ passport.versions.planner ?? "-" }}</dd></div>
              <div><dt>Policy</dt><dd>{{ passport.versions.policy ?? "-" }}</dd></div>
            </dl>
          </section>

          <section class="passport-section">
            <div class="evidence-layers">
              <article>
                <BrainCircuit :size="19" />
                <span>模型理解</span>
                <strong>{{ passport.model_interpretation.status }}</strong>
                <p>{{ passport.model_interpretation.notice }}</p>
              </article>
              <article>
                <UserCheck :size="19" />
                <span>用户授权</span>
                <strong>{{ authorizationLabel(passport.user_authorization.method) }}</strong>
                <p>{{ passport.user_authorization.status }} · {{ passport.user_authorization.decided_at ?? "尚未发生" }}</p>
              </article>
              <article :class="{ trusted: passport.bank_sandbox_result.success_claim_allowed }">
                <Landmark :size="19" />
                <span>银行沙箱结果</span>
                <strong>{{ resultLabel(passport.bank_sandbox_result.status) }}</strong>
                <p>{{ passport.bank_sandbox_result.notice }}</p>
              </article>
            </div>
          </section>

          <section class="passport-section split-section">
            <div>
              <div class="passport-section-title"><ShieldCheck :size="18" /><div><strong>风险与授权快照</strong><span>{{ passport.risk_evidence.policy?.level ?? "未评估" }}</span></div></div>
              <dl class="passport-facts compact">
                <div><dt>风险分数</dt><dd>{{ passport.risk_evidence.policy?.risk_score ?? 0 }}/100</dd></div>
                <div><dt>授权方式</dt><dd>{{ authorizationLabel(passport.user_authorization.method) }}</dd></div>
                <div><dt>快照校验</dt><dd>{{ passport.operation_snapshot.verified === true ? "通过" : passport.operation_snapshot.verified === false ? "异常" : "不适用" }}</dd></div>
                <div><dt>快照摘要</dt><dd><code>{{ shortDigest(passport.operation_snapshot.digest) }}</code></dd></div>
              </dl>
              <p class="passport-reason">{{ passport.risk_evidence.policy?.reason ?? "当前任务没有形成策略决策。" }}</p>
            </div>
            <div>
              <div class="passport-section-title"><Landmark :size="18" /><div><strong>可信工具回执</strong><span>{{ passport.tool_receipts.length }} 条</span></div></div>
              <ul v-if="passport.tool_receipts.length" class="receipt-list">
                <li v-for="item in passport.tool_receipts" :key="`${item.node_id}-${item.output_digest}`">
                  <div><strong>{{ item.tool }}</strong><span>{{ item.summary }}</span></div>
                  <code>{{ shortDigest(item.output_digest) }}</code>
                </li>
              </ul>
              <p v-else class="passport-empty">当前没有工具回执，护照不会声明业务成功。</p>
            </div>
          </section>

          <section class="passport-section">
            <div class="passport-section-title"><Clock3 :size="18" /><div><strong>北京时间线</strong><span>{{ passport.audit_timeline.length }} 个审计事件</span></div></div>
            <ol class="passport-timeline">
              <li v-for="item in passport.audit_timeline" :key="item.audit_id">
                <time>{{ item.beijing_time }}</time>
                <div><strong>{{ item.action }}</strong><span>{{ item.result }}</span></div>
                <code>#{{ item.chain_index ?? "-" }} · {{ shortDigest(item.record_digest) }}</code>
              </li>
            </ol>
          </section>

          <footer class="passport-disclaimer">
            <ShieldCheck :size="17" />
            <p>{{ passport.integrity.disclaimer }}</p>
          </footer>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.passport-overlay { position: fixed; inset: 0; z-index: 80; display: grid; justify-items: end; background: rgb(20 26 23 / 48%); }
.passport-dialog { width: min(860px, calc(100vw - 44px)); height: 100vh; overflow: hidden; background: #f7f8f5; box-shadow: -12px 0 34px rgb(17 25 21 / 18%); }
.passport-header { height: 92px; display: flex; align-items: center; justify-content: space-between; padding: 0 28px; color: #fff; background: #204d45; }
.passport-header > div:first-child { min-width: 0; }
.passport-header span { color: #b7d2ca; font-size: 9px; font-weight: 800; }
.passport-header h2 { margin: 3px 0 0; font-size: 21px; letter-spacing: 0; }
.passport-header p { margin: 4px 0 0; overflow: hidden; color: #d7e6e1; font-family: ui-monospace, monospace; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.passport-actions { display: flex; gap: 7px; }
.passport-actions button { width: 36px; height: 36px; display: grid; place-items: center; border: 1px solid rgb(255 255 255 / 24%); border-radius: 5px; color: #fff; background: transparent; }
.passport-actions button:hover { background: rgb(255 255 255 / 10%); }
.passport-actions button:disabled { cursor: not-allowed; opacity: .45; }
.passport-state { height: calc(100vh - 92px); display: grid; place-items: center; color: #6c736d; font-size: 13px; }
.passport-error { padding: 30px; color: #a33f33; text-align: center; }
.passport-body { height: calc(100vh - 92px); overflow-y: auto; }
.integrity-strip { min-height: 70px; display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 12px; padding: 14px 28px; color: #285f54; background: #e1ede8; border-bottom: 1px solid #c8d9d2; }
.integrity-strip.tampered { color: #9a3e31; background: #f6dfda; border-color: #e3beb7; }
.integrity-strip.partial_unverified, .integrity-strip.legacy_unverified { color: #815e1c; background: #f7eacb; border-color: #ead49f; }
.integrity-strip strong, .integrity-strip span { display: block; }
.integrity-strip strong { font-size: 12px; }
.integrity-strip span { margin-top: 3px; font-size: 10px; }
.integrity-strip code { font-size: 10px; overflow-wrap: anywhere; }
.passport-section { padding: 24px 28px; border-bottom: 1px solid #dfe3dd; }
.passport-section-title { display: flex; align-items: flex-start; gap: 9px; color: #2d655b; }
.passport-section-title strong, .passport-section-title span { display: block; }
.passport-section-title strong { color: #28302c; font-size: 13px; }
.passport-section-title span { margin-top: 3px; color: #7a817c; font-size: 9px; }
.request-section blockquote { margin: 16px 0; padding: 12px 14px; border-left: 3px solid #3d7b70; color: #343b36; background: #fff; font-size: 13px; line-height: 1.65; }
.passport-facts { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px; margin: 0; background: #dfe3dd; }
.passport-facts > div { min-width: 0; padding: 10px 11px; background: #fff; }
.passport-facts dt { color: #7d837e; font-size: 9px; }
.passport-facts dd { margin: 5px 0 0; overflow-wrap: anywhere; font-size: 11px; font-weight: 750; }
.evidence-layers { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
.evidence-layers article { min-width: 0; padding-left: 13px; border-left: 3px solid #7b8580; }
.evidence-layers article:nth-child(1) { border-color: #4c7290; }
.evidence-layers article:nth-child(2) { border-color: #c28d2d; }
.evidence-layers article.trusted { border-color: #2d8069; }
.evidence-layers article > svg { color: #50615a; }
.evidence-layers article span, .evidence-layers article strong { display: block; }
.evidence-layers article span { margin-top: 9px; color: #7d847f; font-size: 9px; }
.evidence-layers article strong { margin-top: 4px; font-size: 12px; overflow-wrap: anywhere; }
.evidence-layers article p { margin: 7px 0 0; color: #69706b; font-size: 10px; line-height: 1.55; }
.split-section { display: grid; grid-template-columns: 1fr 1fr; gap: 34px; }
.passport-facts.compact { grid-template-columns: 1fr 1fr; margin-top: 14px; }
.passport-reason { margin: 12px 0 0; color: #676f69; font-size: 10px; line-height: 1.6; }
.receipt-list, .passport-timeline { margin: 14px 0 0; padding: 0; list-style: none; }
.receipt-list li { min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; padding: 9px 0; border-bottom: 1px solid #dfe3dd; }
.receipt-list strong, .receipt-list span { display: block; overflow-wrap: anywhere; }
.receipt-list strong { font-size: 10px; }
.receipt-list span { margin-top: 3px; color: #767d78; font-size: 9px; }
.receipt-list code { align-self: center; font-size: 8px; }
.passport-empty { margin: 14px 0 0; color: #777e79; font-size: 10px; line-height: 1.6; }
.passport-timeline li { min-width: 0; display: grid; grid-template-columns: 145px minmax(0, 1fr) 150px; gap: 14px; align-items: center; min-height: 48px; border-bottom: 1px solid #dfe3dd; }
.passport-timeline time { color: #626a64; font-variant-numeric: tabular-nums; font-size: 9px; }
.passport-timeline strong, .passport-timeline span { display: block; }
.passport-timeline strong { font-size: 10px; overflow-wrap: anywhere; }
.passport-timeline span { margin-top: 3px; color: #747b76; font-size: 9px; }
.passport-timeline code { overflow-wrap: anywhere; color: #747c77; font-size: 8px; text-align: right; }
.passport-disclaimer { display: flex; align-items: flex-start; gap: 10px; padding: 20px 28px 28px; color: #4a655d; }
.passport-disclaimer p { margin: 0; color: #69716b; font-size: 10px; line-height: 1.6; }
code { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
@media (max-width: 720px) {
  .passport-dialog { width: 100vw; }
  .passport-header { height: 82px; padding: 0 16px; }
  .passport-body, .passport-state { height: calc(100vh - 82px); }
  .integrity-strip { grid-template-columns: auto minmax(0, 1fr); padding: 13px 16px; }
  .integrity-strip code { grid-column: 2; }
  .passport-section { padding: 20px 16px; }
  .passport-facts, .passport-facts.compact { grid-template-columns: 1fr 1fr; }
  .evidence-layers, .split-section { grid-template-columns: 1fr; gap: 20px; }
  .passport-timeline li { grid-template-columns: 105px minmax(0, 1fr); padding: 8px 0; }
  .passport-timeline code { grid-column: 2; text-align: left; }
  .passport-disclaimer { padding: 18px 16px 28px; }
}
</style>
