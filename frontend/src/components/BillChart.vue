<script setup lang="ts">
import * as echarts from "echarts";
import { nextTick, onBeforeUnmount, ref, watch } from "vue";

const props = defineProps<{ categories: Array<{ name: string; amount: number }> }>();
const element = ref<HTMLDivElement>();
let chart: echarts.ECharts | undefined;

function render() {
  if (!element.value) return;
  chart ??= echarts.init(element.value);
  chart.setOption({
    animationDuration: 500,
    grid: { top: 8, right: 8, bottom: 24, left: 46 },
    xAxis: {
      type: "category",
      data: props.categories.map((item) => item.name),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: "#d9dcd6" } },
      axisLabel: { color: "#686d67", fontSize: 11 },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: "#858a83", fontSize: 10 },
      splitLine: { lineStyle: { color: "#eceee9" } },
    },
    tooltip: { trigger: "axis", valueFormatter: (value: unknown) => `¥${Number(value).toFixed(2)}` },
    series: [{
      type: "bar",
      data: props.categories.map((item, index) => ({
        value: item.amount,
        itemStyle: { color: index === 0 ? "#cf573f" : "#28695d", borderRadius: [3, 3, 0, 0] },
      })),
      barMaxWidth: 38,
    }],
  });
}

watch(() => props.categories, () => nextTick(render), { deep: true, immediate: true });
onBeforeUnmount(() => chart?.dispose());
</script>

<template>
  <div ref="element" class="chart" role="img" aria-label="本月消费分类柱状图"></div>
</template>

<style scoped>
.chart { width: 100%; height: 220px; }
</style>
