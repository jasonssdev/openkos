model qwen3:8b | ceiling (num_predict) 8192 | context window (num_ctx) 12288

Runs
----
fixture             runs  raised   cut  errors  obj med  sec med  sec max
-------------------------------------------------------------------------
large-03-skills-vs-tools    10    0/10     0       0        8     33.4     50.6

Q1. Which call runs away
------------------------
phase                             calls  raised  swallow  max gen  med gen  max prompt  worst s
-----------------------------------------------------------------------------------------------
extracting pass 1/2                  10       0        0     1091      576        5398     28.2
extracting pass 2/2                  10       0        0      696      577        5398     18.0
judging 11 candidates                 2       0        0       58       57        4255      2.5
judging 10 candidates                 1       0        0       56       56        4236      2.5
judging 9 candidates                  3       0        0       53       53        4232      2.4
judging 8 candidates                  3       0        0       47       47        4175     10.6
judging 6 candidates                  1       0        0       23       23        4092      1.2
  raised = the cut-off escaped and failed the run; swallow = the reply was cut off and the phase's own handler ate it, so the run read as clean while returning a degraded result.

Q2. Is a runaway separable from a legitimate reply?
--------------------------------------------------
phase                             legit  max legit gen  med legit  runaways                  runaway gen
--------------------------------------------------------------------------------------------------------
extracting pass 1/2                  10           1091        576         0                            -
extracting pass 2/2                  10            696        577         0                            -
judging 11 candidates                 2             58         57         0                            -
judging 10 candidates                 1             56         56         0                            -
judging 9 candidates                  3             53         53         0                            -
judging 8 candidates                  3             47         47         0                            -
judging 6 candidates                  1             23         23         0                            -

  The question in one line: does a bound B exist with every legitimate reply strictly below B and every runaway at or above it?
  Largest legitimate reply anywhere in this sweep: 1091 tokens, in extracting pass 1/2. That number IS the floor any candidate bound must clear.
  Every runaway generated the ceiling's worth (8192) by definition of the cap, so the runaway side is degenerate and the available band is B in (1091, 8192] -- width 8192 - 1091 = 7101 tokens.

Q3. What would a candidate bound cost and save?
----------------------------------------------
     B  false cuts  of legit      saved              of cut-off wall clock
--------------------------------------------------------------------------
  1024           1        30       0.0s                    0.0s / 0s = n/a
  1536           0        30       0.0s                    0.0s / 0s = n/a
  2048           0        30       0.0s                    0.0s / 0s = n/a
  3072           0        30       0.0s                    0.0s / 0s = n/a
  4096           0        30       0.0s                    0.0s / 0s = n/a
  'of cut-off wall clock' is measured over the 0 run(s) that had some reply cut off, using each runaway call's OWN observed tokens per second.
  Fail-fast SHORTENS the failure, it never rescues the run: a reply cut at B is as unusable as one cut at the ceiling.

  B = 1024 would have FALSELY CUT:
    extracting pass 1/2 generated 1091 (>= 1024)

Reading
-------
0 of 10 runs FAILED on a cut-off reply (the failure #828 reports)
0 of 10 runs had SOME reply cut off, at: none

DIAGNOSTIC: nothing was cut off in this sweep. #828 did not reproduce here, so there is no call to name and no bound to choose. Check Q2's max legit gen against the ceiling before calling the band safe -- a sweep whose worst reply came within a few hundred tokens of the ceiling is one sampling draw from the failure.