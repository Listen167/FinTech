<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import {
  Check,
  Clock3,
  Copy,
  Link2,
  RadioTower,
  RefreshCw,
  ShieldCheck,
  Unlink,
  Wifi,
  WifiOff,
} from "lucide-vue-next";
import {
  ApiError,
  createChannelLinkToken,
  fetchChannelIdentities,
  fetchChannelManagementStatus,
  revokeChannelIdentity,
  type ChannelIdentity,
  type ChannelLinkToken,
  type ChannelManagementStatus,
} from "../api/client";

const identities = ref<ChannelIdentity[]>([]);
const status = ref<ChannelManagementStatus>();
const issuedToken = ref<ChannelLinkToken>();
const loading = ref(false);
const error = ref("");
const notice = ref("");
const copied = ref(false);
const nowMs = ref(Date.now());
let clock: number | undefined;

const remainingSeconds = computed(() => {
  if (!issuedToken.value) return 0;
  return Math.max(0, Math.ceil((new Date(issuedToken.value.expires_at).getTime() - nowMs.value) / 1000));
});
const remainingText = computed(() => {
  const seconds = remainingSeconds.value;
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
});
const activeIdentities = computed(() => identities.value.filter((item) => item.status === "ACTIVE"));

function readableError(reason: unknown) {
  if (reason instanceof ApiError) return reason.message;
  if (reason instanceof TypeError) return "页面处理响应时发生异常，请刷新后重试。";
  return reason instanceof Error ? reason.message : "渠道操作失败，请稍后重试。";
}

function beijingTime(value?: string) {
  if (!value) return "尚无记录";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function transportLabel(value: string) {
  return {
    LONG_CONNECTION: "飞书长连接",
    WEBHOOK: "飞书 Webhook",
    SCHEDULER: "生活事件雷达",
  }[value] ?? value;
}

function workerLabel(value: string) {
  return value === "LIFE_RADAR" ? "主动提醒 Worker" : "消息接入 Worker";
}

async function run(operation: () => Promise<void>) {
  if (loading.value) return;
  loading.value = true;
  error.value = "";
  try {
    await operation();
  } catch (reason) {
    error.value = readableError(reason);
  } finally {
    loading.value = false;
  }
}

async function refresh() {
  await run(async () => {
    const [identityResult, management] = await Promise.all([
      fetchChannelIdentities(),
      fetchChannelManagementStatus(),
    ]);
    identities.value = identityResult.items;
    status.value = management;
  });
}

async function generateCode() {
  await run(async () => {
    issuedToken.value = await createChannelLinkToken();
    nowMs.value = Date.now();
    copied.value = false;
    notice.value = "新的绑定码已生成；此前尚未使用的绑定码已失效。";
  });
}

async function copyCommand() {
  if (!issuedToken.value || remainingSeconds.value <= 0) return;
  await navigator.clipboard.writeText(`绑定 ${issuedToken.value.link_code}`);
  copied.value = true;
  notice.value = "绑定口令已复制，请在飞书中私聊机器人发送。";
  window.setTimeout(() => { copied.value = false; }, 1600);
}

async function revoke(item: ChannelIdentity) {
  await run(async () => {
    await revokeChannelIdentity(item.identity_id);
    notice.value = "渠道身份已撤销，后续飞书消息必须重新绑定。";
    identities.value = (await fetchChannelIdentities()).items;
  });
}

onMounted(() => {
  clock = window.setInterval(() => { nowMs.value = Date.now(); }, 1000);
  void refresh();
});
onBeforeUnmount(() => {
  if (clock !== undefined) window.clearInterval(clock);
});
</script>

<template>
  <div class="channel-workspace">
    <header class="channel-intro">
      <div>
        <small>Channel Identity Gateway</small>
        <h2>飞书渠道与绑定管理</h2>
        <p>绑定只建立渠道身份映射。查询可在飞书完成，确认、强验证和资金操作仍需返回受信银行页面。</p>
      </div>
      <button type="button" class="icon-command" title="刷新渠道状态" aria-label="刷新渠道状态" :disabled="loading" @click="refresh">
        <RefreshCw :size="17" :class="{ spinning: loading }" />
      </button>
    </header>

    <div class="channel-boundary">
      <ShieldCheck :size="19" />
      <div><strong>原始飞书 ID 不会展示</strong><span>页面只显示不可逆脱敏指纹；撤销后旧会话不能继续访问银行 Agent。</span></div>
    </div>

    <p v-if="notice" class="channel-notice" role="status">{{ notice }}</p>
    <p v-if="error" class="channel-error" role="alert">{{ error }}</p>

    <section class="channel-band bind-band">
      <div class="band-title"><Link2 :size="18" /><div><strong>绑定飞书账号</strong><span>一次性绑定码仅在有效时间内可使用</span></div></div>
      <div class="bind-layout">
        <div class="bind-action">
          <p>在飞书中私聊银行机器人并发送绑定口令。重新生成后，旧的未使用绑定码立即失效。</p>
          <button type="button" class="primary-command" :disabled="loading" @click="generateCode"><Link2 :size="16" />生成飞书绑定码</button>
        </div>
        <div v-if="issuedToken" class="token-panel" :class="{ expired: remainingSeconds <= 0 }">
          <div><span>一次性绑定码</span><em><Clock3 :size="14" />{{ remainingSeconds > 0 ? remainingText : "已过期" }}</em></div>
          <code>{{ issuedToken.link_code }}</code>
          <button type="button" class="copy-command" :disabled="remainingSeconds <= 0" @click="copyCommand">
            <Check v-if="copied" :size="15" /><Copy v-else :size="15" />{{ copied ? "已复制" : "复制绑定口令" }}
          </button>
        </div>
        <div v-else class="token-empty"><Clock3 :size="18" /><span>生成后将在此显示绑定码与剩余时间</span></div>
      </div>
    </section>

    <section class="channel-band">
      <div class="band-title"><RadioTower :size="18" /><div><strong>接入运行状态</strong><span>“已配置”不等于 Worker “真实在线”</span></div></div>
      <div class="gateway-summary" :class="{ online: status?.online }">
        <component :is="status?.online ? Wifi : WifiOff" :size="20" />
        <div><strong>{{ status?.online ? "飞书消息接入在线" : "飞书消息接入未在线" }}</strong><span>{{ status?.gateway_configured ? "Channel Gateway 已配置" : "Channel Gateway 尚未配置" }}</span></div>
      </div>
      <div class="transport-list">
        <article v-for="item in status?.transports ?? []" :key="item.transport">
          <div><strong>{{ transportLabel(item.transport) }}</strong><span>最近心跳 {{ beijingTime(item.last_heartbeat_at) }}</span></div>
          <em :class="{ online: item.online, configured: item.configured && !item.online }">{{ item.online ? `${item.worker_count} 个在线` : item.configured ? "已配置，未在线" : "未配置" }}</em>
        </article>
      </div>
      <div v-if="status?.workers.length" class="worker-list">
        <div v-for="item in status.workers" :key="`${item.transport}-${item.worker_fingerprint}`">
          <span>{{ workerLabel(item.worker_kind) }} · {{ item.worker_fingerprint }}</span>
          <strong :class="{ online: item.online }">{{ item.online ? item.state : "OFFLINE" }}</strong>
          <small>{{ transportLabel(item.transport) }} · {{ beijingTime(item.heartbeat_at) }}</small>
          <small v-if="item.last_error_code" class="worker-error">错误码 {{ item.last_error_code }}</small>
        </div>
      </div>
    </section>

    <section class="channel-band">
      <div class="band-title"><ShieldCheck :size="18" /><div><strong>已绑定身份</strong><span>{{ activeIdentities.length }} 个有效身份，可随时撤销</span></div></div>
      <div v-if="!identities.length" class="empty-state">当前账号尚未绑定任何 IM 身份</div>
      <div v-else class="identity-list">
        <article v-for="item in identities" :key="item.identity_id">
          <header><div><strong>{{ item.channel === "FEISHU" ? "飞书私聊身份" : item.channel }}</strong><span>{{ item.external_user_fingerprint }}</span></div><em :class="item.status.toLowerCase()">{{ item.status === "ACTIVE" ? "已绑定" : "已撤销" }}</em></header>
          <dl><div><dt>绑定时间</dt><dd>{{ beijingTime(item.linked_at) }}</dd></div><div><dt>最近活跃</dt><dd>{{ beijingTime(item.last_active_at) }}</dd></div></dl>
          <button v-if="item.status === 'ACTIVE'" type="button" class="secondary-command danger" :disabled="loading" @click="revoke(item)"><Unlink :size="15" />撤销绑定</button>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.channel-workspace { padding: 22px 26px 110px; color: #242b27; }
.channel-intro { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; padding-bottom: 18px; border-bottom: 1px solid #dde3de; }.channel-intro small { color: #357366; font-size: 10px; font-weight: 800; }.channel-intro h2 { margin: 4px 0 7px; font-size: 21px; letter-spacing: 0; }.channel-intro p { max-width: 720px; margin: 0; color: #68706a; font-size: 12px; line-height: 1.65; }
.icon-command { width: 36px; height: 36px; display: grid; place-items: center; flex: 0 0 36px; border: 1px solid #cbd5ce; border-radius: 5px; color: #2c6d5f; background: #fff; }.spinning { animation: spin .8s linear infinite; }@keyframes spin { to { transform: rotate(360deg); } }
.channel-boundary { display: flex; align-items: center; gap: 10px; margin: 16px 0; padding: 12px 14px; border-left: 4px solid #287464; background: #edf5f1; color: #2d675a; }.channel-boundary div { min-width: 0; }.channel-boundary strong, .channel-boundary span { display: block; }.channel-boundary span { margin-top: 3px; color: #566a62; font-size: 10px; line-height: 1.5; }
.channel-notice, .channel-error { margin: 0 0 14px; padding: 10px 12px; font-size: 11px; }.channel-notice { color: #35695d; background: #eaf3ef; }.channel-error { color: #973d31; background: #f8e8e4; }
.channel-band { padding: 20px 0; border-top: 1px solid #dde3de; }.channel-band:first-of-type { border-top: 0; }.band-title { display: flex; align-items: flex-start; gap: 9px; margin-bottom: 14px; color: #285f55; }.band-title div { min-width: 0; }.band-title strong, .band-title span { display: block; }.band-title strong { color: #222b27; font-size: 14px; }.band-title span { margin-top: 3px; color: #7a827c; font-size: 10px; }
.bind-layout { display: grid; grid-template-columns: minmax(0, .9fr) minmax(280px, 1.1fr); gap: 18px; align-items: stretch; }.bind-action { padding: 15px 0; }.bind-action p { margin: 0 0 14px; color: #69726c; font-size: 11px; line-height: 1.7; }.token-panel, .token-empty { min-width: 0; min-height: 128px; padding: 15px; border: 1px solid #d5ddd7; background: #fff; }.token-panel > div { display: flex; align-items: center; justify-content: space-between; gap: 10px; }.token-panel span { color: #69726c; font-size: 10px; }.token-panel em { display: inline-flex; align-items: center; gap: 5px; color: #886116; font-size: 10px; font-style: normal; font-variant-numeric: tabular-nums; }.token-panel code { display: block; margin: 13px 0; overflow-wrap: anywhere; color: #224e46; font-size: 24px; font-weight: 800; letter-spacing: 0; }.token-panel.expired { opacity: .7; }.token-empty { display: grid; place-items: center; align-content: center; gap: 8px; color: #89918b; font-size: 10px; }
.primary-command, .secondary-command, .copy-command { min-height: 36px; display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 0 12px; border-radius: 5px; font-size: 10px; font-weight: 750; }.primary-command { border: 0; color: #fff; background: #2f7465; }.secondary-command, .copy-command { border: 1px solid #c8d2ca; color: #315f55; background: #fff; }.secondary-command.danger { color: #9b3f34; border-color: #ddc0ba; }.primary-command:disabled, .secondary-command:disabled, .copy-command:disabled { cursor: not-allowed; opacity: .5; }
.gateway-summary { display: flex; align-items: center; gap: 11px; padding: 13px 14px; border-left: 4px solid #a7aea8; background: #f1f3f0; color: #626b65; }.gateway-summary.online { border-color: #2e7a67; color: #28695a; background: #edf5f1; }.gateway-summary strong, .gateway-summary span { display: block; }.gateway-summary span { margin-top: 3px; font-size: 9px; }
.transport-list { margin-top: 12px; border-top: 1px solid #e0e5e0; }.transport-list article { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 11px 0; border-bottom: 1px solid #e5e8e5; }.transport-list strong, .transport-list span { display: block; }.transport-list strong { font-size: 11px; }.transport-list span { margin-top: 3px; color: #818883; font-size: 9px; }.transport-list em { padding: 4px 6px; color: #747c76; background: #ecefeb; border-radius: 4px; font-size: 8px; font-style: normal; font-weight: 800; }.transport-list em.online { color: #2f6d5f; background: #e3f0eb; }.transport-list em.configured { color: #85631b; background: #f6edcf; }
.worker-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }.worker-list > div { min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 4px 10px; padding: 10px 11px; border: 1px solid #dce2dd; }.worker-list span { overflow-wrap: anywhere; font-size: 10px; }.worker-list strong { color: #8c453a; font-size: 9px; }.worker-list strong.online { color: #2d6e60; }.worker-list small { color: #858c87; font-size: 8px; }.worker-list .worker-error { grid-column: 1 / -1; color: #9b4438; }
.identity-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }.identity-list article { min-width: 0; padding: 14px; border: 1px solid #d5ddd7; border-radius: 6px; background: #fff; }.identity-list header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }.identity-list header strong, .identity-list header span { display: block; }.identity-list header span { margin-top: 4px; overflow-wrap: anywhere; color: #78817b; font-family: ui-monospace, monospace; font-size: 9px; }.identity-list header em { padding: 4px 6px; color: #707972; background: #edf0ed; border-radius: 4px; font-size: 8px; font-style: normal; font-weight: 800; }.identity-list header em.active { color: #2e6b5d; background: #e3f0eb; }.identity-list dl { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 14px 0; }.identity-list dt { color: #8a908b; font-size: 8px; }.identity-list dd { margin: 3px 0 0; font-size: 10px; }.empty-state { padding: 24px 0; color: #929993; font-size: 11px; text-align: center; }
@media (max-width: 720px) { .channel-workspace { padding: 18px 16px 100px; }.bind-layout, .identity-list, .worker-list { grid-template-columns: 1fr; }.token-panel code { font-size: 21px; }.transport-list article { align-items: flex-start; }.channel-intro p { font-size: 11px; } }
</style>
