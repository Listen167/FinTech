<template>
  <main class="app-shell">
    <section class="workspace">
      <header>
        <p class="eyebrow">SafeBank Agent</p>
        <h1>AI Banking Workspace</h1>
      </header>

      <div class="panel">
        <p>Frontend scaffold is ready. Implement chat, bill analysis, transfer confirmation, subscription management, and audit views here.</p>
      </div>
    </section>
  </main>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  background: #f6f7f9;
  color: #1f2937;
  font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.workspace {
  width: min(1120px, calc(100% - 32px));
  margin: 0 auto;
  padding: 48px 0;
}

.eyebrow {
  margin: 0 0 8px;
  color: #2563eb;
  font-size: 13px;
  font-weight: 700;
  text-transform: uppercase;
}

h1 {
  margin: 0 0 24px;
  font-size: 32px;
}

.panel {
  border: 1px solid #d9dee8;
  border-radius: 8px;
  background: #ffffff;
  padding: 24px;
}
</style>

