/** Web Share with a copy fallback (PRD §8 "Share actions"). */

export type ShareResult = "shared" | "copied" | "cancelled" | "failed";

export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Permission denied or insecure context: fall through to the textarea trick.
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch {
    return false;
  }
}

/** `navigator.share({title, text, url})`; when unavailable, copies the link. */
export async function shareOrCopy(data: { title?: string; text?: string; url: string }): Promise<ShareResult> {
  if (typeof navigator !== "undefined" && typeof navigator.share === "function") {
    try {
      if (!navigator.canShare || navigator.canShare(data)) {
        await navigator.share(data);
        return "shared";
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return "cancelled";
      // NotAllowedError etc.: fall back to copying.
    }
  }
  return (await copyText(data.url)) ? "copied" : "failed";
}

/** Display form of a link: host + path, no scheme ("kitchentab.app/b/Zp4mQ81c"). */
export function displayLink(url: string): string {
  return url.replace(/^https?:\/\//, "");
}
