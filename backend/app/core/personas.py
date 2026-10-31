"""Demo persona catalogue — the spec §4 internal personas plus the Canvas client and heir.

Used by the seed (to create one user per persona) and by the demo-personas API (which powers
the role switcher). All demo users share the password 'aurea'. Persona-only; not for production."""
from __future__ import annotations

from app.models.enums import UserRole

# email, role, full_name, title, description, default_path, group
DEMO_PERSONAS: list[dict] = [
    {"email": "sophie.adviser@aurea.demo", "role": UserRole.ADVISER, "full_name": "Sophie Tran",
     "title": "Senior Adviser", "default_path": "/studio", "group": "Advice",
     "description": "Win and retain relationships; give suitable, personal advice."},
    {"email": "paraplanner@aurea.demo", "role": UserRole.PARAPLANNER, "full_name": "Priya Naidu",
     "title": "Paraplanner", "default_path": "/studio/meetings", "group": "Advice",
     "description": "Prepare reviews, documentation and client packs."},
    {"email": "portfolio@aurea.demo", "role": UserRole.PORTFOLIO_TEAM, "full_name": "Tom Becker",
     "title": "Portfolio Manager", "default_path": "/studio/review", "group": "Investment",
     "description": "Maintain models and target allocations; drift at book scale."},
    {"email": "research@aurea.demo", "role": UserRole.RESEARCH_CIO, "full_name": "Dr. Elena Cho",
     "title": "CIO · Research", "default_path": "/studio/reports", "group": "Investment",
     "description": "Produce proprietary research; ensure it is used well."},
    {"email": "compliance@aurea.demo", "role": UserRole.COMPLIANCE, "full_name": "David Okafor",
     "title": "Head of Compliance", "default_path": "/provenance", "group": "Risk",
     "description": "Ensure suitability, conduct and AML/CFT obligations are met."},
    {"email": "operations@aurea.demo", "role": UserRole.OPERATIONS, "full_name": "Grace Lim",
     "title": "Operations Lead", "default_path": "/studio/onboarding", "group": "Operations",
     "description": "Onboarding, settlements, data and reconciliations."},
    {"email": "branch@aurea.demo", "role": UserRole.BRANCH_LEADER, "full_name": "Mark Sullivan",
     "title": "Branch Leader", "default_path": "/studio/capacity", "group": "Leadership",
     "description": "Capacity, growth and service quality across advisers."},
    {"email": "admin@aurea.demo", "role": UserRole.ADMIN, "full_name": "Jordan Reyes",
     "title": "Platform Admin", "default_path": "/admin", "group": "Platform",
     "description": "Configure the whole platform — branding, agents, connectors, models."},
    {"email": "client@aurea.demo", "role": UserRole.CLIENT, "full_name": "Wei Chen",
     "title": "Client", "default_path": "/canvas", "group": "Client",
     "description": "The client's plain-language wealth view and assistant (Canvas)."},
    {"email": "heir@aurea.demo", "role": UserRole.CLIENT, "full_name": "Lucas Chen",
     "title": "Next-gen heir", "default_path": "/canvas/next-gen", "group": "Client",
     "description": "The digital-first, education-led next-gen onboarding journey."},
]

# The Indian demonstration firm (seed/india_demo.py). Same password; the people are invented.
INDIA_PERSONAS: list[dict] = [
    {"email": "adviser@astra-india.demo", "role": UserRole.ADVISER, "full_name": "Ananya Rao",
     "title": "Investment Adviser", "default_path": "/studio", "group": "Advice",
     "description": "Advises under an Investment Adviser's licence: the client consents to every trade."},
    {"email": "paraplanner@astra-india.demo", "role": UserRole.PARAPLANNER, "full_name": "Ishaan Verma",
     "title": "Paraplanner", "default_path": "/studio/meetings", "group": "Advice",
     "description": "Prepares reviews, and records clients' answers from the evidence."},
    {"email": "portfolio@astra-india.demo", "role": UserRole.PORTFOLIO_TEAM, "full_name": "Vikram Shah",
     "title": "Portfolio Manager", "default_path": "/studio/review", "group": "Investment",
     "description": "Manages discretionary mandates: approves every order set by name."},
    {"email": "research@astra-india.demo", "role": UserRole.RESEARCH_CIO, "full_name": "Dr. Meenakshi Sundaram",
     "title": "CIO · Research", "default_path": "/studio/reports", "group": "Investment",
     "description": "Produces the firm's research."},
    {"email": "compliance@astra-india.demo", "role": UserRole.COMPLIANCE, "full_name": "Neha Kulkarni",
     "title": "Compliance Officer", "default_path": "/provenance", "group": "Risk",
     "description": "Holds the veto, and the ledger."},
    {"email": "operations@astra-india.demo", "role": UserRole.OPERATIONS, "full_name": "Suresh Pillai",
     "title": "Operations Lead", "default_path": "/studio/onboarding", "group": "Operations",
     "description": "Onboarding, settlements and reconciliations."},
    {"email": "branch@astra-india.demo", "role": UserRole.BRANCH_LEADER, "full_name": "Kavita Joshi",
     "title": "Branch Leader", "default_path": "/studio/capacity", "group": "Leadership",
     "description": "Capacity and growth across advisers."},
    {"email": "admin@astra-india.demo", "role": UserRole.ADMIN, "full_name": "Rohan Gupta",
     "title": "Platform Admin", "default_path": "/admin", "group": "Platform",
     "description": "Configures the platform, inside what the licences allow."},
    {"email": "client@astra-india.demo", "role": UserRole.CLIENT, "full_name": "Rajesh Sharma",
     "title": "Client", "default_path": "/canvas", "group": "Client",
     "description": "The client's view of their own household."},
]
