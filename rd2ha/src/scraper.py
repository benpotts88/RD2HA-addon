from __future__ import annotations

import logging
import os
import stat

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from .config import Config

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

    def scrape_text(self) -> str:
        self.config.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.config.playwright_headless)
            try:
                return self._scrape_with_browser(browser)
            finally:
                browser.close()

    def _scrape_with_browser(self, browser: Browser) -> str:
        context = self._context_from_saved_state(browser)
        if context:
            try:
                return self._read_device_page(context)
            except AuthExpiredError:
                LOGGER.warning("step=auth saved_session_expired=true")
                self._remove_storage_state()
            finally:
                context.close()

        context = self._login_context(browser)
        try:
            text = self._read_device_page(context)
            self._save_storage_state(context)
            return text
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

    def _read_device_page(self, context: BrowserContext) -> str:
        page = context.new_page()
        page.goto(
            self.config.tanklevels_device_url,
            wait_until="domcontentloaded",
            timeout=self.config.playwright_timeout_ms,
        )
        if self._is_auth_challenge(page):
            raise AuthExpiredError("Tanklevels device page requires login")

        try:
            page.get_by_text(self.config.device_name, exact=False).wait_for(
                timeout=self.config.playwright_timeout_ms
            )
            page.get_by_text("Last update received:", exact=False).wait_for(
                timeout=self.config.playwright_timeout_ms
            )
            page.wait_for_function(
                """
                () => {
                    const text = document.body.innerText || "";
                    return /\\d+(?:\\.\\d+)?\\s*%/.test(text)
                        && /-?\\d+(?:\\.\\d+)?\\s*°\\s*C/.test(text);
                }
                """,
                timeout=self.config.playwright_timeout_ms,
            )
        except PlaywrightTimeoutError as exc:
            raise DevicePageError("Device page did not render expected tank text") from exc

        text = page.evaluate("document.body.innerText")
        if not isinstance(text, str) or not text.strip():
            raise DevicePageError("Device page body text was empty")
        return text

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


def scrape_device_text(config: Config) -> str:
    return TanklevelsScraper(config).scrape_text()
