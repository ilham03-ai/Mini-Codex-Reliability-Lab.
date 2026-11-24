"""search_code must say `truncated=true` when there are >50 matches.

The silent-truncation case was a trap: the model saw 50 matches + a total
of e.g. 200 and had no explicit cue that the list was clipped. The flag
makes it explicit.
"""
from agent.tools import search_code


def test_search_marks_truncated_when_over_50(tmp_path):
    # 55 files, each containing one match for "needle".
    for i in range(55):
        (tmp_path / f"m{i}.py").write_text("needle = 1\n")
    r = search_code("needle", str(tmp_path))
    assert r["success"] is True
    assert r["total"] == 55
    assert len(r["matches"]) == 50
    assert r["truncated"] is True
    assert "50" in r["hint"]


def test_search_not_truncated_when_under_50(tmp_path):
    for i in range(10):
        (tmp_path / f"m{i}.py").write_text("needle = 1\n")
    r = search_code("needle", str(tmp_path))
    assert r["truncated"] is False
    assert r["hint"] is None
