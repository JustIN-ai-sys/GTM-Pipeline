import json
from types import SimpleNamespace

from gtm_review import precheck, review, run_evals

CLEAN = run_evals.load_cases()[0]["diff"]
# Built at runtime so this file holds no literal address or key (the precheck would flag it).
REAL_EMAIL = "jordan" + "@" + "zz-invented-co.io"
FAKE_KEY = "sk-ant-" + "api03-" + "x" * 24


class FakeClient:
    """Stands in for anthropic.Anthropic(): records the request, returns a canned answer."""

    def __init__(self, answer=None, stop_reason="end_turn", text=None):
        self.calls = []
        payload = text if text is not None else json.dumps(answer)
        self._response = SimpleNamespace(
            stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=payload)]
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def _answer(verdict="approve", findings=()):
    return {"verdict": verdict, "summary": "ok", "findings": list(findings)}


def _finding(severity, rule="G4"):
    return {
        "severity": severity,
        "rule": rule,
        "file": "x.py",
        "line": 1,
        "problem": "p",
        "fix": "f",
    }


# ---- prechecks


def test_precheck_flags_secrets_real_emails_and_private_files():
    diff = (
        "--- a/f\n+++ b/data/private/people.csv\n@@ -0,0 +1,2 @@\n"
        f"+a,b,{REAL_EMAIL}\n"
        "--- a/f\n+++ b/src/x.py\n@@ -1,1 +1,2 @@\n"
        f"+API_KEY = '{FAKE_KEY}'\n"
    )
    found = precheck.run(diff)
    assert {(f.rule, f.file, f.line) for f in found} == {
        ("R2", "data/private/people.csv", 0),
        ("R2", "data/private/people.csv", 1),
        ("R2", "src/x.py", 1),
    }


def test_precheck_allows_invented_domains_and_placeholders():
    diff = (
        "--- a/f\n+++ b/tests/test_x.py\n@@ -1,1 +1,4 @@\n"
        "+EMAIL = 'ada" + "@strong.example'\n"
        "+hubspot.Client('pat-secret-token')\n"
        "+Co-Authored-By: Claude <noreply" + "@anthropic.com>\n"
        "+contact = 'sam" + "@invented.test'\n"
        "+@pytest.fixture\n"
        '+    "+@pytest.fixture"  # a decorator inside a quoted diff is not an email\n'
    )
    assert precheck.run(diff) == []


def test_precheck_only_allows_the_exact_trailer_address():
    trailer = "noreply" + "@anthropic.com"
    contact = "jane" + "@anthropic.com"
    diff = f"--- a/f\n+++ b/x.py\n@@ -0,0 +1,2 @@\n+{trailer}\n+{contact}\n"
    assert [f.line for f in precheck.run(diff)] == [2]


def test_added_lines_starting_with_plus_plus_are_scanned():
    diff = f"--- a/x.py\n+++ b/x.py\n@@ -0,0 +1,1 @@\n+++ {REAL_EMAIL}\n"
    assert precheck.added_lines(diff) == [("x.py", 1, f"++ {REAL_EMAIL}")]
    assert len(precheck.run(diff)) == 1


def test_added_lines_follow_hunk_line_numbers():
    diff = "--- a/a.py\n+++ b/a.py\n@@ -10,3 +10,4 @@\n same\n-old\n+new\n same\n"
    assert precheck.added_lines(diff) == [("a.py", 11, "new")]


# ---- review


def test_model_request_uses_schema_rubric_cache_and_fallback():
    client = FakeClient(_answer())
    result = review.review(CLEAN, client)
    sent = client.calls[0]
    assert result["verdict"] == "approve"
    assert sent["model"] == review.MODEL
    assert sent["output_config"]["format"]["schema"] == review.SCHEMA
    assert sent["fallbacks"] == "default"
    assert "R1 spend-gate" in sent["system"][0]["text"]
    assert "Rules that must not be broken" in sent["system"][0]["text"]  # AGENTS.md included
    assert CLEAN in sent["messages"][0]["content"]


def test_worst_finding_sets_the_verdict():
    assert (
        review.review(CLEAN, FakeClient(_answer("approve", [_finding("high")])))["verdict"]
        == "block"
    )
    assert (
        review.review(CLEAN, FakeClient(_answer("approve", [_finding("medium")])))["verdict"]
        == "needs_human"
    )
    assert (
        review.review(CLEAN, FakeClient(_answer("needs_human", [_finding("low")])))["verdict"]
        == "needs_human"
    )


def test_a_precheck_hit_blocks_even_if_the_model_approves():
    diff = f"--- a/f\n+++ b/src/x.py\n@@ -1,1 +1,1 @@\n+contact = '{REAL_EMAIL}'\n"
    client = FakeClient(_answer("approve"))
    result = review.review(diff, client)
    assert result["verdict"] == "block" and result["prechecks"] == 1
    assert client.calls == []  # the leaked line is never sent to the model


def test_refusal_truncation_and_bad_json_never_pass():
    for client in (
        FakeClient(_answer(), stop_reason="refusal"),
        FakeClient(_answer(), stop_reason="max_tokens"),
        FakeClient(text="not json"),
    ):
        assert review.review(CLEAN, client)["verdict"] == "needs_human"


def test_oversized_diff_goes_to_a_human_without_calling_the_model():
    client = FakeClient(_answer())
    result = review.review("+x\n" * (review.MAX_DIFF_CHARS // 3 + 1), client)
    assert result["verdict"] == "needs_human" and client.calls == []


def test_without_a_key_only_prechecks_run_and_the_comment_says_so():
    result = review.review(CLEAN, None)
    assert result["verdict"] == "approve"
    assert "ANTHROPIC_API_KEY is not set" in review.render(result)
    assert "Prechecks passed, no model review" in review.render(result)


def test_render_is_a_markdown_comment_with_a_marker():
    text = review.render(review.review(CLEAN, FakeClient(_answer("block", [_finding("high")]))))
    assert text.startswith(review.MARKER)
    assert "## GTM review: Block" in text and "| high | G4 | `x.py:1` |" in text


def test_cli_exit_code_is_1_only_on_block(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    clean, leak = tmp_path / "clean.diff", tmp_path / "leak.diff"
    clean.write_text(CLEAN)
    leak.write_text("--- a/f\n+++ b/.env\n@@ -0,0 +1,1 @@\n+X=1\n")
    assert review.main(["--diff-file", str(clean), "--out", str(tmp_path / "c.md")]) == 0
    assert review.main(["--diff-file", str(leak)]) == 1


# ---- evals


def test_eval_cases_are_complete_and_labelled():
    cases = run_evals.load_cases()
    assert len(cases) >= 12
    assert {c["expected"] for c in cases} == {"approve", "needs_human", "block"}
    for case in cases:
        assert case["diff"].startswith("diff --git")
        assert set(case["rules"]) <= set(review.RULES)
        assert (case["expected"] == "approve") == (not case["rules"])


def test_prechecks_alone_never_block_a_clean_eval_case():
    for case in run_evals.load_cases():
        if case["expected"] != "block":
            assert precheck.run(case["diff"]) == [], case["id"]


def test_eval_scoring_gate():
    cases = run_evals.load_cases()
    expected = {c["id"]: c for c in cases}

    def perfect(diff):
        case = next(c for c in cases if c["diff"] == diff)
        rules = case["rules"][:1]
        sev = {"block": "high", "needs_human": "medium", "approve": "low"}[case["expected"]]
        return _answer(case["expected"], [_finding(sev, r) for r in rules])

    report = run_evals.score(cases, perfect)
    assert report["metrics"]["passed"] and report["metrics"]["accuracy"] == 1.0

    def approves_everything(diff):
        return _answer("approve")

    bad = run_evals.score(cases, approves_everything)["metrics"]
    assert not bad["passed"]
    assert bad["missed_blocks"] == sum(c["expected"] == "block" for c in expected.values())
    assert "FAIL" in run_evals.render(run_evals.score(cases, approves_everything))
