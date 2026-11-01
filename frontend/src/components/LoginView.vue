<script setup lang="ts">
import { ref } from "vue";

defineProps<{
  loading: boolean;
  error: string;
}>();

const emit = defineEmits<{
  login: [username: string, password: string];
}>();

const username = ref("lin_an");
const password = ref("");
const showPassword = ref(false);

function submit() {
  if (!username.value.trim() || !password.value) return;
  emit("login", username.value.trim(), password.value);
}
</script>

<template>
  <main class="login-shell">
    <section class="login-panel" aria-labelledby="login-title">
      <div class="login-brand">
        <span class="login-mark">安</span>
        <div><strong>安心办</strong><small>SafeBank Guardian</small></div>
      </div>

      <div class="login-heading">
        <p>演示银行工作台</p>
        <h1 id="login-title">登录你的账户</h1>
      </div>

      <form class="login-form" @submit.prevent="submit">
        <label>
          <span>用户名</span>
          <input
            v-model="username"
            name="username"
            autocomplete="username"
            maxlength="100"
            required
          >
        </label>
        <label>
          <span>密码</span>
          <input
            v-model="password"
            name="password"
            :type="showPassword ? 'text' : 'password'"
            autocomplete="current-password"
            maxlength="128"
            required
          >
        </label>
        <label class="password-toggle">
          <input v-model="showPassword" type="checkbox">
          <span>显示密码</span>
        </label>
        <p v-if="error" class="login-error" role="alert">{{ error }}</p>
        <button type="submit" :disabled="loading || !username.trim() || !password">
          {{ loading ? "正在验证" : "登录" }}
        </button>
      </form>

      <div class="login-footer">
        <span>演示环境</span>
        <span>受保护连接</span>
      </div>
    </section>

    <aside class="login-context" aria-label="演示账户">
      <div>
        <p>可用账户</p>
        <button type="button" :class="{ active: username === 'lin_an' }" @click="username = 'lin_an'">
          <span>LA</span><strong>林安</strong><small>lin_an</small>
        </button>
        <button type="button" :class="{ active: username === 'zhou_ning' }" @click="username = 'zhou_ning'">
          <span>ZN</span><strong>周宁</strong><small>zhou_ning</small>
        </button>
      </div>
    </aside>
  </main>
</template>

<style scoped>
.login-shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: minmax(320px, 460px) minmax(260px, 360px);
  justify-content: center;
  align-items: stretch;
  background: #eef0eb;
}
.login-panel {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  padding: 44px 52px 34px;
  background: #fff;
  border-left: 1px solid #dfe2dc;
}
.login-brand { display: flex; align-items: center; gap: 12px; }
.login-mark {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border-radius: 7px;
  color: #fff;
  background: #1f655a;
  font-weight: 800;
}
.login-brand div { display: flex; align-items: flex-start; flex-direction: column; line-height: 1.1; }
.login-brand small { margin-top: 4px; color: #747a73; }
.login-heading { margin-top: auto; padding-top: 72px; }
.login-heading p { margin: 0 0 8px; color: #287064; font-size: 12px; font-weight: 750; }
.login-heading h1 { margin: 0; font-size: 28px; letter-spacing: 0; }
.login-form { display: grid; gap: 16px; margin-top: 30px; }
.login-form label > span { display: block; margin-bottom: 7px; color: #4e554f; font-size: 13px; font-weight: 700; }
.login-form input:not([type="checkbox"]) {
  width: 100%;
  height: 46px;
  padding: 0 12px;
  border: 1px solid #cbd1ca;
  border-radius: 6px;
  color: #202420;
  background: #fbfcfa;
  outline: none;
}
.login-form input:focus { border-color: #397b70; box-shadow: 0 0 0 3px #e1eeea; }
.password-toggle { display: flex; align-items: center; gap: 8px; width: fit-content; }
.password-toggle input { width: 16px; height: 16px; accent-color: #27695d; }
.password-toggle span { margin: 0 !important; color: #656c65 !important; font-size: 12px !important; font-weight: 500 !important; }
.login-form > button {
  height: 46px;
  border: 0;
  border-radius: 6px;
  color: #fff;
  background: #205f54;
  font-weight: 750;
}
.login-form > button:disabled { cursor: not-allowed; opacity: .55; }
.login-error { margin: -2px 0 0; color: #ad4031; font-size: 12px; line-height: 1.5; }
.login-footer { display: flex; justify-content: space-between; margin-top: auto; padding-top: 56px; color: #858b84; font-size: 11px; }
.login-context {
  display: flex;
  align-items: center;
  padding: 46px;
  border-right: 1px solid #dfe2dc;
  background: #f7f8f5;
}
.login-context > div { width: 100%; }
.login-context p { margin: 0 0 14px; color: #737a73; font-size: 12px; font-weight: 700; }
.login-context button {
  width: 100%;
  min-height: 64px;
  display: grid;
  grid-template-columns: 34px 1fr;
  grid-template-rows: 1fr 1fr;
  column-gap: 11px;
  margin-bottom: 8px;
  padding: 11px;
  border: 1px solid #d9ded8;
  border-radius: 6px;
  color: #353a35;
  background: #fff;
  text-align: left;
}
.login-context button.active { border-color: #78a69c; box-shadow: inset 3px 0 #27695d; }
.login-context button > span { grid-row: 1 / 3; align-self: center; width: 34px; height: 34px; display: grid; place-items: center; border-radius: 5px; color: #28695d; background: #e2eee9; font-size: 11px; font-weight: 800; }
.login-context button strong { align-self: end; font-size: 13px; }
.login-context button small { align-self: start; margin-top: 2px; color: #818781; }
@media (max-width: 760px) {
  .login-shell { display: block; background: #fff; }
  .login-panel { min-height: 100vh; padding: 28px 24px; border: 0; }
  .login-context { display: none; }
  .login-heading { padding-top: 64px; }
}
</style>
