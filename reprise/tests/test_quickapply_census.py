import base64
import io
import json
import plistlib
import struct
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import quickapply_census as census

# Archive built by the official @electron/asar tool: index.js, package.json, node_modules/x/i.js.
OFFICIAL_ASAR = base64.b64decode(
    'BAAAAEADAAA8AwAANwMAAHsiZmlsZXMiOnsiLnZpdGUiOnsiZmlsZXMiOnsiYnVpbGQiOnsiZmlsZXMiOnsiaW5kZXguanMiOnsic2l6ZSI6ODksIm9mZnNldCI6IjAiLCJpbnRlZ3JpdHkiOnsiYWxnb3JpdGhtIjoiU0hBMjU2IiwiaGFzaCI6IjRhOTdiNTYxNmY3ZDc3OTAxYTNkMGYxODg2MjNmNWJlZDg5NzQ2ODBmY2Q2NWY3MzExMjRmMmQ3ZmM5MzA5YWQiLCJibG9ja1NpemUiOjQxOTQzMDQsImJsb2NrcyI6WyI0YTk3YjU2MTZmN2Q3NzkwMWEzZDBmMTg4NjIzZjViZWQ4OTc0NjgwZmNkNjVmNzMxMTI0ZjJkN2ZjOTMwOWFkIl19fX19fX0sIm5vZGVfbW9kdWxlcyI6eyJmaWxlcyI6eyJ4Ijp7ImZpbGVzIjp7ImkuanMiOnsic2l6ZSI6Niwib2Zmc2V0IjoiODkiLCJpbnRlZ3JpdHkiOnsiYWxnb3JpdGhtIjoiU0hBMjU2IiwiaGFzaCI6ImQyOGIzOWIwZWQ3YzIxZDNlZjRkZWU0ZTgyMzIxYjQzMDhiZmE2YjAyZTBmNzJjNzE4MmUxODkwNTE0ZmI2OWMiLCJibG9ja1NpemUiOjQxOTQzMDQsImJsb2NrcyI6WyJkMjhiMzliMGVkN2MyMWQzZWY0ZGVlNGU4MjMyMWI0MzA4YmZhNmIwMmUwZjcyYzcxODJlMTg5MDUxNGZiNjljIl19fX19fX0sInBhY2thZ2UuanNvbiI6eyJzaXplIjo4LCJvZmZzZXQiOiI5NSIsImludGVncml0eSI6eyJhbGdvcml0aG0iOiJTSEEyNTYiLCJoYXNoIjoiZTM0NjQzMjAyMWIwNDE3OTUxOGQ5NjE0ZjM1NjBjY2Q3MTM1NGE0ZWUxMDFkZGNiODkzZDY5NTlhOWQ2MzAxYyIsImJsb2NrU2l6ZSI6NDE5NDMwNCwiYmxvY2tzIjpbImUzNDY0MzIwMjFiMDQxNzk1MThkOTYxNGYzNTYwY2NkNzEzNTRhNGVlMTAxZGRjYjg5M2Q2OTU5YTlkNjMwMWMiXX19fX0AZnVuY3Rpb24gRyhlLHQpe1guZ2V0RGlzcGF0Y2hlcihlKT8uZGlzcGF0Y2hPblF1aWNrRW50cnlTdWJtaXQodCl9CmZ1bmN0aW9uIFYoZSl7Ryh3LGUpfQrDuCDDqQp7ImEiOjF9Cg==')


def write_asar(path, files):
    entries, blob, offset = {}, b'', 0
    for name, data in files.items():
        node = entries
        parts = name.split('/')
        for part in parts[:-1]:
            node = node.setdefault(part, {'files': {}})['files']
        node[parts[-1]] = {'size': len(data), 'offset': str(offset)}
        blob += data
        offset += len(data)
    header = json.dumps({'files': entries}).encode()
    padded = (len(header) + 3) // 4 * 4
    payload = 4 + padded
    Path(path).write_bytes(struct.pack('<IIII', 4, 4 + payload, payload, len(header))
                           + header.ljust(padded, b' ') + blob)


CHAIN = (
    'function G4(e,t){H.getDispatcher(e)?.dispatchOnQuickEntrySubmit(t)}\n'
    'async function K4(e,t){G4(win,e)}\n'
    'async function V4(e,t){return K4(e,t)}\n'
    'function z4(e){return V4({text:e??"",images:[]})}\n'
    'app.on("open-url",(ev,url)=>{z4(new URL(url).searchParams.get("q"))});\n'
    'const secret="' + 'A' * 60 + '";\n')


class CensusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))

    def app(self, script=CHAIN, plist=None):
        contents = self.tmp / 'Claude.app' / 'Contents'
        (contents / 'Resources').mkdir(parents=True)
        info = {'CFBundleIdentifier': 'test.claude', 'CFBundleShortVersionString': '9.9.9',
                'CFBundleURLTypes': [{'CFBundleURLName': 'Claude', 'CFBundleURLSchemes': ['claude']}],
                'NSServices': [{'NSMenuItem': {'default': 'Ask Claude'}, 'NSMessage': 'askClaude',
                                'NSSendTypes': ['NSStringPboardType']}]}
        info.update(plist or {})
        (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
        write_asar(contents / 'Resources' / 'app.asar',
                   {'.vite/build/index.js': (script + '/*' + 'x' * 25000 + '*/').encode(),
                    'node_modules/dep/index.js': b'x' * 30000})
        return self.tmp / 'Claude.app'

    def test_official_archive_is_read_with_exact_contents(self):
        path = self.tmp / 'official.asar'
        path.write_bytes(OFFICIAL_ASAR)
        files, base = census.read_asar(path)
        self.assertEqual(sorted(files), ['.vite/build/index.js', 'node_modules/x/i.js', 'package.json'])
        self.assertIn(b'dispatchOnQuickEntrySubmit', census.read_entry(path, base, files['.vite/build/index.js']))
        self.assertEqual(census.read_entry(path, base, files['node_modules/x/i.js']).decode().strip(), '\u00f8 \u00e9')

    def test_report_names_native_declarations_and_the_chain_to_the_external_event(self):
        text = census.report(self.app())
        for expected in ('9.9.9', "['claude']", 'Ask Claude', '`G4`', '`K4`', '`V4`', '`z4`', 'open-url'):
            self.assertIn(expected, text)
        self.assertIn('appelée dans `z4`', text)
        self.assertIn('appelée dans `V4`', text)
        self.assertNotIn('node_modules/dep', text)

    def test_long_tokens_are_redacted_in_snippets(self):
        text = census.report(self.app())
        self.assertNotIn('A' * 40, text)

    def test_bundle_without_the_anchor_says_the_documented_chain_does_not_match(self):
        text = census.report(self.app(script='function a(){return 1}\n'))
        self.assertIn('absent de tous les scripts analys', text)

    def test_missing_or_symlinked_archive_is_refused(self):
        app = self.app()
        archive = app / 'Contents' / 'Resources' / 'app.asar'
        real = archive.with_name('real.asar')
        archive.rename(real)
        archive.symlink_to(real)
        with self.assertRaises(census.CensusError):
            census.report(app)
        with self.assertRaises(census.CensusError):
            census.read_asar(self.tmp / 'absent.asar')

    def test_cli_never_overwrites_the_report_and_reports_errors_without_traceback(self):
        app, out = self.app(), self.tmp / 'report.md'
        self.assertEqual(census.main([str(app), '--out', str(out)]), 0)
        self.assertIn('Recensement statique', out.read_text())
        with self.assertRaises(FileExistsError):
            census.main([str(app), '--out', str(out)])
        errors = io.StringIO()
        with redirect_stderr(errors), redirect_stdout(io.StringIO()):
            self.assertEqual(census.main([str(self.tmp / 'absent.app')]), 1)
        self.assertIn('Recensement impossible', errors.getvalue())

    def test_short_minified_names_do_not_become_chain_nodes(self):
        script = ('function G4(e,t){H.getDispatcher(e)?.dispatchOnQuickEntrySubmit(t)}\n'
                  'function r(e){return G4(e)}\nfunction K4(e){return G4(e)}\n')
        text = census.report(self.app(script=script))
        self.assertIn('appelée dans `K4`', text)
        self.assertNotIn('appelée dans `r`', text)

    def test_global_counts_cover_every_script_not_only_the_anchor_one(self):
        app = self.app()
        write_asar(app / 'Contents' / 'Resources' / 'app.asar',
                   {'.vite/build/index.js': (CHAIN + '/*' + 'x' * 25000 + '*/').encode(),
                    '.vite/build/other.js': ('requestQuickWindowDismissWithPayload;' + 'y' * 25000).encode()})
        text = census.report(app)
        self.assertIn('requestQuickWindowDismissWithPayload` : 1 occurrence(s)', text)
        self.assertIn('other.js', text)

    def test_show_prints_the_named_function_and_flags_unknown_ones(self):
        text = census.show_functions(self.app(), ['z4', 'absent'], 300)
        self.assertIn('function z4(e)', text)
        self.assertIn('`absent` : déclaration introuvable', text)
        self.assertNotIn('A' * 40, text)

    def test_grep_lists_contexts_across_scripts_with_a_limit(self):
        app = self.app()
        write_asar(app / 'Contents' / 'Resources' / 'app.asar',
                   {'.vite/build/a.js': ('function f(){return "scheduled-task"}' * 3 + 'x' * 25000).encode(),
                    '.vite/build/b.js': ('scheduled-task' + 'y' * 25000).encode()})
        text = census.grep_scripts(app, 'scheduled-task', 2)
        self.assertIn('a.js` : 3 occurrence(s)', text)
        self.assertIn('b.js` : 1 occurrence(s)', text)
        self.assertEqual(text.count('- offset'), 2)
        self.assertIn('Aucune occurrence', census.grep_scripts(app, 'nothing-here', 5))

    def test_tool_is_read_only_by_construction(self):
        source = Path(census.__file__).read_text()
        for forbidden in ('subprocess', 'socket', 'urllib', 'requests', 'os.system', "open(args.app", "'w'"):
            self.assertNotIn(forbidden, source.split('def main')[0])


if __name__ == '__main__':
    unittest.main()
