"""Scope rules for the supply-chain tracker.

Same stakes as the software tracker's rules: a title wrongly excluded is
invisible forever, and this job family shares its vocabulary with half the
company - "planning", "forecast", "buyer" and "sourcing" all belong to other
professions too. Every case below is a real title this scanner saw on a live
board, grouped by the judgement it is there to pin down.
"""
import pytest

from monitor import filters_scm as F


# ---- the core discipline: nothing here may ever be dropped -----------------

@pytest.mark.parametrize("title", [
    "Demand Planner",
    "Sr Demand Planner",
    "Consultant - Demand Planning",
    "Analyst – Demand Forecasting",
    "Demand Planning Manager",
    "Supply Chain Planner, Professional II",
    "Market Supply Planner",
    "Production Planner PPDS",
    "Materials Jr Planner",
    "Master Scheduler",
    "Inventory Analyst",
    "S&OP Manager",
    "Integrated Business Planning Lead",
    "Supply Chain Program Manager (SCPM) - AI Infrastructure",
    "Community Support Forecasting and Demand Planning Analyst",
    "Buyer/Planner",
    "Sr Planner",
    "Senior Specialist, Global Raw Material Planning",
    "Merchandise Planning Analyst",
    "Machine Learning Data Scientist, Forecasting",
    # planning-system product roles are adjacent enough to be worth a look
    "Senior Business Technology Product Manager- Supply Chain Planning",
])
def test_core_planning_titles_are_in_scope(title):
    assert F.classify(title) is not None, title


# ---- other professions that own the same words ----------------------------

@pytest.mark.parametrize("title", [
    "Financial Planning & Analysis Manager",
    "Sr. Finance Analyst, Sales Strategy & Planning",
    "Senior Financial Analyst, Planning & Analysis",
    "Media Planner",
    "Marketing Strategy & Planning Manager",
    "Event Planning Coordinator",
    "Urban Planner",
    "Space Planning Analyst",
    "Bastrop Campus Planning Manager",          # facilities, not supply chain
    "Real Estate Strategic Planning Lead",
    "Maintenance Planner - Albuquerque, NM",    # a trades discipline
    "Manufacturing Maintenance Planner - Buffalo, NY",
    "Technical Sourcer",                        # recruiting, not procurement
    "HR Business Partner Manufacturing & Supply Chain",
    "People Lead - Supply Chain",
    "Software Engineer, Supply Chain Systems",  # different job family entirely
    "Pricing and Revenue Planner",
])
def test_adjacent_professions_are_excluded(title):
    assert F.classify(title) is None, title


# ---- hourly and shift roles ------------------------------------------------
# These outnumber the planning jobs at every retailer and manufacturer on the
# list; letting them in would bury the feed they are supposed to surface.

@pytest.mark.parametrize("title", [
    "Logistics Supervisor (Swing shift) - Buffalo, NY",
    "Logistics Supervisor - Nights - Buffalo, NY",
    "Supply Chain Leader - 3rd Shift",
    "Warehouse Associate",
    "Order Selector - Overnight",
    "Dimensional Inspector - Supply Chain (Starship)",
    "Inventory Control Technician",
    "2nd Shift Replenishment Lead",
])
def test_floor_and_shift_roles_are_excluded(title):
    assert F.classify(title) is None, title


# ---- the band: analyst through manager ------------------------------------

@pytest.mark.parametrize("title", [
    "Director of Supply Chain",
    "Senior Director, Supply Chain Operations",
    "Head of Supply Planning",
    "VP, Global Planning",
    "Principal Supply Chain Partner Manager",
    "Associate Director - Global Supply Planning",
])
def test_director_and_above_are_out_of_band(title):
    assert F.classify(title) is None, title


def test_include_senior_widens_the_band_upward():
    """The one switch that reaches above manager, for when the search does."""
    assert F.classify("Director, Demand Planning") is None
    assert F.classify("Director, Demand Planning", include_senior=True) == "manager"


# ---- tiers -----------------------------------------------------------------

@pytest.mark.parametrize("title,tier", [
    ("2027 Summer Intern: Supply Chain", "intern"),
    ("Supply Chain Co-op - Fall 2027", "intern"),
    ("Internship - Supply Chain Logistics Associate", "intern"),
    ("Associate Demand Planner", "entry"),
    ("Logistics Coordinator", "entry"),
    ("Junior Materials Planner", "entry"),
    ("2027 Full Time: Supply Chain Associate", "entry"),
    # a manager word inside a campus programme's name is not a manager role
    ("Taste The Future Management Trainee Program - Supply Chain", "entry"),
    ("Demand Planner", "mid"),
    ("Sr Demand Planner", "mid"),
    ("Supply Chain Analyst", "mid"),
    ("Inventory Specialist", "mid"),
    ("Demand Planning Manager", "manager"),
    ("Supply Chain Program Manager", "manager"),
    ("Logistics Supervisor", "manager"),
    # "Associate Manager" and "Assistant Manager" are manager-band roles whose
    # first word would otherwise read as entry level
    ("Category Management Assoc Manager", "manager"),
    ("Assistant Manager - Logistics Operations", "manager"),
])
def test_tier_assignment(title, tier):
    assert F.classify(title) == tier, title


# ---- role families ---------------------------------------------------------

@pytest.mark.parametrize("title,family", [
    ("Sr Demand Planner", "demand-planning"),
    ("S&OP Manager", "demand-planning"),
    ("Analyst – Demand Forecasting", "demand-planning"),
    ("Production Planner", "supply-planning"),
    ("Master Scheduler", "supply-planning"),
    ("Inventory Analyst", "inventory"),
    ("Merchandise Planning Manager", "merch-planning"),
    ("Senior Buyer, Packaging Procurement", "procurement"),
    ("Logistics Brokerage Coordinator", "logistics"),
    ("Supply Chain Data Scientist", "analytics"),
    ("Supply Chain Program Manager", "program-mgmt"),
    ("WFM Capacity & Analytics Planner", "workforce"),
    # in scope, but the title names no discipline
    ("Supply Chain Associate", "supply-chain"),
])
def test_role_family(title, family):
    assert F.role_family(title) == family, title


# ---- location --------------------------------------------------------------

def test_in_scope_applies_the_shared_us_rules():
    """Geography does not care what the job is, so both trackers share it."""
    us = F.in_scope({"title": "Demand Planner", "location": "Minneapolis, MN"})
    assert us and us["tier"] == "mid" and us["role"] == "demand-planning"
    # General Mills' demand planning team is in Mumbai - correctly dropped
    assert F.in_scope({"title": "Sr Demand Planner", "location": "Powai, Mumbai, MH"}) is None
