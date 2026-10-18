"""
YadaCoin Open Source License (YOSL) v1.1

Copyright (c) 2017-2025 Matthew Vogel, Reynold Vogel, Inc.
"""

import asyncio
import json
import re

from plugins.passwordchange.failures import classify_page_text

INTERACTIVE_JS = r"""
() => {
  const isVisible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (!style || style.visibility === "hidden" || style.display === "none") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const labelFor = (el) => {
    if (el.labels && el.labels.length) {
      return Array.from(el.labels).map((l) => l.innerText || l.textContent || "").join(" ").trim();
    }
    const aria = el.getAttribute("aria-label") || "";
    const labelled = el.getAttribute("aria-labelledby");
    let labelledText = "";
    if (labelled) {
      labelledText = labelled.split(/\s+/).map((id) => {
        const n = document.getElementById(id);
        return n ? (n.innerText || n.textContent || "") : "";
      }).join(" ");
    }
    return [aria, labelledText, el.getAttribute("placeholder") || "", el.getAttribute("name") || "", el.getAttribute("id") || ""].join(" ").trim();
  };
  const selectorFor = (el, idx) => {
    if (el.id) return `#${CSS.escape(el.id)}`;
    const name = el.getAttribute("name");
    const type = (el.getAttribute("type") || el.tagName || "").toLowerCase();
    if (name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(name)}"]`;
    if (el.getAttribute("data-testid")) return `[data-testid="${CSS.escape(el.getAttribute("data-testid"))}"]`;
    return `${el.tagName.toLowerCase()}:nth-of-type(${idx + 1})`;
  };
  const nodes = Array.from(document.querySelectorAll("input, button, a, select, textarea, [role='button'], [role='link'], [role='textbox']"));
  const items = [];
  nodes.forEach((el, idx) => {
    if (!isVisible(el)) return;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    items.push({
      selector: selectorFor(el, idx),
      tag,
      type,
      name: el.getAttribute("name") || "",
      id: el.id || "",
      text: ((el.innerText || el.textContent || "").trim()).slice(0, 120),
      label: labelFor(el).slice(0, 160),
      autocomplete: el.getAttribute("autocomplete") || "",
      href: el.getAttribute("href") || "",
      disabled: !!el.disabled,
    });
  });
  const bodyText = (document.body && (document.body.innerText || "")) || "";
  return {
    title: document.title || "",
    url: location.href,
    text: bodyText.slice(0, 4000),
    elements: items.slice(0, 80),
  };
}
"""


class BrowserSession:
    def __init__(self, page, timeout_ms=30000):
        self.page = page
        self.timeout_ms = timeout_ms

    async def goto(self, url):
        await self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        await self.page.wait_for_timeout(400)
        return await self.snapshot()

    async def snapshot(self):
        data = await self.page.evaluate(INTERACTIVE_JS)
        data["error_code"] = classify_page_text(data.get("title"), data.get("text"), data.get("url"))
        return data

    async def fill(self, selector, value):
        loc = self.page.locator(selector).first
        await loc.wait_for(state="visible", timeout=self.timeout_ms)
        await loc.fill(str(value), timeout=self.timeout_ms)
        return await self.snapshot()

    async def click(self, selector):
        loc = self.page.locator(selector).first
        await loc.wait_for(state="visible", timeout=self.timeout_ms)
        await loc.click(timeout=self.timeout_ms)
        await self.page.wait_for_timeout(500)
        return await self.snapshot()

    async def press(self, selector, key="Enter"):
        loc = self.page.locator(selector).first
        await loc.press(key, timeout=self.timeout_ms)
        await self.page.wait_for_timeout(500)
        return await self.snapshot()

    async def wait_for_url(self, pattern, timeout_ms=None):
        await self.page.wait_for_url(pattern, timeout=timeout_ms or self.timeout_ms)
        return await self.snapshot()


async def launch_browser(playwright, *, headless=True, user_agent=None):
    browser = await playwright.chromium.launch(headless=headless)
    context_kwargs = {
        "viewport": {"width": 1280, "height": 800},
        "ignore_https_errors": True,
    }
    if user_agent:
        context_kwargs["user_agent"] = user_agent
    try:
        from playwright_stealth import Stealth
        context = await browser.new_context(**context_kwargs)
        await Stealth().hook_playwright_context(context)
    except Exception:
        context = await browser.new_context(**context_kwargs)
    page = await context.new_page()
    return browser, context, page


def looks_like_success(snapshot, new_password):
    text = ((snapshot or {}).get("text") or "").lower()
    title = ((snapshot or {}).get("title") or "").lower()
    hay = f"{title} {text}"
    positives = (
        "password updated",
        "password has been updated",
        "password changed",
        "password has been changed",
        "password successfully",
        "successfully changed",
        "successfully updated",
        "your password has been",
        "password saved",
    )
    if any(p in hay for p in positives):
        return True
    password_inputs = [
        e
        for e in (snapshot or {}).get("elements") or []
        if e.get("type") == "password"
    ]
    if not password_inputs and any(k in hay for k in ("security", "account", "settings", "profile")):
        return True
    return False


def looks_logged_in(snapshot):
    text = ((snapshot or {}).get("text") or "").lower()
    positives = ("sign out", "log out", "logout", "account settings", "my account")
    return any(p in text for p in positives)
