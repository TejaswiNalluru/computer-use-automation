"""Playwright web surface — semantic locators + accessibility snapshot."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from playwright.async_api import Browser, Page, async_playwright

from computer_use.models import Locator, LocatorStrategy
from computer_use.safety import Policy, PolicyViolation


@dataclass
class ObservedState:
    url: str
    title: str
    text: str
    a11y_snapshot: str
    dialog_present: bool


class WebSurface:
    def __init__(
        self,
        *,
        policy: Policy,
        headless: bool = True,
        entry_url: str = "http://127.0.0.1:3000/",
    ):
        self.policy = policy
        self.headless = headless
        self.entry_url = entry_url
        self._pw = None
        self.browser: Browser | None = None
        self.page: Page | None = None
        self.paused = False

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(headless=self.headless)
        context = await self.browser.new_context(viewport={"width": 1100, "height": 800})
        self.page = await context.new_page()

    async def stop(self) -> None:
        if self.browser:
            await self.browser.close()
        if self._pw:
            await self._pw.stop()
        self.browser = None
        self.page = None

    def _page(self) -> Page:
        if not self.page:
            raise RuntimeError("Surface not started")
        return self.page

    async def navigate(self, url: str) -> None:
        if url.startswith("/"):
            url = urljoin(self.entry_url, url)
        self.policy.check_url(url)
        await self._page().goto(url, wait_until="domcontentloaded")

    async def observe(self) -> ObservedState:
        page = self._page()
        url = page.url
        self.policy.check_url(url)
        title = await page.title()
        text = await page.inner_text("body")
        dialog_present = await page.locator("div.overlay").count() > 0
        try:
            snap = await page.locator("body").aria_snapshot()
        except Exception:
            snap = "(a11y snapshot unavailable)"
        return ObservedState(
            url=url,
            title=title,
            text=text,
            a11y_snapshot=snap if isinstance(snap, str) else str(snap),
            dialog_present=dialog_present,
        )

    async def click(self, locator: Locator) -> None:
        self.policy.check_locator_risk(locator)
        await self._resolve(locator).click(timeout=locator_timeout(locator))

    async def fill(self, locator: Locator, value: str) -> None:
        self.policy.check_locator_risk(locator)
        handle = self._resolve(locator)
        await handle.fill(value, timeout=locator_timeout(locator))

    async def press(self, key: str) -> None:
        await self._page().keyboard.press(key)

    async def extract(self, locator: Locator) -> str:
        handle = self._resolve(locator)
        return (await handle.inner_text(timeout=locator_timeout(locator))).strip()

    async def wait_for_text(self, text: str, timeout_ms: int = 10_000) -> None:
        await self._page().get_by_text(text).first.wait_for(timeout=timeout_ms)

    async def dismiss_dialog_if_present(self) -> bool:
        page = self._page()
        overlay = page.locator("div.overlay")
        if await overlay.count() == 0:
            return False
        dismiss = overlay.get_by_role("link", name=re.compile("Dismiss", re.I))
        if await dismiss.count() == 0:
            dismiss = overlay.get_by_text(re.compile("Dismiss", re.I))
        await dismiss.first.click()
        return True

    async def confirm_dialog_if_present(self) -> bool:
        page = self._page()
        if "Proceed with member lookup" not in await page.inner_text("body"):
            return False
        confirm = page.get_by_role("link", name=re.compile("Confirm", re.I))
        if await confirm.count() == 0:
            confirm = page.get_by_text(re.compile("^Confirm$", re.I))
        await confirm.first.click()
        return True

    def entry_without_query(self) -> str:
        parsed = urlparse(self.entry_url)
        return urlunparse(parsed._replace(query=""))

    async def screenshot(self, path: str) -> None:
        await self._page().screenshot(path=path, full_page=True)

    async def text_present(self, text: str) -> bool:
        body = await self._page().inner_text("body")
        return text in body

    def current_url(self) -> str:
        return self._page().url

    def _resolve(self, locator: Locator):
        page = self._page()
        match locator.strategy:
            case LocatorStrategy.ROLE:
                role = locator.role or locator.value
                kwargs: dict[str, Any] = {}
                name = locator.name
                if name:
                    kwargs["name"] = (
                        re.compile(f"^{re.escape(name)}$")
                        if locator.exact
                        else re.compile(name, re.I)
                    )
                handle = page.get_by_role(role, **kwargs)  # type: ignore[arg-type]
            case LocatorStrategy.LABEL:
                handle = page.get_by_label(
                    locator.value, exact=locator.exact
                )
            case LocatorStrategy.TEXT:
                handle = page.get_by_text(locator.value, exact=locator.exact)
            case LocatorStrategy.PLACEHOLDER:
                handle = page.get_by_placeholder(
                    locator.value, exact=locator.exact
                )
            case LocatorStrategy.CSS:
                handle = page.locator(locator.value)
            case LocatorStrategy.TEST_ID:
                handle = page.get_by_test_id(locator.value)
            case _:
                raise ValueError(f"Unknown locator strategy: {locator.strategy}")
        if locator.nth is not None:
            handle = handle.nth(locator.nth)
        else:
            handle = handle.first
        return handle

    async def guard_navigation(self) -> None:
        """Re-check URL after an action that may navigate."""
        url = self._page().url
        try:
            self.policy.check_url(url)
        except PolicyViolation:
            # Back out of blocked page if we somehow landed there.
            await self._page().go_back()
            raise


def locator_timeout(locator: Locator) -> int:
    return 10_000


def host_from_url(url: str) -> str:
    return urlparse(url).netloc
