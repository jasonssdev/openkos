import { describe, expect, it, vi } from "vitest";
import { ApiError, type Schemas } from "../../client/index.ts";
import { ActivityModel, POLL_MS } from "./model.ts";

const EMPTY: Schemas["EngineActivity"] = {
  current: null,
  recent: [],
  waiting_files: 0,
  uncommitted_paths: 0,
  budget: { calls_used: 0, calls_limit: 500, exhausted: false },
  jobs_unreadable: false,
};

describe("the activity model", () => {
  it("has nothing until a client arrives, then reads once", async () => {
    const engineActivity = vi.fn().mockResolvedValue(EMPTY);
    const model = new ActivityModel();
    expect(model.state).toEqual({ activity: null, failed: false });
    await model.setClient({ engineActivity });
    expect(engineActivity).toHaveBeenCalledTimes(1);
    expect(model.state).toEqual({ activity: EMPTY, failed: false });
  });

  it("tells its listeners and forgets everything when the engine goes", async () => {
    const model = new ActivityModel();
    const seen = vi.fn();
    model.subscribe(seen);
    await model.setClient({ engineActivity: vi.fn().mockResolvedValue(EMPTY) });
    await model.setClient(null);
    expect(model.state).toEqual({ activity: null, failed: false });
    expect(seen).toHaveBeenCalled();
  });

  it("keeps what was shown when a later read fails, and says it failed", async () => {
    const engineActivity = vi.fn().mockResolvedValueOnce(EMPTY).mockRejectedValueOnce(new ApiError({ status: 500, code: "internal", retryable: false, message: "no", retryAfterSeconds: null }))
      .mockResolvedValue(EMPTY);
    const model = new ActivityModel();
    await model.setClient({ engineActivity });
    await model.load();
    expect(model.state).toEqual({ activity: EMPTY, failed: true });
    await model.load();
    expect(model.state.failed).toBe(false);
  });

  it("treats an answer in another shape as a failed read", async () => {
    const model = new ActivityModel();
    await model.setClient({ engineActivity: vi.fn().mockResolvedValue({ state: "ready" }) });
    expect(model.state).toEqual({ activity: null, failed: true });
  });

  it("polls only while the screen is open", async () => {
    vi.useFakeTimers();
    try {
      const engineActivity = vi.fn().mockResolvedValue(EMPTY);
      const model = new ActivityModel();
      await model.setClient({ engineActivity });
      engineActivity.mockClear();
      model.watch(true);
      await vi.advanceTimersByTimeAsync(POLL_MS * 2);
      // Once on opening, then once per interval.
      expect(engineActivity).toHaveBeenCalledTimes(3);
      model.watch(false);
      await vi.advanceTimersByTimeAsync(POLL_MS * 3);
      expect(engineActivity).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });
});
