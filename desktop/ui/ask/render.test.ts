// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import type { Schemas } from "../../client/index.ts";
import type { AskState, Turn } from "./model.ts";
import { renderAsk, type AskActions } from "./render.ts";

type Answer = Schemas["Answer"];

const ANSWER: Answer = {
  question: "Who runs the allotment?",
  outcome: "answered",
  answer: "Maria runs it.\n\nShe started in <b>2019</b>.",
  no_match_cause: "none",
  citations: [
    { concept_id: "people/maria", title: "Maria", confidential: false, excerpted: false, history: null },
    { concept_id: "places/allotment", title: "The allotment", confidential: false, excerpted: false, history: null },
  ],
  omitted: [],
  attribution: "reported",
  withheld: 1,
  sufficiency_checked: true,
  sufficiency_degraded: false,
  lexical_unavailable: false,
  dense_unavailable: false,
  stale_indexes: [],
};

const turn = (id: number, result: Turn["result"], question = "Who runs the allotment?"): Turn => ({ id, question, result });

function state(patch: Partial<AskState> = {}): AskState {
  return { ready: true, running: false, blank: false, turns: [], shown: null, ...patch };
}

function actions() {
  return {
    ask: vi.fn((_question: string) => {}),
    retry: vi.fn(() => {}),
    showPrivate: vi.fn(() => {}),
    show: vi.fn((_id: number) => {}),
    onToggle: vi.fn((_key: string, _open: boolean) => {}),
    expanded: new Set<string>(),
  } satisfies AskActions;
}

function draw(s: AskState, a: AskActions = actions(), root: HTMLElement = document.createElement("div")): HTMLElement {
  if (!root.isConnected) document.body.replaceChildren(root);
  renderAsk(root, s, a);
  return root;
}

const input = (root: HTMLElement) => root.querySelector("#ask-question") as HTMLInputElement;
const askButton = (root: HTMLElement) => root.querySelector(".ui-button--primary") as HTMLButtonElement;

describe("the ask screen", () => {
  it("opens on its focusable title with a labelled question and one primary Ask", () => {
    const root = draw(state());
    const title = root.querySelector("h1") as HTMLElement;
    expect(title.textContent).toBe("Ask");
    expect(title.dataset["view"]).toBe("heading");
    expect(root.querySelector('label[for="ask-question"]')?.textContent).toBe("Your question");
    expect(root.querySelectorAll(".ui-button--primary")).toHaveLength(1);
    expect(askButton(root).textContent).toBe("Ask");
    expect(askButton(root).disabled).toBe(false);
    expect(root.querySelector("#ask-waiting")).toBeNull();
    expect(root.querySelector(".ui-card")).toBeNull();
  });

  it("waits for the engine, saying why Ask is unavailable", () => {
    const root = draw(state({ ready: false }));
    expect(askButton(root).disabled).toBe(true);
    expect(askButton(root).getAttribute("aria-describedby")).toBe("ask-waiting");
    expect(root.querySelector("#ask-waiting")?.textContent).toBe("Waiting for OpenKOS to start…");
  });

  it("asks what was typed, from the button and from Enter", () => {
    const a = actions();
    const root = draw(state(), a);
    input(root).value = "Who runs the allotment?";
    askButton(root).click();
    root.querySelector("form")?.dispatchEvent(new Event("submit", { cancelable: true }));
    expect(a.ask).toHaveBeenCalledTimes(2);
    expect(a.ask).toHaveBeenNthCalledWith(1, "Who runs the allotment?");
  });

  it("does not leave the page on submit", () => {
    const root = draw(state());
    const submit = new Event("submit", { cancelable: true });
    root.querySelector("form")?.dispatchEvent(submit);
    expect(submit.defaultPrevented).toBe(true);
  });

  it("marks a blank question on the field", () => {
    const root = draw(state({ blank: true }));
    expect(input(root).getAttribute("aria-invalid")).toBe("true");
    expect(input(root).getAttribute("aria-describedby")).toBe("ask-question-error");
    expect(root.querySelector("#ask-question-error")?.textContent).toBe("✕ Type a question first.");
    const clean = draw(state(), actions(), root);
    expect(input(clean).hasAttribute("aria-invalid")).toBe(false);
    expect(clean.querySelector("#ask-question-error")).toBeNull();
  });

  it("shows the question and its phase while the answer is on its way", () => {
    const root = draw(state({ running: true, turns: [turn(1, { kind: "running", phase: "synthesizing" })], shown: 1 }));
    expect(askButton(root).getAttribute("aria-busy")).toBe("true");
    expect(askButton(root).textContent).toBe("Asking…");
    expect(root.querySelector(".ui-card__title")?.textContent).toBe("Who runs the allotment?");
    expect(root.querySelector(".ui-loading")?.textContent).toBe("Writing the answer…");
  });

  it("shows the answer as text, the pages it came from, and its notes", () => {
    const root = draw(state({ turns: [turn(1, { kind: "answer", answer: ANSWER })], shown: 1 }));
    const card = root.querySelector(".ui-card") as HTMLElement;
    const paragraphs = Array.from(card.querySelectorAll(".ui-card__body > p:not(.ui-card__meta)"), (n) => n.textContent);
    expect(paragraphs).toEqual(["Maria runs it.", "She started in <b>2019</b>."]);
    expect(card.querySelector("b")).toBeNull();
    expect(card.querySelector(".ui-card__footer")?.textContent).toBe("From: Maria · The allotment");
    expect(Array.from(card.querySelectorAll(".ui-card__body > .ui-card__meta"), (n) => n.textContent)).toEqual([
      "1 page marked Only on this Mac was left out.",
    ]);
  });

  it("leaves out From when the answer points to no page", () => {
    const answer = { ...ANSWER, citations: [], withheld: 0 };
    const root = draw(state({ turns: [turn(1, { kind: "answer", answer })], shown: 1 }));
    expect(root.querySelector(".ui-card__footer")).toBeNull();
    expect(root.querySelector(".ui-card__body > .ui-card__meta")?.textContent).toBe("This answer doesn't point to a particular page.");
  });

  it("words an answer that stays private, with a count and no names", () => {
    const answer: Answer = { ...ANSWER, outcome: "withheld", answer: null, citations: [], withheld: 2 };
    const root = draw(state({ turns: [turn(1, { kind: "answer", answer })], shown: 1 }));
    const card = root.querySelector(".ui-card") as HTMLElement;
    expect(card.querySelector(".ui-card__meta")?.textContent).toBe("This answer stays on this Mac");
    expect(card.querySelector(".ui-card__body > p")?.textContent).toMatch(/^It drew on 2 pages marked Only on this Mac/);
    expect(card.textContent).not.toContain("Maria");
    expect(card.querySelector(".ui-card__footer")).toBeNull();
  });

  it("offers to show a private answer, and only for one", () => {
    const a = actions();
    const answer: Answer = { ...ANSWER, outcome: "withheld", answer: null, citations: [], withheld: 2 };
    const root = draw(state({ turns: [turn(1, { kind: "answer", answer })], shown: 1 }), a);
    const button = root.querySelector(".ui-card button") as HTMLButtonElement;
    expect(button.textContent).toBe("Show this answer");
    button.click();
    expect(a.showPrivate).toHaveBeenCalledTimes(1);
    const plain = draw(state({ turns: [turn(1, { kind: "answer", answer: { ...ANSWER, withheld: 0 } })], shown: 1 }), a);
    expect(plain.querySelector(".ui-card button")).toBeNull();
  });

  it("offers Try again on a problem that asking again can fix, and only then", () => {
    const a = actions();
    const problem = { kind: "problem" as const, problem: { text: "OpenKOS isn't responding.", retry: true } };
    const root = draw(state({ turns: [turn(1, problem)], shown: 1 }), a);
    const notice = root.querySelector(".ui-callout--error") as HTMLElement;
    expect(notice.querySelector(".ui-callout__text")?.textContent).toBe("OpenKOS isn't responding.");
    (notice.querySelector("button") as HTMLButtonElement).click();
    expect(a.retry).toHaveBeenCalledOnce();
    const final = { kind: "problem" as const, problem: { text: "The AI model isn't installed.", retry: false } };
    expect(draw(state({ turns: [turn(1, final)], shown: 1 })).querySelector(".ui-callout button")).toBeNull();
  });

  it("keeps earlier questions behind a disclosure and shows one on request", () => {
    const a = actions();
    const turns = [
      turn(3, { kind: "answer", answer: ANSWER }, "Third?"),
      turn(2, { kind: "problem", problem: { text: "x", retry: true } }, "Second?"),
      turn(1, { kind: "answer", answer: ANSWER }, "First?"),
    ];
    const root = draw(state({ turns, shown: 2 }), a);
    const earlier = root.querySelector("details") as HTMLDetailsElement;
    expect(earlier.open).toBe(false);
    expect(earlier.querySelector("summary")?.textContent).toBe("Earlier questions");
    const rows = Array.from(earlier.querySelectorAll(".ui-row"));
    expect(rows.map((r) => r.querySelector(".ui-row__title")?.textContent)).toEqual(["Third?", "First?"]);
    expect(rows.map((r) => r.querySelector(".ui-row__meta")?.textContent)).toEqual(["Answered", "Answered"]);
    (rows[1] as HTMLButtonElement).click();
    expect(a.show).toHaveBeenCalledWith(1);
    earlier.open = true;
    earlier.dispatchEvent(new Event("toggle"));
    expect(a.onToggle).toHaveBeenCalledWith("ask-earlier", true);
    expect(draw(state({ turns: [turns[0] as Turn], shown: 3 })).querySelector("details")).toBeNull();
  });

  it("keeps the field, what was typed and its focus across a redraw", () => {
    const root = draw(state());
    const field = input(root);
    field.value = "Who";
    field.focus();
    draw(state({ running: true, turns: [turn(1, { kind: "running", phase: null })], shown: 1 }), actions(), root);
    expect(input(root)).toBe(field);
    expect(field.value).toBe("Who");
    expect(document.activeElement).toBe(field);
    expect(root.querySelectorAll("h1")).toHaveLength(1);
  });

  it("keeps focus on Ask while it runs", () => {
    const root = draw(state());
    askButton(root).focus();
    draw(state({ running: true, turns: [turn(1, { kind: "running", phase: null })], shown: 1 }), actions(), root);
    expect(document.activeElement).toBe(askButton(root));
  });
});

describe("the question field after an answer", () => {
  it("is cleared once its answer arrives, and kept while it is on its way or fails", () => {
    const root = draw(state());
    input(root).value = "Who runs the allotment?";
    draw(state({ running: true, turns: [turn(1, { kind: "running", phase: null })], shown: 1 }), actions(), root);
    expect(input(root).value).toBe("Who runs the allotment?");
    draw(state({ turns: [turn(1, { kind: "problem", problem: { text: "x", retry: true } })], shown: 1 }), actions(), root);
    expect(input(root).value).toBe("Who runs the allotment?");
    draw(state({ turns: [turn(1, { kind: "answer", answer: ANSWER })], shown: 1 }), actions(), root);
    expect(input(root).value).toBe("");
  });

  it("leaves what the person has begun typing for the next question", () => {
    const root = draw(state());
    input(root).value = "Who runs the allotment?";
    draw(state({ turns: [turn(1, { kind: "running", phase: null })], shown: 1, running: true }), actions(), root);
    input(root).value = "And the shed?";
    draw(state({ turns: [turn(1, { kind: "answer", answer: ANSWER })], shown: 1 }), actions(), root);
    expect(input(root).value).toBe("And the shed?");
  });

  it("does not clear again when redrawn", () => {
    const root = draw(state());
    input(root).value = "Who runs the allotment?";
    const done = state({ turns: [turn(1, { kind: "answer", answer: ANSWER })], shown: 1 });
    draw(done, actions(), root);
    input(root).value = "Who runs the allotment?";
    draw(done, actions(), root);
    expect(input(root).value).toBe("Who runs the allotment?");
  });
});

describe("the pages an answer came from, and search", () => {
  it("lists each page it came from with the screen's action beside it", () => {
    const a = {
      ...actions(),
      citationAction: vi.fn((citation: Schemas["AnswerCitation"]) => {
        const node = document.createElement("span");
        node.textContent = `act:${citation.concept_id}`;
        return node;
      }),
    };
    const root = draw(state({ turns: [turn(1, { kind: "answer", answer: ANSWER })], shown: 1 }), a);
    const footer = root.querySelector(".ui-card__footer") as HTMLElement;
    expect(footer.querySelector("p")?.textContent).toBe("From:");
    expect(Array.from(footer.querySelectorAll("li"), (li) => li.textContent)).toEqual([
      "Mariaact:people/maria",
      "The allotmentact:places/allotment",
    ]);
  });

  it("keeps each page's state apart from its action", () => {
    const a = {
      ...actions(),
      citationState: vi.fn((citation: Schemas["AnswerCitation"]) => {
        if (citation.concept_id !== "people/maria") return null;
        const node = document.createElement("span");
        node.textContent = "state";
        return node;
      }),
      citationAction: vi.fn(() => {
        const node = document.createElement("button");
        node.textContent = "act";
        return node;
      }),
    };
    const root = draw(state({ turns: [turn(1, { kind: "answer", answer: ANSWER })], shown: 1 }), a);
    const rows = Array.from(root.querySelectorAll(".ask-from__page"));
    const first = rows[0] as HTMLElement;
    expect(first.querySelector(".ask-from__what")?.textContent).toBe("Mariastate");
    expect(first.querySelector(".ask-from__do")?.textContent).toBe("act");
    expect((rows[1] as HTMLElement).querySelector(".ask-from__what")?.textContent).toBe("The allotment");
  });

  it("places the screen's search note under an answer", () => {
    const a = {
      ...actions(),
      afterAnswer: vi.fn((answer: Answer) => {
        const node = document.createElement("section");
        node.textContent = `search:${answer.stale_indexes.join(",")}`;
        return node;
      }),
    };
    const root = draw(state({ turns: [turn(1, { kind: "answer", answer: { ...ANSWER, stale_indexes: ["fts"] } })], shown: 1 }), a);
    expect(root.querySelector("section")?.textContent).toBe("search:fts");
  });
});
