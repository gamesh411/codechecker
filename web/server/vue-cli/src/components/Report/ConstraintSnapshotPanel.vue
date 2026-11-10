<template>
  <v-card
    class="constraint-snapshot-panel d-flex flex-column"
    variant="flat"
    rounded="0"
  >
    <div class="panel-header d-flex align-center px-3 py-2">
      <span class="text-subtitle-2">Constraint Snapshot</span>
      <v-spacer />
      <v-btn
        class="panel-close-btn"
        icon="mdi-close"
        variant="text"
        size="small"
        density="compact"
        aria-label="Close constraint snapshot"
        title="Close"
        @click="emit('close')"
      />
    </div>

    <v-divider />

    <div class="panel-step px-3 py-2 text-body-2">
      <span class="panel-step-index text-medium-emphasis mr-1">
        Step {{ step.index }}
      </span>
      {{ step.message }}
    </div>

    <v-divider />

    <div
      v-if="step.symbolRanges.length"
      class="panel-ranges flex-grow-1 px-3 py-2"
    >
      <div
        v-for="range in step.symbolRanges"
        :key="range.symbol"
        class="panel-range mb-2"
      >
        <div
          class="panel-range-symbol text-body-2"
          :title="range.symbol"
        >
          {{ range.symbol }}
        </div>
        <div class="panel-range-value text-caption text-medium-emphasis">
          {{ range.range }}
        </div>
      </div>
    </div>
    <p
      v-else
      class="panel-empty flex-grow-1 ma-0 px-3 py-2 text-body-2
        text-medium-emphasis"
    >
      No symbol ranges are known at this step.
    </p>
  </v-card>
</template>

<script setup>
defineProps({
  step: { type: Object, required: true }
});

const emit = defineEmits([ "close" ]);
</script>

<style lang="scss">
.constraint-snapshot-panel {
  border-top: 1px solid #d8dbe0;
  border-right: 1px solid #d8dbe0;
  border-bottom: 1px solid #d8dbe0;

  .panel-header {
    background-color: #f7f7f7;
  }

  .panel-step-index {
    font-weight: 500;
  }

  .panel-ranges {
    overflow-y: auto;
  }

  .panel-range-symbol,
  .panel-range-value {
    font-family: monospace;
    overflow-wrap: anywhere;
  }
}
</style>
