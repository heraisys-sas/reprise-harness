import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class RestartChain(unittest.TestCase):
    def test_server_restart_preserves_codex_and_existing_socket(self):
        script = Path(__file__).parents[1]/'restart-chain'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            commands = root/'commands'
            commands.mkdir()
            log = root/'calls'
            python = commands/'python3'
            python.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_CALLS"\nprintf "{}\\n"\n')
            python.chmod(0o700)
            for name in ('codex', 'pkill', 'rm', 'open'):
                path = commands/name
                path.write_text('#!/bin/sh\nprintf "FORBIDDEN %s\\n" "$0 $*" >> "$TEST_CALLS"\nexit 91\n')
                path.chmod(0o700)
            endpoint = root/'.claude-desktop/quick-entry.sock'
            endpoint.parent.mkdir()
            endpoint.write_text('existing endpoint must stay')
            env = dict(os.environ, HOME=str(root), PATH=str(commands)+':/usr/bin:/bin', TEST_CALLS=str(log))
            result = subprocess.run(['/bin/sh',str(script),'--no-build','--server'],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            calls=log.read_text()
            self.assertNotIn('FORBIDDEN',calls)
            self.assertNotIn('daemon stop',calls)
            self.assertIn('quick-apply-daemon-stop',calls)
            self.assertIn('quick-apply-daemon-start',calls)
            self.assertEqual(endpoint.read_text(),'existing endpoint must stay')

    def test_cli_action_passes_explicit_development_mode(self):
        # Revue MAJEUR 9 : le script refuse tout appel sans mode explicite
        # (exit 2). L'action CLI « restart-chain » (bouton GUI « Redémarrage
        # groupé ») doit donc passer --development, jamais appeler le script
        # sans argument.
        import ast
        source = (Path(__file__).parents[1]/'reprise').read_text()
        tree = ast.parse(source)
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test = ast.unparse(node.test)
            if "args.action == 'restart-chain'" not in test:
                continue
            branch = ast.Module(body=node.body, type_ignores=[])
            for sub in ast.walk(branch):
                if isinstance(sub, ast.Call) and getattr(sub.func, 'attr', '') == 'run' and sub.args:
                    command = sub.args[0]
                    if isinstance(command, ast.List):
                        found.append([elt.value for elt in command.elts if isinstance(elt, ast.Constant)])
        self.assertTrue(found, 'appel subprocess.run introuvable dans la branche restart-chain')
        self.assertIn('--development', found[-1])
