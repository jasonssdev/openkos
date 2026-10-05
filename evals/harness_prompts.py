"""Which prompt text a harness actually sent, named for its stamp (#1277).

`harness_stamp.build_stamp` takes `{prompt id: text}`. The harness knows the
text; this module knows the ids and how a treatment is named, so the
eighteen harnesses that stamp the extraction or answer pipeline do not each
restate the id table.

THE TEXT IS READ WHERE IT WAS SENT. Arms swap or splice the production
constant (`concept._SYSTEM_PROMPT`, a replaced clause), and several restore
it before they write results. A harness therefore passes the text it ran
under, and this module hashes THAT, never the registry's file: a stamp of
the shipped text on a result that measured a spliced one would be a wrong
identity, which is worse than none.

NAMING. Text equal to the registered prompt keeps the registered id
(`extraction/system`). A treated text takes `<registered-id>+<arm>`
(`extraction/system+persons`) and its own hash. Treated text with no arm name
is refused: an unnamed treatment is exactly the result that cannot be told
apart from the baseline afterwards.

Imports `openkos` lazily, so a harness that has put `src/` on `sys.path`
(every one does) can import this module at the top. Standard library only
otherwise.
"""

from __future__ import annotations


def sent_prompt(
    prompt_id: str, sent: str, shipped: str, arm: str | None = None
) -> tuple[str, str]:
    """`(id, text)` for a prompt that was sent as `sent`.

    `shipped` is what the registry says that prompt is. Equal text keeps
    `prompt_id`; different text is a treatment and must name its `arm`."""
    if sent == shipped:
        return prompt_id, sent
    if not arm or not arm.strip():
        raise ValueError(
            f"{prompt_id}: the sent text differs from the registered prompt "
            "but no arm names the treatment"
        )
    return f"{prompt_id}+{arm}", sent


def extraction_prompts(
    *,
    system: str | None = None,
    arm: str | None = None,
    participant_capture: str | None = None,
    participant_arm: str | None = None,
) -> dict[str, str]:
    """The four system prompts the extraction pipeline can send.

    `system` and `participant_capture` are the prompts as the arm ran them
    (default: the live `concept` constants), each named by its own arm when
    it is a treatment; the other two are read live, so a harness that
    patched one of them stamps the patched text."""
    from openkos.extraction import concept, judge
    from openkos.llm.prompts import load_prompt

    shipped = load_prompt(
        "extraction/system",
        transcript_subjects_clause=load_prompt("extraction/transcript_subjects_clause"),
    )
    sent = concept._SYSTEM_PROMPT if system is None else system
    prompts = dict([sent_prompt("extraction/system", sent, shipped, arm)])
    prompts["extraction/reask"] = concept._REASK_SYSTEM_PROMPT
    capture = (
        concept._PARTICIPANT_CAPTURE_SYSTEM_PROMPT
        if participant_capture is None
        else participant_capture
    )
    prompts.update(
        [
            sent_prompt(
                "extraction/participant_capture",
                capture,
                load_prompt("extraction/participant_capture"),
                participant_arm,
            )
        ]
    )
    prompts["extraction/judge"] = judge._JUDGE_SYSTEM_PROMPT
    return prompts


def language_anchor_prompt(arm: str | None = None) -> dict[str, str]:
    """The `_LANGUAGE_ANCHOR` fragment as sent, read live. It rides the user
    turn, but an arm that rewrites it is a different prompt, so it is stamped
    (`extraction/language_anchor`, or `+<arm>` when it was rewritten)."""
    from openkos.extraction import concept
    from openkos.llm.prompts import load_prompt

    return dict(
        [
            sent_prompt(
                "extraction/language_anchor",
                concept._LANGUAGE_ANCHOR,
                load_prompt("extraction/language_anchor"),
                arm,
            )
        ]
    )


def answer_prompts(*, sufficiency: bool = True) -> dict[str, str]:
    """The production answer prompt, plus the sufficiency check's when the
    run had it on."""
    from openkos.retrieval import answer

    prompts = {"answer/system": answer._SYSTEM_PROMPT}
    if sufficiency:
        prompts["answer/sufficiency"] = answer._SUFFICIENCY_PROMPT
    return prompts


def _self_test() -> int:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from harness_stamp import prompt_hash

    from openkos.extraction import concept
    from openkos.llm.prompts import load_prompt

    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    check(
        "equal text keeps the registered id",
        sent_prompt("a/b", "x", "x", None),
        ("a/b", "x"),
    )
    check(
        "treated text takes id+arm",
        sent_prompt("a/b", "xy", "x", "arm"),
        ("a/b+arm", "xy"),
    )
    try:
        sent_prompt("a/b", "xy", "x")
        check("an unnamed treatment is refused", True, False)
    except ValueError:
        pass

    live = extraction_prompts()
    check(
        "extraction ids",
        sorted(live),
        [
            "extraction/judge",
            "extraction/participant_capture",
            "extraction/reask",
            "extraction/system",
        ],
    )
    for prompt_id in ("extraction/judge", "extraction/reask"):
        check(
            f"{prompt_id} is the registered text",
            live[prompt_id],
            load_prompt(prompt_id),
        )
    treated = extraction_prompts(system=concept._SYSTEM_PROMPT + " extra", arm="t")
    check("treated system gets its own id", "extraction/system+t" in treated, True)
    check(
        "treated system hashes the text sent",
        prompt_hash(treated["extraction/system+t"]),
        prompt_hash(concept._SYSTEM_PROMPT + " extra"),
    )
    check(
        "treatment changes the hash",
        prompt_hash(treated["extraction/system+t"])
        == prompt_hash(live["extraction/system"]),
        False,
    )
    check(
        "answer ids",
        sorted(answer_prompts()),
        ["answer/sufficiency", "answer/system"],
    )
    check(
        "answer without sufficiency",
        sorted(answer_prompts(sufficiency=False)),
        ["answer/system"],
    )

    for name in failures:
        print(f"FAIL: {name}")
    print(f"self-test: {'FAILED' if failures else 'passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    import sys

    if "--self-test" in sys.argv[1:]:
        raise SystemExit(_self_test())
    raise SystemExit("nothing to run without --self-test; import this module instead")
