# Fetch 'Em All

Download a list of URLs **through your own browser** on macOS, in the background, one file at a time, filed into
folders with a checksum index.

Some sites serve files to a person's browser but refuse scripts: `curl`, Python and headless browsers all get
`403 Access Denied`. Fetch 'Em All asks your real browser (Chrome, Edge or Brave) to open each link exactly as if you
clicked it, then moves the download from `~/Downloads` to where your config says it belongs. You can keep working
meanwhile: the browser doesn't take focus, apart from a flicker of about a fifth of a second at the start of a run.

```
$ fetch-em-all run fetch.yaml
6 files from 3 sources; 0 already in /Users/you/Documents/fetched; 6 to fetch
[1/6]     2.21 MB  browser  papers/1706.03762.pdf
[2/6]     6.78 MB  browser  papers/gpt3.pdf
[3/6]     0.41 MB  fallback course/1/week1-notes.pdf
...
Done: 6 filed, 0 failed. Index: .../index.jsonl; failures: .../failed.jsonl
```

## Use it responsibly

This is for **personal-scale** downloading of files you could click yourself. It opens one file at a time with a pause
between files. It doesn't disguise automation, rotate IPs, solve CAPTCHAs or copy cookies into scripts, and it won't
help if a site blocks your browser too. Respect each site's terms and the copyright of what you download.

## Requirements

- macOS, and Google Chrome (or Chrome Beta/Canary, Microsoft Edge, Brave; only Chrome has been tested)
- Python 3.9+

`--fallback-only` runs anywhere: it skips the browser and fetches the fallback URLs (e.g. Internet Archive copies) directly.

## Install

```bash
python3 -m pip install "git+https://github.com/ajfonthemove/fetch-em-all"
fetch-em-all doctor        # read-only check of the setup below
```

## One-time setup

In the browser (Chrome shown; Edge and Brave use `edge://` and `brave://`):

1. **PDFs are downloaded, not shown:** `chrome://settings/content/pdfDocuments` → *Download PDFs*.
2. **No save dialog per file:** `chrome://settings/downloads` → *Ask where to save each file before downloading* off.
   Note the download location; if it isn't `~/Downloads`, set `downloads_dir`.
3. **Multiple downloads allowed for the controller page:** `chrome://settings/content/automaticDownloads` →
   *Allowed to automatically download multiple files* → **Add** `http://127.0.0.1:8765` (or your `port`).
4. On the first run macOS asks whether your terminal may control the browser: **Allow** (System Settings →
   Privacy & Security → Automation).

`fetch-em-all doctor` reads the browser profile to check 1–3. The browser saves settings with a delay, so a fresh
change may not show yet; `fetch-em-all run CONFIG --limit 1` is the real test.

## Quick start

```bash
fetch-em-all get https://example.edu/a.pdf https://example.edu/b.pdf --out ~/Downloads/fetched   # no config
fetch-em-all plan fetch.yaml --list       # validate and show URL -> destination for every file
fetch-em-all run fetch.yaml --limit 1     # smoke test
fetch-em-all run fetch.yaml               # everything; re-run to resume
fetch-em-all run fetch.yaml --only papers --fallback-only
```

`run` keeps the Mac awake for its own duration. Leave the browser open; if you close the tool's tab, it opens a new one.

## Config

See [`examples/fetch.yaml`](examples/fetch.yaml). Top-level keys:

| Key | Default | |
|---|---|---|
| `out` | (required) | Folder everything is filed under; relative to the config file. `--out` overrides it. |
| `browser` | `chrome` | `chrome`, `chrome-beta`, `chrome-canary`, `edge`, `brave` |
| `pace_seconds` | `2` | Pause between files |
| `fallback_pace_seconds` | `5` | Minimum interval between requests to `web.archive.org` |
| `downloads_dir` | `~/Downloads` | Where the browser saves downloads |
| `port` | `8765` | Local controller page port (must match the browser permission) |
| `start_timeout`, `finish_timeout` | `15`, `300` | Seconds for a download to start, and to finish |
| `sources` | (required) | List of sources (below) |

Each source has a unique `name` and exactly one of:

| Kind | Keys |
|---|---|
| Inline | `urls:` a list of URL strings, or mappings `{url, dest, fallback}` |
| File | `from:` a `.csv`, `.tsv`, `.txt` (one URL per line, `#` comments), `.json` (list of strings or objects) or `.jsonl` file. Tables and JSON use `url_column` (default `url`) and optionally `fallback_column`. |
| Saved page | `from_html:` a page saved from the browser; its `<a href>` links. Relative links need `base_url`. |

Optional on any source:
- **`dest`**: a destination template, default `{source}/{file_name}`. Fields are any column of the row, plus
  `{source}`, `{host}`, `{path}`, `{file_name}`, `{stem}`, `{ext}` and `{n}` (position within the source). Each path
  component is sanitized and can't escape `out`.
- **`where`**: keep only rows whose column equals a value (or one of a list).
- **`match`**: a regular expression URLs must match.

Rows without a URL are skipped. Every problem in a config is reported together before anything runs: unknown keys,
missing files or columns, bad URLs, and two URLs mapped to one destination (compared case-insensitively). Quote URLs
that contain ` #`; YAML would otherwise treat the rest as a comment.

## Output

```
<out>/
  <dest>...           the files
  index.jsonl         one line per filed file: path, source, via (browser/fallback), url, origin_url, bytes, sha256, fields, filed_at
  failed.jsonl        what failed in the latest run, and why
```

Files are checked before filing: a PDF must start like a PDF, and a web page (an error or login page, say) is never
filed under a non-HTML name. A file already at its destination is skipped, so re-running resumes.

## How it works

Any AppleScript command that loads a URL brings the browser to the front, so Fetch 'Em All sends just one, which
opens a small controller page (served on `127.0.0.1`) in a new background tab and immediately puts your app back in
front. From then on the page itself navigates to each link; that doesn't change focus, and because the response is
saved as a download the page stays loaded and asks for the next link. A link that opens a page instead (dead or
blocked) is recorded as failed, the controller is reloaded, and the run continues; fallback URLs are then fetched
directly. Details, design constraints and troubleshooting are in [`AGENTS.md`](AGENTS.md).

## Development

```bash
python3 -m pip install -e .
python3 -W error::ResourceWarning -m unittest discover -s tests -t .
```

The tests run offline and never drive a real browser.

## License

[MIT](LICENSE)
