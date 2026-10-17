// A type-level test, checked by `pnpm typecheck` and never run: the preview's
// plan is a discriminated union on `kind`, so a switch over it narrows each
// branch to that plan's fields and is exhaustive.
import type { Schemas } from "./index.ts";

export function describePlan(plan: Schemas["DecisionPreview"]["plan"]): string {
  switch (plan.kind) {
    case "merge":
      return plan.survivor_id;
    case "relate":
      return plan.relation_type;
    case "set_tier":
      return plan.tier;
    case "keep_distinct":
      return plan.kind;
    default: {
      const unreachable: never = plan;
      return unreachable;
    }
  }
}

// The finding preview's plan is its own union (ADR-0072), narrowed the same way.
export function describeFindingPlan(plan: Schemas["FindingPreview"]["plan"]): string {
  switch (plan.kind) {
    case "reconcile":
      return plan.relation_type;
    case "decline":
      return plan.first_id;
    default: {
      const unreachable: never = plan;
      return unreachable;
    }
  }
}
