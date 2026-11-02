(function () {
  "use strict";
  const q = selector => document.querySelector(selector);
  const labels = {nexus: "NEXUS", hermes: "HERMES", aether: "AETHER", gpt: "NEXUS GPT"};
  let partner = localStorage.getItem("nexus_phase2_partner") || "nexus";
  let mode = localStorage.getItem("nexus_chat_mode") === "group" ? "group" : "single";
  if (!labels[partner]) partner = "nexus";

  function select(id) {
    if (!labels[id]) return;
    partner = id;
    window.NEXUS_PHASE2_PARTNER = id;
    localStorage.setItem("nexus_phase2_partner", id);
    document.querySelectorAll(".nx-partner").forEach(button => {
      button.classList.toggle("active", button.dataset.partner === id);
      button.setAttribute("aria-pressed", String(button.dataset.partner === id));
    });
    // AETHER always uses its separate local conversation.
    window.NEXUS_CHAT_MODE = id === "aether" ? "single" : mode;
    const input = q("#nxInput"), state = q("#nxRoomState");
    const group = window.NEXUS_CHAT_MODE === "group";
    if (input) input.placeholder = group ? "Nachricht an HERMES, NEXUS und NEXUS GPT…" : "Nachricht an " + labels[id] + "…";
    if (state) state.textContent = group ? "Gemeinsamer Chat" : labels[id] + " aktiv";
    const chooser = q("#nxChatMode");
    if (chooser) chooser.value = window.NEXUS_CHAT_MODE;
  }

  function status(id, ok, text) {
    const element = q("#nxPartnerStatus-" + id);
    if (!element) return;
    element.className = "nx-partner-status" + (ok === null ? "" : ok ? " ok" : " err");
    element.textContent = text;
  }

  async function probe() {
    try {
      const response = await fetch("/api/health");
      status("nexus", response.ok, response.ok ? "Dienst erreichbar" : "nicht erreichbar");
    } catch (_) { status("nexus", false, "nicht erreichbar"); }
    try {
      const response = await fetch("/api/hermes/health"), data = await response.json();
      const ok = response.ok && data.reachable === true;
      status("hermes", ok, ok ? "CLI-Voraussetzungen erfüllt" : "nicht bereit");
    } catch (_) { status("hermes", false, "nicht erreichbar"); }
    try {
      const response = await fetch("/api/aether/health"), data = await response.json();
      const ok = !!(response.ok && data.partner === "aether" && data.provider === "ollama" && data.model === "qwen3.5:9b" && data.success === true);
      status("aether", ok, ok ? "lokal verbunden" : "nicht bereit");
    } catch (_) { status("aether", false, "nicht erreichbar"); }
    // Local AI status is not a cloud readiness proof.
    const gpt = q("#nxPartnerStatus-gpt");
    if (gpt && !gpt.dataset.responseChecked) status("gpt", null, "OpenAI · beim Senden prüfen");
  }

  function syncSpeaking() {
    const speaking = !!(window.voice && window.voice.speaking);
    const speaker = window.voice && window.voice.speakingPartner;
    if (window.NEXUS_AVATARS) {
      if (speaking && speaker) window.NEXUS_AVATARS.setExclusive(speaker, "speaking");
      else document.querySelectorAll(".nx-partner").forEach(button => {
        if (button.dataset.avatarState === "speaking") window.NEXUS_AVATARS.setState(button.dataset.partner, "idle");
      });
    } else document.querySelectorAll(".nx-partner").forEach(button => button.classList.toggle("nx-speaking", speaking && button.dataset.partner === speaker));
    requestAnimationFrame(syncSpeaking);
  }

  window.NEXUS_PHASE2_PARTNER = partner;
  window.NEXUS_PHASE2_LABELS = labels;
  document.addEventListener("DOMContentLoaded", () => {
    if (window.NEXUS_AVATARS) window.NEXUS_AVATARS.init();
    document.querySelectorAll(".nx-partner").forEach(button => button.addEventListener("click", () => select(button.dataset.partner)));
    const chooser = q("#nxChatMode");
    if (chooser) chooser.addEventListener("change", () => {
      mode = chooser.value === "group" ? "group" : "single";
      localStorage.setItem("nexus_chat_mode", mode);
      select(mode === "group" && partner === "aether" ? "nexus" : partner);
    });
    select(partner);
    probe();
    setInterval(probe, 30000);
    requestAnimationFrame(syncSpeaking);
    const room = q("#nxAiRoom"), thread = q("#nxThread");
    if (room && thread && window.ResizeObserver) new ResizeObserver(() => {
      thread.style.setProperty("--nx-room-height", (room.offsetHeight + 24) + "px");
    }).observe(room);
  });
  window.addEventListener("nexus:partner-state", event => {
    const detail = event.detail || {};
    if (!labels[detail.partner]) return;
    document.querySelectorAll('.nx-partner[data-partner="' + detail.partner + '"]').forEach(card => {
      card.dataset.workState = detail.state || "idle";
    });
    if (detail.state === "thinking") {
      if (window.NEXUS_AVATARS) window.NEXUS_AVATARS.setExclusive(detail.partner, "thinking");
    } else if (detail.state !== "speaking" && window.NEXUS_AVATARS) window.NEXUS_AVATARS.setState(detail.partner, "idle");
    if (detail.result) {
      status(detail.partner, detail.state === "success", detail.state === "success" ? "Antwort erhalten" : "Antwort fehlgeschlagen");
      const element = q("#nxPartnerStatus-" + detail.partner);
      if (element) { element.dataset.responseChecked = "true"; element.title = detail.result.error || ""; }
    }
  });
})();
