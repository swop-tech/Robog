/* Theme: light (primary), dark, and "system" — persisted, no flash on load.
 *
 * The resolved theme goes on `<html data-theme="light|dark">` so every colour in
 * styles.css is a token lookup; components never hardcode a palette. `color-scheme`
 * follows, which keeps native scrollbars, form controls and the caret legible.
 *
 * Small module, DOM-touching by nature: `applyTheme` is the only impure part, so
 * `resolveTheme` stays unit-testable.
 */

export const THEMES = ["light", "dark", "system"];

/** "system" resolves through `prefers-color-scheme`; anything unknown is light. */
export function resolveTheme(preference, prefersDark = false) {
  if (preference === "dark") return "dark";
  if (preference === "light") return "light";
  return prefersDark ? "dark" : "light";
}

/**
 * Wire the theme to the document.
 *
 * @param {() => string} getPreference  reads the stored setting
 * @param {(theme: string) => void} [onChange]  called with the resolved theme
 */
export function initTheme(getPreference, onChange = null) {
  const root = document.documentElement;
  const media = globalThis.matchMedia?.("(prefers-color-scheme: dark)");

  const apply = () => {
    const resolved = resolveTheme(getPreference(), Boolean(media?.matches));
    root.setAttribute("data-theme", resolved);
    root.style.colorScheme = resolved;
    onChange?.(resolved);
    return resolved;
  };

  apply();
  // Follow the OS while the setting is "system" (and only then).
  media?.addEventListener?.("change", () => {
    if (getPreference() === "system") apply();
  });
  return apply;
}
