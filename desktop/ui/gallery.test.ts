// @vitest-environment jsdom
// The gallery is a development page with no other check: it must render every
// section, both screens included, without throwing.
import { describe, expect, it } from "vitest";

async function settle(): Promise<void> {
  for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("the gallery", () => {
  it("renders both screens and every component section", async () => {
    document.body.append(Object.assign(document.createElement("div"), { id: "gallery" }));
    await import("./gallery.ts");
    await settle();
    const titles = Array.from(document.querySelectorAll(".gallery-section__title"), (n) => n.textContent);
    expect(titles).toEqual([
      "To review",
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
      "Answers",
      "Choices",
      "Text input",
      "Dialog",
      "States",
    ]);
    const review = document.querySelector(".gallery-frame") as HTMLElement;
    expect(review.querySelector('[aria-current="page"]')?.textContent).toBe("To review4");
    expect(review.querySelectorAll(".ui-split__list .ui-row")).toHaveLength(4);
  });

  it("opens a decision of the real screen when its row is clicked", async () => {
    const review = document.querySelector(".gallery-frame") as HTMLElement;
    (review.querySelectorAll<HTMLButtonElement>(".ui-row")[0] as HTMLButtonElement).click();
    await settle();
    expect(review.querySelector(".ui-split__detail h2")?.textContent).toBe("Are these the same person?");
    expect(review.querySelectorAll(".ui-split__detail .ui-compare .ui-card")).toHaveLength(2);
  });
});
