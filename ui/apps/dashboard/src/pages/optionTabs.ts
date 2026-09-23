/** The Options page's tabs, apart from the page itself so the app's router can name them without loading it. */
export const OPTION_TABS = [
  { id: "dashboards", label: "Dashboards" },
  { id: "rig", label: "Rig file" },
  { id: "runner", label: "Runner" },
  { id: "appearance", label: "Appearance" },
  { id: "pages", label: "Pages" },
] as const;
export type OptionTab = (typeof OPTION_TABS)[number]["id"];

/** The tab a route names, or the first when it names none (or one that no longer exists). */
export const optionTab = (name: string | null): OptionTab => OPTION_TABS.find((t) => t.id === name)?.id ?? "dashboards";
