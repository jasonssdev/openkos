// Draws the settings screen from its state with the design system's components
// (ADR-0066, ADR-0069). It decides nothing: the state and its transitions live
// in model.ts, every word in wording.ts, and every value is written as text.
// The key field is write-only: it starts empty on every draw and its value only
// goes to the model.
import {
  badge,
  callout,
  disclosure,
  el,
  facts,
  openDialog,
  pageHeader,
  redraw,
  segmented,
  skeleton,
  spinner,
  textField,
  uiButton,
  uiCard,
} from "../system/index.ts";
import {
  type DraftField,
  type SettingsModel,
  type SettingsState,
  type UndoList,
  type UndoRow,
  type WatchKind,
  updateOffered,
  watchKind,
} from "./model.ts";
import { UPDATES, WATCH, WATCH_STATUS, WORDS, modelFacts, whereItRuns } from "./wording.ts";

type Dialog = ReturnType<typeof openDialog>;

/** The warning dialog each model has open, so a redraw never opens a second one. */
const dialogs = new WeakMap<SettingsModel, Dialog>();
/** The install confirmation each model has open, likewise. */
const installDialogs = new WeakMap<SettingsModel, Dialog>();

/** Redraws the settings screen into `root`, keeping focus on the same control. */
export function renderSettings(root: HTMLElement, model: SettingsModel, state: SettingsState): void {
  const key = state.editing ? `settings:edit:${state.draft.where}` : "settings";
  redraw(root, key, () => root.replaceChildren(...screen(model, state)));
  warning(model, state);
  installConfirmation(model, state);
}

/**
 * The confirmation before an update is installed (ADR-0079): nothing is
 * downloaded until the person says yes, and it starts on "Not now".
 */
function installConfirmation(model: SettingsModel, state: SettingsState): void {
  const open = installDialogs.get(model);
  const version = updateOffered(state);
  if (!state.updateConfirm || version === null) {
    if (open) {
      installDialogs.delete(model);
      open.close();
    }
    return;
  }
  if (open) return;
  const forget = () => installDialogs.delete(model);
  installDialogs.set(
    model,
    openDialog({
      title: UPDATES.confirmTitle(version),
      body: UPDATES.confirmLines.map((line) => el("p", {}, line)),
      confirmLabel: UPDATES.confirm,
      cancelLabel: UPDATES.cancel,
      destructive: true,
      onConfirm: () => {
        forget();
        return model.confirmInstall();
      },
      onCancel: () => {
        forget();
        model.cancelInstall();
      },
    }),
  );
}

function warning(model: SettingsModel, state: SettingsState): void {
  const open = dialogs.get(model);
  if (state.confirming === null) {
    if (open) {
      dialogs.delete(model);
      open.close();
    }
    return;
  }
  if (open) return;
  const forget = () => dialogs.delete(model);
  dialogs.set(
    model,
    openDialog({
      title: WORDS.warningTitle,
      body: state.confirming.lines.map((line) => el("p", {}, line)),
      confirmLabel: WORDS.warningConfirm,
      cancelLabel: WORDS.warningCancel,
      // Consequential: it starts on keeping the model on this Mac.
      destructive: true,
      onConfirm: () => {
        forget();
        return model.confirm();
      },
      onCancel: () => {
        forget();
        model.dismiss();
      },
    }),
  );
}

function screen(model: SettingsModel, state: SettingsState): HTMLElement[] {
  const header = pageHeader({ title: WORDS.title });
  if (state.settings === null) {
    if (state.phase === "failed") {
      const retry = uiButton(WORDS.tryAgain, () => model.load(), { size: "sm", focusKey: "settings:retry" });
      return [header, callout("error", WORDS.loadFailed, { actions: [retry], live: "alert" }), ...updatesCard(model, state)];
    }
    // Updates need no engine: a newer version may be what fixes one that does not start.
    return [header, skeleton(3, WORDS.loading), ...updatesCard(model, state)];
  }
  const notes = [
    state.note ? callout("success", state.note, { live: "status" }) : null,
    state.problem ? callout("error", state.problem, { live: "alert" }) : null,
  ].filter((node): node is HTMLElement => node !== null);
  return [header, ...notes, modelCard(model, state),
    ...watchCard(model, state),
    ...undoCard(model, state, "settled", WORDS.settledTitle, WORDS.undo),
    ...undoCard(model, state, "declined", WORDS.declinedTitle, WORDS.pointOutAgain),
    ...updatesCard(model, state),
    libraryCard(model, state),
  ];
}

function modelCard(model: SettingsModel, state: SettingsState): HTMLElement {
  return uiCard(
    { title: WORDS.modelTitle, headingLevel: 2 },
    ...(state.editing ? modelForm(model, state) : modelSummary(model, state)),
  );
}

function modelSummary(model: SettingsModel, state: SettingsState): HTMLElement[] {
  const settings = state.settings as NonNullable<SettingsState["settings"]>;
  const cloud = settings.takes_api_key;
  const keyLine = cloud && state.key !== null ? keyStatus(state) : null;
  const removable = cloud && state.key === "saved";
  const actions = [
    uiButton(WORDS.change, () => model.edit(), { focusKey: "settings:change" }),
    removable
      ? uiButton(WORDS.removeKey, () => model.removeKey(), {
          variant: "quiet",
          loading: state.removingKey,
          loadingLabel: WORDS.removingKey,
          focusKey: "settings:remove-key",
        })
      : null,
  ].filter((node): node is HTMLButtonElement => node !== null);
  return [
    el("p", {}, whereItRuns(settings)),
    ...(keyLine ? [keyLine] : []),
    disclosure({ summary: WORDS.showDetails, focusKey: "settings:details" }, facts(modelFacts(settings))),
    el("div", { class: "ui-actions" }, ...actions),
  ];
}

function keyStatus(state: SettingsState): HTMLElement {
  if (state.key === "unsupported") return el("p", { class: "ui-field__hint" }, WORDS.keyNotSupported);
  return el("p", {}, `${WORDS.key}: ${state.key === "saved" ? WORDS.keySaved : WORDS.noKey}`);
}

function modelForm(model: SettingsModel, state: SettingsState): HTMLElement[] {
  const { draft } = state;
  const where = segmented(
    WORDS.whereLabel,
    [
      { value: "local", label: WORDS.local },
      { value: "cloud", label: WORDS.cloud },
    ],
    draft.where,
    (value) => model.choose(value === "cloud" ? "cloud" : "local"),
  );
  const field = (id: DraftField, label: string, value: string, hint: string) =>
    textField({ id: `settings-${id}`, label, value, hint, onInput: (typed) => model.setField(id, typed) });
  const body: HTMLElement[] = [el("p", { class: "ui-field__label" }, WORDS.whereLabel), where];
  if (draft.where === "local") {
    body.push(el("p", {}, WORDS.localText));
  } else {
    body.push(
      el("p", {}, WORDS.cloudText),
      field("baseUrl", WORDS.address, draft.baseUrl, WORDS.addressHint),
      field("model", WORDS.chatModel, draft.model, WORDS.chatModelHint),
      field("embeddingModel", WORDS.searchModel, draft.embeddingModel, WORDS.searchModelHint),
    );
    if (state.key === "unsupported") {
      body.push(el("p", { class: "ui-field__hint" }, WORDS.keyNotSupported));
    } else {
      const hint = state.key === "saved" ? `${WORDS.keySaved}. ${WORDS.keyHint}` : WORDS.keyHint;
      body.push(
        textField({ id: "settings-key", label: WORDS.key, type: "password", hint, onInput: (typed) => model.setField("key", typed) }),
      );
    }
  }
  body.push(
    el(
      "div",
      { class: "ui-actions" },
      uiButton(WORDS.save, () => model.save(), {
        variant: "primary",
        loading: state.saving,
        loadingLabel: WORDS.saving,
        focusKey: "settings:save",
      }),
      uiButton(WORDS.cancel, () => model.cancel(), { variant: "quiet", disabled: state.saving, focusKey: "settings:cancel" }),
    ),
  );
  return body;
}

/**
 * "Watch a folder": drawn once the engine has said what the folder is. It says
 * what happens before anything is turned on, and that the watch also runs the
 * daemon's other jobs (ADR-0078).
 */
function watchCard(model: SettingsModel, state: SettingsState): HTMLElement[] {
  const { inbox, watchBusy: busy, watchConfirm: confirm } = state;
  if (inbox === null) return [];
  const body: HTMLElement[] = [
    el("p", {}, WATCH.intro),
    el("ul", {}, ...WATCH.what.map((line) => el("li", {}, line))),
    el("p", {}, WATCH.also),
    el("p", { class: "ui-field__hint" }, WATCH.stops),
  ];
  if (confirm !== null) {
    body.push(
      el("p", { class: "ui-field__label" }, WATCH.confirmTitle(confirm.folder)),
      el(
        "div",
        { class: "ui-actions" },
        uiButton(WATCH.start, () => model.confirmWatch(), { variant: "primary", focusKey: "settings:watch-confirm" }),
        uiButton(WATCH.cancel, () => model.cancelWatch(), { variant: "quiet", focusKey: "settings:watch-cancel" }),
      ),
    );
    return [uiCard({ title: WATCH.title, headingLevel: 2 }, ...body)];
  }
  const wanted = state.watch?.wanted === true;
  const kind = watchKind(state);
  if (kind !== null) body.unshift(watchStatusLine(kind, state));
  if (inbox.inbox !== null) body.push(facts([[WATCH.folder, inbox.inbox, "code"]]));
  const actions: HTMLButtonElement[] = [];
  if (inbox.inbox === null) {
    actions.push(
      uiButton(WATCH.choose, () => model.chooseWatchFolder(), {
        variant: "primary",
        loading: busy === "choosing",
        loadingLabel: WATCH.choosing,
        disabled: busy !== null && busy !== "choosing",
        focusKey: "settings:watch-choose",
      }),
    );
  } else {
    actions.push(
      wanted
        ? uiButton(WATCH.stop, () => model.stopWatch(), {
            loading: busy === "stopping",
            loadingLabel: WATCH.stopping,
            disabled: busy !== null && busy !== "stopping",
            focusKey: "settings:watch-stop",
          })
        : uiButton(WATCH.start, () => model.startWatch(), {
            variant: "primary",
            loading: busy === "starting",
            loadingLabel: WATCH.starting,
            disabled: busy !== null && busy !== "starting",
            focusKey: "settings:watch-start",
          }),
      uiButton(WATCH.chooseAnother, () => model.chooseWatchFolder(), {
        variant: "quiet",
        loading: busy === "choosing",
        loadingLabel: WATCH.choosing,
        disabled: busy !== null && busy !== "choosing",
        focusKey: "settings:watch-choose",
      }),
    );
  }
  if (kind === "failed") {
    // The shell gave up restarting the daemon: a restart is one click, and what it said waits behind a disclosure.
    actions.unshift(
      uiButton(WATCH_STATUS.startAgain, () => model.startWatch(), {
        variant: "primary",
        loading: busy === "starting",
        loadingLabel: WATCH.starting,
        disabled: busy !== null && busy !== "starting",
        focusKey: "settings:watch-again",
      }),
    );
    if (state.watch?.state === "failed") {
      body.push(disclosure({ summary: WORDS.showDetails, focusKey: "settings:watch-details" }, el("pre", {}, state.watch.detail)));
    }
  }
  body.push(el("div", { class: "ui-actions" }, ...actions));
  return [uiCard({ title: WATCH.title, headingLevel: 2 }, ...body)];
}

const WATCH_TONES = { on: "success", stopped: "neutral", budget: "warning", failed: "error" } as const;

/** One line: a badge and the sentence, in the card and nowhere else a person must look for it. */
function watchStatusLine(kind: WatchKind, state: SettingsState): HTMLElement {
  return el(
    "div",
    { class: "ui-line", attrs: { "data-watch-status": kind, role: kind === "failed" ? "alert" : "status" } },
    badge(WATCH_TONES[kind], WATCH_STATUS.badge[kind]),
    el("span", {}, WATCH_STATUS[kind]),
  );
}

/** An earlier decision list, drawn only while it has something in it. */
function undoCard(model: SettingsModel, state: SettingsState, list: UndoList, title: string, label: string): HTMLElement[] {
  const rows: UndoRow[] = state[list];
  if (rows.length === 0) return [];
  return [
    uiCard(
      { title, headingLevel: 2 },
      el(
        "ul",
        { class: "ui-plain-list" },
        ...rows.map((row) =>
          el(
            "li",
            { class: "ui-line" },
            el("span", {}, row.text),
            uiButton(label, () => model.undo(list, row.key), {
              size: "sm",
              loading: state.undoing === row.key,
              loadingLabel: WORDS.undoing,
              disabled: state.undoing !== null && state.undoing !== row.key,
              focusKey: `settings:undo:${list}:${row.key}`,
            }),
          ),
        ),
      ),
    ),
  ];
}

/**
 * "Updates": whether the app checks, what the last check found, and the
 * install, which only asks for a confirmation here (ADR-0079). Drawn once the
 * shell has said whether it checks.
 */
function updatesCard(model: SettingsModel, state: SettingsState): HTMLElement[] {
  const { updates, update, updateBusy: busy } = state;
  if (updates === null) return [];
  const body: HTMLElement[] = [el("p", {}, UPDATES.intro), el("p", {}, updates.check ? UPDATES.on : UPDATES.off)];
  if (update?.state === "up_to_date") body.push(el("p", {}, UPDATES.upToDate));
  if (update?.state === "failed") body.push(el("p", { class: "ui-field__hint" }, UPDATES.failed));
  if (update?.state === "available") {
    body.push(el("p", { class: "ui-field__label", attrs: { role: "status" } }, UPDATES.available(update.version, update.current_version)));
    if (update.notes) body.push(el("p", { class: "ui-field__label" }, UPDATES.notes), el("p", {}, update.notes));
  }
  if (state.updateProblem) body.push(callout("error", state.updateProblem, { live: "alert" }));
  const actions: HTMLButtonElement[] = [];
  if (update?.state === "available") {
    actions.push(
      uiButton(UPDATES.install, () => model.askInstall(), {
        variant: "primary",
        loading: busy === "installing",
        loadingLabel: UPDATES.installing,
        disabled: busy !== null && busy !== "installing",
        focusKey: "settings:update-install",
      }),
    );
  }
  actions.push(
    uiButton(UPDATES.checkNow, () => model.checkForUpdate(), {
      loading: busy === "checking",
      loadingLabel: UPDATES.checking,
      disabled: busy !== null && busy !== "checking",
      focusKey: "settings:update-check",
    }),
    uiButton(updates.check ? UPDATES.turnOff : UPDATES.turnOn, () => model.setUpdateCheck(!updates.check), {
      variant: "quiet",
      loading: busy === "switching",
      loadingLabel: UPDATES.switching,
      disabled: busy !== null && busy !== "switching",
      focusKey: "settings:update-switch",
    }),
  );
  body.push(el("div", { class: "ui-actions" }, ...actions));
  return [uiCard({ title: UPDATES.title, headingLevel: 2 }, ...body)];
}

function libraryCard(model: SettingsModel, state: SettingsState): HTMLElement {
  const library = state.library;
  const body: HTMLElement[] = [el("p", {}, WORDS.libraryText)];
  body.push(library?.path ? facts([[WORDS.folder, library.path, "code"]]) : el("p", {}, WORDS.noLibrary));
  if (library?.source === "environment") {
    body.push(el("p", { class: "ui-field__hint" }, WORDS.libraryFromOutside));
  } else if (state.librarySwitching) {
    body.push(spinner(WORDS.switching));
  } else {
    body.push(
      el(
        "div",
        { class: "ui-actions" },
        uiButton(WORDS.openAnother, () => model.openLibrary(), {
          loading: state.libraryBusy === "open",
          loadingLabel: WORDS.opening,
          disabled: state.libraryBusy === "create",
          focusKey: "settings:open-library",
        }),
        uiButton(WORDS.createNew, () => model.createLibrary(), {
          variant: "quiet",
          loading: state.libraryBusy === "create",
          loadingLabel: WORDS.creating,
          disabled: state.libraryBusy === "open",
          focusKey: "settings:create-library",
        }),
      ),
    );
  }
  if (state.libraryProblem) body.push(callout("error", state.libraryProblem.text, { live: "alert" }));
  return uiCard({ title: WORDS.libraryTitle, headingLevel: 2 }, ...body);
}
