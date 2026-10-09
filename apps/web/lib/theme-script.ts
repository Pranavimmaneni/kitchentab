/**
 * No-flash theme script (ADR-0007). It runs in <head> before first paint and sets `data-theme`
 * on <html> from the saved preference. "system" (or nothing saved) leaves the attribute off so
 * `prefers-color-scheme` decides.
 *
 * This string is fixed: its SHA-256 goes into the CSP `script-src` (see lib/csp.ts) and a unit
 * test recomputes the hash from the rendered markup, so any edit here must keep the test green.
 */
export const THEME_STORAGE_KEY = "kt-theme";

export const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem("${THEME_STORAGE_KEY}");if(t==="light"||t==="dark"){document.documentElement.setAttribute("data-theme",t)}else{document.documentElement.removeAttribute("data-theme")}}catch(e){}})();`;
