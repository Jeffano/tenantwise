from ingestion.parsers import clean_lines, parse_headed_page, parse_statute, split_long

SAMPLE = """PART VII
RULES RELATING TO RENT
Notice of rent increase required
116 (1) A landlord shall not increase the rent charged to a tenant for a rental unit without first giving the tenant at least 90 days written notice of the landlord's intention to do so.
Same
(2) Subsection (1) applies even if the rent charge is being increased in accordance with an order.
Section Amendments with date in force (d/m/y)
12 month rule
119 (1) A landlord who is lawfully entitled to increase the rent charged to a tenant for a rental unit may do so only if at least 12 months have elapsed.
"""


def test_statute_sections_and_headnotes():
    chunks = parse_statute(clean_lines(SAMPLE), "RTA")
    refs = [c.section_ref for c in chunks]
    assert refs == ["RTA s. 116", "RTA s. 119"]
    assert chunks[0].heading_path == "Part VII Rules Relating To Rent > Notice of rent increase required"
    assert "(2) Subsection (1)" in chunks[0].text      # subsections stay with their section
    assert "Section Amendments" not in chunks[0].text  # boilerplate removed
    assert chunks[1].heading_path.endswith("12 month rule")


def test_long_sections_are_split():
    text = "\n".join(["word " * 100] * 8)
    pieces = split_long(text)
    assert len(pieces) > 1 and all(len(p.split()) <= 450 for p in pieces)


def test_headed_page():
    blocks = [("h", "Rent increase guideline"), ("p", "The guideline is set each year."), ("h", "Exemptions"), ("p", "Some units are exempt.")]
    chunks = parse_headed_page(blocks, "Ontario.ca rent increases")
    assert [c.section_ref for c in chunks] == ["Ontario.ca rent increases: Rent increase guideline",
                                               "Ontario.ca rent increases: Exemptions"]
