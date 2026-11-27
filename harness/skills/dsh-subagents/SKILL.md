---
name: dsh-subagents
description: Choisir et lancer un sous-agent DSH (Harness) sur la route Morph pour une synthèse d'état ou un lot court, avec les paramètres que la route accepte.
---

# Sous-agents DSH sur la route Morph

Règles générales (quand déléguer, prompt en lecture seule, escalade) : `docs/runbooks/AGENT-WORKFLOW.md`. Ce skill ne garde que les particularités de la route ; revérifier le catalogue du profil au moment du choix.

## Modèles

- Provider `morph` (api.morphllm.com), renommé depuis `deepseek` le 27/10/2026 pour lever l'ambiguïté avec le DeepSeek officiel ; les projections de sessions antérieures peuvent encore porter l'ancien identifiant.
- `morph-dsv41flash-1017` (DeepSeek V4.1 Flash) : rapide et fiable pour les synthèses d'état et les lots cadrés.
- `morph-glm53-744b` : second modèle disponible pour les sous-agents.

## Paramètres

- **Ne jamais passer `reasoning_effort`** : la route le refuse (refus observé le 26/10/2026, issue #81). Ne pas non plus l'évoquer comme consigne dans le prompt du sous-agent.
- Si le sous-agent ne peut pas utiliser la route, ouvrir un nouveau chat qui l'utilise.

## Limites

- Limite exacte de la route gratuite non mesurée. L'opérateur observe une limite par session sur Morph Flash 1017 : une session de remplacement avec checkpoint est permise, sans extrapoler cette propriété aux autres quotas.
