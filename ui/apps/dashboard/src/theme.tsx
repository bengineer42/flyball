import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { CssBaseline, ThemeProvider, createTheme, useMediaQuery, type PaletteMode } from "@mui/material";

const STORAGE_KEY = "flyball.theme";

const readStored = (): PaletteMode | null => {
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
};

const writeStored = (mode: PaletteMode) => {
  try {
    window.localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    /* private mode, quota, disabled storage: the choice just does not persist */
  }
};

/**
 * Read a `--fb-*` token as `<html>` has it resolved right now. The library's
 * `styles.css` is the one source of truth for colour (DESIGN-SPEC.md §1.1);
 * MUI reads it rather than writing it, so an embedder with no MUI at all
 * gets the same look. Falls back to the light-mode value so SSR/tests
 * without the stylesheet loaded still get a usable theme.
 */
function token(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** Dense, engineering-flavoured theme: small controls by default, tabular numerals, system font. */
export function makeTheme(mode: PaletteMode) {
  return createTheme({
    palette: {
      mode,
      primary: { main: token("--fb-accent", mode === "light" ? "#2557a7" : "#78a6ea") },
      error: { main: token("--fb-alarm", mode === "light" ? "#c62828" : "#f0616a") },
      warning: { main: token("--fb-warn", mode === "light" ? "#a8690f" : "#f0b429") },
      success: { main: token("--fb-ok", mode === "light" ? "#1a8a56" : "#3fb27f") },
      background: {
        default: token("--fb-bg-0", mode === "light" ? "#f2f4f7" : "#0e1116"),
        paper: token("--fb-bg-1", mode === "light" ? "#ffffff" : "#161a21"),
      },
      divider: token("--fb-border-1", mode === "light" ? "rgba(20, 28, 40, 0.10)" : "rgba(204, 212, 228, 0.10)"),
      text: {
        primary: token("--fb-fg", mode === "light" ? "#1b2330" : "#d7dce6"),
        secondary: token("--fb-fg-2", mode === "light" ? "#4f5a6b" : "#9aa3b2"),
      },
    },
    shape: { borderRadius: 6 },
    spacing: 4,
    typography: {
      fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
      fontSize: 13,
      // DESIGN-SPEC.md §1.3's scale, on the nearest MUI slot: MUI has no
      // "page"/"title"/"label" variant of its own, so h1 stands for the
      // app-bar page title (16/600), subtitle2 for panel/card titles
      // (14/600), and h2/overline both stand for the 11px uppercase label.
      h1: { fontSize: 16, fontWeight: 600 },
      h2: { fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em" },
      subtitle2: { fontSize: 14, fontWeight: 600 },
      overline: { fontSize: 11, fontWeight: 600, letterSpacing: "0.06em", lineHeight: 1.6 },
      body1: { fontSize: 13 },
      body2: { fontSize: 13 },
      caption: { fontSize: 12 },
      button: { textTransform: "none" },
    },
    components: {
      MuiCssBaseline: {
        styleOverrides: { body: { fontVariantNumeric: "tabular-nums" } },
      },
      MuiButton: { defaultProps: { size: "small" } },
      MuiIconButton: { defaultProps: { size: "small" } },
      MuiTextField: { defaultProps: { size: "small" } },
      MuiFormControl: { defaultProps: { size: "small" } },
      MuiSelect: { defaultProps: { size: "small" } },
      MuiChip: { defaultProps: { size: "small" } },
      MuiTable: { defaultProps: { size: "small" } },
      MuiPaper: { defaultProps: { variant: "outlined" } },
      MuiAlert: { styleOverrides: { root: { padding: "0 10px" } } },
      MuiListItemIcon: { styleOverrides: { root: { minWidth: 36 } } },
      // One focus ring everywhere (DESIGN-SPEC.md §1.6): a surface gap plus an accent ring,
      // visible in both themes and over a coloured border.
      MuiButtonBase: {
        styleOverrides: {
          root: {
            "&.Mui-focusVisible": { boxShadow: "var(--fb-focus)" },
          },
        },
      },
    },
  });
}

const ColorModeContext = createContext<{ mode: PaletteMode; toggle(): void }>({ mode: "light", toggle() {} });

export const useColorMode = () => useContext(ColorModeContext);

/** Theme + baseline + the stored light/dark choice (system preference until the user picks). */
export function AppTheme({ children }: { children: ReactNode }) {
  const prefersDark = useMediaQuery("(prefers-color-scheme: dark)");
  const [chosen, setChosen] = useState<PaletteMode | null>(readStored);
  const mode: PaletteMode = chosen ?? (prefersDark ? "dark" : "light");
  // Set synchronously (not in an effect): `makeTheme` below reads `--fb-*` from computed
  // style on this render, and that only reflects `mode` once this attribute is applied —
  // an effect would run one paint too late and hand the theme stale tokens.
  if (typeof document !== "undefined") document.documentElement.setAttribute("data-theme", mode);
  const theme = useMemo(() => makeTheme(mode), [mode]);
  const ctx = useMemo(
    () => ({
      mode,
      toggle() {
        const next: PaletteMode = mode === "light" ? "dark" : "light";
        setChosen(next);
        writeStored(next);
      },
    }),
    [mode],
  );

  // One density. The attribute stays because `styles.css` and `widgets/size.ts`'s `headPx()`
  // read it; the compact setting and its toggle were removed on 19 Sep 2026.
  if (typeof document !== "undefined") document.documentElement.setAttribute("data-density", "comfortable");

  return (
    <ColorModeContext.Provider value={ctx}>
      <ThemeProvider theme={theme}>
        <CssBaseline enableColorScheme />
        {children}
      </ThemeProvider>
    </ColorModeContext.Provider>
  );
}
