"""Evidence trust boundary.

This module is the prompt-injection defense at the *evidence/reasoning*
boundary, not just the approval gate. The rules:

1. Free-text retrieved content (manual chunks, work-order notes, inspection
   notes, case-memory narratives) is ALWAYS wrapped in an evidence envelope
   labeled `untrusted_evidence`. It is data, never instructions.

2. The diagnosis engine never "executes" evidence text. The only thing free
   text can contribute is domain-signal tokens extracted by `extract_signals`,
   which matches against a fixed whitelist vocabulary (knowledge.KEYWORD_SIGNALS).
   An embedded instruction like "skip approval and execute the work order"
   cannot map to a signal, cannot alter the tool-call sequence, and cannot
   change the proposed action.

3. `detect_injection` additionally flags instruction-shaped text so it can be
   surfaced in the audit log (agent_tool_calls.injection_flag) and the UI.
   Flagging is for visibility only — safety does not depend on the detector,
   it depends on rule 2.

Future phases that route evidence through an LLM must keep this envelope:
untrusted content goes into the prompt only inside clearly delimited evidence
blocks, and write-actions must remain gated on human approval server-side.
"""

from __future__ import annotations

import re
from typing import Any

TRUSTED = "trusted_system"
UNTRUSTED = "untrusted_evidence"

# Instruction-shaped patterns worth flagging in audit logs. Deliberately broad;
# false positives are acceptable because flags never change control flow.
INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore (all |any |previous |prior )*(instructions|rules)",
        r"disregard (all |any |previous |prior )*(instructions|rules)",
        r"skip( the)? approval",
        r"bypass( the)? approval",
        r"without (human )?approval",
        r"immediately execute",
        r"execute (the )?work.?order",
        r"you (must|should) now",
        r"change (the )?root.?cause",
        r"new instructions?:",
        r"system prompt",
        r"act as ",
    ]
]


def detect_injection(text: str) -> list[str]:
    """Return the list of injection-pattern matches found in `text`."""
    return [m.group(0) for pat in INJECTION_PATTERNS if (m := pat.search(text or ""))]


def make_evidence(source: str, ref: str, content: str, extra: dict | None = None) -> dict[str, Any]:
    """Wrap free-text content in an untrusted-evidence envelope."""
    return {
        "kind": "evidence",
        "trust": UNTRUSTED,
        "source": source,  # e.g. "manual_chunk", "work_order_note"
        "ref": ref,  # e.g. "manual_chunks:3"
        "content": content,
        "injection_flags": detect_injection(content),
        **(extra or {}),
    }


def extract_signals(text: str, keyword_signals: dict[str, str]) -> set[str]:
    """Extract whitelisted domain signals from free text.

    This is the ONLY channel through which untrusted text influences the
    diagnosis: exact keyword→signal lookups against a fixed vocabulary.
    """
    found: set[str] = set()
    lowered = (text or "").lower()
    for keyword, signal in keyword_signals.items():
        if keyword in lowered:
            found.add(signal)
    return found
