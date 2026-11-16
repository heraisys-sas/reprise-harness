#!/usr/bin/env python3
"""Module Ansible : localise le bundle Reprise.app le plus récent.

Retourne le chemin du bundle, le Python du runtime, le répertoire helpers
et le SHA de release dérivé du dossier builds/<sha>/.
"""
import os

from ansible.module_utils.basic import AnsibleModule


def find_newest(builds_dir, pattern):
    matches = []
    matches = []
    for root, dirs, files in os.walk(builds_dir):
        if os.path.basename(root) == pattern and os.path.isdir(
            os.path.join(root, "Contents")
        ):
            matches.append(root)
    if not matches:
        return None
    return max(matches, key=os.path.getmtime)


def main():
    module = AnsibleModule(
        argument_spec={
            "builds_dir": {"type": "str", "required": True},
            "pattern": {"type": "str", "default": "Reprise.app"},
        }
    )
    app = find_newest(module.params["builds_dir"], module.params["pattern"])
    if app is None:
        module.fail_json(msg="Aucun bundle %s sous %s" % (module.params["pattern"], module.params["builds_dir"]))
    helpers = app + "/Contents/Resources/helpers"
    runtime_python = app + "/Contents/Resources/runtime/bin/python3"
    if not os.path.isdir(helpers):
        module.fail_json(msg="Helpers introuvables dans %s" % app)
    # builds/<sha>/Reprise.app -> SHA = nom du dossier parent
    release_sha = os.path.basename(os.path.dirname(app))
    module.exit_json(
        changed=False,
        app_path=app,
        helpers=helpers,
        runtime_python=runtime_python,
        release_sha=release_sha,
    )


if __name__ == "__main__":
    main()
