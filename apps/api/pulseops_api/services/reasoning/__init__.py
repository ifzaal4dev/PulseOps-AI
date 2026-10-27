"""Hybrid reasoning layer.

The deterministic orchestrator (agent_loop) remains authoritative over phase
transitions, evidence collection, allowed tools, approval, execution,
verification, memory validation, and retry limits. Providers here may only
return a schema-validated DiagnosisResult over an immutable evidence
snapshot; anything invalid fails closed into the deterministic fallback.
"""
