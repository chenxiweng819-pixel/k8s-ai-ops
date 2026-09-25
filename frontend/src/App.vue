<template>
  <router-view />
</template>

<script setup>
import { onMounted } from "vue";
import { useClusterStore } from "@/stores/cluster";

// 启动即拉一次集群状态，顶部栏与演示模式提示依赖它
onMounted(() => useClusterStore().refreshState());
</script>