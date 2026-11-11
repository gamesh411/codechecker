<template>
  <v-chip
    v-if="type === 'macro' || type === 'note'"
    :color="color"
    rounded="0"
    size="small"
  >
    <span class="text-weight-bold text-white">{{ index }}</span>
  </v-chip>

  <v-avatar
    v-else
    :class="{
      'report-step-enum-icon--long': digits >= 3,
      'report-step-enum-icon--wide': digits >= 4
    }"
    :color="color"
    :size="size"
    :style="digits >= 4 ? { width: 'auto', minWidth: `${size}px` } : undefined"
  >
    <span class="text-caption text-white">{{ index }}</span>
  </v-avatar>
</template>

<script setup>
import { computed } from "vue";
import { getCssColor } from "@/utilities/colors";

const props = defineProps({
  type: { type: String, default: null },
  index: { type: [ Number, String ], default: null },
  size: { type: Number, default: 24 }
});

const digits = computed(() => String(props.index ?? "").length);

const color = computed(() => {
  switch (props.type) {
  case "error":
    return getCssColor("color-report-step-message-error");
  case "fixit":
    return getCssColor("color-report-step-message-fixit");
  case "macro":
    return getCssColor("color-report-step-message-macro");
  case "note":
    return getCssColor("color-report-step-message-note");
  default:
    return getCssColor("color-report-step-message-default");
  }
});
</script>

<style scoped>
/* Three digits fit the round badge only in a smaller font; from four on the
   badge grows into a pill, so the number never touches its edge. */
.report-step-enum-icon--long .text-caption {
  font-size: 10px !important;
  letter-spacing: 0 !important;
}

.report-step-enum-icon--wide {
  padding: 0 5px;
}
</style>
