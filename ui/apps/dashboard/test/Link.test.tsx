// @vitest-environment jsdom
import { createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { link } from "../src/widgets/Link.js";
import type { WidgetComponentProps } from "../src/widgets/types.js";

afterEach(cleanup);

describe("Link widget (F2): an operator-stored URL goes through the same scheme allow-list as Markdown", () => {
  it("keeps a plain https URL as the href", () => {
    render(createElement(link.Component, { widget: {} as WidgetComponentProps["widget"], config: { page: "url", url: "https://example.com/status", label: "Status" }, editing: false }));
    const a = screen.getByText("Status").closest("a");
    expect(a).not.toBeNull();
    expect(a?.getAttribute("href")).toBe("https://example.com/status");
  });

  it("drops a javascript: URL rather than putting it in href", () => {
    render(createElement(link.Component, { widget: {} as WidgetComponentProps["widget"], config: { page: "url", url: "javascript:alert(document.cookie)", label: "Click me" }, editing: false }));
    const a = screen.getByText("Click me").closest("a");
    expect(a).not.toBeNull();
    expect(a?.getAttribute("href")).toBeNull();
  });

  it("drops a data: URL rather than putting it in href", () => {
    render(createElement(link.Component, { widget: {} as WidgetComponentProps["widget"], config: { page: "url", url: "data:text/html,<script>alert(1)</script>", label: "Open" }, editing: false }));
    const a = screen.getByText("Open").closest("a");
    expect(a).not.toBeNull();
    expect(a?.getAttribute("href")).toBeNull();
  });

  it("keeps an internal page link (app-generated, never operator-supplied) untouched", () => {
    render(createElement(link.Component, { widget: {} as WidgetComponentProps["widget"], config: { page: "devices", name: "" }, editing: false }));
    const a = screen.getByRole("link");
    expect(a.getAttribute("href")).toMatch(/^#/);
  });
});
