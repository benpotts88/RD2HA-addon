from __future__ import annotations

import logging
import os
import stat
import time

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    sync_playwright,
)

from .config import Config
from .models import PortalDevice, PortalReading
from .parser import PortalParseError, parse_device_reading

LOGGER = logging.getLogger(__name__)


class ScraperError(RuntimeError):
    pass


class AuthExpiredError(ScraperError):
    pass


class LoginError(ScraperError):
    pass


class DevicePageError(ScraperError):
    pass


class TanklevelsScraper:
    def __init__(self, config: Config) -> None:
        self.config = config

    def scrape_reading(self, device: PortalDevice | None = None) -> PortalReading:
        if device is None:
            device = self.config.devices[0]
        self.config.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.config.playwright_headless)
            try:
                return self._scrape_with_browser(browser, device)
            finally:
                browser.close()

    def _scrape_with_browser(self, browser: Browser, device: PortalDevice) -> PortalReading:
        context = self._context_from_saved_state(browser)
        if context:
            try:
                return self._read_device_page(context, device)
            except AuthExpiredError:
                LOGGER.warning("step=auth saved_session_expired=true")
                self._remove_storage_state()
            finally:
                context.close()

        context = self._login_context(browser)
        try:
            reading = self._read_device_page(context, device)
            self._save_storage_state(context)
            return reading
        finally:
            context.close()

    def _context_from_saved_state(self, browser: Browser) -> BrowserContext | None:
        if not self.config.storage_state_path.exists():
            return None

        LOGGER.info("step=auth using_saved_state=true")
        try:
            return browser.new_context(storage_state=str(self.config.storage_state_path))
        except PlaywrightError as exc:
            LOGGER.warning(
                "step=auth saved_session_load_failed=%s",
                exc.__class__.__name__,
            )
            self._remove_storage_state()
            return None

    def _login_context(self, browser: Browser) -> BrowserContext:
        LOGGER.info("step=auth login_required=true")
        self.config.require_tanklevels_credentials()
        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto(
                self.config.login_url,
                wait_until="domcontentloaded",
                timeout=self.config.playwright_timeout_ms,
            )
            self._submit_login(page)
            return context
        except Exception:
            context.close()
            raise

    def _submit_login(self, page: Page) -> None:
        self._fill_first(
            page,
            [
                "input[type='email']",
                "input[name='email']",
                "input[name='Email']",
                "input[autocomplete='username']",
                "input[type='text']",
            ],
            self.config.tanklevels_email,
            "email field",
        )
        self._fill_first(
            page,
            [
                "input[type='password']",
                "input[name='password']",
                "input[name='Password']",
                "input[autocomplete='current-password']",
            ],
            self.config.tanklevels_password,
            "password field",
        )

        clicked = self._click_first(
            page,
            [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Log in')",
                "button:has-text('Login')",
                "text=Log in",
                "text=Login",
            ],
        )
        if not clicked:
            page.keyboard.press("Enter")

        for _ in range(30):
            if not self._is_auth_challenge(page):
                return
            page.wait_for_timeout(500)

        raise LoginError("Tanklevels login form did not complete")

    def _read_device_page(self, context: BrowserContext, device: PortalDevice) -> PortalReading:
        page = context.new_page()
        page.goto(
            device.device_url,
            wait_until="domcontentloaded",
            timeout=self.config.playwright_timeout_ms,
        )
        if self._is_auth_challenge(page):
            raise AuthExpiredError("Tanklevels device page requires login")

        return self._wait_for_stable_reading(page, device)

    def _wait_for_stable_reading(self, page: Page, device: PortalDevice) -> PortalReading:
        started_at = time.monotonic()
        deadline = started_at + (self.config.playwright_timeout_ms / 1_000)
        last_fingerprint: tuple[tuple[str, object], ...] | None = None
        stable_since: float | None = None
        last_parse_error: PortalParseError | None = None
        last_page_error: PlaywrightError | None = None

        while time.monotonic() < deadline:
            text, page_error = self._body_text(page)
            if page_error is not None:
                last_page_error = page_error
            if text.strip():
                last_page_error = None
                try:
                    reading = parse_device_reading(
                        text,
                        device,
                        timezone_name=self.config.timezone,
                    )
                except PortalParseError as exc:
                    last_parse_error = exc
                else:
                    last_parse_error = None
                    now = time.monotonic()
                    fingerprint = reading.stable_fingerprint()
                    observed_long_enough = self._observed_long_enough(started_at, now)
                    if fingerprint == last_fingerprint:
                        if (
                            stable_since is not None
                            and now - stable_since >= self.config.page_stable_seconds
                            and observed_long_enough
                        ):
                            LOGGER.info(
                                "step=device_page_ready device_id=%s min_ready_seconds=%s stable_seconds=%s",
                                device.device_id,
                                self.config.page_min_ready_seconds,
                                self.config.page_stable_seconds,
                            )
                            return reading
                    else:
                        last_fingerprint = fingerprint
                        stable_since = now
                        if self.config.page_stable_seconds == 0 and observed_long_enough:
                            return reading

            try:
                page.wait_for_timeout(self.config.page_stable_sample_interval_ms)
            except PlaywrightError as exc:
                last_page_error = exc
                break

        detail = self._stability_error_detail(last_parse_error, last_page_error)
        raise DevicePageError(
            f"Device page did not produce a stable complete reading for {device.device_id}."
            f"{detail}"
        )

    def _body_text(self, page: Page) -> tuple[str, PlaywrightError | None]:
        try:
            text = page.evaluate("document.body.innerText")
        except PlaywrightError as exc:
            return "", exc
        if not isinstance(text, str) or not text.strip():
            return "", None
        return text, None

    def _observed_long_enough(self, started_at: float, now: float) -> bool:
        return now - started_at >= self.config.page_min_ready_seconds

    def _stability_error_detail(
        self,
        parse_error: PortalParseError | None,
        page_error: PlaywrightError | None,
    ) -> str:
        details: list[str] = []
        if parse_error is not None:
            details.append(f"last_parse_error={parse_error}")
        if page_error is not None:
            details.append(
                f"last_page_error={page_error.__class__.__name__}: {page_error}"
            )
        if not details:
            return ""
        return " " + " ".join(details)

    def _is_auth_challenge(self, page: Page) -> bool:
        url = page.url.lower()
        if "/login" in url:
            return True
        try:
            has_password_field = page.locator("input[type='password']").count() > 0
            body_text = page.evaluate("document.body.innerText") or ""
        except PlaywrightError:
            return False
        return has_password_field and "last update received:" not in body_text.casefold()

    def _fill_first(
        self,
        page: Page,
        selectors: list[str],
        value: str,
        label: str,
    ) -> None:
        for selector in selectors:
            try:
                page.locator(selector).first.fill(value, timeout=2_000)
                return
            except PlaywrightError:
                continue
        raise LoginError(f"Could not find Tanklevels login {label}")

    def _click_first(self, page: Page, selectors: list[str]) -> bool:
        for selector in selectors:
            try:
                page.locator(selector).first.click(timeout=2_000)
                return True
            except PlaywrightError:
                continue
        return False

    def _remove_storage_state(self) -> None:
        try:
            self.config.storage_state_path.unlink(missing_ok=True)
        except OSError as exc:
            LOGGER.warning("step=auth storage_state_remove_failed=%s", exc.__class__.__name__)

    def _chmod_storage_state(self) -> None:
        try:
            os.chmod(self.config.storage_state_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            LOGGER.warning("step=auth storage_state_chmod_failed=%s", exc.__class__.__name__)

    def _save_storage_state(self, context: BrowserContext) -> None:
        context.storage_state(path=str(self.config.storage_state_path))
        self._chmod_storage_state()
        LOGGER.info("step=auth storage_state_saved=true")


def scrape_device_reading(
    config: Config,
    device: PortalDevice | None = None,
) -> PortalReading:
    return TanklevelsScraper(config).scrape_reading(device)
