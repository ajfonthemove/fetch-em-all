import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from fetch_em_all import config
from fetch_em_all.config import ConfigError


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
        return path

    def load(self, yaml_text, **kw):
        return config.load(self.write("fetch.yaml", yaml_text), **kw)


class Loading(Workspace):
    def test_all_source_kinds(self):
        self.write("m/pdfs.csv", "collection,file,origin_url,archive_url\n"
                                 "books,a.pdf,https://ex.edu/a.pdf,https://web.archive.org/web/1id_/https://ex.edu/a.pdf\n"
                                 "books,b.pdf,https://ex.edu/b.pdf,\n"
                                 "talks,c.pdf,https://ex.edu/c.pdf,\n")
        self.write("lists/extra.txt", "# extras\nhttps://ex.org/x.pdf\n\nhttps://ex.org/y.pdf\n")
        self.write("lists/more.jsonl", '"https://ex.org/j1.pdf"\n{"url": "https://ex.org/j2.pdf", "topic": "t"}\n')
        self.write("saved.html", '<a href="notes/n1.pdf">Note 1</a> <a href="/about">About</a> '
                                 '<a href="https://other.org/n2.PDF">N2</a> <a href="#top">top</a>')
        settings, jobs = self.load("""
            out: data
            sources:
              - name: inline
                dest: "inline/{file_name}"
                urls:
                  - https://ex.edu/files/one.pdf
                  - {url: https://ex.edu/files/two.pdf, dest: special/two.pdf, fallback: https://mirror.org/two.pdf}
              - name: books
                from: m/pdfs.csv
                url_column: origin_url
                fallback_column: archive_url
                where: {collection: books}
                dest: "{collection}/{file}"
              - name: extras
                from: lists/extra.txt
              - name: more
                from: lists/more.jsonl
                dest: "{host}/{stem}.{ext}"
              - name: page
                from_html: saved.html
                base_url: https://ex.edu/teaching/
                match: '(?i)\\.pdf$'
                dest: "page/{n}-{file_name}"
            """)
        self.assertEqual(settings.out, (self.dir / "data").resolve())
        got = {(j.source, j.url, str(j.dest), j.fallback) for j in jobs}
        self.assertEqual(got, {
            ("inline", "https://ex.edu/files/one.pdf", "inline/one.pdf", ""),
            ("inline", "https://ex.edu/files/two.pdf", "special/two.pdf", "https://mirror.org/two.pdf"),
            ("books", "https://ex.edu/a.pdf", "books/a.pdf", "https://web.archive.org/web/1id_/https://ex.edu/a.pdf"),
            ("books", "https://ex.edu/b.pdf", "books/b.pdf", ""),
            ("extras", "https://ex.org/x.pdf", "extras/x.pdf", ""),
            ("extras", "https://ex.org/y.pdf", "extras/y.pdf", ""),
            ("more", "https://ex.org/j1.pdf", "ex.org/j1.pdf", ""),
            ("more", "https://ex.org/j2.pdf", "ex.org/j2.pdf", ""),
            ("page", "https://ex.edu/teaching/notes/n1.pdf", "page/1-n1.pdf", ""),
            ("page", "https://other.org/n2.PDF", "page/2-n2.PDF", ""),
        })
        self.assertEqual(next(j for j in jobs if j.source == "more" and "j2" in j.url).fields["topic"], "t")

    def test_defaults_and_overrides(self):
        settings, _ = self.load("""
            out: ~/x
            sources: [{name: a, urls: [https://ex.org/a.pdf]}]
            """, out_override="elsewhere")
        self.assertEqual(settings.out, Path("elsewhere").resolve())
        self.assertEqual((settings.browser, settings.port, settings.pace_seconds), ("chrome", 8765, 2.0))

    def test_only_selects_sources(self):
        _, jobs = self.load("""
            out: d
            sources:
              - {name: a, urls: [https://ex.org/a.pdf]}
              - {name: b, urls: [https://ex.org/b.pdf]}
            """, only=["b"])
        self.assertEqual([j.source for j in jobs], ["b"])


class Problems(Workspace):
    def problems(self, yaml_text, **kw):
        with self.assertRaises(ConfigError) as ctx:
            self.load(yaml_text, **kw)
        return "\n".join(ctx.exception.problems)

    def test_reports_everything_at_once(self):
        self.write("t.csv", "link\nhttps://ex.org/a.pdf\n")
        text = self.problems("""
            browser: netscape
            colour: blue
            sources:
              - name: a
                from: missing.csv
              - name: b
                from: t.csv
                url_column: url
              - name: c
                urls: ["ftp://ex.org/a.pdf", "https://ex.org/b.pdf"]
                dest: "{nope}/{file_name}"
              - name: c
                urls: [https://ex.org/z.pdf]
                bogus: 1
              - urls: [https://ex.org/q.pdf]
            """)
        for expected in ["unknown key 'colour'", "out: missing", "'netscape' is not one of", "file not found",
                         "no column 'url' in t.csv (columns: link)", "not an http(s) URL: 'ftp://ex.org/a.pdf'",
                         "unknown field 'nope'", "duplicate name", "unknown key 'bogus'", "missing name"]:
            self.assertIn(expected, text)

    def test_two_urls_one_destination(self):
        text = self.problems("""
            out: d
            sources:
              - {name: a, dest: "x/{file_name}", urls: [https://one.org/f.pdf]}
              - {name: b, dest: "X/{file_name}", urls: [https://two.org/F.pdf]}
            """)
        self.assertIn("is used by two URLs", text)

    def test_same_url_twice_is_merged(self):
        _, jobs = self.load("""
            out: d
            sources: [{name: a, urls: [https://ex.org/f.pdf, https://ex.org/f.pdf]}]
            """)
        self.assertEqual(len(jobs), 1)

    def test_relative_links_need_base_url(self):
        self.write("p.html", '<a href="a.pdf">A</a>')
        self.assertIn("set base_url", self.problems("""
            out: d
            sources: [{name: p, from_html: p.html}]
            """))

    def test_unknown_only_name(self):
        self.assertIn("no source named zz", self.problems("""
            out: d
            sources: [{name: a, urls: [https://ex.org/a.pdf]}]
            """, only=["zz"]))

    def test_not_yaml(self):
        self.assertIn("not valid YAML", self.problems("out: [unclosed\n"))


class Destinations(unittest.TestCase):
    def test_components_are_sanitized_and_cannot_escape(self):
        self.assertEqual(config.render_dest("../../etc/{x}", {"x": "pass:wd"}), Path("etc/pass_wd"))
        self.assertEqual(config.render_dest("/abs/{x}", {"x": "a/b"}), Path("abs/a/b"))
        self.assertEqual(config.render_dest("{x}", {"x": "é" * 300}).name.encode().__len__() <= 150, True)
        with self.assertRaises(ValueError):
            config.render_dest("{x}", {"x": "/../"})

    def test_url_fields(self):
        f = config.url_fields("https://ex.org/a%20b/file%201.tar.gz?x=1", 3)
        self.assertEqual((f["host"], f["path"], f["file_name"], f["stem"], f["ext"], f["n"]),
                         ("ex.org", "a b/file 1.tar.gz", "file 1.tar.gz", "file 1.tar", "gz", "3"))
        self.assertEqual(config.url_fields("https://ex.org/", 7)["file_name"], "7")

    def test_jobs_for_urls(self):
        jobs = config.jobs_for_urls(["https://ex.org/a.pdf", "https://ex.org/d/b.pdf"])
        self.assertEqual([str(j.dest) for j in jobs], ["a.pdf", "b.pdf"])


class JsonSource(Workspace):
    def test_json_list_of_objects(self):
        self.write("l.json", json.dumps([{"link": "https://ex.org/a.pdf", "n": 1}, "https://ex.org/b.pdf"]))
        _, jobs = self.load("""
            out: d
            sources: [{name: j, from: l.json, url_column: link}]
            """)
        self.assertEqual([j.url for j in jobs], ["https://ex.org/a.pdf"])  # the bare string has no 'link'


if __name__ == "__main__":
    unittest.main()
