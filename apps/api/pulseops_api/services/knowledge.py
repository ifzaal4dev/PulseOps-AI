"""Domain knowledge for the deterministic diagnosis engine.

Failure modes are described as weighted signal signatures. Signals come from
two channels:

  * numeric signals — computed from trusted sensor statistics
    (e.g. vibration_high, pressure_drop). See tools.get_recent_sensor_readings.
  * keyword signals — extracted from untrusted free-text evidence via the
    fixed KEYWORD_SIGNALS whitelist (see evidence.extract_signals).

An optional LLM can rerank these causes, but the signature table stays the
deterministic fallback and the eval baseline.
"""

# keyword (lowercase substring) -> signal token. The ONLY vocabulary through
# which free text can influence diagnosis.
KEYWORD_SIGNALS: dict[str, str] = {
    # bearing / mechanical wear
    "bearing": "bearing_keyword",
    "spalling": "bearing_keyword",
    "whine": "bearing_keyword",
    "lubrication": "bearing_keyword",
    "iron particulate": "oil_particulates",
    "particulates": "oil_particulates",
    "oil analysis": "oil_particulates",
    "misalign": "alignment_keyword",
    # refrigerant
    "refrigerant": "refrigerant_keyword",
    "charge loss": "refrigerant_keyword",
    "purge unit": "refrigerant_keyword",
    # condenser
    "fouling": "fouling_keyword",
    "approach temp": "fouling_keyword",
    "condenser": "fouling_keyword",
    # electrical
    "winding": "electrical_keyword",
    "phase imbalance": "electrical_keyword",
    "megger": "electrical_keyword",
    "insulation breakdown": "electrical_keyword",
    # pump-specific
    "seal": "seal_keyword",
    "leak": "leak_keyword",
    "drip": "leak_keyword",
    "residue": "leak_keyword",
    "gland": "seal_keyword",
    "cavitation": "cavitation_keyword",
    "npsh": "cavitation_keyword",
    "impeller": "impeller_keyword",
    # air-compressor-specific (APU)
    "air leak": "air_leak_keyword",
    "hissing": "air_leak_keyword",
    "pneumatic": "air_leak_keyword",
    "dryer": "air_leak_keyword",
    "oil leak": "oil_system_keyword",
    "oil level": "oil_system_keyword",
    "oil cooler": "oil_system_keyword",
    "oil circuit": "oil_system_keyword",
}

# Failure-mode signatures per asset type. `signals` maps signal token -> weight.
# `action` is the maintenance action proposed when this cause ranks first.
FAILURE_MODES: dict[str, list[dict]] = {
    "chiller": [
        {
            "cause": "compressor bearing wear",
            "signals": {
                "vibration_high": 3.0,
                "vibration_ramp": 2.0,
                "pressure_drop": 1.5,
                "power_increase": 1.0,
                "bearing_keyword": 2.0,
                "oil_particulates": 1.5,
            },
            "action": {
                "title": "URGENT: Compressor bearing inspection & vibration spectrum analysis",
                "description": (
                    "Perform vibration spectrum analysis at compressor drive end. Inspect "
                    "drive-end bearing for spalling; replace bearing set and verify "
                    "lubrication supply if wear is confirmed. Laser-align coupling on close-out."
                ),
                "priority": "urgent",
                "specialty": "hvac",
            },
        },
        {
            "cause": "refrigerant charge loss",
            "signals": {"pressure_drop": 2.0, "vibration_low": 1.5, "refrigerant_keyword": 2.0},
            "action": {
                "title": "Refrigerant leak survey and recharge",
                "description": "Electronic leak detection at flanges/relief valve; weigh in charge to spec.",
                "priority": "high",
                "specialty": "hvac",
            },
        },
        {
            "cause": "condenser tube fouling",
            "signals": {"supply_temp_high": 1.5, "fouling_keyword": 2.0},
            "action": {
                "title": "Condenser tube cleaning",
                "description": "Brush-clean condenser tubes; verify approach temperature recovery.",
                "priority": "medium",
                "specialty": "hvac",
            },
        },
        {
            "cause": "motor electrical fault",
            "signals": {"power_anomaly": 1.0, "electrical_keyword": 2.0},
            "action": {
                "title": "Motor electrical testing (megger + phase balance)",
                "description": "Insulation resistance test and phase-current balance check before restart.",
                "priority": "high",
                "specialty": "electrical",
            },
        },
    ],
    # APU air compressor (MetroPT replay asset). PROVENANCE: the four real
    # MetroPT-3 failure reports are ALL air leaks; the oil-system mode below
    # is SYNTHETIC facility knowledge for evaluation breadth and must never
    # be described as MetroPT-3 ground truth.
    "air_compressor": [
        {
            "cause": "compressed-air leak",
            "signals": {
                "air_pressure_drop": 2.5,
                "compressor_duty_high": 2.0,
                "motor_current_high": 1.5,
                "air_leak_keyword": 2.0,
                "leak_keyword": 1.0,
                "air_leak_confirmed": 4.0,
            },
            "contradicted_by": ["air_leak_not_detected"],
            "action": {
                "title": "Locate and seal compressed-air leak",
                "description": (
                    "Soap-test pneumatic circuit, dryer towers, and reservoir "
                    "connections; verify unloader/drain valves seat; confirm duty "
                    "cycle returns to baseline after repair."
                ),
                "priority": "high",
                "specialty": "hvac",
            },
        },
        {
            "cause": "oil-system abnormality (synthetic scenario)",
            "signals": {
                "oil_temp_high": 2.5,
                "oil_system_keyword": 2.0,
                "motor_current_high": 1.0,
                # Independent evidence only: temperature never creates these.
                "oil_quality_bad": 3.0,
                "oil_pressure_abnormal": 2.5,
                "oil_cooler_abnormal": 2.5,
            },
            "contradicted_by": ["oil_quality_ok", "oil_pressure_ok", "oil_cooler_ok"],
            "action": {
                "title": "Inspect compressor oil circuit",
                "description": (
                    "Check oil level/quality and cooler flow; verify oil-level "
                    "switch; sample for analysis. (Synthetic facility scenario — "
                    "not a MetroPT-3 documented failure mode.)"
                ),
                "priority": "high",
                "specialty": "hvac",
            },
        },
    ],
    "pump": [
        {
            "cause": "mechanical seal failure",
            "signals": {
                "pressure_drop": 2.0,
                "flow_drop": 1.5,
                "leak_keyword": 2.5,
                "seal_keyword": 2.0,
            },
            "action": {
                "title": "Replace mechanical seal cartridge",
                "description": "Replace seal cartridge; inspect shaft sleeve for scoring; verify gland torque.",
                "priority": "high",
                "specialty": "plumbing",
            },
        },
        {
            "cause": "cavitation",
            "signals": {"vibration_high": 1.5, "pressure_unstable": 2.0, "cavitation_keyword": 2.5},
            "action": {
                "title": "NPSH and suction strainer investigation",
                "description": "Verify NPSH margin, inspect suction strainer, check valve lineup.",
                "priority": "high",
                "specialty": "plumbing",
            },
        },
        {
            "cause": "impeller wear",
            "signals": {"flow_drop": 1.5, "impeller_keyword": 2.5},
            "action": {
                "title": "Pump curve test and impeller inspection",
                "description": "Run head/flow curve test; pull and inspect impeller if degraded.",
                "priority": "medium",
                "specialty": "plumbing",
            },
        },
        {
            "cause": "pump bearing wear",
            "signals": {"vibration_high": 2.5, "vibration_ramp": 2.0, "bearing_keyword": 2.0},
            "action": {
                "title": "Pump bearing inspection",
                "description": "Vibration analysis and bearing replacement if confirmed.",
                "priority": "high",
                "specialty": "plumbing",
            },
        },
    ],
}

# ---------------------------------------------------------------------------
# Abstention policy (documented thresholds — see docs/agentic-loop.md).
# The diagnostician must abstain rather than fabricate a work order when the
# evidence does not clearly support a hypothesis.
# ---------------------------------------------------------------------------

# A hypothesis must accumulate at least this much weighted evidence to be
# proposed at all. (For scale: one keyword hit contributes ~1.2–1.5; the
# golden bearing-wear scenario scores ~9.)
MIN_EVIDENCE_SCORE = 2.0

# If the runner-up scores >= this fraction of the leader, the evidence is
# materially tied and the agent must abstain and request discriminating
# evidence instead of guessing.
TIE_RATIO = 0.8


# ---------------------------------------------------------------------------
# Independent diagnostic-evidence requests (next-best-evidence allow-list).
# Results must come from a persisted human/lab/sensor/import observation; the
# agent never manufactures them from telemetry. Operational tests that change
# equipment state remain subject to site procedure and human approval.
# ---------------------------------------------------------------------------


def tests_for(asset_type: str) -> dict[str, dict]:
    """Independent evidence types available for the asset.

    Definitions live in diagnostic_evidence.py beside their typed validation
    and deterministic signal mapping. No test result is synthesized here.
    """
    from .diagnostic_evidence import tests_for as evidence_tests_for

    return evidence_tests_for(asset_type)


def failure_modes_for(asset_type: str) -> list[dict]:
    """Signature table for the asset type. An EMPTY list means the asset type
    is unsupported and the diagnostician must abstain — there is deliberately
    no generic fallback work order."""
    return FAILURE_MODES.get(asset_type, [])
