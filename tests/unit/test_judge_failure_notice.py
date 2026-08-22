"""The judge-failure notice names the CAUSE, not only the outcome (#795).

#795 reports judge selection failing on 2 of 3 ordinary transcripts "with no
diagnostic about *why* the judge could not run", and says plainly what that
costs: "timeout, parse failure, and backend refusal need different fixes and
are currently indistinguishable."

These pin the rendering. The causes themselves are pinned in
`tests/unit/extraction/test_judge.py`.
"""

from openkos.cli.main import (
    _judge_failure_notice,
    _pre_judge_ceiling_notice,
    _unfiltered_source_notice,
)
from openkos.extraction.concept import ExtractionReport


class TestTheFailureNoticeNamesTheCause:
    def test_the_causes_are_named_in_attempt_order(self) -> None:
        notice = _judge_failure_notice(
            ExtractionReport(
                judge_status="failed",
                retained=9,
                judge_failure_causes=("chat_error: OllamaTimeout", "wrong_shape"),
            )
        )

        assert notice is not None
        assert "chat_error: OllamaTimeout" in notice
        assert notice.index("chat_error: OllamaTimeout") < notice.index("wrong_shape")

    def test_the_existing_wording_survives(self) -> None:
        """The line already told an operator that no quality selection ran
        and the positional cap was skipped. Naming the cause adds to that;
        it must not replace it."""
        notice = _judge_failure_notice(
            ExtractionReport(
                judge_status="failed",
                retained=9,
                judge_failure_causes=("wrong_shape",),
            )
        )

        assert notice is not None
        assert "judge selection unavailable" in notice
        assert "no quality selection ran" in notice

    def test_a_failure_with_no_recorded_cause_still_renders(self) -> None:
        """Empty causes must not produce a dangling `because:` fragment. A
        report from a path that never recorded them is not a crash and not a
        half-sentence."""
        notice = _judge_failure_notice(
            ExtractionReport(judge_status="failed", retained=9)
        )

        assert notice is not None
        assert "judge selection unavailable" in notice
        assert notice.rstrip().endswith(")")


class TestARecoveredFailureIsStillReported:
    """The retry hides a real event.

    A source whose judge failed once and succeeded on the retry looks
    identical to one that never failed, so the 2-of-3 rate #795 measured
    could not be seen from a run's own output at all.
    """

    def test_a_recovered_failure_renders_even_though_selection_succeeded(
        self,
    ) -> None:
        notice = _judge_failure_notice(
            ExtractionReport(
                judge_status="ok",
                retained=9,
                judge_failure_causes=("unparseable: no-json",),
            )
        )

        assert notice is not None
        assert "unparseable: no-json" in notice
        assert "retry" in notice

    def test_a_degraded_run_keeps_its_own_notice_when_a_retry_recovered(
        self,
    ) -> None:
        """The ordering the recovered-retry branch must respect.

        Causes are recorded on every run that CALLED the judge, degrades
        included, so testing them before the degrade branches swallowed the
        real notice and replaced it with "the selection itself is
        unaffected" -- false exactly when it matters. `"empty"` is the case
        that exposes it: the Source is marked, the whole unfiltered union
        was kept, and an operator would have been told nothing happened.
        """
        notice = _judge_failure_notice(
            ExtractionReport(
                judge_status="empty",
                retained=9,
                judge_failure_causes=("unparseable: no-json",),
            )
        )

        assert notice is not None
        assert "judge reply matched no candidate" in notice
        assert "judge-selection-empty" in notice
        assert "unaffected" not in notice

    def test_a_degraded_failed_run_keeps_its_own_notice_too(self) -> None:
        notice = _judge_failure_notice(
            ExtractionReport(
                judge_status="failed",
                retained=9,
                judge_failure_causes=("unparseable: no-json",),
            )
        )

        assert notice is not None
        assert "judge selection unavailable" in notice
        assert "unaffected" not in notice

    def test_the_empty_degrade_still_names_the_cause(self) -> None:
        """Moving the recovered branch last must not cost the `"empty"`
        path its diagnostics -- that is the whole point of #795."""
        notice = _judge_failure_notice(
            ExtractionReport(
                judge_status="empty",
                retained=9,
                judge_failure_causes=("chat_error: OllamaTimeout",),
            )
        )

        assert notice is not None
        assert "chat_error: OllamaTimeout" in notice

    def test_a_clean_selection_says_nothing(self) -> None:
        assert (
            _judge_failure_notice(ExtractionReport(judge_status="ok", retained=9))
            is None
        )

    def test_a_skipped_judge_says_nothing(self) -> None:
        """ "skipped" means the judge was never called, so it failed at
        nothing -- and the single-run path lands here for every source."""
        assert _judge_failure_notice(ExtractionReport(judge_status="skipped")) is None


class TestTheCompoundState:
    """#795 point 3: "Treat 'ceiling truncation AND judge unavailable in the
    same extraction' as its own louder state: individually each is degraded,
    together the source is effectively unfiltered."

    On the reported file 3 both fired: 5 candidates never reached the judge,
    and the judge then failed, so all 24 survivors were kept with the
    positional cap skipped too. That file produced 24 objects filtered by
    nothing at all.
    """

    def test_both_degrades_together_say_so(self) -> None:
        notice = _unfiltered_source_notice(
            ExtractionReport(judge_status="failed", retained=24, pre_judge_dropped=5)
        )

        assert notice is not None
        assert "unfiltered" in notice.lower()

    def test_it_does_not_say_the_judge_never_ran(self) -> None:
        """`"failed"` means the judge WAS invoked, up to JUDGE_ATTEMPTS
        times, and gave up -- which is not `"skipped"`. Wording that reads
        as "never called" erases the very distinction #795 added."""
        notice = _unfiltered_source_notice(
            ExtractionReport(judge_status="failed", retained=24, pre_judge_dropped=5)
        )

        assert notice is not None
        assert "did not run" not in notice

    def test_the_ceiling_alone_is_not_the_compound_state(self) -> None:
        assert (
            _unfiltered_source_notice(
                ExtractionReport(judge_status="ok", retained=24, pre_judge_dropped=5)
            )
            is None
        )

    def test_the_judge_failing_alone_is_not_the_compound_state(self) -> None:
        assert (
            _unfiltered_source_notice(
                ExtractionReport(judge_status="failed", retained=9, pre_judge_dropped=0)
            )
            is None
        )

    def test_the_two_component_notices_still_fire_on_their_own(self) -> None:
        """The compound line ADDS; it does not swallow either half. An
        operator grepping for the ceiling line must still find it."""
        report = ExtractionReport(
            judge_status="failed", retained=24, pre_judge_dropped=5
        )

        assert _pre_judge_ceiling_notice(report) is not None
        assert _judge_failure_notice(report) is not None
