#!/usr/bin/env python3
"""Build an isolated, noninstalled App Intents metadata probe."""
import argparse
import json
from pathlib import Path
import platform
import plistlib
import subprocess
import sys

SOURCE = '''import AppIntents
import Foundation
struct RepriseStatusIntent: AppIntent {
    static var title: LocalizedStringResource = "État de Reprise"
    static var description = IntentDescription("Consulter un état de démonstration en lecture seule.")
    func perform() async throws -> some IntentResult & ProvidesDialog {
        return .result(dialog: "État de démonstration indisponible.")
    }
}
struct RepriseShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: RepriseStatusIntent(), phrases: ["Quel est l’état de \\(.applicationName)"], shortTitle: "État", systemImageName: "info.circle")
    }
}
@main struct ProbeMain { static func main() {} }
'''
EXTENSION_SOURCE = SOURCE.replace('import AppIntents\n', 'import AppIntents\nimport ExtensionFoundation\n').replace(
    '    func perform() async throws',
    '    static var allowedExecutionTargets: IntentExecutionTargets { .appIntentsExtension }\n    func perform() async throws'
).replace('@main struct ProbeMain { static func main() {} }',
          '@main struct RepriseIntentsExtension: AppIntentsExtension {}')
MODULE = 'RepriseIntentProbe'
BUNDLE_ID = 'local.reprise.intent-probe'


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode:
        raise RuntimeError(f"Échec de {args[0]} (code {result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def verify_metadata(path, *, extension=False):
    data = json.loads(path.read_text(encoding='utf-8'))
    action = data.get('actions', {}).get('RepriseStatusIntent', {})
    shortcuts = data.get('autoShortcuts', [])
    if not action.get('isDiscoverable') or action.get('openAppWhenRun') is not False:
        raise ValueError('Métadonnées de l’action absentes ou inattendues')
    if not any(x.get('actionIdentifier') == 'RepriseStatusIntent' and any(
        '${applicationName}' in p.get('key', '') for p in x.get('phraseTemplates', [])
    ) for x in shortcuts):
        raise ValueError('Raccourci ou phrase absente des métadonnées')
    if extension and action.get('allowedTargets') != [{'extensionPointIdentifier': 'com.apple.appintents-extension', 'type': 3}]:
        raise ValueError('Cible d’exécution extension absente des métadonnées')
    return data.get('generator', {})


def build(output, *, command=run, machine=None, extension=False):
    output = Path(output)
    if not output.is_absolute() or output.name in ('', '.', '..') or any(c in str(output) for c in ('\n', '\r', '\x00')):
        raise ValueError('Le répertoire de sortie doit être un chemin absolu neuf')
    if not output.parent.is_dir():
        raise ValueError('Le répertoire parent doit déjà exister')
    machine = machine or platform.machine()
    if machine not in ('arm64', 'x86_64'):
        raise ValueError(f'Architecture non prise en charge : {machine}')
    output.mkdir(mode=0o700)  # Fail if anything already exists, including symlinks.
    source = output / 'Intent.swift'
    source.write_text(EXTENSION_SOURCE if extension else SOURCE, encoding='utf-8')
    bundle = output / (MODULE + ('.appex' if extension else '.app'))
    binary_dir = bundle / 'Contents' / 'MacOS'
    resources = bundle / 'Contents' / 'Resources'
    binary_dir.mkdir(parents=True)
    resources.mkdir()
    plist = {'CFBundleIdentifier': BUNDLE_ID, 'CFBundleName': 'Reprise Intent Probe',
             'CFBundleExecutable': MODULE, 'CFBundlePackageType': 'APPL',
             'CFBundleVersion': '1', 'CFBundleShortVersionString': '1.0',
             'LSMinimumSystemVersion': '26.0'}
    if extension:
        plist.update(CFBundleIdentifier=BUNDLE_ID + '.extension', CFBundlePackageType='XPC!',
                     LSMinimumSystemVersion='27.0', EXAppExtensionAttributes={
                         'EXExtensionPointIdentifier': 'com.apple.appintents-extension'})
    with (bundle / 'Contents' / 'Info.plist').open('wb') as f:
        plistlib.dump(plist, f)
    swiftc = Path(command(['xcrun', '--find', 'swiftc']))
    processor = command(['xcrun', '--find', 'appintentsmetadataprocessor'])
    sdk = command(['xcrun', '--sdk', 'macosx', '--show-sdk-path'])
    xcode = command(['xcodebuild', '-version']).splitlines()
    version = next((s.removeprefix('Build version ').strip() for s in xcode if s.startswith('Build version ')), '')
    if not version or not swiftc.is_absolute() or not Path(processor).is_absolute() or not Path(sdk).is_absolute():
        raise ValueError('Chemins des outils ou version Xcode invalides')
    toolchain = swiftc.parent.parent.parent
    protocol_file = toolchain / 'usr/share/swift/SwiftConstantValues/AppIntents.json'
    protocols = json.loads(protocol_file.read_text(encoding='utf-8')).get('constValueProtocols')
    if not isinstance(protocols, list) or 'AppIntent' not in protocols or 'AppShortcutsProvider' not in protocols:
        raise ValueError('Liste des protocoles App Intents absente ou invalide')
    protocol_list = output / 'protocols.json'
    protocol_list.write_text(json.dumps(protocols), encoding='utf-8')
    constvalues = output / 'Intent.swiftconstvalues'
    obj = output / 'Intent.o'
    deployment_target = '27.0' if extension else '26.0'
    target = f'{machine}-apple-macosx{deployment_target}'
    common = [str(swiftc), '-parse-as-library', '-sdk', sdk, '-target', target, '-module-name', MODULE,
              '-module-cache-path', str(output / 'module-cache')]
    command(common + ['-c', '-const-gather-protocols-list', str(protocol_list),
                      '-emit-const-values-path', str(constvalues), str(source), '-o', str(obj)])
    command(common + [str(source), '-o', str(binary_dir / MODULE)])
    sources = output / 'sources.list'
    constants = output / 'constvalues.list'
    sources.write_text(str(source) + '\n', encoding='utf-8')
    constants.write_text(str(constvalues) + '\n', encoding='utf-8')
    command([processor, '--output', str(resources), '--toolchain-dir', str(toolchain),
             '--module-name', MODULE, '--sdk-root', sdk, '--xcode-version', version,
             '--platform-family', 'macOS', '--deployment-target', deployment_target, '--target-triple', target,
             '--source-file-list', str(sources), '--swift-const-vals-list', str(constants), '--force'])
    metadata = resources / 'Metadata.appintents' / 'extract.actionsdata'
    generator = verify_metadata(metadata, extension=extension)
    return {'bundle': str(bundle), 'metadata': str(metadata), 'generator': generator,
            'extension': extension, 'installed': False, 'launched': False, 'siri_verified': False}


def main():
    parser = argparse.ArgumentParser(description='Construit sans installer ni lancer un prototype App Intents.')
    parser.add_argument('--output', required=True, help='Chemin absolu vers un nouveau répertoire')
    parser.add_argument('--extension', action='store_true', help='Construit une extension App Intents isolée pour macOS 27')
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.output, extension=args.extension), ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f'Erreur : {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
