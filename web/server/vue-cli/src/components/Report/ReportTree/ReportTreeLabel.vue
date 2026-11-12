<template>
  <span :data-id="item.id" class="text-caption">
    <span v-if="item.kind === ReportTreeKind.REPORT">
      L{{ item.report.line }} &ndash; {{ item.report.checkerId }}
      [{{ item.report.bugPathLength }}]
    </span>

    <span
      v-else-if="item.kind === ReportTreeKind.REPORT_STEPS"
      :title="reportStepContent"
    >
      <v-icon
        v-if="item.silentChange"
        class="silent-change-mark"
        size="14"
        :title="SILENT_CHANGE_HINT"
        :aria-label="SILENT_CHANGE_HINT"
      >
        mdi-help-rhombus-outline
      </v-icon>
      {{ reportStepContent }}
    </span>

    <span v-else-if="item.kind === ReportTreeKind.BUG">
      <b><u>{{ item.name }}</u></b>
    </span>

    <span v-else-if="isExtendedReportData">
      <b>{{ item.name }}</b>
    </span>

    <span v-else-if="isExtendedReportDataItem">
      L{{ item.data.startLine }} &ndash; {{ item.name }}
    </span>
    <span v-else-if="item.kind === ReportTreeKind.SEVERITY_LEVEL">
      {{ item.name }}
      <span v-if="newReportCount" style="color: var(--color-soft-red);">
        {{ newReportCountLabel }}
      </span>
    </span>
    <span v-else>
      {{ item.name }}
    </span>
  </span>
</template>

<script setup>
import { DetectionStatus } from "@cc/report-server-types";
import { computed } from "vue";
import ReportTreeKind from "./ReportTreeKind";

const props = defineProps({
  item: { type: Object, required: true }
});

const SILENT_CHANGE_HINT = "Next to this step the analyzer assumed " +
  "something that the Bug Path does not describe";

const reportStepContent = computed(function() {
  return `${fileName.value}${props.item.step.startLine} ` +
    `- ${props.item.step.msg}`;
});

const fileName = computed(function() {
  return props.item.fileName ? `${props.item.fileName}:` : "L";
});

const isExtendedReportData = computed(function() {
  return props.item.kind === ReportTreeKind.MACRO_EXPANSION ||
         props.item.kind === ReportTreeKind.NOTE;
});

const isExtendedReportDataItem = computed(function() {
  return props.item.kind === ReportTreeKind.MACRO_EXPANSION_ITEM ||
         props.item.kind === ReportTreeKind.NOTE_ITEM;
});

const newReportCount = computed(function() {
  if (props.item && props.item.kind === ReportTreeKind.SEVERITY_LEVEL) {
    return props.item.children.filter(element => {
      return element.report.detectionStatus == DetectionStatus.NEW;
    }).length;
  }
  return 0;
});

const newReportCountLabel = computed(function() {
  return ` [${newReportCount.value} new]`;
});
</script>

<style lang="scss" scoped>
.silent-change-mark {
  margin-right: 3px;
  color: #a67c00;
  vertical-align: text-bottom;
  cursor: help;
}
</style>
