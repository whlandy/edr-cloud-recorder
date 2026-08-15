import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_skill_stays_small_enough_for_progressive_disclosure():
    lines = (ROOT / "SKILL.md").read_text(encoding="utf-8").splitlines()

    assert len(lines) <= 300, f"SKILL.md has {len(lines)} lines; move details to references/"


def test_skill_local_markdown_links_exist():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    links = re.findall(r"\[[^]]+\]\(([^)]+\.md)\)", skill)

    assert links
    missing = [path for path in links if not (ROOT / path).is_file()]
    assert not missing, f"broken SKILL.md links: {missing}"


def test_skill_routes_trace_work_to_dedicated_reference():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "references/trace-replay.md" in skill
    assert (ROOT / "references/trace-replay.md").is_file()


def test_openai_skill_metadata_is_present_and_invocable():
    metadata = (ROOT / "agents/openai.yaml").read_text(encoding="utf-8")

    assert 'display_name: "Web Recorder"' in metadata
    assert "$web-record" in metadata
    assert "allow_implicit_invocation: true" in metadata
