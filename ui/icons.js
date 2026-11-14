/* Icon set — inline SVG, stroke-based, `currentColor`.
 *
 * No icon font, no CDN, no emoji-as-icon: the repo's UI is zero-build and must
 * work offline behind a preview proxy, and `<svg>` inherits the theme's text
 * colour so light/dark need no second asset set. Every icon is 24×24 on the same
 * 1.75px stroke grid so they sit on one optical line next to 14px labels.
 *
 * `icon("copy")` -> `<svg …>…</svg>` (decorative by default: `aria-hidden`).
 * Pass `{ label: "Copy answer" }` when the icon *is* the button's content, which
 * also gives the caller a `<title>` for tooltips and screen readers.
 */

const PATHS = {
  // -- brand ---------------------------------------------------------------
  brand: `<path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7a2.5 2.5 0 0 1-2.5 2.5H11l-4.4 3.3a.6.6 0 0 1-.96-.48V16H6.5A2.5 2.5 0 0 1 4 13.5z"/>
    <path d="M8.4 11.6a4.6 4.6 0 0 1 7.2 0"/><path d="M10.6 9.4a7.6 7.6 0 0 0 0 0"/>
    <path d="M10.5 13.4a2 2 0 0 1 3 0"/><circle cx="12" cy="10.2" r=".9" fill="currentColor" stroke="none"/>`,

  // -- conversation --------------------------------------------------------
  plus: `<path d="M12 5v14M5 12h14"/>`,
  message: `<path d="M21 12a8 8 0 0 1-8 8H8l-4.3 2.6A.6.6 0 0 1 3 22V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8z"/>`,
  search: `<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>`,
  trash: `<path d="M4 7h16M9.5 7V5.2A1.2 1.2 0 0 1 10.7 4h2.6a1.2 1.2 0 0 1 1.2 1.2V7"/>
    <path d="M6.5 7 7.4 19a1.6 1.6 0 0 0 1.6 1.5h6a1.6 1.6 0 0 0 1.6-1.5L17.5 7"/>
    <path d="M10.5 11v5.5M13.5 11v5.5"/>`,
  clock: `<circle cx="12" cy="12" r="8.2"/><path d="M12 7.6V12l3 1.8"/>`,
  panelLeft: `<rect x="3.2" y="4.5" width="17.6" height="15" rx="2.4"/><path d="M9.4 4.5v15"/>`,
  menu: `<path d="M4 7h16M4 12h16M4 17h16"/>`,

  // -- composer ------------------------------------------------------------
  send: `<path d="M4.4 12 20 5l-6.2 15-2.3-6.1z"/><path d="m11.5 13.9 3.2-3.2"/>`,
  arrowDown: `<path d="M12 4.8v14.4M6.4 13.6 12 19.2l5.6-5.6"/>`,
  stop: `<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>`,

  // -- message actions -----------------------------------------------------
  copy: `<rect x="9" y="9" width="11" height="11" rx="2.2"/>
    <path d="M15 6.6V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h.6"/>`,
  check: `<path d="m5 12.8 4.4 4.4L19 7.6"/>`,
  refresh: `<path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4.6V10h-5.4"/>`,
  thumbUp: `<path d="M7 10.4 11.3 3a2.1 2.1 0 0 1 2 1.5l-.3 3.9h4.6a2.1 2.1 0 0 1 2.1 2.5l-1.3 6.4a2.4 2.4 0 0 1-2.4 1.9H7z"/>
    <rect x="3.2" y="10.4" width="3.8" height="10.4" rx="1.2"/>`,
  thumbDown: `<path d="M17 13.6 12.7 21a2.1 2.1 0 0 1-2-1.5l.3-3.9H6.4a2.1 2.1 0 0 1-2.1-2.5l1.3-6.4A2.4 2.4 0 0 1 8 4.8h9z"/>
    <rect x="17" y="3.2" width="3.8" height="10.4" rx="1.2"/>`,
  share: `<path d="M12 15.5V4.2M8.2 7.8 12 4l3.8 3.8"/><path d="M5 14v4.6A1.4 1.4 0 0 0 6.4 20h11.2a1.4 1.4 0 0 0 1.4-1.4V14"/>`,

  // -- evidence ------------------------------------------------------------
  code: `<path d="m8.6 8-4.4 4 4.4 4M15.4 8l4.4 4-4.4 4M13.6 5l-3.2 14"/>`,
  doc: `<path d="M13.6 3.6H7.4A1.8 1.8 0 0 0 5.6 5.4v13.2a1.8 1.8 0 0 0 1.8 1.8h9.2a1.8 1.8 0 0 0 1.8-1.8V8.4z"/>
    <path d="M13.6 3.6v4.8h4.8M8.8 13h6.4M8.8 16.4h4.4"/>`,
  image: `<rect x="3.6" y="5" width="16.8" height="14" rx="2.2"/><circle cx="9" cy="10" r="1.6"/>
    <path d="m4.4 17.4 4.3-4a1.7 1.7 0 0 1 2.3 0l4.6 4.3M14.4 15.2l1.5-1.4a1.7 1.7 0 0 1 2.3 0l1.4 1.3"/>`,
  table: `<rect x="3.6" y="4.8" width="16.8" height="14.4" rx="2.2"/><path d="M3.6 9.8h16.8M9.6 9.8v9.4"/>`,
  shieldCheck: `<path d="M12 3.4 5.4 6v5.6c0 4 2.8 7.4 6.6 8.9 3.8-1.5 6.6-4.9 6.6-8.9V6z"/>
    <path d="m9.2 12 2 2 3.6-3.8"/>`,
  quote: `<path d="M9.4 6.6C7 7.8 5.6 10 5.6 12.8c0 2.4 1.3 4.2 3.2 4.2 1.6 0 2.8-1.2 2.8-2.8 0-1.5-1-2.6-2.5-2.6-.3 0-.6 0-.8.1.2-1.3 1.2-2.5 2.7-3.3z"/>
    <path d="M18 6.6c-2.4 1.2-3.8 3.4-3.8 6.2 0 2.4 1.3 4.2 3.2 4.2 1.6 0 2.8-1.2 2.8-2.8 0-1.5-1-2.6-2.5-2.6-.3 0-.6 0-.8.1.2-1.3 1.2-2.5 2.7-3.3z"/>`,

  // -- status & settings ---------------------------------------------------
  sun: `<circle cx="12" cy="12" r="4"/><path d="M12 2.8v2.4M12 18.8v2.4M4.7 4.7l1.7 1.7M17.6 17.6l1.7 1.7M2.8 12h2.4M18.8 12h2.4M4.7 19.3l1.7-1.7M17.6 6.4l1.7-1.7"/>`,
  moon: `<path d="M20 14.2A8.4 8.4 0 0 1 9.8 4 8.4 8.4 0 1 0 20 14.2z"/>`,
  monitor: `<rect x="3" y="4.8" width="18" height="12" rx="2"/><path d="M9 20.2h6M12 16.8v3.4"/>`,
  sliders: `<path d="M4 7.5h9M17 7.5h3M4 16.5h3M11 16.5h9"/>
    <circle cx="15" cy="7.5" r="2.2"/><circle cx="9" cy="16.5" r="2.2"/>`,
  globe: `<circle cx="12" cy="12" r="8.4"/><path d="M3.8 9.6h16.4M3.8 14.4h16.4"/>
    <path d="M12 3.6c-4.2 4.6-4.2 12.2 0 16.8 4.2-4.6 4.2-12.2 0-16.8z"/>`,
  database: `<ellipse cx="12" cy="6.4" rx="7.4" ry="3"/><path d="M4.6 6.4v11.2c0 1.7 3.3 3 7.4 3s7.4-1.3 7.4-3V6.4"/>
    <path d="M4.6 12c0 1.7 3.3 3 7.4 3s7.4-1.3 7.4-3"/>`,
  info: `<circle cx="12" cy="12" r="8.4"/><path d="M12 11v5.2M12 7.9h.01"/>`,
  alert: `<path d="M10.6 4.3 3.3 17a1.6 1.6 0 0 0 1.4 2.4h14.6a1.6 1.6 0 0 0 1.4-2.4L13.4 4.3a1.6 1.6 0 0 0-2.8 0z"/>
    <path d="M12 9.4v4M12 16.4h.01"/>`,
  x: `<path d="m6.4 6.4 11.2 11.2M17.6 6.4 6.4 17.6"/>`,
  chevronDown: `<path d="m6.6 9.4 5.4 5.2 5.4-5.2"/>`,
  chevronRight: `<path d="m9.4 6.6 5.2 5.4-5.2 5.4"/>`,
  spark: `<path d="M12 3.6 13.9 9l5.4 1.9-5.4 2L12 18.4 10.1 12.9 4.7 11 10.1 9z"/>`,
  keyboard: `<rect x="2.8" y="6" width="18.4" height="12" rx="2.2"/>
    <path d="M6.4 9.6h.01M9.6 9.6h.01M12.8 9.6h.01M16 9.6h.01M6.4 12.8h.01M17.6 9.6h.01M17.6 12.8h.01M9.2 15.6h5.6M9.6 12.8h4.8"/>`,
};

const NS = "http://www.w3.org/2000/svg";

/** Escape text that lands inside an attribute. */
function escAttr(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/**
 * An inline SVG icon.
 *
 * @param {keyof typeof PATHS} name
 * @param {{size?: number, label?: string, class?: string, strokeWidth?: number}} [opts]
 */
export function icon(name, opts = {}) {
  const body = PATHS[name];
  if (!body) return "";                      // a missing icon renders nothing, never "undefined"
  const size = opts.size ?? 18;
  const label = opts.label ? `<title>${escAttr(opts.label)}</title>` : "";
  const a11y = opts.label ? `role="img" aria-label="${escAttr(opts.label)}"` : 'aria-hidden="true"';
  const cls = opts.class ? ` class="${escAttr(opts.class)}"` : "";
  return `<svg${cls} xmlns="${NS}" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" ` +
    `stroke="currentColor" stroke-width="${opts.strokeWidth ?? 1.75}" stroke-linecap="round" ` +
    `stroke-linejoin="round" ${a11y}>${label}${body}</svg>`;
}

/** The product mark: a rounded badge with the signal-bubble glyph inside. */
export function brandMark(size = 34) {
  return `<span class="brand-mark" aria-hidden="true">
    <svg xmlns="${NS}" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none"
         stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
      ${PATHS.brand}
    </svg>
  </span>`;
}

export const ICON_NAMES = Object.keys(PATHS);
