# AGENTS.md: Fetch 'Em All

Instructions for coding agents building, maintaining or setting up this tool. Read all of it before changing code or
touching anyone's browser.

## What this is

Fetch 'Em All (`fetch-em-all`) downloads a list of URLs **through the user's own desktop browser** on macOS, in the background, one
file at a time, and files each download into a folder tree with a checksum index. It exists for sites whose CDN refuses
scripted clients (curl, Python, headless browsers all get `403 Access Denied`) but serves a person's browser normally:
the tool asks the user's real browser to open each link exactly as if the user clicked it.

It reads a YAML config listing URL sources (inline lists, CSV manifests, URL files, saved HTML pages) and resolves each
URL to a destination path. Manifests usually come from elsewhere, e.g. a CSV written by a crawler, with an
optional archived-copy column to fall back on.

## Hard rules

These are not style preferences. Do not implement, suggest or "temporarily try" anything below, even if a site keeps
refusing.

1. **No disguise.** No headless-browser stealth plugins, fingerprint or user-agent spoofing, `navigator.webdriver`
   patching, CAPTCHA solving, proxy or IP rotation, or copying the browser's cookies into scripted requests. The tool
   works only because it *is* the user's browser doing what the user asked. If a site blocks the browser too, that is
   the site's answer: report it and stop.
2. **Personal scale and paced.** One download at a time, with a pause between files (default 2 s). No parallel
   downloads, no retry storms. For Internet Archive fallbacks, at most one request every 5 s; about 40 a minute got
   this client's IP blocked for a while, and 12 a minute was fine.
3. **The human changes settings, not you.** Never edit browser preferences files, macOS privacy (TCC) databases or
   permissions. Tell the human exactly what to change (see [Setup](#one-time-setup-on-a-mac-the-human-does-these)) and
   wait.
4. **Touch only what you created.** Open at most one tab of your own (the worker tab) and close it at the end. Never
   navigate, close or read the contents of the user's tabs; reading the worker tab's URL and title is fine. Move only
   files that appeared in the downloads folder during the run and match the job, never anything that was already
   there or doesn't match.
5. **Ask before acting on the user's machine.** Opening tabs in the user's browser, starting a long run, or fetching
   more than a `--limit` smoke test needs the human's go-ahead in this session.
6. **Respect the content.** Fetched material is usually copyrighted. The tool records what it fetched and from where;
   it never commits downloaded files to a repository.

## Platform

- macOS only: AppleScript (`osascript`), `lsappinfo`, `open -b`, `caffeinate`.
- Google Chrome. Chromium browsers with the same AppleScript suite (Chrome Beta/Canary, Microsoft Edge, Brave) should
  work through a `browser:` setting (app name plus bundle id). Only Google Chrome is verified. Safari and Firefox are
  out of scope.
- Python 3.9+, standard library plus **PyYAML** (the only dependency).
- Verified behaviour (below) on macOS 26.5 with Chrome 156, October 2026.

## How it works, and why

Each of these was found the hard way; keep the design unless you re-verify on a real machine.

| Fact | Consequence |
|---|---|
| Chrome comes to the front whenever an AppleScript command loads a URL: `make new tab`, `set URL of tab`, and `open -g -a "Google Chrome" URL` all steal focus. | Send the browser **one** URL per run: a local controller page. Before that command, record the frontmost app (`lsappinfo front`, then `lsappinfo info -only bundleID <asn>`); afterwards restore it with `open -b <bundle id>`. That gives one ~0.2 s flicker per run instead of one per file. |
| Navigation started *by a page* (`location.href = url`) does not change focus, and a response saved as a download leaves the page loaded. | The controller page (served from `http://127.0.0.1:<port>/`) polls `/next` once a second and navigates itself to each URL the Python side hands out. Python hands out the next URL only after the previous file has been filed. |
| Downloads started from hidden cross-origin `<iframe>`s did not happen (cause unconfirmed: Chrome's download restrictions or the site's framing headers). | Use top-level navigation, not iframes. |
| After the first automatic download, Chrome blocks further downloads from the same page until the site is allowed under *automatic downloads*. | The controller must have a fixed origin (default port **8765**) so the human can allow `http://127.0.0.1:8765` once. |
| PDFs open in Chrome's viewer unless "Download PDFs" is set. | For PDF jobs that setting is required; detect the viewer (below) and stop with instructions. |
| A dead or blocked link loads an HTML page that replaces the controller. | After a start timeout, read the worker tab's URL and title (reading does not change focus). The URL is still the controller: downloads are being blocked, so stop. The title ends in `.pdf`: that's the PDF viewer, so stop with the settings hint. Anything else (e.g. "Page not found \| …"): record the URL as dead, reload the controller (restoring focus) and continue. Do **not** infer "viewer" just from the tab URL equalling the link; error pages often sit at the same URL. |
| Chrome writes `*.crdownload` while downloading and de-duplicates names as `name (1).ext`. Redirect links (e.g. Drupal `/file_url/<n>`) may arrive under the server's file name. | Detect a started download as any new `*.crdownload` or matching file. Match finished files by expected name or `name (n).ext`; if nothing matches and exactly one new completed file of the expected type appeared, take it. Never guess between several. |
| Reading tab URL or title, and closing a tab by id, do not change focus. | Safe to use freely for the worker tab. |
| The user's own browser may use a non-default downloads folder or "Ask where to save". | Config `downloads_dir`; the setup check requires "Ask where to save each file" to be **off** (otherwise a dialog appears per file). |

Filing: verify content before moving. For `.pdf`, `%PDF` must appear in the first 1024 bytes; add similar checks for
other types. Never file a web page under a non-HTML name. Then move the file to `<out>/<dest>`, append to `<out>/index.jsonl`
(`path`, `source`, `via` (`browser` or `fallback`), `url`, `origin_url`, `bytes`, `sha256`, `fields` (the source row),
`filed_at`) and rewrite `<out>/failed.jsonl` at the end of each run. A file already at its destination is skipped, which is what makes re-runs resume.

## Config (YAML)

```yaml
out: ~/Data/Fetched             # every dest is relative to this
browser: chrome                 # chrome | chrome-beta | edge | brave
pace_seconds: 2
downloads_dir: ~/Downloads
port: 8765

sources:
  - name: errata                           # inline URLs: strings or {url, dest, fallback}
    dest: "errata/{file_name}"
    urls:
      - https://example.edu/files/corrections.pdf
      - url: https://example.edu/files/relcorrections.pdf
        dest: errata/relativity/relcorrections.pdf

  - name: course-pdfs                      # CSV manifest
    from: manifests/pdfs.csv
    url_column: origin_url
    fallback_column: archive_url           # optional: archived copy if the browser gets nothing
    dest: "{collection}/{page}/{file_name}"
    where: { archive_match: none }         # optional equality filter on columns

  - name: extras                           # text file: one URL per line, '#' comments
    from: lists/extra-urls.txt

  - name: lecture-notes                    # links from a page the user saved in the browser
    from_html: "~/Downloads/Teaching | Example Department.html"
    base_url: https://example.edu/teaching
    match: '\.pdf$'
    dest: "notes/{file_name}"
```

Rules:
- `from` paths resolve relative to the config file; `~` expands. File type comes from the extension: `.csv`, `.tsv`,
  `.txt`, `.json`, or `.jsonl`.
- `dest` templates may use any column of the source row, plus `{host}`, `{path}`, `{file_name}` (URL-decoded last path
  segment) and `{n}` (1-based index within the source). A non-empty row column wins over a derived field of the same
  name. Sanitize every path component: no separators, no control
  characters, bounded to 150 bytes.
- Validate the whole config before opening anything: unknown keys, missing files, unknown columns, duplicate
  destinations (compare case-insensitively; macOS volumes usually are), and URLs that are not `http(s)`. Report every
  problem at once.
- YAML gotcha: a URL containing ` #` is truncated as a comment unless it's quoted. The README says to quote unusual
  URLs; the parser cannot detect this.

CLI (exit codes: 0 all fetched, 1 some failed, 2 config or setup error):
- `fetch-em-all plan CONFIG [--only NAME,...]`: validate and print every resolved URL → destination, and what is
  already present. Never touches the browser.
- `fetch-em-all run CONFIG [--only NAME,...] [--limit N] [--fallback-only] [--no-fallback] [--out DIR]`: fetch.
  Keeps the Mac awake for its own duration (`caffeinate -i -w <pid>`).
- `fetch-em-all get URL... --out DIR`: one-off fetch without a config.
- `fetch-em-all doctor [--browser NAME] [--port N]`: read-only setup check (see below).

## Suggested layout

```
fetch_em_all/
  cli.py          # subcommands, exit codes
  config.py       # YAML loading, validation, source expansion, dest templates
  sources.py      # csv / tsv / txt / json / jsonl / saved-HTML readers
  browser.py      # AppleScript: worker tab, URL/title reads, close; focus save/restore; browser table
  controller.py   # local HTTP server + controller page + hand-off queue
  session.py      # one browser fetch: hand off a URL, classify the outcome, return the finished download
  downloads.py    # downloads-folder watching, name matching, content checks, index
  fallback.py     # direct/archive fetch for fallback URLs (urllib, paced per host)
  engine.py       # the run loop: skip present, browser, fallback, file, index, failed
  doctor.py       # read-only setup check
tests/            # offline only (see below)
README.md         # user-facing setup and usage
AGENTS.md
```

## Testing

- **Automated tests never drive a real browser or the user's Downloads folder.** Test the controller hand-off and its
  HTTP server on an ephemeral port (`port=0`), download matching against a temp directory, config validation, source
  readers, template resolution, and archive fallback against a local `http.server`. Run with
  `python3 -W error::ResourceWarning -m unittest discover -s tests`; it must be warning-clean, so close `HTTPError`
  objects and servers.
- **Manual smoke test**, only with the human's go-ahead: `--limit 1`, then `--limit 3`, while sampling the frontmost
  app every 0.1 s with `lsappinfo`. Expected: one switch to the browser lasting under half a second at the start and
  none afterwards; the user's active tab and tab count unchanged at the end; files filed; nothing left in Downloads.
- CI runs the offline tests on Ubuntu only. Do not add macOS runners (cost); they could not run the smoke
  test anyway.

## One-time setup on a Mac (the human does these)

Walk the human through these in order and wait for confirmation. `fetch-em-all doctor` checks what can be checked
without changing anything: it reads the browser profile's `Preferences` JSON for `plugins.always_open_pdf_externally`,
`download.*` and the automatic-downloads exception. The browser flushes that file with a delay, so a missing value
is not proof; the first file of a `run --limit 1` is.

1. Install: `python3 -m pip install git+https://github.com/ajfonthemove/fetch-em-all`, then run
   `fetch-em-all doctor`, which reports the items below that it can read. On python.org builds of Python, run
   *Install Certificates.command* if HTTPS fails with `CERTIFICATE_VERIFY_FAILED`.
2. PDF jobs: `chrome://settings/content/pdfDocuments` → **Download PDFs**.
3. `chrome://settings/downloads` → **Ask where to save each file before downloading: off**. Note the download
   location; if it isn't `~/Downloads`, set `downloads_dir`.
4. `chrome://settings/content/automaticDownloads` → *Allowed to automatically download multiple files* → **Add**
   `http://127.0.0.1:8765`, or the configured port.
5. On the first run macOS asks whether the terminal may control the browser: **Allow** (System Settings → Privacy &
   Security → Automation).
6. During a run: keep the browser open and the Mac awake. Run under `caffeinate -i`. The user can keep working; if they
   close the worker tab it is recreated.

Afterwards, tell the human they can revert steps 2 and 4 if they won't re-run.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Stops on the first file: "showed the PDF instead of saving it" | PDF viewer active | Setup step 2 |
| First file saved, then stops: "did not save the file" | Automatic-downloads protection | Setup step 4 (port must match) |
| Save dialog appears for every file | "Ask where to save" on | Setup step 3 |
| `FAIL … (Page not found …)` | Dead link (often a stale manifest from an old page snapshot) | Find the current link; for blocked pages, have the human save the page and use `from_html` |
| Browser comes forward on every file | Something sends URLs via AppleScript per file | Restore the controller design; only the controller load may use AppleScript |
| `osascript` error "not allowed to send Apple events" | Automation permission denied | Setup step 5 |
| `Access Denied` in the browser too | The site blocks this network, not just scripts | Stop. Do not escalate (hard rule 1). Offer an archive fallback or contacting the author. |
| Archive requests hang, `connection refused`, or 403 | Internet Archive rate limit or block | Wait, then use ≥ 5 s between requests |
| Wayback replays `403` for a page that exists | Nearest capture is the CDN's block page | Pick the newest capture with `statuscode:200` from the CDX API (`limit=-1`), then fetch `…/web/<ts>id_/<url>` |
| Archived page bytes look like binary noise | Stored gzip body replayed as-is | Decompress when the body starts `\x1f\x8b` |
