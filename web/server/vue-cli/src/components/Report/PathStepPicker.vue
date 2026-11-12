<template>
  <v-menu
    :model-value="modelValue"
    :target="target"
    location="bottom start"
    offset="4"
    scroll-strategy="close"
    max-width="440"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card class="path-step-picker" variant="flat" rounded="sm">
      <div class="picker-title px-3 pt-2 pb-1 text-caption">
        {{ title }}: {{ steps.length }} Path Steps
      </div>
      <v-divider />
      <v-list class="picker-list" density="compact">
        <v-list-item
          v-for="step in steps"
          :key="step.key"
          class="picker-item"
          @click="emit('select', step)"
        >
          <div class="picker-row">
            <span class="picker-label">{{ step.label }}</span>
            <span class="picker-message">{{ step.message }}</span>
          </div>
          <div
            v-if="step.values"
            class="picker-values"
            :title="step.valuesTitle"
          >
            {{ step.values }}
          </div>
        </v-list-item>
      </v-list>
      <template v-if="canShowGraph">
        <v-divider />
        <v-btn
          class="picker-graph-btn"
          variant="text"
          size="small"
          block
          prepend-icon="mdi-graph-outline"
          @click="emit('open-graph')"
        >
          Choose in the control flow graph
        </v-btn>
      </template>
      <template v-if="canShowStates">
        <v-divider v-if="!canShowGraph" />
        <v-btn
          class="picker-graph-btn"
          variant="text"
          size="small"
          block
          prepend-icon="mdi-layers-outline"
          @click="emit('open-states')"
        >
          Choose by analyzer state
        </v-btn>
      </template>
    </v-card>
  </v-menu>
</template>

<script setup>
defineProps({
  modelValue: { type: Boolean, required: true },
  target: { type: Array, default: null },
  title: { type: String, default: "" },
  steps: { type: Array, required: true },
  canShowGraph: { type: Boolean, default: false },
  canShowStates: { type: Boolean, default: false }
});

const emit = defineEmits([
  "update:modelValue", "select", "open-graph", "open-states"
]);
</script>

<style lang="scss">
.path-step-picker {
  border: 1px solid #d8dbe0;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);

  .picker-title {
    color: rgba(0, 0, 0, 0.6);
  }

  .picker-list {
    max-height: 280px;
    overflow-y: auto;
    padding: 0;
  }

  .picker-item {
    min-height: 0;
    padding: 4px 12px;
  }

  .picker-row {
    display: flex;
    gap: 8px;
    align-items: baseline;
    font-size: 0.8125rem;
    line-height: 1.3;
  }

  .picker-label {
    flex: none;
    font-weight: 600;
    white-space: nowrap;
  }

  .picker-message {
    min-width: 0;
    overflow-wrap: anywhere;
  }

  .picker-graph-btn {
    justify-content: flex-start;
    text-transform: none;
    letter-spacing: normal;
  }

  .picker-values {
    font-family: monospace;
    font-size: 0.75rem;
    color: rgba(0, 0, 0, 0.6);
    overflow-wrap: anywhere;
  }
}
</style>
