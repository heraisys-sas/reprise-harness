# Portage Desktop Apply vers `dev`

## Lot 85 — suffixe `Z` pour `resume --at`

- **Baseline** : `dev` `d162a2c401ba306815dc4a268457252de7f8fe57`.
- **Sources fournies** : PR84 `8b0b761c92eb43a2549d07828c6980b060016da8`, PR85 `28c00d43a635698101411e0fff3a017a412af9fd`, PR86 `4bd6b9e74d572468cded63b309a03d8011b648c7`.
- **Portage** : `resume` normalise le suffixe UTC `Z`/`z` en `+00:00` avant `datetime.fromisoformat`; les instants à décalage explicite restent acceptés. Ajout d'un test CLI pour `Z`, `z`, `+02:00` et le rejet d'un instant sans fuseau.
- **Limites** : lot 85 uniquement. Aucun portage des PR84/86, raccord au daemon, appel de service ni reprise d'issue réservée à un autre agent. Aucun `git add`, commit ou push.
- **Validation** : `python3 -m unittest discover -s agent-resume/tests -p test_resume.py -q` — 28 tests réussis, lancé avec `HOME` temporaire (`python3` enveloppé par `subprocess`); aucun service ni inférence démarré.
