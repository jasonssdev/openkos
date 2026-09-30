# Proximity-threshold calibration (#1052)

embedding model: bge-m3
current CANDIDATE_SIMILARITY_THRESHOLD: 0.70

related scored: 41 of 41
hard positives scored: 21 of 21
unrelated scored: 50 of 50
hard negatives scored: 34 of 34
easy negatives scored: 16 of 16

weakest related (min): 0.4238
strongest unrelated (max): 0.5827
classes OVERLAP: min(related) - max(unrelated) = -0.1589
median hard negative: 0.4840
median easy negative: 0.3239
hard-negative false-nomination budget: 1 (floor(0.05 x 34))

| floor | recall | related | hard FP | easy FP | admissible |
|---|---|---|---|---|---|
| 0.40 | 1.000 | 41 of 41 | 31 of 34 | 1 of 16 | no |
| 0.45 | 0.951 | 39 of 41 | 24 of 34 | 0 of 16 | no |
| 0.50 | 0.951 | 39 of 41 | 13 of 34 | 0 of 16 | no |
| 0.55 | 0.854 | 35 of 41 | 3 of 34 | 0 of 16 | no |
| 0.57 | 0.829 | 34 of 41 | 1 of 34 | 0 of 16 | yes |
| 0.59 | 0.707 | 29 of 41 | 0 of 34 | 0 of 16 | yes |
| 0.60 | 0.659 | 27 of 41 | 0 of 34 | 0 of 16 | yes |
| 0.65 | 0.415 | 17 of 41 | 0 of 34 | 0 of 16 | yes |
| 0.70 | 0.317 | 13 of 41 | 0 of 34 | 0 of 16 | yes |
| 0.75 | 0.195 | 8 of 41 | 0 of 34 | 0 of 16 | yes |
| 0.80 | 0.171 | 7 of 41 | 0 of 34 | 0 of 16 | yes |
| 0.85 | 0.073 | 3 of 41 | 0 of 34 | 0 of 16 | yes |
| 0.90 | 0.024 | 1 of 41 | 0 of 34 | 0 of 16 | yes |

## Per-pair cosines (descending)

| cosine | label | hard | pair | reason |
|---|---|---|---|---|
| 0.9473 | related | yes | type-2-diabetes / diabetes-tipo-2 | the same disease in Spanish |
| 0.8984 | related | yes | tcp-congestion-control / control-de-congestion-tcp | the same concept in Spanish |
| 0.8828 | related | yes | photosynthesis / fotosintesis | the same process in Spanish |
| 0.8362 | related | yes | stoicism / estoicismo | the same concept in Spanish |
| 0.8237 | related | yes | monetary-policy / inflacion | cross-lingual: the policy and its target |
| 0.8045 | related | yes | spaced-repetition / repaso-espaciado | the same technique in Spanish |
| 0.8043 | related | no | existentialism / existentialist-ethics | the ethics is a part of the movement |
| 0.7997 | related | no | stoicism / stoic-ethics | the ethics is a part of the school |
| 0.7492 | related | no | css-grid-layout / flexbox | the two CSS layout models |
| 0.7356 | related | yes | stoic-ethics / estoicismo | Spanish account of the same virtue ethics |
| 0.7238 | related | no | type-2-diabetes / insulin-resistance | the disease's underlying mechanism |
| 0.7169 | related | no | index-fund-investing / dollar-cost-averaging | how index funds are usually bought |
| 0.7029 | related | yes | modern-crop-irrigation / riego-por-goteo | drip irrigation described in Spanish |
| 0.6903 | related | no | chord-progressions / voice-leading | how one chord moves to the next |
| 0.6864 | related | no | photosynthesis / calvin-cycle | the cycle is photosynthesis's second stage |
| 0.6848 | related | no | git-merge-strategies / merge-conflict-resolution | conflicts arise from merges |
| 0.6654 | related | no | type-2-diabetes / metformin | the first-line drug for the disease |
| 0.6494 | related | yes | tcp-congestion-control / bufferbloat | oversized queues hide the loss signal senders rely on |
| 0.6454 | related | yes | sourdough-bread-baking / masa-madre | the same practice in Spanish |
| 0.6424 | related | yes | insulin-resistance / metformin | the drug improves insulin sensitivity; no shared tags |
| 0.6384 | related | no | circle-of-fifths / chord-progressions | progressions move by fifths |
| 0.6350 | related | no | git-rebase-workflow / git-merge-strategies | the two ways to integrate a branch |
| 0.6341 | related | no | git-rebase-workflow / merge-conflict-resolution | conflicts arise during rebases |
| 0.6226 | related | yes | calvin-cycle / fotosintesis | cross-lingual whole and part |
| 0.6219 | related | yes | diabetes-tipo-2 / metformin | cross-lingual disease and its drug |
| 0.6122 | related | no | byzantine-naval-architecture / greek-fire | the weapon the dromons carried |
| 0.6111 | related | yes | bufferbloat / control-de-congestion-tcp | cross-lingual, and linked by mechanism |
| 0.5941 | related | no | flexbox / responsive-web-design | flexbox is a responsive layout tool |
| 0.5933 | related | no | medieval-crop-rotation / cover-cropping | both restore soil fertility with legumes |
| 0.5877 | related | no | existentialism / bad-faith | a core concept of the movement |
| 0.5846 | related | no | spaced-repetition / forgetting-curve | the curve the schedule is built on |
| 0.5842 | related | yes | medieval-crop-rotation / modern-crop-irrigation | both manage field-crop inputs; the weakest original positive |
| 0.5827 | unrelated | yes | circle-of-fifths / guitar-string-gauges | both music; theory vs hardware |
| 0.5799 | related | yes | sourdough-bread-baking / lacto-fermented-vegetables | same lactobacilli fermentation, different food |
| 0.5743 | related | yes | compound-interest / index-fund-investing | long-horizon compounding is the index case; different vocabulary |
| 0.5624 | unrelated | yes | sourdough-bread-baking / sous-vide-cooking | both cooking; fermentation vs water bath |
| 0.5518 | unrelated | yes | stoicism / mathematical-platonism | both philosophy; no shared subject |
| 0.5516 | related | yes | cover-cropping / mycorrhizal-networks | living roots feed soil fungi; mycology vocabulary, no shared tags |
| 0.5446 | related | no | css-grid-layout / responsive-web-design | grid is a main responsive layout tool |
| 0.5423 | unrelated | yes | sous-vide-cooking / knife-sharpening | both cooking technique; share tags only |
| 0.5396 | unrelated | yes | existentialist-ethics / mathematical-platonism | both philosophy; ethics vs metaphysics of number |
| 0.5313 | related | no | existentialist-ethics / bad-faith | bad faith is its central failure |
| 0.5300 | unrelated | yes | spaced-repetition / pomodoro-technique | both study techniques; memory vs time-boxing |
| 0.5294 | unrelated | yes | tcp-congestion-control / css-grid-layout | both computing; networking vs page layout |
| 0.5147 | related | no | zettelkasten / evergreen-notes | two methods of atomic linked notes |
| 0.5139 | related | yes | forgetting-curve / repaso-espaciado | cross-lingual, and linked by mechanism |
| 0.5135 | unrelated | yes | type-2-diabetes / migraine | both chronic diseases, shared tags; metabolic vs neurological |
| 0.5101 | unrelated | yes | bufferbloat / generational-garbage-collection | both performance topics; routers vs heaps |
| 0.5065 | unrelated | yes | speech-act-theory / mathematical-platonism | both philosophy; share only tags |
| 0.5049 | unrelated | yes | stoic-ethics / speech-act-theory | both philosophy; ethics vs language |
| 0.5027 | unrelated | yes | compound-interest / double-entry-bookkeeping | both finance; share tags only |
| 0.5018 | unrelated | yes | voice-leading / guitar-string-gauges | both music; composition vs strings |
| 0.4927 | unrelated | yes | git-rebase-workflow / generational-garbage-collection | both software tooling; no shared subject |
| 0.4888 | unrelated | yes | zettelkasten / inbox-zero | both personal workflows; notes vs email |
| 0.4884 | unrelated | yes | lacto-fermented-vegetables / knife-sharpening | both kitchen topics; no shared subject |
| 0.4866 | unrelated | yes | chord-progressions / concert-hall-acoustics | both music; harmony vs room acoustics |
| 0.4814 | unrelated | yes | medieval-crop-rotation / dairy-cattle-breeding | both agriculture; field rotation vs animal genetics |
| 0.4805 | unrelated | yes | index-fund-investing / double-entry-bookkeeping | both finance; investing vs record-keeping |
| 0.4675 | unrelated | yes | masa-madre / sous-vide-cooking | both cooking, across languages |
| 0.4634 | unrelated | yes | insulin-resistance / migraine | both medicine; no shared mechanism |
| 0.4610 | unrelated | yes | metformin / ankle-sprain-rehabilitation | both medicine; a drug vs an injury |
| 0.4604 | unrelated | yes | control-de-congestion-tcp / responsive-web-design | both web technology, across languages |
| 0.4543 | unrelated | yes | flexbox / git-merge-strategies | both developer topics; layout vs version control |
| 0.4461 | unrelated | yes | riego-por-goteo / dairy-cattle-breeding | both agriculture, across languages; no shared subject |
| 0.4455 | unrelated | yes | photosynthesis / bird-migration | both biology; plant energy vs animal behaviour |
| 0.4418 | unrelated | yes | monetary-policy / double-entry-bookkeeping | both finance; macroeconomics vs ledgers |
| 0.4389 | related | yes | stoicism / marcus-aurelius-meditations | the canonical Stoic text; never names the school |
| 0.4355 | unrelated | yes | byzantine-naval-architecture / byzantine-iconoclasm | same empire and title word; ships vs religious images |
| 0.4257 | unrelated | yes | modern-crop-irrigation / beekeeping-hive-management | both agriculture; watering vs bee colonies |
| 0.4238 | related | yes | stoic-ethics / marcus-aurelius-meditations | applied Stoic ethics in diary form |
| 0.4195 | unrelated | yes | diabetes-tipo-2 / ankle-sprain-rehabilitation | both medicine, across languages |
| 0.4071 | unrelated | no | tcp-congestion-control / photosynthesis | networking vs plant biology |
| 0.4068 | unrelated | yes | evergreen-notes / pomodoro-technique | both productivity; notes vs focus timer |
| 0.3996 | unrelated | no | medieval-crop-rotation / byzantine-naval-architecture | farming vs shipbuilding |
| 0.3911 | unrelated | no | existentialist-ethics / sourdough-bread-baking | philosophy vs baking |
| 0.3877 | unrelated | no | stoicism / sourdough-bread-baking | philosophy vs baking |
| 0.3837 | unrelated | yes | byzantine-naval-architecture / hanseatic-league | both medieval maritime history; war galleys vs a Baltic trade alliance |
| 0.3816 | unrelated | yes | greek-fire / byzantine-iconoclasm | same empire; a weapon vs a religious dispute |
| 0.3756 | unrelated | no | metformin / circle-of-fifths | pharmacology vs music theory |
| 0.3558 | unrelated | no | stoicism / medieval-crop-rotation | the original calibration's unrelated anchor |
| 0.3291 | unrelated | no | inflacion / calvin-cycle | economics vs biochemistry |
| 0.3248 | unrelated | no | css-grid-layout / migraine | web layout vs neurology |
| 0.3230 | unrelated | no | masa-madre / hanseatic-league | baking vs trade history |
| 0.3217 | unrelated | no | compound-interest / greek-fire | finance vs medieval weapons |
| 0.3195 | unrelated | no | zettelkasten / sous-vide-cooking | note-taking vs cooking |
| 0.3150 | unrelated | no | diabetes-tipo-2 / git-rebase-workflow | medicine vs version control |
| 0.3122 | unrelated | no | riego-por-goteo / bad-faith | irrigation vs philosophy, across languages |
| 0.2928 | unrelated | yes | calvin-cycle / bird-migration | both biology; no shared subject |
| 0.2774 | unrelated | no | existentialism / byzantine-naval-architecture | philosophy vs naval history |
| 0.2705 | unrelated | no | stoic-ethics / modern-crop-irrigation | philosophy vs irrigation |
| 0.2643 | unrelated | no | estoicismo / bufferbloat | philosophy vs networking, across languages |

verdict: MOVE -- lower to 0.59: recall 0.317 -> 0.707, 14 hard negatives within the band
t_min 0.57, t* 0.59, recall(t*) 0.7073, recall(0.70) 0.3171
hard negatives within 0.10 of t*: 14
