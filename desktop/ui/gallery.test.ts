// @vitest-environment jsdom
// The gallery is a development page with no other check: it must render every
// section, both compositions included, without throwing.
import { describe, expect, it } from "vitest";

describe("the gallery", () => {
  it("renders both compositions and every component section", async () => {
    document.body.append(Object.assign(document.createElement("div"), { id: "gallery" }));
    await import("./gallery.ts");
    const titles = Array.from(document.querySelectorAll(".gallery-section__title"), (n) => n.textContent);
    expect(titles).toEqual([
      "To review: list and detail",
      "Status",
      "Surfaces and borders",
      "Text",
      "Type scale",
      "Buttons",
      "Badges",
      "Navigation",
      "Callouts",
      "List rows",
      "Cards",
      "Two-up compare",
      "Segmented choice",
      "Text input",
      "Dialog",
      "States",
    ]);
    const review = document.querySelector(".gallery-frame") as HTMLElement;
    expect(review.querySelector('[aria-current="page"]')?.textContent).toBe("To review3");
    const answers = Array.from(review.querySelectorAll(".mock-decision button"), (n) => n.textContent);
    expect(answers).toEqual(["Same thing", "Different things", "Decide later"]);
    expect(review.querySelectorAll(".ui-compare .ui-card")).toHaveLength(2);
  });

  it("opens the selected decision when its row is clicked", () => {
    const review = document.querySelector(".gallery-frame") as HTMLElement;
    (document.querySelectorAll<HTMLButtonElement>(".gallery-frame .ui-row")[1] as HTMLButtonElement).click();
    const again = document.querySelector(".gallery-frame") as HTMLElement;
    expect(again).not.toBe(review);
    expect(again.querySelector(".mock-decision__eyebrow")?.textContent).toBe("Decision 2 of 3");
    expect(again.querySelector('.ui-row[aria-current="true"] .ui-row__meta')?.textContent).toBe("Lisbon trip · Team offsite");
    expect(document.activeElement).toBe(again.querySelector('.ui-row[aria-current="true"]'));
  });
});
