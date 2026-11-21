import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.uname().sysname == "Darwin", "requires Swift on macOS")
class EcosystemStatusTests(unittest.TestCase):
    def test_presence_freshness_and_evidence_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            main = root / "main.swift"
            main.write_text(r'''import Foundation
let now = Date(timeIntervalSince1970: 1000)
precondition(!EcosystemStatus.present(installed: false, detected: false))
precondition(EcosystemStatus.present(installed: true, detected: false))
precondition(EcosystemStatus.present(installed: false, detected: true))
precondition(EcosystemStatus.probe(connected: true, checkedAt: nil, now: now).level == .attention)
precondition(EcosystemStatus.probe(connected: true, checkedAt: Date(timeIntervalSince1970: 800), now: now).level == .attention)
precondition(EcosystemStatus.probe(connected: true, checkedAt: now, now: now).level == .ready)
precondition(EcosystemStatus.probe(connected: false, checkedAt: now, now: now).level == .problem)
precondition(EcosystemStatus.scheduler(["auto_enabled": true], now: now).level == .attention)
precondition(EcosystemStatus.scheduler(["auto_enabled": true, "last_tick": 999.0], now: now).level == .ready)
precondition(EcosystemStatus.scheduler(["auto_enabled": false, "last_tick": 999.0], now: now).level == .attention)
precondition(EcosystemStatus.cdp(["enabled": true, "state": "prête"], checkedAt: nil, now: now).level == .attention)
precondition(EcosystemStatus.cdp(["enabled": true, "state": "prête"], checkedAt: now, now: now).level == .ready)
precondition(EcosystemStatus.cdp(["enabled": false, "state": "indisponible"], checkedAt: now, now: now).level == .problem)
''')
            binary = root / "probe"
            subprocess.run(["swiftc", "-module-cache-path", str(root / "cache"), str(ROOT / "swift" / "EcosystemStatus.swift"), str(main), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
