"""The Analytics layer (Analytics Companion §2).

Analytics in Aurea are not a bolted-on BI tool — they are computed directly over the Unified
Client Brain, so the risk number, the tax number and the client-facing 'am I okay?' number
all reconcile by design and every figure carries its lineage. This package implements the
five analytics layers; each module reuses the Core engines (valuation, planning, signals,
evaluation) where they already exist and fills the gaps with documented calculators."""
