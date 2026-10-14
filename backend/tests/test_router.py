from app.retrieval import Chunk
from app.router import route


def chunk(ref, rrf):
    return Chunk(id=ref, section_ref=ref, heading_path="", url="", text="", source_title="", rrf=rrf, similarity=0.7)


def test_simple_question_goes_to_small_model():
    d = route("How much notice is needed for a rent increase?", [chunk("RTA s. 116", 0.03), chunk("RTA s. 119", 0.01)])
    assert d.tier == "small" and d.reason == "default_simple"


def test_scenario_escalates():
    d = route("My rent is $1,800 and I got 30 days notice in March. Is that valid?", [chunk("RTA s. 116", 0.03)])
    assert d.tier == "large" and "scenario_with_specifics" in d.reason


def test_multi_part_escalates():
    d = route("Can they raise rent? And can they enter without notice?", [chunk("RTA s. 116", 0.03)])
    assert d.tier == "large" and "multi_part" in d.reason


def test_cross_reference_escalates():
    chunks = [chunk("RTA s. 48", 0.032), chunk("RTA s. 48.1", 0.031), chunk("RTA s. 49", 0.030)]
    assert "cross_reference" in route("What about my N12?", chunks).reason


def test_force_overrides():
    assert route("anything", [], force="large").tier == "large"
