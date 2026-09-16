/** Which dashboard the app opens on (`#/` and the Dashboards entry); a browser convenience, not server state. */
const KEY = "flyball.dashboards.home";

export const readHome = (): string | null => {
  try {
    return window.localStorage.getItem(KEY) || null;
  } catch {
    return null;
  }
};

export const writeHome = (name: string | null) => {
  try {
    if (name) window.localStorage.setItem(KEY, name);
    else window.localStorage.removeItem(KEY);
  } catch {
    /* not persisted */
  }
};
