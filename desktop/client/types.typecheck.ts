// A type-level test, checked by `pnpm typecheck` and never run: every method
// of the client takes and returns exactly the schema's types, tuples included
// (`edges`, `member_digests` and `merged` are `prefixItems` in the snapshot).
import type { Client, Schemas } from "./index.ts";

type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;
type Assert<T extends true> = T;
type Returns<K extends keyof Client> = Awaited<ReturnType<Client[K]>>;
type Takes<K extends keyof Client, N extends number> = Parameters<Client[K]>[N];

export type Checks = [
  Assert<Equal<Returns<"health">, Schemas["HealthBody"]>>,
  Assert<Equal<Returns<"status">, Schemas["WorkspaceStatus"]>>,
  Assert<Equal<Returns<"pending">, Schemas["PendingReport"]>>,
  Assert<Equal<Returns<"decisions">, Schemas["DecisionList"]>>,
  Assert<Equal<Takes<"previewDecision", 1>, Schemas["PreviewRequest"]>>,
  Assert<Equal<Returns<"previewDecision">, Schemas["DecisionPreview"]>>,
  Assert<Equal<Takes<"applyDecision", 1>, Schemas["ApplyRequest"]>>,
  Assert<Equal<Returns<"applyDecision">, Schemas["ApplyResponse"]>>,
  Assert<Equal<Takes<"refreshQueue", 0>, Schemas["RefreshRequest"] | undefined>>,
  Assert<Equal<Returns<"refreshQueue">, Schemas["QueueRefresh"]>>,
  Assert<Equal<Takes<"quoteJudging", 0>, Schemas["QuoteRequest"]>>,
  Assert<Equal<Returns<"quoteJudging">, Schemas["JudgingQuote"]>>,
  Assert<Equal<Takes<"startJudgingRun", 0>, Schemas["RunRequest"]>>,
  Assert<Equal<Returns<"startJudgingRun">, Schemas["RunStatus"]>>,
  Assert<Equal<Returns<"judgingRun">, Schemas["RunStatus"]>>,
  Assert<Equal<Returns<"stopJudgingRun">, Schemas["RunStatus"]>>,
  Assert<Equal<Takes<"ask", 0>, Schemas["AskRequest"]>>,
  Assert<Equal<Returns<"ask">, Schemas["AskJob"]>>,
  Assert<Equal<Returns<"askJob">, Schemas["AskJob"]>>,
  Assert<Equal<Takes<"quoteIngest", 0>, Schemas["IngestQuoteRequest"]>>,
  Assert<Equal<Returns<"quoteIngest">, Schemas["IngestQuote"]>>,
  Assert<Equal<Takes<"quoteRefusedFile", 1>, Schemas["RefusedFileQuoteRequest"]>>,
  Assert<Equal<Returns<"quoteRefusedFile">, Schemas["IngestQuote"]>>,
  Assert<Equal<Takes<"startIngest", 0>, Schemas["IngestRunRequest"]>>,
  Assert<Equal<Returns<"startIngest">, Schemas["IngestJob"]>>,
  Assert<Equal<Returns<"ingestRun">, Schemas["IngestJob"]>>,
  Assert<Equal<Returns<"stopIngest">, Schemas["IngestJob"]>>,
  // The quote's token is what a run takes back, unchanged.
  Assert<Equal<Returns<"quoteIngest">["token"], NonNullable<Schemas["IngestRunRequest"]["token"]>>>,
  Assert<Equal<Returns<"mergeHistory">, Schemas["MergeHistory"]>>,
  Assert<Equal<Returns<"readPage">, Schemas["Page"]>>,
  Assert<Equal<Takes<"previewUnmerge", 0>, Schemas["UnmergePreviewRequest"]>>,
  Assert<Equal<Returns<"previewUnmerge">, Schemas["UnmergePreview"]>>,
  Assert<Equal<Takes<"applyUnmerge", 0>, Schemas["UnmergeApplyRequest"]>>,
  Assert<Equal<Returns<"applyUnmerge">, Schemas["UnmergeResponse"]>>,
];
