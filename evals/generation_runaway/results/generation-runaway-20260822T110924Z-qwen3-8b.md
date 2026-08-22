model qwen3:8b | ceiling (num_predict) 8192 | context window (num_ctx) 12288

Runs
----
fixture             runs  raised   cut  errors  obj med  sec med  sec max
-------------------------------------------------------------------------
kickoff               10    3/10     3       0       11     59.1    232.2
helios-overview       10    0/10     0       0        7     14.7     20.5

Q1. Which call runs away
------------------------
phase                             calls  raised  swallow  max gen  med gen  max prompt  worst s
-----------------------------------------------------------------------------------------------
extracting pass 2/2                  19       2        0     8192      334        1979    211.5
extracting pass 1/2                  20       1        0     8192      383        1979    216.2
capturing further participants        7       0        0      149      149         553      4.8
judging 16 candidates                 1       0        0      112      112         959      3.8
judging 17 candidates                 1       0        0      103      103         978      3.6
judging 14 candidates                 2       0        0       75       71         899      3.8
judging 15 candidates                 1       0        0       72       72         934      2.7
judging 23 candidates                 1       0        0       51       51        1200      2.9
judging 10 candidates                 1       0        0       45       45         729      2.7
judging 7 candidates                  6       0        0       37       37         711      1.4
judging 5 candidates                  4       0        0       35       35         580      1.2
  raised = the cut-off escaped and failed the run; swallow = the reply was cut off and the phase's own handler ate it, so the run read as clean while returning a degraded result.

Q2. Is a runaway separable from a legitimate reply?
--------------------------------------------------
phase                             legit  max legit gen  med legit  runaways                  runaway gen
--------------------------------------------------------------------------------------------------------
extracting pass 2/2                  17            933        334         2                   8192, 8192
extracting pass 1/2                  19           1874        383         1                         8192
capturing further participants        7            149        149         0                            -
judging 16 candidates                 1            112        112         0                            -
judging 17 candidates                 1            103        103         0                            -
judging 14 candidates                 2             75         71         0                            -
judging 15 candidates                 1             72         72         0                            -
judging 23 candidates                 1             51         51         0                            -
judging 10 candidates                 1             45         45         0                            -
judging 7 candidates                  6             37         37         0                            -
judging 5 candidates                  4             35         35         0                            -

  The question in one line: does a bound B exist with every legitimate reply strictly below B and every runaway at or above it?
  Largest legitimate reply anywhere in this sweep: 1874 tokens, in extracting pass 1/2. That number IS the floor any candidate bound must clear.
  Every runaway generated the ceiling's worth (8192) by definition of the cap, so the runaway side is degenerate and the available band is B in (1874, 8192] -- width 8192 - 1874 = 6318 tokens.

Q3. What would a candidate bound cost and save?
----------------------------------------------
     B  false cuts  of legit      saved              of cut-off wall clock
--------------------------------------------------------------------------
  1024           2        60     559.2s             559.2s / 673.3s = 0.83
  1536           1        60     519.3s             519.3s / 673.3s = 0.77
  2048           0        60     479.3s             479.3s / 673.3s = 0.71
  3072           0        60     399.4s             399.4s / 673.3s = 0.59
  4096           0        60     319.6s             319.6s / 673.3s = 0.47
  'of cut-off wall clock' is measured over the 3 run(s) that had some reply cut off, using each runaway call's OWN observed tokens per second.
  Fail-fast SHORTENS the failure, it never rescues the run: a reply cut at B is as unusable as one cut at the ceiling.

  B = 1024 would have FALSELY CUT:
    extracting pass 1/2 generated 1874 (>= 1024)
    extracting pass 1/2 generated 1231 (>= 1024)

  B = 1536 would have FALSELY CUT:
    extracting pass 1/2 generated 1874 (>= 1536)

Reading
-------
3 of 20 runs FAILED on a cut-off reply (the failure #828 reports)
3 of 20 runs had SOME reply cut off, at: extracting pass 1/2, extracting pass 2/2

DIAGNOSTIC half:
  extracting pass 2/2: 2 raised, 0 swallowed, worst latency 211.5s
  extracting pass 1/2: 1 raised, 0 swallowed, worst latency 216.2s
  Recording `done_reason` per call names the runaway without a second run; that half is answerable on this evidence.

FAIL-FAST half:
  `OllamaClient.chat` sends stream=False, so the only lever is a lower num_predict. The bound must clear 1874 (the largest legitimate reply measured) and sit below 8192.
  B = 2048 cut nothing legitimate in this sweep (0 of 60 finished replies reached it) and would have saved 479.3s / 673.3s = 0.71 of the wall clock the 3 cut-off run(s) burned.
  Caveat, not a footnote: 1874 is a SAMPLE maximum over 60 finished replies, not a distribution ceiling. A bound set just above it will falsely cut some rate of healthy replies that this sweep did not draw; the wider the margin between 1874 and the chosen B, the smaller that rate.