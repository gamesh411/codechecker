<template>
  <v-chip
    small
    class="report-step-msg"
    :color="color"
    :text-color="textColor"
    :step-id="id"
    :type="type"
    :style="{ 'margin-left': marginLeft }"
  >
    <report-step-enum-icon
      :type="type"
      :index="index"
      class="mr-1"
    />

    <v-btn
      v-if="prevStep"
      left
      text
      icon
      small
      @click="showPrevReport"
    >
      <v-icon>
        mdi-chevron-left
      </v-icon>
    </v-btn>
    

    {{ value }}

    <v-btn
      v-if="nextStep"
      right
      text
      icon
      small
      @click="showNextReport"
    >
      <v-icon>
        mdi-chevron-right
      </v-icon>
    </v-btn>
  </v-chip>
</template>

<script>
import { ReportStepEnumIcon } from "@/components/Icons";

export default {
  name: "EditorBugStep",
  components: {
    ReportStepEnumIcon
  },
  props: {
    id: { type: [ String, Number ], required: true },
    value: { type: String, required: true },
    marginLeft: { type: String, default: "" },
    type: { type: String, default: null },
    index: { type: [ Number, String ], default: null },
    bus: { type: Object, default: null },
    prevStep: { type: Object, default: null },
    nextStep: { type: Object, default: null }
  },
  computed: {
    color() {
      switch (this.type) {
      case "error":
        return "#f2dede";
      case "fixit":
        return "#fcf8e3";
      case "macro":
        return "#d7dac2";
      case "note":
        return "#d7d7d7";
      default:
        return "#bfdfe9";
      }
    },
    textColor() {
      switch (this.type) {
      case "error":
        return "#a94442";
      case "fixit":
        return "#8a6d3b";
      case "macro":
        return "#4f5c6d";
      case "note":
        return "#4f5c6d";
      default:
        return "#00546f";
      }
    }
  },

  methods: {
    showPrevReport() {
      if (this.prevStep && this.bus)
        this.bus.$emit("jpmToPrevReport", this.prevStep);
    },

    showNextReport() {
      if (this.nextStep && this.bus)
        this.bus.$emit("jpmToNextReport", this.nextStep);
    }
  }
};
</script>

<style type="scss">
.report-step-msg {
  padding-top: 2px;
  padding-bottom: 2px;
  margin-bottom: 2px;
}
</style>
