// @vitest-environment jsdom
import { createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { Exposure } from "@flyball/client";
import { ExposureBanner } from "../src/panels/ExposureBanner.js";

afterEach(cleanup);

const base: Exposure = {
  requested_host: "127.0.0.1",
  host: "127.0.0.1",
  port: 8000,
  open: true,
  restricted: false,
  open_network: false,
  warning: null,
};

describe("ExposureBanner", () => {
  it("says nothing for a runner served where it was asked, on loopback", () => {
    render(createElement(ExposureBanner, { exposure: base }));
    expect(screen.queryByTestId("exposure-banner")).toBeNull();
    cleanup();
    render(createElement(ExposureBanner, { exposure: null }));
    expect(screen.queryByTestId("exposure-banner")).toBeNull();
  });

  it("warns, with no way to dismiss it, while an open runner is on the network", () => {
    const warning = "serving an OPEN runner on '0.0.0.0' (--insecure-open): anyone who can reach it may operate the rig";
    render(createElement(ExposureBanner, { exposure: { ...base, requested_host: "0.0.0.0", host: "0.0.0.0", open_network: true, warning } }));
    const banner = screen.getByTestId("exposure-banner");
    expect(banner.textContent).toMatch(/anyone on the network/i);
    expect(banner.textContent).toContain(warning);
    expect(banner.querySelector("button")).toBeNull();
  });

  it("says why an open runner asked for the network is on loopback only", () => {
    const warning = "host is '0.0.0.0' but the runner has no password and no token: serving on 127.0.0.1:8000 only";
    render(createElement(ExposureBanner, { exposure: { ...base, requested_host: "0.0.0.0", restricted: true, warning } }));
    expect(screen.getByTestId("exposure-banner").textContent).toMatch(/this machine only/i);
  });
});

describe("ExposureBanner at a front that fell back", () => {
  it("does not say 'no password and no token' when a configured sign-in could not be used", () => {
    const warning =
      "front: auth: password needs a password: line (`flyball password` makes one) -- serving the local shape on 127.0.0.1:8443 only; the rig keeps running (D-028)";
    render(createElement(ExposureBanner, { exposure: { ...base, requested_host: "0.0.0.0:8443", host: "127.0.0.1", restricted: true, warning } }));
    const text = screen.getByTestId("exposure-banner").textContent ?? "";
    expect(text).not.toMatch(/no password and no token/i);
    expect(text).toMatch(/this machine only/i);
    expect(text).toContain(warning);
  });

  it("an open network rig is described without claiming which credentials it lacks", () => {
    render(createElement(ExposureBanner, { exposure: { ...base, requested_host: "0.0.0.0", host: "0.0.0.0", open_network: true } }));
    expect(screen.getByTestId("exposure-banner").textContent).not.toMatch(/no password and no token/i);
  });
});
