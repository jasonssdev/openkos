"""The English-question arm of the Ask screen (#1581): the translated
questions, their leak check, the `en-*` sets and the paired language penalty."""

import importlib.util
import pathlib
import sys
from dataclasses import replace
from types import ModuleType
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_EVALS = _REPO_ROOT / "evals"


def _load(name: str, path: pathlib.Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ask = _load(
    "run_ask_model_screen_en",
    _EVALS / "ask_model_screen" / "run_ask_model_screen.py",
)
cl = sys.modules["crosslingual_corpus"]


def test_english_questions_mirror_the_spanish_ones_one_to_one() -> None:
    assert len(cl.EN_TRANSFERRED) == len(cl.TRANSFERRED) == 20
    assert len(cl.EN_ANCHORED) == len(cl.ANCHORED) == 12
    assert cl.EN_QUESTIONS == cl.EN_TRANSFERRED + cl.EN_ANCHORED
    assert len(set(cl.EN_QUESTIONS)) == 32


def test_no_gold_token_appears_in_an_english_question() -> None:
    assert cl.check_english_questions() == []


def test_the_leak_check_catches_a_token_in_the_question() -> None:
    leaky = (*cl.EN_ANCHORED[:-1], "did Ana Ríos open the first session?")
    problems = cl.check_english_questions(anchored=leaky)
    assert any("Ana Ríos" in p for p in problems)


def test_the_leak_check_catches_a_length_mismatch() -> None:
    assert cl.check_english_questions(transferred=cl.EN_TRANSFERRED[:-1])


def test_the_leak_check_catches_a_duplicate_question() -> None:
    dup = (cl.EN_TRANSFERRED[0], *cl.EN_TRANSFERRED[1:-1], cl.EN_TRANSFERRED[0])
    assert any("duplicate" in p for p in cl.check_english_questions(transferred=dup))


def test_questions_en_keeps_labels_and_anchored_indices_of_the_spanish_set() -> None:
    es, en = ask.questions(), ask.questions_en()
    assert [(lbl, a) for lbl, _q, a in es] == [(lbl, a) for lbl, _q, a in en]
    assert [q for _l, q, _a in en] == list(cl.EN_QUESTIONS)


def test_question_index_pairs_a_spanish_and_an_english_item() -> None:
    for i, ((_l, es_q, _a), (_l2, en_q, _a2)) in enumerate(
        zip(ask.questions(), ask.questions_en(), strict=True)
    ):
        assert ask.question_index(f"es-en|{es_q}") == i
        assert ask.question_index(f"en-en|{en_q}") == i


def test_the_english_corpus_hash_covers_the_questions_and_not_the_spanish_one() -> None:
    assert ask.corpus_sha256(english=True) != ask.corpus_sha256()


def test_english_runs_never_share_a_file_with_the_spanish_runs(
    tmp_path: pathlib.Path,
) -> None:
    es = ask.runs_path(tmp_path, "qwen3:8b")
    en = ask.runs_path(tmp_path, "qwen3:8b", english=True)
    assert es != en
    assert en == tmp_path / "runs/runs-qwen3-8b-en.json"
    assert ask.contexts_path(tmp_path, english=True) != ask.contexts_path(tmp_path)


def _row(
    set_name: str, question: str, label: str, *, lang: str, sufficient: bool
) -> Any:
    return ask.Row(
        model="m",
        run=1,
        set=set_name,
        item=f"{set_name}|{question}",
        label=label,
        anchored=None,
        blocks=3,
        gold_in_context=None,
        gold_hit=None,
        suff_outcome="ok",
        sufficient=sufficient,
        suff_elapsed_s=1.0,
        ans_outcome="ok",
        attribution="reported",
        cited=[],
        lang=lang,
        fact_recall=None,
        ans_elapsed_s=1.0,
        answer_chars=10,
        answer="x",
    )


def test_language_penalty_is_the_spanish_set_minus_the_english_set_per_question() -> (
    None
):
    es_q = [q for _l, q, _a in ask.questions()][:4]
    en_q = [q for _l, q, _a in ask.questions_en()][:4]
    labels = [lbl for lbl, _q, _a in ask.questions()][:4]
    # Spanish asks lose the first two sufficiency verdicts the English ones win.
    es_rows = [
        _row("es-en", q, lbl, lang="es", sufficient=i >= 2)
        for i, (q, lbl) in enumerate(zip(es_q, labels, strict=True))
    ]
    en_rows = [
        _row("en-en", q, lbl, lang="en", sufficient=True)
        for q, lbl in zip(en_q, labels, strict=True)
    ]
    out = ask.language_penalty(
        es_rows, en_rows, "sufficiency", es_set="es-en", en_set="en-en", resamples=200
    )
    assert out.n == 4
    assert out.mean == -0.5


def test_language_penalty_refuses_unpaired_questions() -> None:
    q = [q for _l, q, _a in ask.questions()]
    qe = [q for _l, q, _a in ask.questions_en()]
    lbl = [lbl for lbl, _q, _a in ask.questions()]
    es_rows = [_row("es-en", q[0], lbl[0], lang="es", sufficient=True)]
    en_rows = [_row("en-en", qe[1], lbl[1], lang="en", sufficient=True)]
    try:
        ask.language_penalty(
            es_rows, en_rows, "sufficiency", es_set="es-en", en_set="en-en"
        )
    except ValueError:
        return
    raise AssertionError("unpaired questions must be refused")


def test_in_language_share_counts_the_questions_own_language() -> None:
    rows = [
        _row("en-en", "q1", ask.ADJACENT, lang="en", sufficient=True),
        _row("en-en", "q2", ask.ADJACENT, lang="es", sufficient=True),
        replace(_row("en-en", "q3", ask.GROUNDED, lang="en", sufficient=True)),
    ]
    assert ask.in_language_share(rows, "en", ask.ADJACENT) == 0.5
    assert ask.in_language_share(rows, "en", ask.GROUNDED) == 1.0


def test_compare_language_reports_the_candidate_minus_baseline_in_english() -> None:
    def rows(model: str, sufficient: bool) -> tuple[list[Any], list[Any]]:
        en = [
            replace(
                _row("en-en", q, lbl, lang="en", sufficient=sufficient), model=model
            )
            for lbl, q, _a in ask.questions_en()
        ]
        sp = [
            replace(
                _row("es-en", q, lbl, lang="es", sufficient=sufficient), model=model
            )
            for lbl, q, _a in ask.questions()
        ]
        return sp, en

    b_es, b_en = rows("base", True)
    c_es, c_en = rows("cand", False)
    out = ask.compare_language(
        {"base": b_es, "cand": c_es},
        {"base": b_es, "cand": c_es},
        {"base": b_en, "cand": c_en},
        baseline="base",
        resamples=100,
    )
    pooled = out["english_delta"]["cand"]["sufficiency"]["pooled"]
    # The baseline accepts everything, the candidate refuses everything: they
    # differ on the 22 grounded questions and agree on the 10 adjacent ones
    # (an acceptance is wrong there, a refusal right) -- 32 of 32 differ in
    # score, so the delta is -1 on grounded and +1 on adjacent.
    assert pooled["n"] == 32
    assert abs(pooled["mean"] - (10 - 22) / 32) < 1e-9
