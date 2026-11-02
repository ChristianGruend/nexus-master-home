/* 005D.54A — shared chat dispatch contract. No UI ownership, no TTS ownership. */
(function () {
  "use strict";
  const VALID_PARTNERS = new Set(["nexus", "hermes", "aether", "gpt"]);
  function request(detail) {
    if (typeof detail === "string") return { text: detail.trim(), partner: null, source: "external" };
    detail = detail || {};
    const partner = VALID_PARTNERS.has(detail.partner) ? detail.partner : null;
    return { text: String(detail.text || "").trim(), partner, source: String(detail.source || "external") };
  }
  function aetherProvenance(result) {
    return !!(result && result.partner === "aether" && result.provider === "ollama" && result.model === "qwen3.5:9b" && result.reply);
  }
  // Resolve server provenance before the selected recipient. Delegation can change
  // the answering speaker; response text itself is never used as identity proof.
  function speaker(result, fallback = "nexus") {
    result = result || {};
    const data = result.data || {};
    const sources = [result.source || data.source, result.agent || data.agent].map(v => String(v || "").toLowerCase());
    if (sources.some(v => v === "hermes" || v.startsWith("hermes_brain"))) return "hermes";
    if (sources.includes("aether")) return "aether";
    if ((result.mode || data.mode) === "cloud/openai" || sources.includes("openai_orchestrator") ||
        String(result.provider || data.provider || "").toLowerCase() === "openai") return "gpt";
    const partner = String(result.partner || data.partner || "").toLowerCase();
    if (VALID_PARTNERS.has(partner)) return partner;
    return VALID_PARTNERS.has(fallback) ? fallback : "nexus";
  }
  window.NEXUS_CHAT_PIPELINE = Object.freeze({ request, aetherProvenance, speaker });
})();
