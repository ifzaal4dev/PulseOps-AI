"""Synthetic facility seed data.

Everything here is SYNTHETIC — no real company data. The seed builds one
mid-size smart facility and two incident-ready assets:

  * Chiller-03 (CH-03) — the golden vertical slice: rising vibration RMS and
    falling discharge pressure over the last 48h (classic compressor bearing
    wear signature), an active critical incident, a prior bearing-wear
    postmortem in case memory, and manual chunks that support the diagnosis.

  * Pump-07 (PU-07) — the second eval scenario: falling discharge pressure
    with a seal-leak inspection note and a seal-leak precedent in case memory.

Determinism matters: the eval harness and tests assert against this data, so
all randomness uses a fixed seed. Run via `python -m pulseops_api.seed.seed_db`.
"""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..db import Base, SessionLocal, engine
from ..models import (
    Asset,
    Building,
    CaseMemory,
    Floor,
    Incident,
    Inspection,
    ManualChunk,
    Room,
    Sensor,
    SensorReading,
    Technician,
    WorkOrder,
)
from ..timeutil import utcnow

READING_INTERVAL_MIN = 30
HISTORY_DAYS = 7


def _series(
    rng: random.Random,
    now: datetime,
    baseline: float,
    noise: float,
    ramp_to: float | None = None,
    ramp_hours: float = 48.0,
    daily_swing: float = 0.0,
) -> list[tuple[datetime, float]]:
    """Generate a reading series: baseline + noise + optional daily cycle,
    with an optional linear ramp toward `ramp_to` over the final `ramp_hours`."""
    points = []
    n = int(HISTORY_DAYS * 24 * 60 / READING_INTERVAL_MIN)
    for i in range(n):
        ts = now - timedelta(minutes=READING_INTERVAL_MIN * (n - 1 - i))
        value = baseline + rng.gauss(0, noise)
        if daily_swing:
            value += daily_swing * math.sin(2 * math.pi * (ts.hour / 24.0))
        if ramp_to is not None:
            hours_from_end = (now - ts).total_seconds() / 3600.0
            if hours_from_end < ramp_hours:
                frac = 1.0 - hours_from_end / ramp_hours
                value += (ramp_to - baseline) * frac
        points.append((ts, round(value, 3)))
    return points


def seed(session: Session, now: datetime | None = None) -> dict:
    """Populate an empty database. Returns a map of key entity ids for tests."""
    now = now or utcnow()
    rng = random.Random(42)

    # --- Facility topology -------------------------------------------------
    hq = Building(
        name="Aurora Tech Campus - Tower A", address="1200 Innovation Way, Blacksburg, VA"
    )
    session.add(hq)
    session.flush()

    floors = {}
    for level, fname in [
        (-1, "Basement Mechanical"),
        (1, "Lobby & Retail"),
        (5, "Data Hall"),
        (9, "Offices"),
    ]:
        f = Floor(building_id=hq.id, level=level, name=fname)
        session.add(f)
        session.flush()
        floors[level] = f

    def room(level: int, name: str, kind: str = "mechanical") -> Room:
        r = Room(floor_id=floors[level].id, name=name, kind=kind)
        session.add(r)
        session.flush()
        return r

    chiller_plant = room(-1, "Central Chiller Plant")
    pump_room = room(-1, "Pump Gallery")
    ahu_room = room(5, "AHU Mezzanine")
    crac_room = room(5, "Data Hall CRAC Row", "server")
    boiler_room = room(-1, "Boiler Room")

    # --- Assets + sensors ---------------------------------------------------
    def asset(tag, name, atype, rm, crit, status="online", mfr="", mdl="", installed="") -> Asset:
        a = Asset(
            tag=tag,
            name=name,
            asset_type=atype,
            room_id=rm.id,
            criticality=crit,
            status=status,
            manufacturer=mfr,
            model=mdl,
            install_date=installed,
        )
        session.add(a)
        session.flush()
        return a

    def sensor(a: Asset, kind: str, unit: str, baseline: float) -> Sensor:
        s = Sensor(asset_id=a.id, kind=kind, unit=unit, baseline_value=baseline)
        session.add(s)
        session.flush()
        return s

    def readings(s: Sensor, pts: list[tuple[datetime, float]]) -> None:
        session.add_all(SensorReading(sensor_id=s.id, ts=ts, value=v) for ts, v in pts)

    # Golden-slice asset: Chiller-03 — degrading bearing signature.
    ch3 = asset(
        "CH-03",
        "Chiller-03",
        "chiller",
        chiller_plant,
        "critical",
        "degraded",
        "Trane",
        "CVHF-1250 Centrifugal",
        "2018-06-14",
    )
    ch3_vib = sensor(ch3, "vibration_rms", "mm/s", 2.2)
    ch3_press = sensor(ch3, "discharge_pressure", "bar", 9.2)
    ch3_temp = sensor(ch3, "supply_temp", "degC", 6.5)
    ch3_power = sensor(ch3, "power_kw", "kW", 310.0)
    readings(ch3_vib, _series(rng, now, 2.2, 0.12, ramp_to=7.6, ramp_hours=48))
    readings(ch3_press, _series(rng, now, 9.2, 0.08, ramp_to=7.3, ramp_hours=48))
    readings(ch3_temp, _series(rng, now, 6.5, 0.15, ramp_to=7.9, ramp_hours=36, daily_swing=0.2))
    readings(
        ch3_power, _series(rng, now, 310.0, 4.0, ramp_to=342.0, ramp_hours=48, daily_swing=6.0)
    )

    # Healthy siblings.
    ch1 = asset(
        "CH-01",
        "Chiller-01",
        "chiller",
        chiller_plant,
        "critical",
        "online",
        "Trane",
        "CVHF-1250 Centrifugal",
        "2018-06-14",
    )
    readings(sensor(ch1, "vibration_rms", "mm/s", 2.1), _series(rng, now, 2.1, 0.1))
    readings(sensor(ch1, "discharge_pressure", "bar", 9.3), _series(rng, now, 9.3, 0.07))
    readings(sensor(ch1, "supply_temp", "degC", 6.4), _series(rng, now, 6.4, 0.12, daily_swing=0.2))

    ch2 = asset(
        "CH-02",
        "Chiller-02",
        "chiller",
        chiller_plant,
        "critical",
        "online",
        "Trane",
        "CVHF-1250 Centrifugal",
        "2020-03-02",
    )
    readings(sensor(ch2, "vibration_rms", "mm/s", 2.3), _series(rng, now, 2.3, 0.1))
    readings(sensor(ch2, "discharge_pressure", "bar", 9.1), _series(rng, now, 9.1, 0.07))
    readings(sensor(ch2, "supply_temp", "degC", 6.6), _series(rng, now, 6.6, 0.12, daily_swing=0.2))

    # Second eval scenario: Pump-07 — seal-leak pressure decline.
    pu7 = asset(
        "PU-07",
        "CHW Pump-07",
        "pump",
        pump_room,
        "high",
        "degraded",
        "Grundfos",
        "NB 125-315",
        "2019-09-30",
    )
    pu7_press = sensor(pu7, "discharge_pressure", "bar", 4.8)
    pu7_vib = sensor(pu7, "vibration_rms", "mm/s", 1.8)
    pu7_flow = sensor(pu7, "flow_rate", "m3/h", 240.0)
    readings(pu7_press, _series(rng, now, 4.8, 0.05, ramp_to=3.2, ramp_hours=72))
    readings(pu7_vib, _series(rng, now, 1.8, 0.08, ramp_to=2.4, ramp_hours=72))
    readings(pu7_flow, _series(rng, now, 240.0, 2.5, ramp_to=198.0, ramp_hours=72))

    pu1 = asset(
        "PU-01",
        "CHW Pump-01",
        "pump",
        pump_room,
        "high",
        "online",
        "Grundfos",
        "NB 125-315",
        "2019-09-30",
    )
    readings(sensor(pu1, "discharge_pressure", "bar", 4.9), _series(rng, now, 4.9, 0.05))
    readings(sensor(pu1, "vibration_rms", "mm/s", 1.7), _series(rng, now, 1.7, 0.07))

    ahu5 = asset(
        "AHU-05",
        "Air Handler-05",
        "ahu",
        ahu_room,
        "medium",
        "online",
        "Daikin",
        "Vision AHU",
        "2021-01-20",
    )
    readings(
        sensor(ahu5, "supply_temp", "degC", 13.0), _series(rng, now, 13.0, 0.2, daily_swing=0.5)
    )
    readings(sensor(ahu5, "fan_speed", "%", 72.0), _series(rng, now, 72.0, 1.5, daily_swing=4.0))

    crac2 = asset(
        "CRAC-02",
        "CRAC Unit-02",
        "crac",
        crac_room,
        "high",
        "online",
        "Vertiv",
        "Liebert DS",
        "2019-05-11",
    )
    readings(
        sensor(crac2, "return_temp", "degC", 24.0), _series(rng, now, 24.0, 0.25, daily_swing=0.4)
    )
    readings(sensor(crac2, "humidity", "%RH", 45.0), _series(rng, now, 45.0, 0.8))

    blr1 = asset(
        "BLR-01",
        "Boiler-01",
        "boiler",
        boiler_room,
        "medium",
        "online",
        "Cleaver-Brooks",
        "CBEX Elite",
        "2017-11-08",
    )
    readings(sensor(blr1, "steam_pressure", "bar", 7.0), _series(rng, now, 7.0, 0.06))
    readings(sensor(blr1, "stack_temp", "degC", 180.0), _series(rng, now, 180.0, 1.2))

    # MetroPT-3 replay target — a metro-train Air Production Unit
    # compressor, clearly labeled as such (NOT part of the synthetic HVAC
    # plant, and never scored by the MetroPT model's chiller/pump neighbors).
    # Sensor kinds use the dataset's own analog channel names, mapped honestly;
    # the eight digital MetroPT channels (COMP, DV_eletric, Towers, MPG, LPS,
    # Pressure_switch, Oil_level, Caudal_impulses) are operating-STATE inputs
    # consumed by the feature pipeline, deliberately NOT modeled as sensors.
    # No readings are seeded: telemetry arrives exclusively via replay
    # (baseline_value here is a nominal healthy display reference only).
    apu_bay = room(-1, "MetroPT Replay Bay (simulated rig)")
    apu1 = asset(
        "APU-01",
        "APU Compressor (MetroPT-3 replay)",
        "air_compressor",
        apu_bay,
        "high",
        "online",
        "Metro do Porto (dataset)",
        "APU / MetroPT-3",
        "2020-02-01",
    )
    sensor(apu1, "TP2", "bar", 1.4)  # compressor pressure
    sensor(apu1, "TP3", "bar", 8.9)  # pneumatic panel pressure
    sensor(apu1, "H1", "bar", 0.1)  # cyclonic separator pressure drop
    sensor(apu1, "DV_pressure", "bar", 0.05)  # air-dryer tower discharge pressure
    sensor(apu1, "Reservoirs", "bar", 8.9)  # downstream reservoir pressure
    sensor(apu1, "Oil_temperature", "degC", 60.0)  # compressor oil temperature
    sensor(apu1, "Motor_current", "A", 2.0)  # three-phase motor current

    # --- Technicians ---------------------------------------------------------
    techs = [
        Technician(name="Maya Rodriguez", specialty="hvac", shift="day", available=True),
        Technician(name="Sam Okafor", specialty="hvac", shift="night", available=True),
        Technician(name="Elena Petrova", specialty="electrical", shift="day", available=True),
        Technician(name="Derek Hall", specialty="plumbing", shift="day", available=False),
        Technician(name="Priya Nair", specialty="controls", shift="day", available=True),
    ]
    session.add_all(techs)
    session.flush()
    maya, sam, elena, derek, priya = techs

    # --- Inspections (free-text notes are untrusted evidence to the agent) ---
    session.add_all(
        [
            Inspection(
                asset_id=ch3.id,
                technician_id=maya.id,
                ts=now - timedelta(days=3),
                summary="Quarterly PM walkthrough - Chiller-03",
                notes=(
                    "Audible whine near compressor drive end that was not present last quarter. "
                    "Slight oil weep at the bearing housing sight glass. Recommended follow-up "
                    "vibration analysis. Coupling guard intact, no refrigerant alarm."
                ),
            ),
            Inspection(
                asset_id=ch3.id,
                technician_id=sam.id,
                ts=now - timedelta(days=30),
                summary="Monthly check - Chiller-03",
                notes="Normal operation. Vibration within spec. Oil level nominal.",
            ),
            Inspection(
                asset_id=pu7.id,
                technician_id=derek.id,
                ts=now - timedelta(days=2),
                summary="Pump gallery rounds - Pump-07",
                notes=(
                    "Noticed drip trail and mineral crust under Pump-07 mechanical seal area. "
                    "Gland follower slightly loose. Discharge gauge reading lower than plate spec."
                ),
            ),
            Inspection(
                asset_id=ch1.id,
                technician_id=maya.id,
                ts=now - timedelta(days=5),
                summary="Quarterly PM walkthrough - Chiller-01",
                notes="All parameters nominal.",
            ),
        ]
    )

    # --- Work orders ----------------------------------------------------------
    session.add_all(
        [
            WorkOrder(
                asset_id=ch3.id,
                technician_id=maya.id,
                status="completed",
                priority="medium",
                title="Chiller-03 condenser tube brush cleaning",
                description="Annual condenser tube cleaning per PM schedule.",
                notes="Completed. Approach temps improved 0.4degC. No abnormal wear observed on water side.",
                source="human",
                created_at=now - timedelta(days=90),
                completed_at=now - timedelta(days=89),
            ),
            WorkOrder(
                asset_id=ch3.id,
                technician_id=sam.id,
                status="open",
                priority="low",
                title="Chiller-03 oil analysis sample pickup",
                description="Collect compressor oil sample for quarterly lab analysis.",
                notes="Lab kit staged at plant office. Last sample showed elevated iron particulates (12 ppm, up from 4 ppm).",
                source="human",
                created_at=now - timedelta(days=6),
            ),
            WorkOrder(
                asset_id=pu7.id,
                technician_id=derek.id,
                status="open",
                priority="medium",
                title="Pump-07 seal area housekeeping and torque check",
                description="Clean seal drip residue, check gland follower torque.",
                notes="Residue pattern suggests slow steady leak rather than splash. Recheck after 48h.",
                source="human",
                created_at=now - timedelta(days=2),
            ),
            WorkOrder(
                asset_id=ahu5.id,
                technician_id=priya.id,
                status="completed",
                priority="low",
                title="AHU-05 filter replacement",
                description="Replace MERV-13 filter bank.",
                notes="Done. dP across filters back to 0.3 inwc.",
                source="human",
                created_at=now - timedelta(days=14),
                completed_at=now - timedelta(days=13),
            ),
        ]
    )

    # --- Maintenance manual chunks (untrusted evidence; keyword-searchable) ---
    session.add_all(
        [
            ManualChunk(
                doc_title="Trane CVHF Centrifugal Chiller - Service Manual",
                section="7.3 Compressor Bearing Wear",
                asset_type="chiller",
                content=(
                    "Progressive increase in vibration RMS at the compressor drive end, accompanied by "
                    "declining discharge pressure and rising motor current, indicates bearing wear or "
                    "loss of bearing lubrication. Elevated iron particulate counts in oil analysis "
                    "confirm mechanical wear. Continued operation above 7.1 mm/s risks catastrophic "
                    "impeller contact. Recommended action: immediate vibration spectrum analysis, "
                    "bearing inspection, and bearing replacement if spalling is found."
                ),
            ),
            ManualChunk(
                doc_title="Trane CVHF Centrifugal Chiller - Service Manual",
                section="8.1 Refrigerant Charge Loss",
                asset_type="chiller",
                content=(
                    "Low refrigerant charge presents as reduced discharge pressure with LOW compressor "
                    "vibration and falling motor current, often with purge unit run-time increase. "
                    "Verify with leak detector at flanges and relief valve. Bearing vibration is NOT "
                    "a symptom of charge loss."
                ),
            ),
            ManualChunk(
                doc_title="Trane CVHF Centrifugal Chiller - Service Manual",
                section="9.2 Condenser Fouling",
                asset_type="chiller",
                content=(
                    "Condenser fouling raises head pressure and approach temperature; supply temperature "
                    "may drift upward under load. Vibration is unaffected. Confirm via approach-temp "
                    "trend and eddy-current tube testing."
                ),
            ),
            ManualChunk(
                doc_title="Facility Electrical Standards",
                section="4.4 Motor Electrical Faults",
                asset_type="chiller",
                content=(
                    "Motor electrical faults (winding insulation breakdown, phase imbalance) present as "
                    "current asymmetry and thermal alarms, generally without a matching mechanical "
                    "vibration ramp. Megger testing required before restart."
                ),
            ),
            ManualChunk(
                doc_title="Grundfos NB Pump - O&M Manual",
                section="5.2 Mechanical Seal Failure",
                asset_type="pump",
                content=(
                    "Mechanical seal failure presents as visible leakage at the seal gland, gradual "
                    "discharge pressure decline, and mineral residue accumulation. Flow rate drops as "
                    "leakage increases. Replace seal cartridge; inspect shaft sleeve for scoring."
                ),
            ),
            ManualChunk(
                doc_title="Grundfos NB Pump - O&M Manual",
                section="5.4 Cavitation",
                asset_type="pump",
                content=(
                    "Cavitation produces a distinctive crackling noise and broadband vibration with "
                    "fluctuating discharge pressure. Verify NPSH margin and suction strainer condition. "
                    "Unlike seal failure, cavitation shows no external leakage."
                ),
            ),
            ManualChunk(
                doc_title="Grundfos NB Pump - O&M Manual",
                section="5.6 Impeller Wear",
                asset_type="pump",
                content=(
                    "Impeller wear causes slow head/flow degradation over months without leakage. "
                    "Confirm by pump curve test. Vibration typically remains near baseline until "
                    "severe erosion develops."
                ),
            ),
        ]
    )

    # --- APU (air_compressor) knowledge: manuals + SYNTHETIC precedents ------
    # PROVENANCE: MetroPT-3's four documented failures are all air leaks. The
    # oil-system entries below are synthetic facility knowledge for evaluation
    # breadth and are labeled as such — never MetroPT ground truth.
    session.add_all(
        [
            ManualChunk(
                doc_title="APU Air Production Unit - O&M Manual (synthetic)",
                section="4.2 Compressed-Air Leaks",
                asset_type="air_compressor",
                content=(
                    "A compressed-air leak presents as falling reservoir and pneumatic-panel "
                    "pressure with a rising compressor duty cycle: the unit loads longer and "
                    "more often to hold setpoint, raising motor current draw. Audible hissing "
                    "near dryer towers or pneumatic fittings is common. Soap-test the circuit "
                    "and reseat unloader/drain valves."
                ),
            ),
            ManualChunk(
                doc_title="APU Air Production Unit - O&M Manual (synthetic)",
                section="6.1 Oil Circuit Faults",
                asset_type="air_compressor",
                content=(
                    "Oil-circuit faults present as elevated oil temperature and, when the oil "
                    "level drops, tripping of the oil-level switch. Pneumatic pressures remain "
                    "near setpoint in early stages, distinguishing oil faults from air leaks. "
                    "Check oil level, cooler flow, and sample the oil for analysis."
                ),
            ),
        ]
    )
    session.add_all(
        [
            CaseMemory(
                asset_id=None,
                asset_type="air_compressor",
                title="APU compressed-air leak (synthetic precedent)",
                symptoms_json=json.dumps(
                    ["air pressure drop", "high compressor duty", "high motor current"]
                ),
                root_cause="compressed-air leak",
                resolution="Soap-tested pneumatic circuit; resealed dryer-tower fitting.",
                outcome="Reservoir pressure and duty cycle returned to baseline.",
                risk_reduction=0.5,
                narrative=(
                    "Synthetic training precedent modeled on air-leak physics: pressure sagged, "
                    "duty doubled, current rose; hissing at the dryer manifold. Fixed by "
                    "resealing. (Synthetic knowledge, not a MetroPT-3 report.)"
                ),
                provenance="seeded",
                validation_status="validated_success",
                validated_by="synthetic seeded precedent",
            ),
            CaseMemory(
                asset_id=None,
                asset_type="air_compressor",
                title="APU oil-cooler restriction (synthetic precedent)",
                symptoms_json=json.dumps(["oil temperature high", "high motor current"]),
                root_cause="oil-system abnormality (synthetic scenario)",
                resolution="Flushed oil cooler; restored oil level.",
                outcome="Oil temperature normalized.",
                risk_reduction=0.4,
                narrative=(
                    "Synthetic precedent: oil temperature climbed with stable pneumatic "
                    "pressures; cooler restriction found. (Synthetic knowledge, not a "
                    "MetroPT-3 report.)"
                ),
                provenance="seeded",
                validation_status="validated_success",
                validated_by="synthetic seeded precedent",
            ),
        ]
    )

    # --- Prior postmortems in case memory (the agent queries these FIRST) ----
    session.add_all(
        [
            CaseMemory(
                asset_id=ch1.id,
                asset_type="chiller",
                title="Chiller-01 compressor bearing wear (resolved 2025-11)",
                symptoms_json=json.dumps(
                    [
                        "high vibration",
                        "vibration ramp",
                        "discharge pressure drop",
                        "power increase",
                    ]
                ),
                root_cause="compressor bearing wear",
                resolution="Replaced drive-end bearing set, restored lubrication, laser-aligned coupling.",
                outcome="Vibration returned to 2.1 mm/s; no recurrence in 8 months.",
                risk_reduction=0.63,
                narrative=(
                    "Chiller-01 showed a 5-day vibration ramp from 2.1 to 6.8 mm/s with discharge "
                    "pressure decay. Oil analysis showed iron particulates. Bearing spalling confirmed "
                    "on teardown. Root cause: lubrication line restriction starving the drive-end bearing."
                ),
                provenance="seeded",
                validation_status="validated_success",
                validated_by="historical postmortem (facility records)",
            ),
            CaseMemory(
                asset_id=None,
                asset_type="pump",
                title="CHW Pump-03 mechanical seal failure (resolved 2025-08)",
                symptoms_json=json.dumps(
                    ["discharge pressure drop", "visible leakage", "flow reduction"]
                ),
                root_cause="mechanical seal failure",
                resolution="Replaced seal cartridge and scored shaft sleeve.",
                outcome="Pressure restored to spec; leak stopped.",
                risk_reduction=0.55,
                narrative=(
                    "Pump-03 lost 20% discharge pressure over a week with residue at the gland. "
                    "Seal faces were heat-checked. Replaced cartridge seal; verified NPSH margin OK."
                ),
                provenance="seeded",
                validation_status="validated_success",
                validated_by="historical postmortem (facility records)",
            ),
            CaseMemory(
                asset_id=None,
                asset_type="chiller",
                title="Chiller-02 condenser fouling (resolved 2025-06)",
                symptoms_json=json.dumps(
                    ["high approach temp", "high head pressure", "supply temp drift"]
                ),
                root_cause="condenser tube fouling",
                resolution="Brush-cleaned condenser tubes; treated cooling water loop.",
                outcome="Approach temp recovered 1.1degC.",
                risk_reduction=0.35,
                narrative="Seasonal fouling event. No vibration involvement.",
                provenance="seeded",
                validation_status="validated_success",
                validated_by="historical postmortem (facility records)",
            ),
        ]
    )

    # --- Active incident: the golden demo entry point -------------------------
    incident = Incident(
        asset_id=ch3.id,
        severity="critical",
        status="active",
        title="Chiller-03 critical vibration anomaly",
        summary=(
            "Vibration RMS trending from 2.2 to >7 mm/s over 48h with concurrent discharge "
            "pressure decline. Exceeds ISO 10816 zone C threshold for this frame size."
        ),
        detected_at=now - timedelta(hours=4),
    )
    session.add(incident)
    session.flush()

    session.commit()
    return {
        "chiller3_id": ch3.id,
        "pump7_id": pu7.id,
        "apu_id": apu1.id,
        "incident_id": incident.id,
        "now": now.isoformat(),
    }


def main() -> None:
    print(f"Seeding database at {engine.url} ...")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        ids = seed(session)
    finally:
        session.close()
    print(f"Seed complete: {ids}")


if __name__ == "__main__":
    main()
