"""Hand-written, labelled calibration fixture for the proximity floor (#1052).

Reproducible from a clean checkout: every document is written here, never
resolved from a private workspace (the `evals/pair_nomination` labels do
not resolve outside one). The labelling criteria and the pre-registered
decision rule this fixture feeds are in `DESIGN.md` beside this file.

Ten domains -- philosophy, agriculture, cooking, history, computing,
medicine, finance, music, personal knowledge practice, biology -- so the
floor is not calibrated on one register of prose. Seven documents are
Spanish: `bge-m3` is multilingual (ADR-0006) and Spanish is a language the
product is used in, so cross-lingual pairs are part of what the floor must
serve.

Each `LabelledPair` carries:

- `label`: `related` (a curator would plausibly record a relation) or
  `unrelated` (a curator would dismiss the nomination);
- `hard`: for `related`, the pair is worded differently -- cross-lingual,
  cross-domain, or linked by mechanism rather than vocabulary; for
  `unrelated`, the two documents share a domain (and often tags or a title
  word) but not a subject. `hard=False` unrelated pairs share neither;
- `reason`: one line saying why the label holds.

The first 8 documents and the first 9 pairs (3 related, 6 unrelated,
`origin="smoke-v1"`) are the original smoke fixture, kept verbatim so the
calibration stays comparable with the measurement it extends.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal


@dataclass(frozen=True)
class FixtureDoc:
    concept_id: str
    """Bundle-relative path minus `.md` (OKF §2 identity)."""
    title: str
    description: str
    tags: tuple[str, ...]
    body: str


@dataclass(frozen=True)
class LabelledPair:
    a: str
    b: str
    label: Literal["related", "unrelated"]
    hard: bool
    reason: str
    origin: str = "calibration-v1"


FIXTURE_DOCS: Final[tuple[FixtureDoc, ...]] = (
    # -- smoke-v1: the original 8 documents, verbatim -------------------
    FixtureDoc(
        "concepts/stoicism",
        "Stoicism",
        "Hellenistic school holding that virtue is the only good, and that "
        "freedom comes from knowing what is up to us.",
        ("philosophy", "hellenistic", "ethics"),
        "A Hellenistic school founded by Zeno of Citium that holds virtue to "
        "be the only true good. Its practical core is the dichotomy of "
        "control: some things are up to us -- judgement, impulse, desire, "
        "aversion -- and some are not -- the body, reputation, office, the "
        "actions of others. Suffering comes from wanting what was never "
        "ours to govern.\n\nApatheia is freedom from the destructive "
        "passions, not the absence of feeling. The Stoics kept the "
        "eupatheiai, the good feelings: joy, caution, wishing. The goal is "
        "not to stop feeling but to stop being ruled by it.",
    ),
    FixtureDoc(
        "concepts/stoic-ethics",
        "Stoic Ethics",
        "The Stoic account of virtue as the sole good and the discipline "
        "of assent to impressions.",
        ("philosophy", "ethics", "stoicism"),
        "Stoic ethics holds that virtue -- wisdom, courage, justice, "
        "temperance -- is the only thing genuinely good, and vice the only "
        "thing genuinely bad; everything else, health, wealth, reputation, "
        "is merely preferred or dispreferred, never good or bad in "
        "itself.\n\nThe discipline of assent governs which impressions a "
        "person endorses as true: living in agreement with nature means "
        "assenting only to correct impressions and acting from virtue "
        "regardless of external outcome.",
    ),
    FixtureDoc(
        "concepts/existentialism",
        "Existentialism",
        "20th-century philosophy centered on individual freedom, choice, "
        "and authentic existence.",
        ("philosophy", "existentialism"),
        "Existentialism holds that existence precedes essence: a person is "
        "not born with a fixed nature but creates one through free choices "
        "made under conditions of radical freedom and responsibility. "
        "Anxiety arises from confronting that freedom directly, without "
        "the comfort of a pre-given purpose.\n\nBad faith names the "
        "attempt to escape that freedom by pretending one's choices are "
        "forced by circumstance, role, or nature, rather than owned.",
    ),
    FixtureDoc(
        "concepts/existentialist-ethics",
        "Existentialist Ethics",
        "Existentialist arguments that authentic action creates value "
        "through free choice rather than discovering it.",
        ("philosophy", "ethics", "existentialism"),
        "An existentialist ethics rejects a fixed, discoverable moral "
        "order: value is created, not found, through an individual's "
        "committed, authentic choices. Acting in bad faith -- treating a "
        "choice as though it were forced -- is the central ethical "
        "failure, not breaking an external rule.\n\nAuthenticity requires "
        "owning the full weight of one's freedom and its consequences for "
        "others, since every choice implicitly proposes a value others "
        "could also choose.",
    ),
    FixtureDoc(
        "concepts/medieval-crop-rotation",
        "Medieval Crop Rotation",
        "The three-field system used in medieval European agriculture to "
        "sustain soil fertility across seasons.",
        ("agriculture", "history", "medieval"),
        "The three-field system divided arable land into three parts: one "
        "planted with a autumn cereal, one with a spring legume, and one "
        "left fallow, rotating each year. Resting a third of the land "
        "restored soil fertility without artificial fertilizer, and the "
        "legume field fixed nitrogen for the following cereal crop.\n\n"
        "The system spread across medieval Europe from roughly the eighth "
        "century onward and raised the cultivated share of land from one "
        "half, under the older two-field system, to two thirds.",
    ),
    FixtureDoc(
        "concepts/modern-crop-irrigation",
        "Modern Crop Irrigation Systems",
        "Contemporary irrigation techniques, including drip and "
        "center-pivot systems, for row-crop agriculture.",
        ("agriculture", "irrigation", "technology"),
        "Drip irrigation delivers water directly to a plant's root zone "
        "through a network of tubing and emitters, reducing evaporation "
        "loss compared with flood irrigation and letting a grower fertigate "
        "-- deliver dissolved fertilizer -- through the same network.\n\n"
        "Center-pivot systems rotate a long sprinkler arm around a fixed "
        "point, watering a circular field; paired with soil-moisture "
        "sensors, they let a farm apply water on a schedule closer to crop "
        "demand than a fixed calendar allows.",
    ),
    FixtureDoc(
        "concepts/sourdough-bread-baking",
        "Sourdough Bread Baking",
        "Techniques for cultivating a wild-yeast starter and baking "
        "naturally leavened bread.",
        ("cooking", "baking", "fermentation"),
        "A sourdough starter is a stable culture of wild yeast and "
        "lactobacilli maintained by regular feedings of flour and water; "
        "the yeast produces the carbon dioxide that leavens the dough, "
        "while the bacteria produce the acids that give sourdough its "
        "flavor and help preserve the finished loaf.\n\nBulk fermentation "
        "and a long cold proof develop both flavor and the dough's gluten "
        "structure, so a baker times each stage by the dough's visible "
        "rise rather than by the clock alone.",
    ),
    FixtureDoc(
        "concepts/byzantine-naval-architecture",
        "Byzantine Naval Architecture",
        "Shipbuilding design of the Byzantine navy, including the dromon "
        "galley and its Greek-fire delivery.",
        ("history", "maritime", "byzantine"),
        "The dromon was the principal Byzantine war galley from roughly "
        "the sixth to twelfth centuries: a long, low, oared vessel with "
        "one or two banks of rowers and a lateen sail, built for speed and "
        "maneuverability in the eastern Mediterranean.\n\nSome dromons "
        "carried a bow-mounted siphon for projecting Greek fire, an "
        "incendiary weapon whose exact composition was a closely guarded "
        "state secret and remains only partially reconstructed today.",
    ),
    # -- philosophy --------------------------------------------------------
    FixtureDoc(
        "concepts/marcus-aurelius-meditations",
        "Meditations of Marcus Aurelius",
        "The private notebook a Roman emperor kept on campaign, addressed to himself.",
        ("books", "rome", "journaling"),
        "Written in Greek between roughly 170 and 180 CE, the Meditations "
        "were never meant for publication. The emperor reminds himself, "
        "again and again, that other people's insults cannot harm his "
        "character, that fame is brief, and that each morning he will meet "
        "the ungrateful and the arrogant and must not be moved by them.\n\n"
        "The book returns to the same exercise: separate the event from the "
        "judgement about it, act justly in the role one has been given, and "
        "accept what the whole of nature brings.",
    ),
    FixtureDoc(
        "concepts/estoicismo",
        "Estoicismo",
        "Escuela filosófica helenística según la cual la virtud es el único "
        "bien y la libertad nace de distinguir lo que depende de nosotros.",
        ("filosofía", "ética", "antigüedad"),
        "Fundada por Zenón de Citio en Atenas, la escuela estoica enseña que "
        "solo la virtud -- sabiduría, valentía, justicia y templanza -- es "
        "un bien verdadero. La salud, la riqueza o la fama son indiferentes: "
        "pueden preferirse, pero no hacen buena ni mala una vida.\n\nLa "
        "práctica central consiste en separar lo que depende de nosotros, "
        "como nuestros juicios y deseos, de lo que no depende, como el "
        "cuerpo o la opinión ajena. El sufrimiento surge de querer gobernar "
        "lo que nunca estuvo en nuestras manos.",
    ),
    FixtureDoc(
        "concepts/bad-faith",
        "Bad Faith (Mauvaise Foi)",
        "Sartre's name for the self-deception by which a person denies "
        "their own freedom.",
        ("philosophy", "sartre", "self-deception"),
        "In Being and Nothingness, Sartre describes a waiter who plays at "
        "being a waiter a little too perfectly, as if his role fixed what "
        "he is. Bad faith is this flight: treating oneself as a thing with "
        "a settled nature so as not to face the choices one is always "
        "making.\n\nIt is a lie told to oneself, and so an odd one: the "
        "liar and the deceived are the same person, who must half-know the "
        "truth in order to hide it.",
    ),
    FixtureDoc(
        "concepts/speech-act-theory",
        "Speech Act Theory",
        "The philosophy-of-language account of utterances as actions, not "
        "only descriptions.",
        ("philosophy", "language", "linguistics"),
        "J. L. Austin observed that some sentences do not describe anything: "
        "saying 'I promise' or 'I name this ship' performs the act itself. "
        "He distinguished the locutionary act of saying something, the "
        "illocutionary act done in saying it, and the perlocutionary effect "
        "on the hearer.\n\nJohn Searle later classified illocutionary acts "
        "into assertives, directives, commissives, expressives and "
        "declarations, each with its own conditions for success.",
    ),
    FixtureDoc(
        "concepts/mathematical-platonism",
        "Mathematical Platonism",
        "The view that numbers and sets are abstract objects that exist "
        "independently of minds.",
        ("philosophy", "mathematics", "metaphysics"),
        "Platonists hold that mathematical statements are true because they "
        "describe real, abstract objects: the number seven exists, outside "
        "space and time, whether or not anyone counts. Mathematicians "
        "discover theorems rather than invent them.\n\nThe standard "
        "objection is epistemic: if numbers are causally inert and outside "
        "space, it is unclear how anyone could come to know anything about "
        "them.",
    ),
    # -- agriculture -------------------------------------------------------
    FixtureDoc(
        "concepts/cover-cropping",
        "Cover Cropping",
        "Growing clover, rye or vetch between cash crops to protect and "
        "rebuild farmland soil.",
        ("agriculture", "soil", "sustainability"),
        "A cover crop is sown after harvest, or between rows, not to be "
        "sold but to keep the ground covered. Its roots hold soil against "
        "winter erosion, its biomass feeds soil organisms, and legume "
        "covers such as clover and vetch fix nitrogen that the next cash "
        "crop can use.\n\nFarmers terminate the cover in spring by rolling, "
        "mowing or grazing it, leaving a mulch that suppresses weeds and "
        "holds moisture.",
    ),
    FixtureDoc(
        "concepts/riego-por-goteo",
        "Riego por goteo",
        "Técnica de riego que lleva el agua gota a gota hasta la raíz de cada planta.",
        ("agricultura", "agua", "tecnología"),
        "El riego por goteo distribuye el agua mediante tuberías y goteros "
        "que la depositan junto a la raíz, en lugar de inundar el surco. "
        "Así se pierde mucha menos agua por evaporación y escorrentía, algo "
        "decisivo en zonas secas.\n\nEl mismo sistema permite aplicar "
        "fertilizantes disueltos, la llamada fertirrigación, y combinado "
        "con sensores de humedad del suelo ajusta el riego a la demanda "
        "real del cultivo.",
    ),
    FixtureDoc(
        "concepts/beekeeping-hive-management",
        "Beekeeping Hive Management",
        "Seasonal inspection and care of honeybee colonies kept in "
        "movable-frame hives.",
        ("agriculture", "beekeeping", "livestock"),
        "A beekeeper opens the hive every week or two in the warm season to "
        "check that the queen is laying, that the brood pattern is solid, "
        "and that the colony has room; a crowded colony prepares to swarm. "
        "Queen cells along the bottom of a frame are the usual warning.\n\n"
        "Before winter the keeper reduces the entrance, treats for varroa "
        "mites and leaves enough honey stores for the cluster to survive "
        "until spring.",
    ),
    FixtureDoc(
        "concepts/dairy-cattle-breeding",
        "Dairy Cattle Breeding",
        "Selecting and mating dairy cows to improve milk yield, health and longevity.",
        ("agriculture", "livestock", "genetics"),
        "Modern dairy herds are bred mostly by artificial insemination, "
        "choosing bulls from genomic evaluations that predict their "
        "daughters' milk, fat and protein yield, udder conformation and "
        "fertility. Holsteins dominate on volume; Jerseys give richer milk "
        "from a smaller animal.\n\nBreeders increasingly weigh health traits "
        "such as resistance to mastitis and lameness, since a cow that "
        "stays in the herd longer repays the cost of raising her.",
    ),
    # -- cooking -----------------------------------------------------------
    FixtureDoc(
        "concepts/masa-madre",
        "Masa madre",
        "Cultivo de levaduras salvajes y bacterias con el que se elabora el "
        "pan de fermentación natural.",
        ("cocina", "panadería", "fermentación"),
        "La masa madre es una mezcla de harina y agua en la que conviven "
        "levaduras silvestres y bacterias lácticas. Se mantiene viva con "
        "refrescos periódicos de harina y agua; las levaduras producen el "
        "gas que hace subir el pan y las bacterias aportan la acidez que le "
        "da sabor.\n\nUn pan de masa madre necesita fermentaciones largas, a "
        "menudo con un reposo en frío durante la noche, y el panadero se "
        "guía por el volumen de la masa más que por el reloj.",
    ),
    FixtureDoc(
        "concepts/lacto-fermented-vegetables",
        "Lacto-Fermented Vegetables",
        "Preserving cabbage, cucumbers and other vegetables in brine through "
        "lactic-acid fermentation.",
        ("food", "preservation", "microbiology"),
        "Sauerkraut, kimchi and brined pickles are made by salting "
        "vegetables and letting the lactic-acid bacteria already on them "
        "multiply. Salt holds back spoilage organisms while the "
        "lactobacilli turn sugars into lactic acid, dropping the pH until "
        "the vegetables keep for months.\n\nThe crock must keep the "
        "vegetables submerged under their own brine; exposed to air, the "
        "surface grows yeasts and moulds instead.",
    ),
    FixtureDoc(
        "concepts/sous-vide-cooking",
        "Sous-Vide Cooking",
        "Cooking vacuum-sealed food in a precisely controlled water bath.",
        ("cooking", "technique", "temperature"),
        "Food is sealed in a plastic bag and held in water kept at exactly "
        "the target temperature, for example 56 C for a medium-rare steak. "
        "Because the bath never exceeds that temperature, the meat cannot "
        "overcook, and it arrives evenly done from edge to centre.\n\n"
        "The result is usually seared briefly in a very hot pan afterwards, "
        "since the water bath produces no browning.",
    ),
    FixtureDoc(
        "concepts/knife-sharpening",
        "Kitchen Knife Sharpening",
        "Restoring a kitchen knife's edge on whetstones and keeping it with "
        "a honing rod.",
        ("cooking", "tools", "technique"),
        "Sharpening removes metal to form a new edge: the blade is drawn "
        "across a coarse whetstone at a steady angle, around 15 degrees for "
        "Japanese knives and 20 for many European ones, until a burr forms, "
        "then refined on finer grits.\n\nHoning is different: a steel rod "
        "straightens an edge that has rolled over in use without removing "
        "much metal, so it is done often and sharpening only occasionally.",
    ),
    # -- history -----------------------------------------------------------
    FixtureDoc(
        "concepts/greek-fire",
        "Greek Fire",
        "The incendiary liquid weapon the Eastern Roman navy sprayed at enemy ships.",
        ("history", "weapons", "chemistry"),
        "First recorded around 672 CE, when it helped break an Arab siege "
        "of Constantinople, Greek fire was a liquid that kept burning on "
        "water. It was pumped under pressure through bronze tubes mounted "
        "in the bows of war galleys and aimed at the enemy fleet.\n\nIts "
        "recipe was restricted to a few families of craftsmen and was "
        "eventually lost; modern guesses involve crude petroleum and "
        "resins.",
    ),
    FixtureDoc(
        "concepts/byzantine-iconoclasm",
        "Byzantine Iconoclasm",
        "The eighth- and ninth-century controversies over the veneration of "
        "religious images in the Byzantine Empire.",
        ("history", "religion", "byzantine"),
        "In 726 or 730 the emperor Leo III is said to have ordered the "
        "removal of an icon of Christ from the Chalke Gate, opening more "
        "than a century of dispute over whether venerating images was "
        "idolatry. Iconoclast emperors destroyed images; iconophile monks "
        "defended them.\n\nThe Second Council of Nicaea restored icons in "
        "787, a second iconoclast period followed, and the Triumph of "
        "Orthodoxy in 843 ended the controversy.",
    ),
    FixtureDoc(
        "concepts/hanseatic-league",
        "Hanseatic League",
        "The medieval alliance of northern German merchant towns that "
        "dominated Baltic trade.",
        ("history", "trade", "medieval"),
        "From the thirteenth century, merchant guilds of Lübeck, Hamburg "
        "and dozens of other towns banded together to protect their trade "
        "in the Baltic and North Seas. The League ran trading posts, the "
        "Kontore, in London, Bruges, Bergen and Novgorod.\n\nIts power "
        "rested on privileges and the threat of trade embargoes rather than "
        "on a state, and it faded as territorial monarchies and Atlantic "
        "routes rose in the sixteenth century.",
    ),
    # -- computing ---------------------------------------------------------
    FixtureDoc(
        "concepts/tcp-congestion-control",
        "TCP Congestion Control",
        "How TCP senders adapt their sending rate to avoid overwhelming the network.",
        ("networking", "protocols", "computing"),
        "A TCP sender keeps a congestion window limiting how much "
        "unacknowledged data may be in flight. It grows the window "
        "exponentially in slow start and linearly afterwards, and halves "
        "it when a packet loss signals that a queue somewhere has "
        "overflowed -- additive increase, multiplicative decrease.\n\n"
        "Newer algorithms such as CUBIC and BBR change how the window grows "
        "or estimate bottleneck bandwidth and round-trip time directly "
        "instead of waiting for loss.",
    ),
    FixtureDoc(
        "concepts/bufferbloat",
        "Bufferbloat",
        "Excessive latency caused by oversized packet buffers in routers and modems.",
        ("latency", "routers", "performance"),
        "When a home router holds several seconds of packets in its queue, "
        "a large download fills the buffer and every other packet waits "
        "behind it: a video call stutters and web pages crawl even though "
        "the link is not saturated in any useful sense.\n\nBecause packets "
        "are queued rather than dropped, senders never learn to slow down. "
        "Active queue management such as CoDel and fq_codel keeps queues "
        "short by dropping or marking packets early.",
    ),
    FixtureDoc(
        "concepts/control-de-congestion-tcp",
        "Control de congestión en TCP",
        "Mecanismos con los que un emisor TCP ajusta su ritmo de envío para "
        "no saturar la red.",
        ("redes", "protocolos", "informática"),
        "El emisor TCP mantiene una ventana de congestión que limita cuántos "
        "datos sin confirmar puede tener en tránsito. En el arranque lento "
        "la ventana crece de forma exponencial y después de forma lineal; "
        "ante una pérdida de paquetes se reduce a la mitad.\n\nAlgoritmos "
        "más recientes como CUBIC o BBR modifican ese crecimiento o estiman "
        "directamente el ancho de banda y el tiempo de ida y vuelta.",
    ),
    FixtureDoc(
        "concepts/css-grid-layout",
        "CSS Grid Layout",
        "The two-dimensional layout system of CSS for placing elements in "
        "rows and columns.",
        ("web", "css", "frontend"),
        "A grid container defines tracks with grid-template-columns and "
        "grid-template-rows, often using the fr unit to share free space. "
        "Children are placed onto lines or named areas, and can span "
        "several tracks in both directions at once.\n\nFunctions like "
        "repeat() and minmax() with auto-fill let a gallery reflow its "
        "column count to the viewport without media queries.",
    ),
    FixtureDoc(
        "concepts/flexbox",
        "Flexbox",
        "The one-dimensional CSS layout model for distributing space along "
        "a row or column.",
        ("web", "css", "frontend"),
        "A flex container lays its items out along a main axis; "
        "justify-content distributes them along it and align-items aligns "
        "them across it. Each item's flex-grow, flex-shrink and flex-basis "
        "decide how it absorbs extra space or gives it up.\n\nFlexbox suits "
        "toolbars, navigation bars and centring a single element, while "
        "two-dimensional page layouts are usually easier with grid.",
    ),
    FixtureDoc(
        "concepts/responsive-web-design",
        "Responsive Web Design",
        "Designing web pages that adapt their layout to any screen size.",
        ("web", "design", "mobile"),
        "Ethan Marcotte's 2010 approach combined fluid layouts, flexible "
        "images and media queries so that one page could serve phones, "
        "tablets and desktops. Content reflows instead of being shrunk or "
        "cropped.\n\nToday it usually starts mobile-first, adding columns "
        "as the viewport widens, and relies on modern layout tools that "
        "adapt without a breakpoint for every device.",
    ),
    FixtureDoc(
        "concepts/git-rebase-workflow",
        "Git Rebase Workflow",
        "Replaying a branch's commits onto a new base to keep project history linear.",
        ("git", "version-control", "workflow"),
        "git rebase main takes the commits on a feature branch and "
        "re-applies them, one by one, on top of the current main, producing "
        "new commits with new hashes. The history reads as if the work had "
        "started from the latest main.\n\nBecause it rewrites commits, a "
        "rebased branch that others already pulled must be force-pushed, "
        "which is why teams rebase only private branches.",
    ),
    FixtureDoc(
        "concepts/git-merge-strategies",
        "Git Merge Strategies",
        "Fast-forward, three-way and squash merges, and when each one fits.",
        ("git", "version-control", "collaboration"),
        "When the target branch has not moved, git can fast-forward it to "
        "the feature branch's tip. Otherwise it creates a merge commit with "
        "two parents from a three-way comparison against the common "
        "ancestor.\n\nA squash merge collapses a whole branch into one "
        "commit on the target, trading the branch's detailed history for a "
        "simpler log.",
    ),
    FixtureDoc(
        "concepts/merge-conflict-resolution",
        "Resolving Merge Conflicts",
        "What to do when two branches changed the same lines and version "
        "control cannot combine them.",
        ("git", "collaboration", "troubleshooting"),
        "A conflict appears when both sides of a merge or rebase edited the "
        "same region of a file. The tool stops and writes both versions "
        "between <<<<<<<, ======= and >>>>>>> markers for a person to "
        "reconcile.\n\nAfter editing the file into its intended final form, "
        "the developer stages it and continues the operation; a merge tool "
        "showing base, ours and theirs side by side makes the intent of "
        "each change easier to see.",
    ),
    FixtureDoc(
        "concepts/generational-garbage-collection",
        "Generational Garbage Collection",
        "Memory management that collects young objects often and old ones rarely.",
        ("computing", "memory", "runtimes"),
        "Most objects die young, so collectors in the JVM, .NET and V8 "
        "split the heap into a young generation and an old one. The young "
        "generation is collected frequently with a fast copying collector; "
        "survivors are promoted.\n\nThe old generation is collected rarely "
        "with a slower mark-sweep or mark-compact pass, and write barriers "
        "track pointers from old objects to young ones.",
    ),
    # -- medicine ----------------------------------------------------------
    FixtureDoc(
        "concepts/type-2-diabetes",
        "Type 2 Diabetes",
        "A chronic condition in which the body stops controlling blood "
        "glucose effectively.",
        ("medicine", "chronic-disease", "endocrinology"),
        "In type 2 diabetes the pancreas still makes insulin, but the body "
        "responds to it poorly and, over time, production falls behind. "
        "Blood glucose stays high, and years of hyperglycaemia damage "
        "nerves, kidneys, eyes and blood vessels.\n\nIt is diagnosed by an "
        "HbA1c of 6.5 percent or higher and managed with diet, exercise, "
        "weight loss and medication.",
    ),
    FixtureDoc(
        "concepts/insulin-resistance",
        "Insulin Resistance",
        "Reduced response of muscle, liver and fat cells to the hormone insulin.",
        ("physiology", "metabolism", "hormones"),
        "When cells respond weakly to insulin, glucose is not taken up from "
        "the blood as it should be, and the pancreas compensates by "
        "secreting more. For years the extra insulin keeps glucose normal, "
        "masking the problem.\n\nVisceral fat, inactivity and genetics all "
        "contribute; exercise and weight loss restore sensitivity, and "
        "when compensation fails, blood glucose begins to climb.",
    ),
    FixtureDoc(
        "concepts/metformin",
        "Metformin",
        "The first-line oral drug for lowering blood sugar.",
        ("pharmacology", "drugs", "medication"),
        "Metformin, a biguanide, lowers glucose mainly by reducing the "
        "liver's glucose output and modestly improving how muscles respond "
        "to insulin. It rarely causes hypoglycaemia on its own and does not "
        "cause weight gain.\n\nThe common side effects are gastrointestinal "
        "and ease with slow dose increases or extended-release tablets; it "
        "is avoided when kidney function is severely reduced.",
    ),
    FixtureDoc(
        "concepts/migraine",
        "Migraine",
        "A recurrent neurological headache disorder, often with aura, nausea "
        "and light sensitivity.",
        ("medicine", "chronic-disease", "neurology"),
        "A migraine attack is a throbbing, usually one-sided headache "
        "lasting four to seventy-two hours, worsened by movement and often "
        "accompanied by nausea and sensitivity to light and sound. About a "
        "third of patients see an aura of flickering lights first.\n\n"
        "Attacks are treated with triptans or anti-inflammatories; frequent "
        "attacks justify prevention with beta-blockers, topiramate or "
        "CGRP antibodies.",
    ),
    FixtureDoc(
        "concepts/diabetes-tipo-2",
        "Diabetes tipo 2",
        "Enfermedad crónica en la que el organismo deja de regular bien la "
        "glucosa en sangre.",
        ("medicina", "enfermedad-crónica", "endocrinología"),
        "En la diabetes tipo 2 el páncreas sigue produciendo insulina, pero "
        "los tejidos responden mal a ella y con el tiempo la producción "
        "resulta insuficiente. La glucosa se mantiene alta y, tras años de "
        "hiperglucemia, se dañan nervios, riñones, ojos y vasos "
        "sanguíneos.\n\nSe diagnostica con una HbA1c igual o superior al "
        "6,5 por ciento y se trata con dieta, ejercicio, pérdida de peso y "
        "fármacos.",
    ),
    FixtureDoc(
        "concepts/ankle-sprain-rehabilitation",
        "Ankle Sprain Rehabilitation",
        "Recovering from a stretched or torn ankle ligament, from first aid "
        "to return to sport.",
        ("medicine", "sports", "physiotherapy"),
        "Most sprains tear the anterior talofibular ligament when the foot "
        "rolls inward. The first days call for protection, elevation and "
        "gentle movement within pain limits rather than strict rest.\n\n"
        "Balance training on one leg and on unstable surfaces is what "
        "prevents the next sprain, since a damaged ligament also loses "
        "some of its position sense.",
    ),
    # -- finance -----------------------------------------------------------
    FixtureDoc(
        "concepts/compound-interest",
        "Compound Interest",
        "Interest earned on previously earned interest, so growth "
        "accelerates over time.",
        ("finance", "mathematics", "savings"),
        "With compound interest, each period's interest is added to the "
        "principal and itself earns interest afterwards. At 7 percent a "
        "year a sum doubles in roughly ten years, by the rule of 72, and "
        "quadruples in twenty.\n\nThe length of time matters more than "
        "almost anything else: money invested early has more doubling "
        "periods ahead of it.",
    ),
    FixtureDoc(
        "concepts/index-fund-investing",
        "Index Fund Investing",
        "Owning a whole market cheaply through a fund that tracks an index "
        "instead of picking stocks.",
        ("investing", "stocks", "personal-finance"),
        "An index fund holds every company in an index such as the S&P 500 "
        "in proportion to its size, so it earns the market's return minus "
        "a very small fee. Over long periods most actively managed funds "
        "fail to beat it after costs.\n\nThe approach rewards patience: "
        "reinvested dividends and decades of market growth, left alone, do "
        "most of the work.",
    ),
    FixtureDoc(
        "concepts/dollar-cost-averaging",
        "Dollar-Cost Averaging",
        "Investing a fixed amount at regular intervals regardless of price.",
        ("investing", "strategy", "personal-finance"),
        "Instead of investing a lump sum at once, an investor puts the same "
        "amount into a fund every month. More shares are bought when prices "
        "are low and fewer when they are high, and nobody has to guess the "
        "right moment to buy.\n\nMost workplace retirement plans do this "
        "automatically, one paycheque at a time.",
    ),
    FixtureDoc(
        "concepts/monetary-policy",
        "Monetary Policy",
        "How a central bank uses interest rates and its balance sheet to "
        "keep inflation in check.",
        ("economics", "central-banking", "inflation"),
        "A central bank raises its policy rate to cool an economy whose "
        "prices are rising too fast: borrowing becomes more expensive, "
        "spending and investment slow, and inflation eases, usually with a "
        "lag of a year or more. It cuts the rate to support a weak "
        "economy.\n\nMost central banks target inflation of about 2 percent "
        "and, when rates reach zero, turn to buying bonds.",
    ),
    FixtureDoc(
        "concepts/inflacion",
        "Inflación",
        "Aumento sostenido del nivel general de precios y cómo lo combaten "
        "los bancos centrales.",
        ("economía", "precios", "banca-central"),
        "La inflación es la subida continuada de los precios, que reduce lo "
        "que puede comprarse con la misma cantidad de dinero. Se mide con "
        "índices de precios al consumo.\n\nPara frenarla, el banco central "
        "sube los tipos de interés: el crédito se encarece, el gasto se "
        "modera y los precios se estabilizan con cierto retraso. La mayoría "
        "de los bancos centrales apunta a una inflación cercana al 2 por "
        "ciento.",
    ),
    FixtureDoc(
        "concepts/double-entry-bookkeeping",
        "Double-Entry Bookkeeping",
        "Recording every transaction as equal debits and credits in two accounts.",
        ("accounting", "finance", "business"),
        "Every transaction touches at least two accounts: buying stock for "
        "cash debits inventory and credits cash by the same amount. "
        "Because debits always equal credits, the trial balance checks the "
        "books for arithmetic errors.\n\nThe method was codified by Luca "
        "Pacioli in 1494 and remains the basis of the balance sheet and the "
        "income statement.",
    ),
    # -- music -------------------------------------------------------------
    FixtureDoc(
        "concepts/circle-of-fifths",
        "Circle of Fifths",
        "The arrangement of the twelve keys by ascending perfect fifths.",
        ("music", "theory", "harmony"),
        "Going clockwise from C, each key is a fifth above the last and "
        "adds one sharp: G, D, A, E; going anticlockwise each adds a flat. "
        "Neighbouring keys share all but one note, so modulating between "
        "them sounds smooth.\n\nMusicians use it to read key signatures, "
        "find relative minors and see which chords belong together.",
    ),
    FixtureDoc(
        "concepts/chord-progressions",
        "Chord Progressions",
        "Sequences of chords that give a piece its harmonic movement.",
        ("music", "harmony", "songwriting"),
        "Progressions are usually written in Roman numerals relative to the "
        "key: I-IV-V-I underlies countless folk and blues songs, and "
        "I-V-vi-IV countless pop songs. The pull of V back to I is what "
        "makes a cadence feel resolved.\n\nMany progressions move the root "
        "down by fifths, as in ii-V-I, the backbone of jazz harmony.",
    ),
    FixtureDoc(
        "concepts/voice-leading",
        "Voice Leading",
        "Moving each note of one chord smoothly to a note of the next.",
        ("music", "composition", "counterpoint"),
        "Good voice leading treats each note of a chord as a separate "
        "melodic line. Common tones are held, other voices move by step, "
        "and parallel fifths and octaves are avoided in classical style.\n\n"
        "The leading tone rises to the tonic and the seventh of a chord "
        "falls by step, which is why a dominant seventh chord sounds as if "
        "it wants to resolve.",
    ),
    FixtureDoc(
        "concepts/guitar-string-gauges",
        "Guitar String Gauges",
        "How string thickness changes a guitar's tension, tone and playability.",
        ("music", "guitar", "instruments"),
        "Gauges are named by the thickness of the high E string in "
        "thousandths of an inch: a set of 10s is common on electric "
        "guitars, 12s on acoustics. Heavier strings sit at higher tension, "
        "sound fuller and resist bending.\n\nChanging gauge changes the "
        "neck's relief and the nut slots, so a new gauge often calls for a "
        "setup.",
    ),
    FixtureDoc(
        "concepts/concert-hall-acoustics",
        "Concert Hall Acoustics",
        "How a hall's shape and materials shape the sound an audience hears.",
        ("music", "acoustics", "architecture"),
        "Reverberation time, the time sound takes to decay by 60 decibels, "
        "is around two seconds in the best symphony halls. Narrow "
        "shoebox-shaped halls such as Vienna's Musikverein deliver strong "
        "early reflections from the side walls.\n\nArchitects tune a hall "
        "with diffusing surfaces, balconies and adjustable curtains or "
        "canopies.",
    ),
    # -- personal knowledge practice ---------------------------------------
    FixtureDoc(
        "concepts/spaced-repetition",
        "Spaced Repetition",
        "Reviewing material at expanding intervals to remember it long-term.",
        ("learning", "memory", "study"),
        "Instead of cramming, a learner reviews a fact just before it would "
        "be forgotten: after a day, then three days, a week, a month. Each "
        "successful recall lengthens the next interval.\n\nFlashcard "
        "programs such as Anki schedule reviews automatically from how "
        "easily each card was recalled.",
    ),
    FixtureDoc(
        "concepts/forgetting-curve",
        "Forgetting Curve",
        "Ebbinghaus's finding that memories fade fast at first and then more slowly.",
        ("psychology", "memory", "research"),
        "In the 1880s Hermann Ebbinghaus memorised lists of nonsense "
        "syllables and tested himself at intervals. Retention dropped "
        "steeply within the first hour and day, then levelled off.\n\n"
        "He also found that each relearning made the material fade more "
        "slowly afterwards -- the effect later study schedules were built "
        "on.",
    ),
    FixtureDoc(
        "concepts/repaso-espaciado",
        "Repaso espaciado",
        "Técnica de estudio que reparte los repasos en intervalos crecientes "
        "para recordar a largo plazo.",
        ("aprendizaje", "memoria", "estudio"),
        "En lugar de estudiar todo la víspera del examen, se repasa cada "
        "dato justo antes de olvidarlo: al día siguiente, a los tres días, "
        "a la semana, al mes. Cada recuerdo exitoso alarga el siguiente "
        "intervalo.\n\nAplicaciones de tarjetas como Anki calculan "
        "automáticamente cuándo toca repasar cada tarjeta.",
    ),
    FixtureDoc(
        "concepts/zettelkasten",
        "Zettelkasten",
        "Niklas Luhmann's slip-box method of linked, atomic notes.",
        ("note-taking", "writing", "knowledge-management"),
        "The sociologist Niklas Luhmann kept some 90,000 index cards, each "
        "holding one idea in his own words, numbered and linked to related "
        "cards. New notes were filed next to the notes they continued.\n\n"
        "He described the box as a conversation partner: following links "
        "surfaced connections he had not planned, which fed more than fifty "
        "books.",
    ),
    FixtureDoc(
        "concepts/evergreen-notes",
        "Evergreen Notes",
        "Notes written to be refined and linked over years rather than "
        "filed and forgotten.",
        ("note-taking", "thinking", "writing"),
        "Evergreen notes are concept-oriented, atomic and densely linked. "
        "Each is titled with a claim and rewritten whenever understanding "
        "improves, so the collection grows into a web of ideas instead of "
        "a pile of transcripts.\n\nThe practice treats notes as a tool for "
        "thinking rather than for storing what one has read.",
    ),
    FixtureDoc(
        "concepts/pomodoro-technique",
        "Pomodoro Technique",
        "Working in 25-minute focused intervals separated by short breaks.",
        ("productivity", "focus", "study"),
        "Francesco Cirillo named the method after his tomato-shaped kitchen "
        "timer. Work goes in 25-minute blocks, each followed by a "
        "five-minute break, with a longer break after four blocks.\n\n"
        "The timer makes interruptions visible and turns a vague task into "
        "a count of blocks, which helps with estimating and starting.",
    ),
    FixtureDoc(
        "concepts/inbox-zero",
        "Inbox Zero",
        "An email practice of processing every message to a decision "
        "rather than letting them pile up.",
        ("productivity", "email", "workflow"),
        "Merlin Mann's approach treats the inbox as a place to decide, not "
        "to store: each message is deleted, delegated, answered if it takes "
        "two minutes, deferred to a task list or archived.\n\nChecking mail "
        "at set times, rather than continuously, keeps processing from "
        "swallowing the day.",
    ),
    # -- biology -----------------------------------------------------------
    FixtureDoc(
        "concepts/photosynthesis",
        "Photosynthesis",
        "How plants, algae and cyanobacteria turn light, water and carbon "
        "dioxide into sugar.",
        ("biology", "plants", "energy"),
        "In the light-dependent reactions, chlorophyll in the thylakoid "
        "membranes absorbs light and splits water, releasing oxygen and "
        "producing ATP and NADPH.\n\nThose carriers then power the fixation "
        "of carbon dioxide into sugars in the stroma of the chloroplast, "
        "the ultimate source of almost all food energy on Earth.",
    ),
    FixtureDoc(
        "concepts/calvin-cycle",
        "Calvin Cycle",
        "The light-independent reactions that fix carbon dioxide into "
        "sugar in the chloroplast stroma.",
        ("biochemistry", "metabolism", "plants"),
        "The enzyme RuBisCO attaches carbon dioxide to ribulose "
        "bisphosphate; the product is reduced using ATP and NADPH into "
        "glyceraldehyde-3-phosphate, a three-carbon sugar.\n\nMost of that "
        "sugar regenerates ribulose bisphosphate so the cycle can continue; "
        "one molecule in six leaves to build glucose and starch.",
    ),
    FixtureDoc(
        "concepts/fotosintesis",
        "Fotosíntesis",
        "Proceso por el que plantas y algas convierten luz, agua y dióxido "
        "de carbono en azúcares.",
        ("biología", "plantas", "energía"),
        "En la fase luminosa, la clorofila de los tilacoides absorbe la luz "
        "y rompe moléculas de agua, liberando oxígeno y generando ATP y "
        "NADPH.\n\nEn la fase oscura, dentro del estroma del cloroplasto, "
        "esa energía se usa para fijar el dióxido de carbono en azúcares, "
        "base de casi toda la energía de los seres vivos.",
    ),
    FixtureDoc(
        "concepts/bird-migration",
        "Bird Migration",
        "Seasonal long-distance journeys of birds between breeding and "
        "wintering grounds.",
        ("biology", "animals", "ecology"),
        "Arctic terns fly from the Arctic to the Antarctic and back each "
        "year. Migrants navigate using the sun, the stars, the Earth's "
        "magnetic field and landmarks, and many travel at night.\n\nBefore "
        "departure they fatten rapidly, sometimes doubling their weight, "
        "and stopover sites where they refuel are critical to their "
        "survival.",
    ),
    FixtureDoc(
        "concepts/mycorrhizal-networks",
        "Mycorrhizal Networks",
        "Fungi that colonise plant roots and trade soil nutrients for sugars.",
        ("mycology", "ecology", "fungi"),
        "Mycorrhizal fungi grow into or around plant roots and extend fine "
        "threads, hyphae, far into the soil. They deliver phosphorus and "
        "water the roots could not reach and receive plant sugars in "
        "return.\n\nThe hyphae also bind soil particles into stable "
        "aggregates, and tilling or leaving ground bare without living "
        "roots starves and breaks up the network.",
    ),
)


def _r(
    a: str, b: str, reason: str, *, hard: bool = False, origin: str = ""
) -> LabelledPair:
    return LabelledPair(
        f"concepts/{a}",
        f"concepts/{b}",
        "related",
        hard,
        reason,
        origin or "calibration-v1",
    )


def _u(a: str, b: str, reason: str, *, hard: bool, origin: str = "") -> LabelledPair:
    return LabelledPair(
        f"concepts/{a}",
        f"concepts/{b}",
        "unrelated",
        hard,
        reason,
        origin or "calibration-v1",
    )


_SMOKE: Final = "smoke-v1"

PAIRS: Final[tuple[LabelledPair, ...]] = (
    # -- smoke-v1: the original 9 labelled pairs ---------------------------
    _r("stoicism", "stoic-ethics", "the ethics is a part of the school", origin=_SMOKE),
    _r(
        "existentialism",
        "existentialist-ethics",
        "the ethics is a part of the movement",
        origin=_SMOKE,
    ),
    _r(
        "medieval-crop-rotation",
        "modern-crop-irrigation",
        "both manage field-crop inputs; the weakest original positive",
        hard=True,
        origin=_SMOKE,
    ),
    _u(
        "stoicism",
        "medieval-crop-rotation",
        "the original calibration's unrelated anchor",
        hard=False,
        origin=_SMOKE,
    ),
    _u(
        "stoicism",
        "sourdough-bread-baking",
        "philosophy vs baking",
        hard=False,
        origin=_SMOKE,
    ),
    _u(
        "existentialism",
        "byzantine-naval-architecture",
        "philosophy vs naval history",
        hard=False,
        origin=_SMOKE,
    ),
    _u(
        "stoic-ethics",
        "modern-crop-irrigation",
        "philosophy vs irrigation",
        hard=False,
        origin=_SMOKE,
    ),
    _u(
        "existentialist-ethics",
        "sourdough-bread-baking",
        "philosophy vs baking",
        hard=False,
        origin=_SMOKE,
    ),
    _u(
        "medieval-crop-rotation",
        "byzantine-naval-architecture",
        "farming vs shipbuilding",
        hard=False,
        origin=_SMOKE,
    ),
    # -- related -----------------------------------------------------------
    _r(
        "stoicism",
        "marcus-aurelius-meditations",
        "the canonical Stoic text; never names the school",
        hard=True,
    ),
    _r(
        "stoic-ethics",
        "marcus-aurelius-meditations",
        "applied Stoic ethics in diary form",
        hard=True,
    ),
    _r("stoicism", "estoicismo", "the same concept in Spanish", hard=True),
    _r(
        "stoic-ethics",
        "estoicismo",
        "Spanish account of the same virtue ethics",
        hard=True,
    ),
    _r("existentialism", "bad-faith", "a core concept of the movement"),
    _r("existentialist-ethics", "bad-faith", "bad faith is its central failure"),
    _r(
        "medieval-crop-rotation",
        "cover-cropping",
        "both restore soil fertility with legumes",
    ),
    _r(
        "modern-crop-irrigation",
        "riego-por-goteo",
        "drip irrigation described in Spanish",
        hard=True,
    ),
    _r(
        "cover-cropping",
        "mycorrhizal-networks",
        "living roots feed soil fungi; mycology vocabulary, no shared tags",
        hard=True,
    ),
    _r(
        "sourdough-bread-baking",
        "masa-madre",
        "the same practice in Spanish",
        hard=True,
    ),
    _r(
        "sourdough-bread-baking",
        "lacto-fermented-vegetables",
        "same lactobacilli fermentation, different food",
        hard=True,
    ),
    _r("byzantine-naval-architecture", "greek-fire", "the weapon the dromons carried"),
    _r(
        "tcp-congestion-control",
        "bufferbloat",
        "oversized queues hide the loss signal senders rely on",
        hard=True,
    ),
    _r(
        "tcp-congestion-control",
        "control-de-congestion-tcp",
        "the same concept in Spanish",
        hard=True,
    ),
    _r(
        "bufferbloat",
        "control-de-congestion-tcp",
        "cross-lingual, and linked by mechanism",
        hard=True,
    ),
    _r("css-grid-layout", "flexbox", "the two CSS layout models"),
    _r(
        "css-grid-layout",
        "responsive-web-design",
        "grid is a main responsive layout tool",
    ),
    _r("flexbox", "responsive-web-design", "flexbox is a responsive layout tool"),
    _r(
        "git-rebase-workflow",
        "git-merge-strategies",
        "the two ways to integrate a branch",
    ),
    _r(
        "git-merge-strategies",
        "merge-conflict-resolution",
        "conflicts arise from merges",
    ),
    _r(
        "git-rebase-workflow",
        "merge-conflict-resolution",
        "conflicts arise during rebases",
    ),
    _r("type-2-diabetes", "insulin-resistance", "the disease's underlying mechanism"),
    _r("type-2-diabetes", "metformin", "the first-line drug for the disease"),
    _r(
        "insulin-resistance",
        "metformin",
        "the drug improves insulin sensitivity; no shared tags",
        hard=True,
    ),
    _r("type-2-diabetes", "diabetes-tipo-2", "the same disease in Spanish", hard=True),
    _r("diabetes-tipo-2", "metformin", "cross-lingual disease and its drug", hard=True),
    _r(
        "compound-interest",
        "index-fund-investing",
        "long-horizon compounding is the index case; different vocabulary",
        hard=True,
    ),
    _r(
        "index-fund-investing",
        "dollar-cost-averaging",
        "how index funds are usually bought",
    ),
    _r(
        "monetary-policy",
        "inflacion",
        "cross-lingual: the policy and its target",
        hard=True,
    ),
    _r("circle-of-fifths", "chord-progressions", "progressions move by fifths"),
    _r("chord-progressions", "voice-leading", "how one chord moves to the next"),
    _r("spaced-repetition", "forgetting-curve", "the curve the schedule is built on"),
    _r(
        "spaced-repetition",
        "repaso-espaciado",
        "the same technique in Spanish",
        hard=True,
    ),
    _r(
        "forgetting-curve",
        "repaso-espaciado",
        "cross-lingual, and linked by mechanism",
        hard=True,
    ),
    _r("zettelkasten", "evergreen-notes", "two methods of atomic linked notes"),
    _r("photosynthesis", "calvin-cycle", "the cycle is photosynthesis's second stage"),
    _r("photosynthesis", "fotosintesis", "the same process in Spanish", hard=True),
    _r("calvin-cycle", "fotosintesis", "cross-lingual whole and part", hard=True),
    # -- unrelated, hard: same domain, different subject -------------------
    _u(
        "stoic-ethics",
        "speech-act-theory",
        "both philosophy; ethics vs language",
        hard=True,
    ),
    _u(
        "existentialist-ethics",
        "mathematical-platonism",
        "both philosophy; ethics vs metaphysics of number",
        hard=True,
    ),
    _u(
        "speech-act-theory",
        "mathematical-platonism",
        "both philosophy; share only tags",
        hard=True,
    ),
    _u(
        "stoicism",
        "mathematical-platonism",
        "both philosophy; no shared subject",
        hard=True,
    ),
    _u(
        "modern-crop-irrigation",
        "beekeeping-hive-management",
        "both agriculture; watering vs bee colonies",
        hard=True,
    ),
    _u(
        "medieval-crop-rotation",
        "dairy-cattle-breeding",
        "both agriculture; field rotation vs animal genetics",
        hard=True,
    ),
    _u(
        "riego-por-goteo",
        "dairy-cattle-breeding",
        "both agriculture, across languages; no shared subject",
        hard=True,
    ),
    _u(
        "sourdough-bread-baking",
        "sous-vide-cooking",
        "both cooking; fermentation vs water bath",
        hard=True,
    ),
    _u(
        "sous-vide-cooking",
        "knife-sharpening",
        "both cooking technique; share tags only",
        hard=True,
    ),
    _u(
        "lacto-fermented-vegetables",
        "knife-sharpening",
        "both kitchen topics; no shared subject",
        hard=True,
    ),
    _u("masa-madre", "sous-vide-cooking", "both cooking, across languages", hard=True),
    _u(
        "byzantine-naval-architecture",
        "byzantine-iconoclasm",
        "same empire and title word; ships vs religious images",
        hard=True,
    ),
    _u(
        "greek-fire",
        "byzantine-iconoclasm",
        "same empire; a weapon vs a religious dispute",
        hard=True,
    ),
    _u(
        "byzantine-naval-architecture",
        "hanseatic-league",
        "both medieval maritime history; war galleys vs a Baltic trade alliance",
        hard=True,
    ),
    _u(
        "tcp-congestion-control",
        "css-grid-layout",
        "both computing; networking vs page layout",
        hard=True,
    ),
    _u(
        "bufferbloat",
        "generational-garbage-collection",
        "both performance topics; routers vs heaps",
        hard=True,
    ),
    _u(
        "git-rebase-workflow",
        "generational-garbage-collection",
        "both software tooling; no shared subject",
        hard=True,
    ),
    _u(
        "flexbox",
        "git-merge-strategies",
        "both developer topics; layout vs version control",
        hard=True,
    ),
    _u(
        "control-de-congestion-tcp",
        "responsive-web-design",
        "both web technology, across languages",
        hard=True,
    ),
    _u(
        "type-2-diabetes",
        "migraine",
        "both chronic diseases, shared tags; metabolic vs neurological",
        hard=True,
    ),
    _u(
        "metformin",
        "ankle-sprain-rehabilitation",
        "both medicine; a drug vs an injury",
        hard=True,
    ),
    _u(
        "insulin-resistance",
        "migraine",
        "both medicine; no shared mechanism",
        hard=True,
    ),
    _u(
        "diabetes-tipo-2",
        "ankle-sprain-rehabilitation",
        "both medicine, across languages",
        hard=True,
    ),
    _u(
        "index-fund-investing",
        "double-entry-bookkeeping",
        "both finance; investing vs record-keeping",
        hard=True,
    ),
    _u(
        "compound-interest",
        "double-entry-bookkeeping",
        "both finance; share tags only",
        hard=True,
    ),
    _u(
        "monetary-policy",
        "double-entry-bookkeeping",
        "both finance; macroeconomics vs ledgers",
        hard=True,
    ),
    _u(
        "circle-of-fifths",
        "guitar-string-gauges",
        "both music; theory vs hardware",
        hard=True,
    ),
    _u(
        "chord-progressions",
        "concert-hall-acoustics",
        "both music; harmony vs room acoustics",
        hard=True,
    ),
    _u(
        "voice-leading",
        "guitar-string-gauges",
        "both music; composition vs strings",
        hard=True,
    ),
    _u(
        "spaced-repetition",
        "pomodoro-technique",
        "both study techniques; memory vs time-boxing",
        hard=True,
    ),
    _u(
        "zettelkasten",
        "inbox-zero",
        "both personal workflows; notes vs email",
        hard=True,
    ),
    _u(
        "evergreen-notes",
        "pomodoro-technique",
        "both productivity; notes vs focus timer",
        hard=True,
    ),
    _u(
        "photosynthesis",
        "bird-migration",
        "both biology; plant energy vs animal behaviour",
        hard=True,
    ),
    _u("calvin-cycle", "bird-migration", "both biology; no shared subject", hard=True),
    # -- unrelated, easy: different domains --------------------------------
    _u(
        "tcp-congestion-control",
        "photosynthesis",
        "networking vs plant biology",
        hard=False,
    ),
    _u("metformin", "circle-of-fifths", "pharmacology vs music theory", hard=False),
    _u("compound-interest", "greek-fire", "finance vs medieval weapons", hard=False),
    _u("zettelkasten", "sous-vide-cooking", "note-taking vs cooking", hard=False),
    _u("css-grid-layout", "migraine", "web layout vs neurology", hard=False),
    _u(
        "riego-por-goteo",
        "bad-faith",
        "irrigation vs philosophy, across languages",
        hard=False,
    ),
    _u(
        "diabetes-tipo-2",
        "git-rebase-workflow",
        "medicine vs version control",
        hard=False,
    ),
    _u("masa-madre", "hanseatic-league", "baking vs trade history", hard=False),
    _u(
        "estoicismo",
        "bufferbloat",
        "philosophy vs networking, across languages",
        hard=False,
    ),
    _u("inflacion", "calvin-cycle", "economics vs biochemistry", hard=False),
)
