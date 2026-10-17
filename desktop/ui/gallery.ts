// The component gallery (desktop/DESIGN.md): every component of the design
// system in every state, and two mock compositions, on made-up OpenKOS data.
// A development page only: nothing here reaches the engine.
import type { Schemas } from "../client/index.ts";
import { el } from "./components.ts";
import { renderStatus } from "./status.ts";
import {
  appShell,
  badge,
  callout,
  compare,
  dialogPanel,
  disclosure,
  emptyState,
  facts,
  listRow,
  navItem,
  openDialog,
  pageHeader,
  segmented,
  skeleton,
  spinner,
  splitView,
  textField,
  uiButton,
  uiCard,
  uiList,
  type BadgeTone,
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
  id: string;
  type: string;
  excerpt: string;
  sources: string;
}

interface MockDecision {
  key: string;
  question: string;
  pages: [MockPage, MockPage];
  lead: string;
  status: [BadgeTone, string] | null;
  noticed: string;
}

const OSEI: MockPage = {
  title: "Marguerite Osei",
  id: "people/marguerite-osei",
  type: "Person",
  excerpt: "Product lead at Fieldnote. Ran the March planning workshop and owns the onboarding redesign.",
  sources: "From 3 sources: meeting-2026-03-12.md, onboarding-brief.md, team.md",
};

const M_OSEI: MockPage = {
  title: "M. Osei",
  id: "people/m-osei",
  type: "Person",
  excerpt: "Named as the reviewer of the onboarding redesign proposal, due before the April release.",
  sources: "From 1 source: onboarding-proposal.md",
};

const DECISIONS: MockDecision[] = [
  {
    key: "osei",
    question: "Are these the same?",
    pages: [OSEI, M_OSEI],
    lead: "Two pages may describe one person. If they do, OpenKOS combines them into one page. You can split them again later.",
    status: ["info", "Checked"],
    noticed: "The AI model thinks these are the same person: both own the onboarding redesign at Fieldnote.",
  },
  {
    key: "lisbon",
    question: "Are these the same?",
    pages: [
      { title: "Lisbon trip", id: "events/lisbon-trip", type: "Event", excerpt: "Five days in Lisbon in October with the design team.", sources: "From 2 sources: travel.md, offsite-plan.md" },
      { title: "Team offsite", id: "events/team-offsite", type: "Event", excerpt: "The autumn offsite. Venue still to be confirmed.", sources: "From 1 source: offsite-plan.md" },
    ],
    lead: "Two pages may describe one event.",
    status: ["warning", "Changed"],
    noticed: "A source of one page changed after the check. Look again before you decide.",
  },
  {
    key: "offsite-date",
    question: "Is this a contradiction?",
    pages: [
      { title: "Team offsite", id: "events/team-offsite", type: "Event", excerpt: "Starts on 14 October.", sources: "From offsite-plan.md" },
      { title: "Team offsite", id: "events/team-offsite", type: "Event", excerpt: "Starts on 21 October.", sources: "From travel.md" },
    ],
    lead: "Two sources say different things about one page.",
    status: ["neutral", "Not checked"],
    noticed: "Not checked by the AI model yet.",
  },
];

// -- composition: review list + detail ---------------------------------------- //

const NAV: NavEntry[] = [
  { id: "ask", label: "Ask", disabled: true, hint: "Coming soon" },
  { id: "review", label: "To review", count: DECISIONS.length },
  { id: "status", label: "Status" },
];

function pageCard(page: MockPage): HTMLElement {
  return uiCard(
    { title: page.title, meta: `${page.type} · ${page.id}`, footer: page.sources, aside: badge("neutral", page.type) },
    el("p", {}, page.excerpt),
  );
}

function decisionDetail(decision: MockDecision, index: number): HTMLElement {
  const [left, right] = decision.pages;
  return el(
    "div",
    { class: "mock-decision" },
    el("p", { class: "mock-decision__eyebrow" }, `Decision ${index + 1} of ${DECISIONS.length}`),
    el("h2", { class: "mock-decision__question", attrs: { tabindex: "-1" } }, decision.question),
    el("p", { class: "mock-decision__lead" }, decision.lead),
    compare(`${left.title} and ${right.title}`, pageCard(left), pageCard(right)),
    callout(decision.status?.[0] === "warning" ? "warning" : "info", decision.noticed, { title: "What the model noticed" }),
    el(
      "div",
      { class: "ui-actions" },
      uiButton("Same thing", noop),
      uiButton("Different things", noop),
      uiButton("Decide later", noop, { variant: "quiet" }),
    ),
  );
}

function reviewComposition(): HTMLElement {
  const host = el("div", {});
  let selected = 0;
  const draw = () => {
    const shell = appShell({ nav: NAV, current: "review", onNavigate: noop, footer: badge("success", "Engine running") });
    const rows = DECISIONS.map((decision, index) =>
      listRow({
        title: decision.question,
        meta: `${decision.pages[0].title} · ${decision.pages[1].title}`,
        ...(decision.status ? { trailing: badge(...decision.status) } : {}),
        selected: index === selected,
        focusKey: `row:${decision.key}`,
        onSelect: () => {
          selected = index;
          draw();
          host.querySelector<HTMLElement>(`[data-focus="row:${decision.key}"]`)?.focus();
        },
      }),
    );
    const list = el(
      "div",
      {},
      el(
        "div",
        { class: "mock-list-header" },
        el("h1", { class: "mock-list-header__title" }, "To review"),
        el("p", { class: "mock-list-header__meta" }, `${DECISIONS.length} decisions waiting`),
      ),
      uiList("Decisions", rows),
    );
    shell.main.append(splitView(list, decisionDetail(DECISIONS[selected] as MockDecision, selected), { list: "Decisions", detail: "Decision" }));
    host.replaceChildren(frame(shell.root));
  };
  draw();
  return host;
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
        specimen("not offered yet", navItem({ id: "ask", label: "Ask", disabled: true, hint: "Coming soon" }, false, noop)),
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
            listRow({ title: "Are these the same?", meta: "Marguerite Osei · M. Osei", trailing: badge("info", "Checked"), selected: true, onSelect: noop }),
            listRow({ title: "Are these the same?", meta: "Lisbon trip · Team offsite", trailing: badge("warning", "Changed"), onSelect: noop }),
            listRow({ title: "What is it?", meta: "Okonkwo & Partners", onSelect: noop }),
          ]),
        ),
      ),
    ),
    section(
      "Cards",
      "A quiet raised surface with a hairline edge; a footer for provenance.",
      specimens(wide("page card", pageCard(OSEI)), wide("title only", uiCard({ title: "Workspace" }, el("p", {}, "128 pages from 46 sources.")))),
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
    section("To review: list and detail", "The sidebar, the list of decisions, and the selected decision. Click a row.", reviewComposition()),
    section("Status", "The engine and the workspace.", statusComposition()),
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
