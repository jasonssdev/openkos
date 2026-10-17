// The component gallery (desktop/DESIGN.md): every component of the design
// system in every state, and the real To review, Status and Ask screens, on
// made-up OpenKOS data.
// A development page only: nothing here reaches the engine.
import type { Schemas } from "../client/index.ts";
import type { AskState } from "./ask/model.ts";
import { renderAsk } from "./ask/render.ts";
import { ReviewModel, type ReviewClient } from "./review/model.ts";
import { renderReview } from "./review/render.ts";
import { renderStatus } from "./status.ts";
import {
  answerList,
  appShell,
  badge,
  choiceList,
  callout,
  compare,
  dialogPanel,
  disclosure,
  el,
  emptyState,
  facts,
  listRow,
  navItem,
  openDialog,
  pageHeader,
  progressBar,
  segmented,
  skeleton,
  spinner,
  textField,
  uiButton,
  uiCard,
  uiList,
  type NavEntry,
} from "./system/index.ts";

const noop = () => {};

function section(title: string, note: string, ...children: HTMLElement[]): HTMLElement {
  return el(
    "section",
    { class: "gallery-section" },
    el("h2", { class: "gallery-section__title" }, title),
    note ? el("p", { class: "gallery-section__note" }, note) : null,
    ...children,
  );
}

function specimens(...children: HTMLElement[]): HTMLElement {
  return el("div", { class: "gallery-specimens" }, ...children);
}

function stack(...children: HTMLElement[]): HTMLElement {
  return el("div", { class: "gallery-specimens gallery-specimens--stack" }, ...children);
}

function specimen(label: string, ...children: HTMLElement[]): HTMLElement {
  return el("div", { class: "gallery-specimen" }, el("span", { class: "gallery-specimen__label" }, label), ...children);
}

function wide(label: string, ...children: HTMLElement[]): HTMLElement {
  const node = specimen(label, ...children);
  node.classList.add("gallery-specimen--wide");
  return node;
}

function frame(child: HTMLElement): HTMLElement {
  return el("div", { class: "gallery-frame" }, child);
}

// -- made-up data -------------------------------------------------------------- //

interface MockPage {
  title: string;
  kind: string;
  excerpt: string;
  sources: string[];
}

const OSEI: MockPage = {
  title: "Marguerite Osei",
  kind: "A person · Private",
  excerpt: "Product lead at Fieldnote. Ran the March planning workshop and owns the onboarding redesign.",
  sources: ["From: meeting-2026-03-12.md, added 12 March", "From: team.md, added 2 March"],
};

const M_OSEI: MockPage = {
  title: "M. Osei",
  kind: "A person · Private",
  excerpt: "Named as the reviewer of the onboarding redesign proposal, due before the April release.",
  sources: ["From: onboarding-proposal.md, added 3 April"],
};

function pageCard(page: MockPage): HTMLElement {
  return uiCard(
    { title: page.title, meta: page.kind, footer: el("ul", { class: "ui-plain-list" }, ...page.sources.map((line) => el("li", {}, line))) },
    el("p", {}, page.excerpt),
  );
}

// -- composition: To review ------------------------------------------------------ //

const NAV: NavEntry[] = [
  { id: "ask", label: "Ask" },
  { id: "review", label: "To review", count: 4 },
  { id: "status", label: "Status" },
];

function mockPage(id: string, title: string, okf_type: string, excerpt: string, file: string): Schemas["Page"] {
  return { concept_id: id, title, okf_type, excerpt, sensitivity: "private", sources: [{ source_id: file, file_name: file, added_at: "2026-03-12T09:00:00Z" }] };
}

const MOCK_PAGES = new Map(
  [
    mockPage("people/osei", "Marguerite Osei", "Person", OSEI.excerpt, "meeting-2026-03-12.md"),
    mockPage("people/m-osei", "M. Osei", "Person", M_OSEI.excerpt, "onboarding-proposal.md"),
    mockPage("events/lisbon", "Lisbon trip", "Event", "Five days in Lisbon in October with the design team.", "travel.md"),
    mockPage("events/offsite", "Team offsite", "Event", "The autumn offsite. Venue still to be confirmed.", "offsite-plan.md"),
    mockPage("concepts/adk", "ADK", "Concept", "An agent development kit, as used in the course notes.", "notes.md"),
    mockPage("projects/adk-course", "ADK course", "Project", "The six-week course on building agents.", "course.md"),
  ].map((page) => [page.concept_id, page]),
);

function mockIdentity(key: string, members: string[], over: Partial<Schemas["Decision"]> = {}): Schemas["Decision"] {
  return {
    decision_key: key,
    kind: "identity",
    targets: members,
    fresh: true,
    evaluation: "judged_same",
    actions: ["merge", "keep_distinct", "skip"],
    withheld: [],
    resolves_via: "",
    detail: { member_ids: members, okf_type: "Person", tier: "exact", verdict: "same", rationale: "Both own the onboarding redesign at Fieldnote." },
    ...over,
  };
}

const MOCK_DECISIONS: Schemas["Decision"][] = [
  mockIdentity("osei", ["people/osei", "people/m-osei"]),
  mockIdentity("lisbon", ["events/lisbon", "events/offsite"], { fresh: false }),
  mockIdentity("adk", ["concepts/adk", "projects/adk-course"], {
    actions: ["merge_group", "keep_distinct", "skip"],
    withheld: [{ action: "merge", reason: "cross_type" }],
  }),
  { ...mockIdentity("offsite-date", ["events/offsite"]), kind: "contradiction", actions: [], detail: null, evaluation: "not_applicable" },
];

const never = () => new Promise<never>(() => {});

// A client over the made-up data: it reads, previews and says it applied, and
// never writes. Only what the screen calls when you click around is answered.
const MOCK_CLIENT = {
  decisions: async () => ({ decisions: MOCK_DECISIONS }),
  readPage: async (id: string) => MOCK_PAGES.get(id) ?? never(),
  mergedConcepts: async () => ({ concepts: [] }),
  refreshQueue: async () => ({}),
  previewDecision: async (key: string, body: Schemas["PreviewRequest"]) => ({
    decision_key: key,
    action: body.action,
    version: { decision_key: key, content_digest: "", payload_digest: "" },
    plan:
      body.action === "keep_distinct"
        ? { kind: "keep_distinct", member_ids: [] }
        : {
            kind: "merge",
            survivor_id: body.selection?.survivor_id ?? "people/osei",
            absorbed_id: "people/m-osei",
            survivor_criterion: "chosen",
            survivor_type: "Person",
            cross_source_same_pair: true,
            cross_type_concern: null,
            files_rewritten: 2,
            kept_distinct_ids: [],
            steps: [],
          },
  }),
  applyDecision: async (key: string, body: Schemas["ApplyRequest"]) => ({
    applied: { action: body.action, commit_sha: "", decision_key: key, merged: [["people/osei", "people/m-osei"]] },
    warnings: [],
  }),
  quoteJudging: async () => ({
    kind: "identity",
    total: 1,
    served: 0,
    uncertain: 0,
    to_judge: 1,
    calls: 1,
    binding: { backend: "ollama", model: "m", is_local: true, kind: "identity", effective_confidential: false, rubric_digest: null },
    notices: [],
    token: {},
  }),
} as unknown as ReviewClient;

// The real To review screen (review/render.ts) on made-up data, so the gallery shows what ships.
function reviewComposition(): HTMLElement {
  const shell = appShell({ nav: NAV, current: "review", onNavigate: noop, footer: badge("success", "Engine running") });
  const view = el("div", { class: "ui-fill" });
  // Already "list", so drawing it does not move the gallery's focus to its title.
  view.dataset["view"] = "list";
  const live = { status: el("p", { class: "ui-visually-hidden" }), alert: el("p", { class: "ui-visually-hidden" }) };
  const model = new ReviewModel(MOCK_CLIENT);
  model.subscribe((state) => renderReview(view, live, model, state));
  void model.load();
  shell.main.append(view, live.status, live.alert);
  return frame(shell.root);
}

// -- composition: status --------------------------------------------------------- //

// The real status screen (status.ts) on made-up data, so the gallery shows what ships.
function statusComposition(): HTMLElement {
  const shell = appShell({ nav: NAV, current: "status", onNavigate: noop, footer: badge("success", "Engine running") });
  const view = el("div", { class: "ui-view" });
  // Already "status", so drawing it does not move the gallery's focus to its title.
  view.dataset["view"] = "status";
  const status = {
    root: "/Users/me/Notes/good-life",
    sources: 46,
    concepts: 128,
    open_decisions_by_kind: { identity: 3 },
  } as unknown as Schemas["WorkspaceStatus"];
  renderStatus(
    view,
    { engine: { text: "The engine is running." }, workspace: { kind: "read", status } },
    { expanded: new Set(), onToggle: noop },
  );
  shell.main.append(view);
  return frame(shell.root);
}

// -- composition: ask ------------------------------------------------------------ //

// The real Ask screen (ask/render.ts) on a made-up answer, with one earlier question.
function askComposition(): HTMLElement {
  const shell = appShell({ nav: NAV, current: "ask", onNavigate: noop, footer: badge("success", "Engine running") });
  const view = el("div", { class: "ui-view" });
  const answer: Schemas["Answer"] = {
    question: "Who looks after the allotment on Saturdays?",
    outcome: "answered",
    answer:
      "Maria Lopez opens the allotment on Saturday mornings and keeps the volunteer rota.\n\nShe hands over to the Sunday group at noon.",
    no_match_cause: "none",
    citations: [
      { concept_id: "projects/allotment-volunteers", title: "Allotment volunteers", confidential: false, excerpted: false, history: null },
      { concept_id: "people/maria-lopez", title: "Maria Lopez", confidential: false, excerpted: false, history: null },
    ],
    omitted: [],
    attribution: "reported",
    withheld: 0,
    sufficiency_checked: true,
    sufficiency_degraded: false,
    lexical_unavailable: false,
    dense_unavailable: false,
    stale_indexes: [],
  };
  const state: AskState = {
    ready: true,
    running: false,
    blank: false,
    shown: 2,
    turns: [
      { id: 2, question: answer.question, result: { kind: "answer", answer } },
      {
        id: 1,
        question: "When is the next team offsite?",
        result: { kind: "answer", answer: { ...answer, outcome: "no_match", answer: null, citations: [], no_match_cause: "zero_hits" } },
      },
    ],
  };
  renderAsk(view, state, { ask: noop, retry: noop, show: noop, expanded: new Set(), onToggle: noop });
  shell.main.append(view);
  return frame(shell.root);
}

// -- the components --------------------------------------------------------------- //

function foundations(): HTMLElement[] {
  const swatch = (name: string) => specimen(`--${name}`, el("div", { class: `gallery-swatch gallery-swatch--${name}` }));
  const text = (name: string, sample: string) => el("p", { class: `gallery-text--${name}` }, `${sample} (--${name === "accent" ? "accent-text" : `text-${name}`})`);
  const type = (step: string, sample: string, medium = false) =>
    specimen(`--font-size-${step}`, el("p", { class: `gallery-type--${step}${medium ? " gallery-type--medium" : ""}` }, sample));
  return [
    section(
      "Surfaces and borders",
      "Ink is the canvas. Each step lifts it toward Paper; hairlines separate instead of boxes.",
      specimens(...["surface-0", "surface-1", "surface-2", "surface-3", "border-subtle", "border-strong", "accent"].map(swatch)),
    ),
    section(
      "Text",
      "Three levels and the accent. Every level meets AA on every surface.",
      stack(
        text("primary", "Marguerite Osei owns the onboarding redesign."),
        text("secondary", "Product lead at Fieldnote."),
        text("tertiary", "From 3 sources · updated today"),
        text("accent", "Open the page"),
      ),
    ),
    section(
      "Type scale",
      "System fonts only. Two weights: regular and medium. Body is md.",
      stack(
        type("2xl", "Are these the same?", true),
        type("xl", "To review", true),
        type("lg", "Marguerite Osei", true),
        type("md", "Two pages may describe one person. If they do, OpenKOS combines them into one page."),
        type("sm", "Person · people/marguerite-osei"),
        type("xs", "4 to review"),
        specimen("--font-mono", el("p", { class: "gallery-type--mono" }, "~/Notes/good-life/raw/meeting-2026-03-12.md")),
      ),
    ),
  ];
}

function buttons(): HTMLElement {
  const variants = ["primary", "secondary", "quiet", "destructive"] as const;
  const labels = { primary: "Combine pages", secondary: "Different things", quiet: "Decide later", destructive: "Forget page" };
  return section(
    "Buttons",
    "One primary per view, for an action. When the user answers a question, every answer has the same weight.",
    ...(["md", "sm"] as const).map((size) =>
      specimens(
        ...variants.map((variant) => specimen(`${variant} ${size}`, uiButton(labels[variant], noop, { variant, size }))),
        specimen(`disabled ${size}`, uiButton("Combine pages", noop, { variant: "primary", size, disabled: true })),
        specimen(`loading ${size}`, uiButton("Combine pages", noop, { variant: "primary", size, loading: true, loadingLabel: "Combining…" })),
      ),
    ),
  );
}

function badgesAndNav(): HTMLElement[] {
  return [
    section(
      "Badges",
      "A symbol and a word on a tint. Color is never the only signal.",
      specimens(
        specimen("neutral", badge("neutral", "Not checked")),
        specimen("info", badge("info", "Checked")),
        specimen("warning", badge("warning", "Changed")),
        specimen("success", badge("success", "Engine running")),
        specimen("error", badge("error", "Engine stopped")),
        specimen("default word", badge("warning")),
      ),
    ),
    section(
      "Navigation",
      "Sidebar items: the current page is lifted and medium; a count shows only when something waits.",
      specimens(
        specimen("current, with count", navItem({ id: "review", label: "To review", count: 4 }, true, noop)),
        specimen("rest", navItem({ id: "status", label: "Status" }, false, noop)),
        specimen("rest, with count", navItem({ id: "review", label: "To review", count: 12 }, false, noop)),
        specimen("not offered yet", navItem({ id: "settings", label: "Settings", disabled: true, hint: "Coming soon" }, false, noop)),
      ),
    ),
  ];
}

function callouts(): HTMLElement {
  return section(
    "Callouts",
    "In the flow of the page, never floating. Pass live: status or alert to announce one.",
    stack(
      callout("info", "The AI model compares the pages it hasn't checked yet. You still decide each one.", { title: "Checking pages" }),
      callout("success", "Marguerite Osei and M. Osei are now one page.", {
        title: "Combined",
        actions: [uiButton("Split again", noop, { variant: "secondary", size: "sm" })],
      }),
      callout("warning", "A source of this page changed after the check. Look again before you decide.", { title: "Changed" }),
      callout("error", "The engine stopped unexpectedly. Your files are safe.", {
        title: "Can't reach OpenKOS",
        actions: [uiButton("Try again", noop, { variant: "secondary", size: "sm" })],
      }),
    ),
  );
}

function rowsAndCards(): HTMLElement[] {
  return [
    section(
      "List rows",
      "A title, a meta line and a trailing badge. The selected row is lifted.",
      specimens(
        wide(
          "default, selected, with and without badges",
          uiList("Decisions", [
            listRow({ title: "Marguerite Osei · M. Osei", meta: "Are these the same person?", selected: true, onSelect: noop }),
            listRow({ title: "Lisbon trip · Team offsite", meta: "Are these the same event?", trailing: badge("warning", "Changed"), onSelect: noop }),
            listRow({ title: "Okonkwo & Partners · Okonkwo", meta: "Are these the same thing?", onSelect: noop }),
          ]),
        ),
      ),
    ),
    section(
      "Cards",
      "A quiet raised surface, one step up from the canvas; a footer for where a page came from.",
      specimens(wide("page card", pageCard(OSEI)), wide("title only", uiCard({ title: "Your library" }, el("p", {}, "128 pages from 46 files.")))),
    ),
    section(
      "Two-up compare",
      "For “are these the same?”: the two pages side by side, stacking when the window is narrow.",
      compare("Marguerite Osei and M. Osei", pageCard(OSEI), pageCard(M_OSEI)),
    ),
  ];
}

function inputs(): HTMLElement[] {
  return [
    section(
      "Segmented choice",
      "A radio group with one tab stop: arrows, Home and End move the selection.",
      specimens(
        specimen(
          "nothing chosen",
          segmented("Are these the same?", [
            { value: "same", label: "Same thing" },
            { value: "different", label: "Different things" },
          ], undefined, noop),
        ),
        specimen(
          "chosen",
          segmented("Show", [
            { value: "all", label: "All" },
            { value: "people", label: "People" },
            { value: "events", label: "Events" },
          ], "people", noop),
        ),
      ),
    ),
    section(
      "Answers",
      "The answers to a question, each with what it does under it. Answers share one weight; Decide later is quiet.",
      answerList("Your answer", [
        { label: "Same thing", does: "Next, you'll choose what it is.", onChoose: noop },
        { label: "Different things", does: "We won't suggest combining these again.", onChoose: noop },
        { label: "Decide later", does: "Nothing changes. This stays in To review.", onChoose: noop, variant: "quiet" },
      ]),
    ),
    section(
      "Choices",
      "Native checkboxes or radios in a labelled group, e.g. which pages are the same.",
      specimens(
        wide(
          "checkboxes",
          choiceList({
            legend: "Mark the pages that are the same",
            type: "checkbox",
            name: "g-marked",
            items: [
              { value: "a", label: "Marguerite Osei", checked: true },
              { value: "b", label: "M. Osei", checked: true },
              { value: "c", label: "Maggie Osei", checked: false },
            ],
            onChange: noop,
          }),
        ),
        wide(
          "radios",
          choiceList({
            legend: "Which one should the combined page keep its name from?",
            type: "radio",
            name: "g-name",
            items: [
              { value: "a", label: "Marguerite Osei", checked: true },
              { value: "b", label: "M. Osei", checked: false },
            ],
            onChange: noop,
          }),
        ),
      ),
    ),
    section(
      "Text input",
      "Always labelled; a hint and an error are tied to the input.",
      specimens(
        wide("search, hidden label", textField({ id: "g-search", label: "Search pages", type: "search", placeholder: "Search pages", hideLabel: true })),
        wide("with hint", textField({ id: "g-name", label: "Page title", value: "Marguerite Osei", hint: "As it appears at the top of the page." })),
        wide("with error", textField({ id: "g-type", label: "What is it?", value: "", error: "Choose what this page describes." })),
      ),
    ),
  ];
}

function dialogs(): HTMLElement {
  const confirm = () =>
    openDialog({
      title: "Combine these pages?",
      description: "Marguerite Osei and M. Osei become one page. You can split them again later.",
      confirmLabel: "Combine",
      onConfirm: noop,
    });
  const destructive = () =>
    openDialog({
      title: "Forget this page?",
      description: "OpenKOS removes the page and stops using its sources. The files in raw/ stay where they are.",
      confirmLabel: "Forget page",
      destructive: true,
      onConfirm: noop,
    });
  return section(
    "Dialog",
    "Modal and labelled; Escape cancels and focus returns to the opener. A destructive dialog starts on Cancel.",
    specimens(
      specimen("open a real dialog", el("div", { class: "ui-actions" }, uiButton("Combine…", confirm), uiButton("Forget page…", destructive, { variant: "destructive" }))),
      wide(
        "panel, shown in place",
        dialogPanel({ title: "Combine these pages?", description: "Marguerite Osei and M. Osei become one page. You can split them again later.", confirmLabel: "Combine", onConfirm: noop }, noop, noop),
      ),
    ),
  );
}

function states(): HTMLElement {
  return section(
    "States",
    "Loading shows the shape of what comes; busy keeps the control and says what runs; offline says what still works.",
    specimens(
      wide("loading: first load", skeleton(4, "Loading decisions…")),
      wide("loading: a known wait", spinner("Checking 3 pages…")),
      wide("progress: a download", el("div", {}, progressBar("Downloading bge-m3", 1_300_000_000, 5_200_000_000), el("p", {}, "1.3 GB of 5.2 GB (25%)"))),
      wide("busy", el("div", { class: "ui-actions" }, uiButton("Combine pages", noop, { variant: "primary", loading: true, loadingLabel: "Combining…" }), uiButton("Different things", noop, { disabled: true }))),
      wide("empty", emptyState({ title: "Nothing to review", text: "When OpenKOS finds pages that may be one, or sources that disagree, they wait here.", action: uiButton("Add a source", noop) })),
      wide("error", callout("error", "OpenKOS couldn't load the decisions. Nothing was changed.", { title: "Couldn't load", actions: [uiButton("Try again", noop, { size: "sm" })] })),
      wide("secondary facts, closed until asked", disclosure({ summary: "Show the folder" }, facts([["Folder", "~/Notes/good-life", "code"]]))),
      wide("offline", callout("warning", "The engine isn't running. Your pages and sources are safe on this Mac.", { title: "OpenKOS is offline", actions: [uiButton("Start the engine", noop, { variant: "primary", size: "sm" })] })),
    ),
  );
}

const root = document.getElementById("gallery");
if (root) {
  root.classList.add("gallery");
  root.append(
    el(
      "header",
      { class: "gallery-intro" },
      pageHeader({
        title: "Design system",
        description:
          "Every component of the desktop app in every state, on made-up data. Dark follows the system appearance; switch it to see light. Nothing here talks to the engine.",
      }),
    ),
    section("To review", "The real screen on made-up data: the list of what waits, and the one you open. Click a row; nothing is written.", reviewComposition()),
    section("Status", "The engine and your library.", statusComposition()),
    section("Ask", "A question, its answer and the pages it came from.", askComposition()),
    ...foundations(),
    buttons(),
    ...badgesAndNav(),
    callouts(),
    ...rowsAndCards(),
    ...inputs(),
    dialogs(),
    states(),
  );
}
