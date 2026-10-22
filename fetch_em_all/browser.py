"""Talk to a Chromium browser on macOS through AppleScript, without taking focus from the user.

Any AppleScript command that loads a URL brings the browser to the front, so callers send at most one
such command per run (the controller page) and put the previous app back in front right after it.
Reading a tab's URL or title, and closing a tab, do not change focus.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Browser:
    app: str           # AppleScript application name
    bundle_id: str
    scheme: str        # settings pages: <scheme>://settings/...
    profile_root: str  # under ~/Library/Application Support


BROWSERS = {
    "chrome": Browser("Google Chrome", "com.google.Chrome", "chrome", "Google/Chrome"),
    "chrome-beta": Browser("Google Chrome Beta", "com.google.Chrome.beta", "chrome", "Google/Chrome Beta"),
    "chrome-canary": Browser("Google Chrome Canary", "com.google.Chrome.canary", "chrome", "Google/Chrome Canary"),
    "edge": Browser("Microsoft Edge", "com.microsoft.edgemac", "edge", "Microsoft Edge"),
    "brave": Browser("Brave Browser", "com.brave.Browser", "brave", "BraveSoftware/Brave-Browser"),
}


class AppleScriptError(RuntimeError):
    pass


def osa(script):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode:
        message = r.stderr.strip() or "osascript failed"
        if "-1743" in message or "Not authorized to send Apple events" in message:
            message += (" -- allow your terminal to control the browser in System Settings > Privacy & Security"
                        " > Automation")
        raise AppleScriptError(message)
    return r.stdout.strip()


def quote(text):
    """An AppleScript string literal."""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def front_app():
    """Bundle id of the frontmost app ('' if unknown). lsappinfo needs no extra permissions."""
    try:
        asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True).stdout.strip()
        out = subprocess.run(["lsappinfo", "info", "-only", "bundleID", asn], capture_output=True, text=True).stdout
    except OSError:
        return ""
    return out.split("=")[-1].strip().strip('"')


def restore_front(bundle_id, browser):
    """Put bundle_id back in front, unless it was the browser itself (then nothing changed)."""
    if bundle_id and bundle_id != browser.bundle_id:
        subprocess.run(["open", "-b", bundle_id], capture_output=True)


def is_installed(browser):
    return subprocess.run(["open", "-Ra", browser.app], capture_output=True).returncode == 0


def profile_dir(browser):
    return Path.home() / "Library" / "Application Support" / browser.profile_root


class WorkerTab:
    """The one tab this tool owns: created at the end of the front window, closed at the end."""

    def __init__(self, browser):
        self.browser, self.window_id, self.tab_id = browser, None, None

    def _tell(self, body):
        return osa(f"tell application {quote(self.browser.app)} to {body}")

    def _ref(self):
        return f"(tab id {self.tab_id} of window id {self.window_id})"

    def open(self, url):
        """Create the tab showing url, keeping the user's active tab and frontmost app."""
        previous = front_app()
        ids = osa(f"""tell application {quote(self.browser.app)}
            if (count of windows) = 0 then make new window
            set w to front window
            set keep to active tab index of w
            set t to make new tab at end of tabs of w with properties {{URL:{quote(url)}}}
            set active tab index of w to keep
            return (id of w as text) & "," & (id of t as text)
        end tell""")
        restore_front(previous, self.browser)
        self.window_id, self.tab_id = ids.split(",")

    def load(self, url):
        """Point the tab at url (recreating it if the user closed it), keeping the frontmost app."""
        if self.url() is None:
            self.open(url)
            return
        previous = front_app()
        self._tell(f"set URL of {self._ref()} to {quote(url)}")
        restore_front(previous, self.browser)

    def url(self):
        """The tab's URL, or None if it no longer exists."""
        try:
            return self._tell(f"get URL of {self._ref()}")
        except AppleScriptError:
            return None

    def title(self):
        try:
            return self._tell(f"get title of {self._ref()}")
        except AppleScriptError:
            return ""

    def close(self):
        if self.tab_id is None:
            return
        try:
            osa(f"""tell application {quote(self.browser.app)}
                repeat with w in windows
                    repeat with t in (tabs of w)
                        if (id of t as text) is {quote(self.tab_id)} then
                            close t
                            return
                        end if
                    end repeat
                end repeat
            end tell""")
        except AppleScriptError:
            pass
        self.tab_id = None
