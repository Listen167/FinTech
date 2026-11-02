<script setup lang="ts">
import { computed, ref, watch } from "vue";
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  KeyRound,
  MessageSquareWarning,
  Phone,
  ShieldBan,
  ShieldCheck,
} from "lucide-vue-next";
import {
  ApiError,
  createFraudReleaseChallenge,
  fetchDemoFraudReleaseCode,
  submitFraudAppeal,
  verifyFraudReleaseChallenge,
} from "../api/client";
import type { FraudReleaseChallenge, FraudRiskEvent } from "../api/client";

const props = defineProps<{ event: FraudRiskEvent }>();

const current = ref(props.event);
const appealReason = ref("我认为该风险判断可能是误报，请核实收款人与命中证据。");
const challenge = ref<FraudReleaseChallenge>();
const code = ref("");
const loading = ref(false);
const error = ref("");
const notice = ref("");

watch(
  () => props.event,
  (value) => {
    current.value = value;
    challenge.value = undefined;
    code.value = "";
    error.value = "";
    notice.value = "";
  },
);

const active = computed(() => current.value.status === "COOLING_OFF");

function readableError(reason: unknown) {
  if (reason instanceof ApiError) return reason.message;
  if (reason instanceof TypeError) return "页面处理响应时发生异常，请刷新后重试。";
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

async function appeal() {
  if (appealReason.value.trim().length < 5 || loading.value) return;
  loading.value = true;
  error.value = "";
  notice.value = "";
  try {
    current.value = await submitFraudAppeal(
      current.value.event_id,
      appealReason.value.trim(),
    );
    notice.value = "误报申诉已记录。申诉本身不会解除冷静期或执行原转账。";
  } catch (reason) {
    error.value = readableError(reason);
  } finally {
    loading.value = false;
  }
}

async function startRelease() {
  if (loading.value) return;
  loading.value = true;
  error.value = "";
  notice.value = "";
  try {
    challenge.value = await createFraudReleaseChallenge(current.value.event_id);
    code.value = "";
  } catch (reason) {
    error.value = readableError(reason);
  } finally {
    loading.value = false;
  }
}

async function requestDemoCode() {
  if (!challenge.value || loading.value) return;
  loading.value = true;
  error.value = "";
  try {
    const result = await fetchDemoFraudReleaseCode(challenge.value.challenge_id);
    code.value = result.code;
    notice.value = `演示验证码 ${result.code} 已填入；生产环境不会在页面返回验证码。`;
  } catch (reason) {
    error.value = readableError(reason);
  } finally {
    loading.value = false;
  }
}

async function verifyRelease() {
  if (!challenge.value || !/^\d{6}$/.test(code.value) || loading.value) return;
  loading.value = true;
  error.value = "";
  notice.value = "";
  try {
    const result = await verifyFraudReleaseChallenge(
      challenge.value.challenge_id,
      code.value,
    );
    current.value = result.event;
    challenge.value = result.challenge;
    notice.value = result.message;
  } catch (reason) {
    error.value = readableError(reason);
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <section class="fraud-cooling" :class="current.status.toLowerCase()" aria-labelledby="fraud-cooling-title">
    <header class="fraud-heading">
      <div class="fraud-title">
        <span><ShieldBan :size="21" /></span>
        <div>
          <small>反诈冷静期</small>
          <h2 id="fraud-cooling-title">
            {{ active ? "同一收款人的资金请求已暂停" : "本次冷静期已经结束" }}
          </h2>
        </div>
      </div>
      <em>{{ current.status }}</em>
    </header>

    <div class="fraud-summary">
      <div><span>关联收款人</span><strong>{{ current.recipient.display_name }} · {{ current.recipient.account_masked ?? "账户待核验" }}</strong></div>
      <div><span>冷静期结束</span><strong>{{ beijingTime(current.cooling_until) }}</strong></div>
      <div><span>关联重试</span><strong>{{ current.retry_count }} 次</strong></div>
      <div><span>策略版本</span><strong>{{ current.policy_version }}</strong></div>
    </div>

    <div class="fraud-evidence">
      <div class="fraud-section-heading"><AlertTriangle :size="17" /><strong>命中的事实证据</strong></div>
      <ul>
        <li v-for="item in current.evidence" :key="item.signal">
          <code>{{ item.signal }}</code><span>{{ item.fact }}</span>
        </li>
      </ul>
      <small>收款人指纹 {{ current.recipient.fingerprint_hint }}；系统不保存完整指纹或原始聊天文本到前端。</small>
    </div>

    <div class="fraud-help">
      <div>
        <div class="fraud-section-heading"><Phone :size="17" /><strong>{{ current.help.title }}</strong></div>
        <ol><li v-for="step in current.help.steps" :key="step">{{ step }}</li></ol>
      </div>
      <a :href="`tel:${current.help.hotline}`" :aria-label="`拨打反诈热线 ${current.help.hotline}`">
        <Phone :size="16" />{{ current.help.hotline }}
      </a>
    </div>

    <template v-if="active">
      <div class="fraud-actions">
        <section>
          <div class="fraud-section-heading"><MessageSquareWarning :size="17" /><strong>误报申诉</strong></div>
          <template v-if="current.appeal.status === 'SUBMITTED'">
            <p class="appeal-recorded"><CheckCircle2 :size="16" />已记录申诉，冷静期仍保持生效。</p>
          </template>
          <template v-else>
            <textarea v-model="appealReason" maxlength="500" rows="2" aria-label="误报申诉理由"></textarea>
            <button type="button" class="secondary-command" :disabled="loading || appealReason.trim().length < 5" @click="appeal">
              <MessageSquareWarning :size="16" />提交申诉
            </button>
          </template>
        </section>

        <section>
          <div class="fraud-section-heading"><ShieldCheck :size="17" /><strong>受信 App 强验证</strong></div>
          <p>飞书和其他 IM 不能解除。验证成功也不会自动恢复原转账。</p>
          <button v-if="!challenge" type="button" class="primary-command" :disabled="loading" @click="startRelease">
            <KeyRound :size="16" />获取释放验证码
          </button>
          <div v-else class="release-verification">
            <input v-model="code" maxlength="6" inputmode="numeric" placeholder="六位验证码" aria-label="冷静期释放验证码">
            <button type="button" class="secondary-command" :disabled="loading" @click="requestDemoCode">获取演示码</button>
            <button type="button" class="primary-command" :disabled="loading || !/^\d{6}$/.test(code)" @click="verifyRelease">验证并解除</button>
            <small><Clock3 :size="13" />验证码有效至 {{ beijingTime(challenge.expires_at) }}</small>
          </div>
        </section>
      </div>
    </template>
    <p v-else class="release-result"><CheckCircle2 :size="17" />原转账没有自动执行；如仍需转账，请重新发起并重新接受风险评估。</p>

    <p v-if="notice" class="fraud-notice" role="status">{{ notice }}</p>
    <p v-if="error" class="fraud-error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.fraud-cooling { margin: 20px 0; padding: 16px; border: 1px solid #e0c9c3; border-left: 5px solid #b84938; border-radius: 7px; background: #fff9f7; color: #292d29; }
.fraud-cooling.released, .fraud-cooling.expired { border-color: #cddbd4; border-left-color: #347666; background: #f7fbf9; }
.fraud-heading, .fraud-title, .fraud-section-heading, .fraud-help, .appeal-recorded, .release-result { display: flex; align-items: center; }
.fraud-heading, .fraud-help { justify-content: space-between; gap: 16px; }
.fraud-title { align-items: flex-start; gap: 10px; }.fraud-title > span { width: 34px; height: 34px; display: grid; place-items: center; flex: 0 0 34px; border-radius: 6px; color: #fff; background: #ae4334; }
.fraud-title small { color: #9a493d; font-size: 10px; font-weight: 800; }.fraud-title h2 { margin: 3px 0 0; font-size: 16px; letter-spacing: 0; }
.fraud-heading > em { padding: 5px 7px; border-radius: 4px; color: #963d31; background: #f3dcd7; font-size: 9px; font-style: normal; font-weight: 800; }
.fraud-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; margin: 15px 0; background: #e6d9d5; }
.fraud-summary div { min-width: 0; padding: 10px; background: #fff; }.fraud-summary span, .fraud-summary strong { display: block; }.fraud-summary span { color: #85827e; font-size: 9px; }.fraud-summary strong { margin-top: 4px; overflow-wrap: anywhere; font-size: 11px; }
.fraud-evidence, .fraud-help, .fraud-actions { padding-top: 14px; border-top: 1px solid #e7dad6; }
.fraud-section-heading { gap: 7px; color: #6f443d; font-size: 12px; }.fraud-evidence ul { margin: 10px 0; padding: 0; list-style: none; }.fraud-evidence li { display: grid; grid-template-columns: 118px minmax(0, 1fr); gap: 10px; padding: 7px 0; border-bottom: 1px dashed #e6dbd7; font-size: 10px; line-height: 1.5; }.fraud-evidence code { color: #9b3f32; font-size: 9px; }.fraud-evidence > small { color: #8b8b85; font-size: 8px; overflow-wrap: anywhere; }
.fraud-help { align-items: flex-start; margin-top: 14px; }.fraud-help ol { margin: 9px 0 0; padding-left: 18px; color: #656963; font-size: 10px; line-height: 1.7; }.fraud-help a { min-height: 36px; display: inline-flex; align-items: center; gap: 6px; padding: 0 11px; border: 1px solid #d1b7b0; border-radius: 5px; color: #963f32; background: #fff; text-decoration: none; font-weight: 800; }
.fraud-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-top: 14px; }.fraud-actions section { min-width: 0; }.fraud-actions p { margin: 9px 0; color: #696c67; font-size: 10px; line-height: 1.6; }.fraud-actions textarea, .release-verification input { width: 100%; border: 1px solid #d9cbc7; border-radius: 5px; background: #fff; outline: none; }.fraud-actions textarea { margin: 9px 0 7px; padding: 8px; resize: vertical; }.release-verification { display: grid; grid-template-columns: minmax(100px, 1fr) auto auto; gap: 7px; }.release-verification input { min-width: 0; height: 36px; padding: 0 9px; }.release-verification small { grid-column: 1 / -1; display: flex; align-items: center; gap: 5px; color: #858781; font-size: 9px; }
.primary-command, .secondary-command { min-height: 36px; display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 0 11px; border-radius: 5px; font-size: 10px; font-weight: 750; }.primary-command { border: 0; color: #fff; background: #9f4034; }.secondary-command { border: 1px solid #d4c3be; color: #714a43; background: #fff; }.primary-command:disabled, .secondary-command:disabled { cursor: not-allowed; opacity: .5; }
.appeal-recorded, .release-result { gap: 7px; color: #376b5e; }.release-result { margin: 14px 0 0; padding-top: 13px; border-top: 1px solid #dce6e1; font-size: 11px; }
.fraud-notice, .fraud-error { margin: 12px 0 0; padding: 9px 10px; font-size: 10px; }.fraud-notice { color: #35695d; background: #eaf3ef; }.fraud-error { color: #983d31; background: #f6e2dd; }
@media (max-width: 720px) {
  .fraud-heading, .fraud-help { align-items: flex-start; }.fraud-summary { grid-template-columns: 1fr 1fr; }.fraud-actions { grid-template-columns: 1fr; }.fraud-evidence li { grid-template-columns: 1fr; gap: 3px; }.release-verification { grid-template-columns: 1fr 1fr; }.release-verification input { grid-column: 1 / -1; }
}
</style>
