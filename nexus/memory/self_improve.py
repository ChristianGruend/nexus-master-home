"""Daily / on-demand Self-Improve Loop - lightweight, no test marathon."""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from nexus.common.paths import APEX, LOGS, ROOT, ensure_dirs

QUEUE = APEX / "SELF_IMPROVE_QUEUE.md"
STAMP = APEX / "SELF_IMPROVE_LAST.json"
PROMPT = APEX / "NEXUS_SELBSTVERBESSERUNG_PROMPT.md"
PROMPT_ROOT = ROOT / "NEXUS_SELBSTVERBESSERUNG_PROMPT.md"
KNOWLEDGE = APEX / "PROJECT_KNOWLEDGE.md"
CORRECTIONS = APEX / "eddie_corrections.json"
ARBEITSPLAN = ROOT / "NEXUS_SELBSTENTWICKLUNG_ARBEITSPLAN.md"
ARBEITSPLAN_APEX = APEX / "NEXUS_SELBSTENTWICKLUNG_ARBEITSPLAN.md"

# Chat / Nightly triggers (decision.py also matches)
_TRIGGER = re.compile(
    r"\b("
    r"verbessere\s+dich|selbst\s*verbesser|selbstverbesser|"
    r"improvement\s*loop|daily\s*improve|tägliche?\s+verbesser|"
    r"taegliche?\s+verbesser|lern\s+aus\s+korrekturen|"
    r"schliesse\s+lücken|schließe\s+lücken|capability\s*gap|"
    r"werde\s+besser|nightly\s*check|selbst\s*check\s*loop"
    r")\b",
    re.I,
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def is_self_improve_message(message: str) -> bool:
    raw = (message or "").strip()
    if not raw:
        return False
    return bool(_TRIGGER.search(raw))


def wants_apply(message: str) -> bool:
    low = (message or "").lower()
    return bool(
        re.search(
            r"\b(anwenden|apply|umsetzen|fix\s+jetzt|sofort\s+fix|"
            r"sichere?\s+fixes?|safe\s+fix)\b",
            low,
        )
    )


class SelfImproveLoop:
    """Scan corrections + smokes/logs → Queue; optional tiny safe applies."""

    def __init__(self) -> None:
        ensure_dirs()

    def _load_stamp(self) -> dict:
        if not STAMP.exists():
            return {}
        try:
            return json.loads(STAMP.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_stamp(self, data: dict) -> None:
        STAMP.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def due_nightly(self, hours: float = 20.0) -> bool:
        stamp = self._load_stamp()
        last = stamp.get("last_run")
        if not last:
            return True
        try:
            dt = datetime.fromisoformat(last)
        except Exception:
            return True
        return datetime.now() - dt >= timedelta(hours=hours)

    def _read_corrections(self, limit: int = 20) -> list[dict]:
        if not CORRECTIONS.exists():
            return []
        try:
            items = json.loads(CORRECTIONS.read_text(encoding="utf-8")).get("items", [])
        except Exception:
            return []
        return list(reversed(items))[:limit]

    def _recent_smokes(self, limit: int = 8) -> list[dict]:
        out = []
        if not APEX.exists():
            return out
        files = sorted(
            APEX.glob("SMOKE_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]
        for p in files:
            entry = {"name": p.name, "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    entry["ok"] = data.get("ok", data.get("success", data.get("pass")))
                    entry["summary"] = str(data.get("summary") or data.get("result") or data.get("status") or "")[:160]
                else:
                    entry["ok"] = None
                    entry["summary"] = ""
            except Exception as e:
                entry["ok"] = None
                entry["summary"] = f"parse_error:{e}"
            out.append(entry)
        return out

    def _log_hints(self, limit: int = 6) -> list[str]:
        hints = []
        if not LOGS.exists():
            return hints
        files = sorted(LOGS.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:8]
        for p in files:
            if not p.is_file() or p.stat().st_size > 400_000:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for line in text.splitlines()[-80:]:
                low = line.lower()
                if re.search(r"\b(error|traceback|exception|failed|fail|warn(ing)?)\b", low) and "\"success\": true" not in low and '"success": true' not in low:
                    hints.append(f"{p.name}: {line.strip()[:140]}")
                    if len(hints) >= limit:
                        return hints
        return hints

    def _propose(self, corrections: list, smokes: list, logs: list) -> list[dict]:
        props: list[dict] = []
        # Corrections → behavior rules as proposals (document / reinforce)
        for c in corrections[:12]:
            kind = c.get("kind") or "note"
            text = (c.get("text") or "").strip()
            if not text:
                continue
            props.append(
                {
                    "id": f"CORR-{c.get('id', '?')}",
                    "priority": "high" if kind in ("always", "never") else "medium",
                    "source": "eddie_corrections",
                    "action": "context_enforce",
                    "safe": True,
                    "detail": f"Korrektur [{kind}] dauerhaft beachten: {text}",
                }
            )
        # Failed / weak smokes
        for s in smokes:
            ok = s.get("ok")
            if ok is False or (isinstance(ok, str) and ok.lower() in ("fail", "failed", "error", "false")):
                props.append(
                    {
                        "id": f"SMOKE-{s['name']}",
                        "priority": "high",
                        "source": "smoke",
                        "action": "investigate_fix",
                        "safe": False,
                        "detail": f"Smoke schwach/fehlgeschlagen ({s['name']} @ {s.get('mtime')}): {s.get('summary') or 'kein Summary'} — gezielt nachbessern, kein Test-Marathon.",
                    }
                )
        for h in logs:
            props.append(
                {
                    "id": f"LOG-{abs(hash(h)) % 10000:04d}",
                    "priority": "medium",
                    "source": "logs",
                    "action": "review_log",
                    "safe": False,
                    "detail": h,
                }
            )
        # Standing Grok-parity backlog (from knowledge / prompt)
        gap_defaults = [
            ("GAP-MCP", "medium", "Externe Dienste (Gmail/Drive/Calendar/GitHub): vorhandene Bridges prüfen, fehlende verdrahten — keine Fake-Buttons."),
            ("GAP-PLAN", "high", "Autonomer Arbeitsplan: Auftrag → Plan (3–7 Schritte) → Tools → Validieren → Chat-Report, ohne Rückfragen."),
            ("GAP-BROWSER", "low", "Browser/GUI-Tiefe: Screen+Vision nutzen; bei Bedarf gezielte OS-Aktionen statt Simulation."),
            ("GAP-LOOP", "high", "Self-Improve Loop aktiv halten: Queue abarbeiten, Korrekturen injizieren, nach Builds Self-Check."),
        ]
        for gid, prio, detail in gap_defaults:
            props.append(
                {
                    "id": gid,
                    "priority": prio,
                    "source": "capability_gap",
                    "action": "close_gap",
                    "safe": False,
                    "detail": detail,
                }
            )
        # Dedupe by id
        seen = set()
        uniq = []
        for p in props:
            if p["id"] in seen:
                continue
            seen.add(p["id"])
            uniq.append(p)
        return uniq

    def _write_queue(self, proposals: list[dict], meta: dict) -> Path:
        lines = [
            "# NEXUS Self-Improve Queue",
            "",
            f"**Stand:** {meta.get('last_run')} (Europe/Berlin)",
            f"**Trigger:** {meta.get('trigger', 'manual')}",
            f"**Apply-safe:** {meta.get('apply_safe', False)}",
            "",
            "Vorschläge aus Korrekturen, Smokes/Logs und Capability-Gaps.",
            f"Regel: echte Fixes unter `{ROOT}`, keine Simulation, kein Test-Marathon, kein Warez.",
            "",
            "## Offene Punkte",
            "",
        ]
        if not proposals:
            lines.append("_Keine neuen Punkte._")
        else:
            for p in proposals:
                safe = "safe" if p.get("safe") else "needs-build"
                lines.append(
                    f"- **{p['id']}** [{p.get('priority')}|{safe}|{p.get('source')}] "
                    f"`{p.get('action')}` — {p.get('detail')}"
                )
        lines.extend(
            [
                "",
                "## Nächste Schritte (NEXUS)",
                "1. High/safe zuerst umsetzen (Korrekturen sind schon im Kontext).",
                "2. needs-build über bestehenden Selfbuild-/Tool-Pfad (write_file / powershell / run_script).",
                "3. Nach Fix: ein Health-Check, Chat-Kurzreport.",
                "4. Queue-Einträge nach Erledigung hier als ~~done~~ markieren oder entfernen.",
                "",
                "## Verweise",
                "- `NEXUS_SELBSTVERBESSERUNG_PROMPT.md`",
                "- `PROJECT_KNOWLEDGE.md` (Capability-Gap)",
                "- `eddie_corrections.json`",
                "",
            ]
        )
        QUEUE.write_text("\n".join(lines), encoding="utf-8")
        return QUEUE

    def _apply_safe(self, proposals: list[dict]) -> list[str]:
        """Vertrag fuer produktive Selbstveraenderung: bleibt blockiert.

        NEXUS_SELF_IMPROVE_VERIFY_V1:
        Code- und System-Aenderungen werden hier nicht angewendet.
        Der Marker enthaelt "_skip:" und gilt deshalb in run() als
        apply_failure. Ohne lokale Pruefung und Freigabe gibt es
        keinen Verbesserungs-PASS.
        """
        return ["activation_skip:LOCAL_REVIEW_AND_APPROVAL_REQUIRED"]

    def _sync_documents(self) -> list[str]:
        """Read-back-verifizierte Spiegelung der Self-Improve-Dokumente.

        Nur Markdown-Dokumente, kein Code und keine produktiven
        Systemdateien. Marker:
          - "verified:" = geschrieben und Read-back identisch
          - "_skip:"    = Schreib-, Lesefehler oder Read-back-Mismatch
        """
        done: list[str] = []
        try:
            src = None
            if PROMPT_ROOT.is_file():
                src = PROMPT_ROOT
            elif PROMPT.is_file():
                src = PROMPT
            if src is not None:
                text = src.read_text(encoding="utf-8")
                for target, label in (
                    (PROMPT, f"{PROMPT.name} -> apex"),
                    (PROMPT_ROOT, f"{PROMPT_ROOT.name} -> root"),
                ):
                    try:
                        if (
                            target.is_file()
                            and target.read_text(encoding="utf-8") == text
                        ):
                            continue
                    except Exception:
                        pass
                    try:
                        target.write_text(text, encoding="utf-8")
                    except Exception as exc:
                        done.append(
                            f"_skip: {label} Schreibvorgang fehlgeschlagen: {exc}"
                        )
                        continue
                    try:
                        back = target.read_text(encoding="utf-8")
                    except Exception as exc:
                        done.append(
                            f"_skip: {label} Read-back nicht lesbar: {exc}"
                        )
                        continue
                    if back == text:
                        done.append(f"verified: {label} (Read-back identisch)")
                    else:
                        done.append(f"_skip: {label} Read-back-Mismatch")
        except Exception as exc:
            done.append(f"_skip: prompt_sync_aborted: {exc}")
        try:
            if ARBEITSPLAN.is_file():
                body = ARBEITSPLAN.read_text(encoding="utf-8")
                try:
                    needs_write = (
                        not ARBEITSPLAN_APEX.is_file()
                    ) or ARBEITSPLAN_APEX.read_text(encoding="utf-8") != body
                except Exception:
                    needs_write = True
                if needs_write:
                    label = f"{ARBEITSPLAN.name} -> apex"
                    try:
                        ARBEITSPLAN_APEX.write_text(body, encoding="utf-8")
                    except Exception as exc:
                        done.append(
                            f"_skip: {label} Schreibvorgang fehlgeschlagen: {exc}"
                        )
                    else:
                        try:
                            back = ARBEITSPLAN_APEX.read_text(encoding="utf-8")
                        except Exception as exc:
                            done.append(
                                f"_skip: {label} Read-back nicht lesbar: {exc}"
                            )
                        else:
                            if back == body:
                                done.append(
                                    f"verified: {label} (Read-back identisch)"
                                )
                            else:
                                done.append(f"_skip: {label} Read-back-Mismatch")
        except Exception as exc:
            done.append(f"_skip: arbeitplan_sync_aborted: {exc}")
        return done

    def run(self, *, trigger: str = "manual", apply_safe: bool = False) -> dict:
        ensure_dirs()
        corrections = self._read_corrections()
        smokes = self._recent_smokes()
        logs = self._log_hints()
        proposals = self._propose(corrections, smokes, logs)
        meta = {
            "last_run": _now(),
            "trigger": trigger,
            "apply_safe": apply_safe,
            "counts": {
                "corrections": len(corrections),
                "smokes": len(smokes),
                "log_hints": len(logs),
                "proposals": len(proposals),
            },
        }
        path = self._write_queue(proposals, meta)
        applied: list[str] = []
        if apply_safe:
            applied = self._sync_documents()
        meta["applied"] = applied
        meta["queue"] = str(path)
        self._save_stamp(meta)
        reply_lines = [
            "Self-Improve Loop gelaufen (echt, kein Theater).",
            f"Trigger: {trigger} · Vorschläge: {len(proposals)} · Queue: workspace/apex/SELF_IMPROVE_QUEUE.md",
        ]
        high = [p for p in proposals if p.get("priority") == "high"][:5]
        if high:
            reply_lines.append("Top-Punkte:")
            for p in high:
                reply_lines.append(f"- {p['id']}: {p['detail'][:120]}")
        if applied:
            reply_lines.append("Safe applies: " + "; ".join(applied))
        else:
            reply_lines.append(
                "Vorschläge sind PLANNED. Produktive Aktivierung erst nach isolierter Prüfung und lokaler Freigabe. "
                "oder gib einen Build-Auftrag für needs-build Items."
            )
        reply_lines.append("Capability-Gaps + Bau-Schritte: PROJECT_KNOWLEDGE.md / NEXUS_SELBSTVERBESSERUNG_PROMPT.md")
        apply_failures = [x for x in applied if "_skip:" in x]
        verified_changes = [
            x for x in applied if x.startswith("verified:")
        ]
        applied_ok = [x for x in applied if "_skip:" not in x]
        proposed = bool(proposals)
        applied_any = bool(applied_ok)
        verified = bool(verified_changes) and not apply_failures

        if apply_failures:
            reply_lines.insert(
                0,
                "BLOCKED: Mindestens ein Safe-Apply-Schritt ist fehlgeschlagen.",
            )
            success = False
            error_code = "safe_apply_partial_failure"
        elif apply_safe and not verified:
            reply_lines.insert(
                0,
                "BLOCKED: Keine per Read-back verifizierte Aenderung. "
                "Kein Verbesserungs-PASS ohne Beleg.",
            )
            success = False
            error_code = "no_verified_improvement"
        elif proposed and not applied_any:
            if apply_safe:
                reply_lines.insert(
                    0,
                    "BLOCKED: Vorschlaege wurden erstellt, aber keine "
                    "Aenderung wurde angewendet.",
                )
            success = not apply_safe
            error_code = (
                "no_verified_improvement" if apply_safe else None
            )
        else:
            success = True
            error_code = None

        result = {
            "proposed": proposed,
            "proposal_count": len(proposals),
            "applied": applied_ok,
            "applied_changes": verified_changes,
            "verified_changes": verified_changes,
            "apply_failures": apply_failures,
            "verified": verified,
        }
        return {
            "success": success,
            "reply": "\n".join(reply_lines),
            "error": error_code,
            # Top-Level-Evidence für den Controller-Gate: nur
            # read-back-bestätigte Einträge, niemals reine Behauptungen.
            "applied_changes": verified_changes,
            "result": result,
            "proposals": proposals,
            "meta": meta,
            "queue": str(path),
        }


def self_improve_prompt_snippet(limit_chars: int = 6000) -> str:
    """Load Dauerauftrag prompt for system context."""
    ensure_dirs()
    path = PROMPT if PROMPT.is_file() else PROMPT_ROOT
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    if not text:
        return ""
    if len(text) > limit_chars:
        text = text[:limit_chars] + "\n…"
    return "Selbstverbesserung (Dauerauftrag, verbindlich):\n" + text
