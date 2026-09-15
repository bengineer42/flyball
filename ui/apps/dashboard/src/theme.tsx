import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { CssBaseline, ThemeProvider, createTheme, darken, lighten, useMediaQuery, useTheme, type PaletteMode } from "@mui/material";

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

/** Dense, engineering-flavoured theme: small controls by default, tabular numerals, system font. */
export function makeTheme(mode: PaletteMode) {
  return createTheme({
    palette: {
      mode,
      primary: { main: mode === "light" ? "#2557a7" : "#7aa7e6" },
      error: { main: mode === "light" ? "#b3261e" : "#f2726a" },
      warning: { main: mode === "light" ? "#d97706" : "#f5b342" },
      success: { main: mode === "light" ? "#059669" : "#34d399" },
      background:
        mode === "light" ? { default: "#f3f4f6", paper: "#ffffff" } : { default: "#0f1419", paper: "#181d24" },
    },
    shape: { borderRadius: 6 },
    spacing: 6,
    typography: {
      fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
      fontSize: 13,
      h1: { fontSize: "1.1rem", fontWeight: 600 },
      h2: { fontSize: "0.8rem", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em" },
      overline: { fontSize: "0.72rem", letterSpacing: "0.06em", lineHeight: 1.6 },
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
    },
  });
}

/**
 * Write the library's `--fb-*` variables from the palette so `@flyball/react`
 * panels follow light/dark without knowing MUI exists.
 */
function FbVars() {
  const theme = useTheme();
  useEffect(() => {
    const p = theme.palette;
    const paper = p.background.paper;
    const vars: Record<string, string> = {
      "--fb-fg": p.text.primary,
      "--fb-muted": p.text.secondary,
      "--fb-panel": paper,
      "--fb-bg": p.mode === "light" ? darken(paper, 0.025) : lighten(paper, 0.045),
      "--fb-border": p.divider,
      "--fb-accent": p.primary.main,
      "--fb-error": p.error.main,
      "--fb-ok": p.success.main,
      "--fb-warn": p.warning.main,
      "--fb-alarm": p.error.main,
      "--fb-radius": `${theme.shape.borderRadius}px`,
      "--fb-gap": "0.75rem",
    };
    const root = document.documentElement.style;
    for (const [k, v] of Object.entries(vars)) root.setProperty(k, v);
  }, [theme]);
  return null;
}

const ColorModeContext = createContext<{ mode: PaletteMode; toggle(): void }>({ mode: "light", toggle() {} });

export const useColorMode = () => useContext(ColorModeContext);

/** Theme + baseline + the stored light/dark choice (system preference until the user picks). */
export function AppTheme({ children }: { children: ReactNode }) {
  const prefersDark = useMediaQuery("(prefers-color-scheme: dark)");
  const [chosen, setChosen] = useState<PaletteMode | null>(readStored);
  const mode: PaletteMode = chosen ?? (prefersDark ? "dark" : "light");
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
  return (
    <ColorModeContext.Provider value={ctx}>
      <ThemeProvider theme={theme}>
        <CssBaseline enableColorScheme />
        <FbVars />
        {children}
      </ThemeProvider>
    </ColorModeContext.Provider>
  );
}
