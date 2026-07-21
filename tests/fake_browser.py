"""Fake Selenium browser for testing listing capture flows."""

from bs4 import BeautifulSoup


class FakeWaitClock:
    def __init__(self) -> None:
        self.timeouts: list[int] = []

    def record(self, timeout: int) -> None:
        self.timeouts.append(timeout)


class FakeBrowser:
    def __init__(self, pages: list[str] | None = None, fail_on_next: bool = False,
                 fail_next_click: bool = False,
                 fail_on_find: bool = False, fail_page_source: int = 0,
                 found_selectors: set[str] | None = None):
        self.pages = pages or []
        self._page_index = 0
        self._fail_on_next = fail_on_next
        self._fail_next_click = fail_next_click
        self._fail_on_find = fail_on_find
        self._fail_page_source_remaining = fail_page_source
        self._found_selectors = found_selectors
        self.current_url = ""
        self._page_source = ""
        self.calls: list[str] = []
        self.wait_clock = FakeWaitClock()

    @property
    def page_source(self):
        if self._fail_page_source_remaining > 0:
            self._fail_page_source_remaining -= 1
            raise Exception("page_source_failed")
        return self._page_source

    @page_source.setter
    def page_source(self, value):
        self._page_source = value

    def get(self, url: str) -> None:
        self.calls.append(f"get:{url}")
        self.current_url = url
        if self._fail_on_next:
            self._fail_on_next = False
            raise Exception("navigation_failed")
        if self._page_index < len(self.pages):
            self._page_source = self.pages[self._page_index]
            self._page_index += 1
        else:
            self._page_source = ""

    def find_element(self, by: str, value: str | None = None, selector: str | None = None):
        self.calls.append(f"find_element:{value or selector}")
        if self._fail_on_find:
            raise Exception("find_failed")
        sel = value or selector or ""
        if self._found_selectors is not None and sel not in self._found_selectors:
            raise Exception("element_not_found")
        return _FakeElement(
            browser=self,
            fail_click=(sel == "a.next, .page-next, [rel=next]" and self._fail_next_click),
        )

    def find_elements(self, by: str, value: str | None = None, selector: str | None = None):
        self.calls.append(f"find_elements:{value or selector}")
        if self._fail_on_find:
            raise Exception("find_failed")
        sel = value or selector or ""
        if self._found_selectors is not None:
            return [_FakeElement(browser=self)] if sel in self._found_selectors else []
        return [_FakeElement(browser=self) for _ in BeautifulSoup(
            self._page_source, "html.parser"
        ).select(sel)]

    def quit(self) -> None:
        self.calls.append("quit")


class _FakeElement:
    def __init__(self, browser: FakeBrowser | None = None, fail_click: bool = False):
        self._browser = browser
        self._fail_click = fail_click

    def is_enabled(self) -> bool:
        return True

    def is_displayed(self) -> bool:
        return True

    def click(self) -> None:
        if self._fail_click:
            raise RuntimeError("next_click_failed")
        if self._browser:
            self._browser.current_url = self._browser.current_url + "?page=2"

    @property
    def text(self) -> str:
        return ""

    @property
    def tag_name(self) -> str:
        return "div"
