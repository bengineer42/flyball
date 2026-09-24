// @vitest-environment jsdom
/** A rig edit: confirmed (typing the name at level 2), sent, watched while the runner restarts, and the app told once it is back; a running program is offered a force. */
import { createElement, useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RigClient, type Request, type Response, type Transport } from "@flyball/client";
import { RigEditProvider, useRigEdit } from "../src/rigEdit.js";
import { Confirm } from "../src/Confirm.js";

afterEach(cleanup);

const EDIT = { version: 8, previous: 7, reason: "edited: removed device probe", saved: null, restarting: true, stop: null, detail: "" };

/** A runner that answers the old clock until `restarted()` is called, then a new one; a device DELETE per `edit`. */
function runner(edit: (r: Request) => Response) {
  let start = 1;
  let conditions: unknown[] = [];
  const asked: Request[] = [];
  const transport: Transport = {
    base: "",
    async request(r: Request): Promise<Response> {
      asked.push(r);
      if (r.path === "/api/clock") return { status: 200, json: { start_time_ns: start, now_ns: 0, elapsed_ns: 0, tags: {}, speed: 1 } };
      if (r.path === "/api/health") return { status: 200, json: { ok: true, conditions } };
      if (r.method === "DELETE") return edit(r);
      return { status: 404, json: { detail: "no" } };
    },
    stream: () => ({ close: () => undefined }),
  };
  return {
    transport,
    asked,
    restarted(failed = false) {
      start = 2;
      if (failed) conditions = [{ code: "edit_not_built", severity: "error", message: "probe: no such link", since_ns: 0, scope: "rig", subject: "furnace" }];
    },
  };
}

function Remover({ transport }: { transport: Transport }) {
  const edits = useRigEdit();
  const [done, setDone] = useState<string>("");
  return createElement(
    "div",
    null,
    createElement("button", { onClick: () => void edits.apply((o) => new RigClient(transport).removeDevice("probe", o)).then((e) => setDone(e ? `v${e.version}` : "declined"), (e: Error) => setDone(`error ${e.message}`)) }, "remove"),
    createElement("span", { "data-testid": "done" }, done),
    edits.dialog,
  );
}

describe("a rig edit", () => {
  it("is watched while the runner restarts, and the app rebuilt once a new one answers", async () => {
    const r = runner(() => ({ status: 202, json: EDIT }));
    let backs = 0;
    render(createElement(RigEditProvider, { transport: r.transport, onBack: () => backs++ }, createElement(Remover, { transport: r.transport })));
    fireEvent.click(screen.getByText("remove"));
    await waitFor(() => expect(screen.getByTestId("done").textContent).toBe("v8"));
    expect(screen.getByTestId("rig-edit-status").textContent).toMatch(/Restarting the rig on version 8/);
    r.restarted();
    await waitFor(() => expect(screen.getByTestId("rig-edit-status").textContent).toMatch(/back on version 8/), { timeout: 4000 });
    expect(backs).toBe(1);
  });

  it("says so when the new version could not be built", async () => {
    const r = runner(() => ({ status: 202, json: EDIT }));
    render(createElement(RigEditProvider, { transport: r.transport, onBack: () => undefined }, createElement(Remover, { transport: r.transport })));
    fireEvent.click(screen.getByText("remove"));
    await waitFor(() => screen.getByTestId("rig-edit-status"));
    r.restarted(true);
    await waitFor(() => expect(screen.getByTestId("rig-edit-status").textContent).toMatch(/could not be built.*version 7.*no such link/), { timeout: 4000 });
  });

  it("offers to cancel a running program, and sends force only when asked", async () => {
    const r = runner((req) => (req.query?.force ? { status: 202, json: EDIT } : { status: 409, json: { detail: "A program is running: a rig edit stops the rig and cancels it. Pass force=true to do so" } }));
    render(createElement(RigEditProvider, { transport: r.transport, onBack: () => undefined }, createElement(Remover, { transport: r.transport })));
    fireEvent.click(screen.getByText("remove"));
    await waitFor(() => screen.getByText("A program is running"));
    fireEvent.click(screen.getByRole("button", { name: "Cancel the program and apply" }));
    await waitFor(() => expect(screen.getByTestId("done").textContent).toBe("v8"));
    expect(r.asked.filter((q) => q.method === "DELETE").map((q) => q.query)).toEqual([undefined, { force: true }]);
  });

  it("passes any other refusal to the page", async () => {
    const r = runner(() => ({ status: 409, json: { detail: "Nothing to change" } }));
    render(createElement(RigEditProvider, { transport: r.transport, onBack: () => undefined }, createElement(Remover, { transport: r.transport })));
    fireEvent.click(screen.getByText("remove"));
    await waitFor(() => expect(screen.getByTestId("done").textContent).toMatch(/^error .*Nothing to change/));
  });
});

describe("Confirm at level 2", () => {
  it("keeps the action off until the name is typed", () => {
    let confirmed = 0;
    render(createElement(Confirm, { open: true, title: "Remove probe?", text: "", action: "Remove", level: 2, phrase: "probe", onClose: () => undefined, onConfirm: () => confirmed++ }));
    const action = screen.getByRole("button", { name: "Remove" }) as HTMLButtonElement;
    expect(action.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("confirm-phrase"), { target: { value: "prob" } });
    expect(action.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("confirm-phrase"), { target: { value: "probe" } });
    expect(action.disabled).toBe(false);
    fireEvent.click(action);
    expect(confirmed).toBe(1);
  });
});
