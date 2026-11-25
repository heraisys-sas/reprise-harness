#!/usr/bin/env python3
"""Module Ansible : applique une intégration Reprise managée.

Invoque plan -> apply -> verify via l'API module du bundle (jamais
d'édition directe des fichiers managés). Un module par intégration,
bouclé depuis les tasks. chatgpt retiré : route fermée (#226), le module
côté bundle n'existe plus sur dev.
"""
from ansible.module_utils.basic import AnsibleModule


def run(helpers, integration, project, harness_project, harness_profile):
    import json
    import pathlib
    import sys

    sys.path.insert(0, helpers)
    home = pathlib.Path.home()

    if integration == "claude":
        from claude_integration import plan, apply, verify

        p = plan(helpers, home, project, action="install")
        r = apply(helpers, p)
        v = verify(helpers, home, project=project)
        return {
            "apply": r.get("status"),
            "verify": v["status"],
            "daemon": v.get("daemon", {}),
        }

    if integration == "harness":
        from harness_integration import plan, apply, verify

        dsh = home / ".dsh"
        p = plan(
            helpers, dsh, harness_profile, harness_project, "install",
            True, "standard", False, None, False, False, True, None, None, None,
        )
        r = apply(helpers, p)
        v = verify(dsh, harness_profile, root=helpers, project=harness_project)
        return {"apply": r.get("status"), "verify": v["status"]}

    raise ValueError("Intégration inconnue : %s" % integration)


def main():
    module = AnsibleModule(
        argument_spec={
            "integration": {
                "type": "str", "required": True,
                "choices": ["claude", "harness"],
            },
            "helpers": {"type": "str", "required": True},
            "project": {"type": "str", "required": True},
            "harness_project": {"type": "str", "default": ""},
            "harness_profile": {"type": "str", "default": "desktop"},
        }
    )
    params = module.params
    try:
        result = run(
            params["helpers"], params["integration"], params["project"],
            params["harness_project"], params["harness_profile"],
        )
    except Exception as error:
        module.fail_json(msg="Échec %s : %s" % (params["integration"], error))
    verified = result.get("verify") in ("verified", "ok")
    module.exit_json(
        changed=result.get("apply") == "completed",
        result=result,
        verified=verified,
    )


if __name__ == "__main__":
    main()
