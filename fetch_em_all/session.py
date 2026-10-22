"""One browser session: a worker tab running the controller page, and one fetch at a time through it."""

from . import downloads
from .browser import WorkerTab
from .controller import Controller, serve

PICKUP_TIMEOUT = 10  # seconds for the controller page to ask for a URL


class Blocked(Exception):
    """The browser is not saving downloads at all (a setting); the run should stop."""


class DeadLink(Exception):
    """The link opened a page (error or blocked) instead of a download."""


class FetchTimeout(Exception):
    """A download did not start or did not finish in time."""


class BrowserSession:
    """Context manager: starts the controller server and worker tab; fetch() returns a finished download."""

    def __init__(self, browser, port, downloads_dir, start_timeout=15, finish_timeout=300):
        self.browser, self.port, self.downloads_dir = browser, port, downloads_dir
        self.start_timeout, self.finish_timeout = start_timeout, finish_timeout
        self.controller, self.server, self.tab = Controller(), None, WorkerTab(browser)

    @property
    def home(self):
        return f"http://127.0.0.1:{self.server.server_port}/"

    def __enter__(self):
        self.server = serve(self.controller, self.port)
        self.tab.open(self.home)
        return self

    def __exit__(self, *exc):
        self.controller.finish()
        self.tab.close()
        self.server.shutdown()
        self.server.server_close()

    def settings_url(self, page):
        return f"{self.browser.scheme}://settings/{page}"

    def fetch(self, url, expected_name, expected_ext=""):
        """Have the browser open url; return the completed file in the downloads folder."""
        folder = self.downloads_dir
        before = downloads.snapshot(folder)
        self.controller.offer(url, expected_name or url)
        if not self.controller.taken.wait(PICKUP_TIMEOUT):  # page gone: closed, or replaced by an error page
            self.tab.load(self.home)
            if not self.controller.taken.wait(PICKUP_TIMEOUT):
                raise FetchTimeout("the controller tab is not responding")
        if not downloads.wait_for(lambda: downloads.started(folder, before, expected_name, expected_ext),
                                  self.start_timeout):
            shown, title = self.tab.url() or "", self.tab.title()
            if shown.startswith(self.home):
                raise Blocked("the browser did not save the file; allow multiple downloads for "
                              f"{self.home.rstrip('/')} at {self.settings_url('content/automaticDownloads')}")
            self.tab.load(self.home)  # the link opened a page instead, replacing the controller
            if title.lower().endswith(".pdf"):  # the PDF viewer titles the tab with the file name
                raise Blocked("the browser showed the PDF instead of saving it; set "
                              f"{self.settings_url('content/pdfDocuments')} to 'Download PDFs'")
            raise DeadLink(f"the link opened a page instead of a download ({title or shown})")

        def done():
            src = downloads.finished_copy(folder, before, expected_name, expected_ext)
            return src if src and not downloads.in_progress(folder, before) else None

        src = downloads.wait_for(done, self.finish_timeout)
        if not src:
            raise FetchTimeout("the download did not finish")
        return src
