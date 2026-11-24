#!/usr/bin/env python3
"""Module Ansible : sonde de vérification des intégrations Reprise.

Appelle uniquement verify() via l'API module du bundle (pas de
déploiement). Échoue si une intégration n'est pas « verified ».
"""
from ansible.module_utils.basic import AnsibleModule


def main():
    module = AnsibleModule(
        argument_spec={
            "helpers": {"type": "str", "required": True},
            "project": {"type": "str", "required": True},
            "harness_project": {"type": "str", "required": True},
            "harness_profile": {"type": "str", "default": "desktop"},
        }
    )
    import pathlib
    import sys

    params = module.params
    sys.path.insert(0, params["helpers"])
    home = pathlib.Path.home()
    out = {}

    try:
        from claude_integration import verify as cv

        out["claude"] = cv(params["helpers"], home, project=params["project"])["status"]
    except Exception as error:
        out["claude"] = "error: %s" % error
    try:
        from chatgpt_integration import verify as gv

        out["chatgpt"] = gv(params["helpers"], home, project=params["project"])["status"]
    except Exception as error:
        out["chatgpt"] = "error: %s" % error
    try:
        from harness_integration import verify as hv

        out["harness"] = hv(
            home / ".dsh", params["harness_profile"],
            root=params["helpers"], project=params["harness_project"],
        )["status"]
    except Exception as error:
        out["harness"] = "error: %s" % error

    bad = [k for k, v in out.items() if v != "verified"]
    module.exit_json(
        changed=False,
        statuses=out,
        failed=bool(bad),
        msg=("Intégrations non vérifiées : %s" % ", ".join(bad)) if bad else "Intégrations vérifiées",
    )


if __name__ == "__main__":
    main()
