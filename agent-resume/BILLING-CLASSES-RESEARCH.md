# Classes de facturation — recherche croisée (lot ②)

Source : sous-agent web (DuckDuckGo HTML, web_search KO 401), le 29/10/2026.
Statut : **propositions** — les déclarations dans `route_admissibility` restent à la main de l'opérateur.
Règle : classe non étayée par ≥ 2 sources cohérentes = `unknown` + question opérateur.

## Tableau

| Canal | Classe proposée | Confiance | Sources principales |
|---|---|---|---|
| codex | included | haute | chatgpt.com/codex/pricing (officielle) + aq.dev + usagebox.com |
| claude-code (OAuth Pro/Max) | included | haute | tokensforgood.ai + dev.to + blog.laozhang.ai |
| claude-code (API key) | paid | haute | idem |
| claude-desktop (Pro/Max, Cowork inclus) | included | moyenne | claude.com/pricing (officielle) + sources secondaires |
| asi1-mini (ASI:Cloud) | free | moyenne | asicloud.cudos.org (« Free to use ASI-1 Mini ») + docs.asi1.ai |
| asi1 / asi1-ultra (API per-token) | paid — **faible, 1 source → unknown** | faible | asicloud.cudos.org seul |
| super-decider | unknown | — | aucun document public |
| ironclaw (SSH local) | free par construction | moyenne | aucune doc publique ; défini par le projet seul |
| foundation-models (Apple) | free | haute | developer.apple.com/private-cloud-compute (« at no cost ») + docs |

## Faits utiles au contrat

- **Codex** : inclus dans les plans ChatGPT (fenêtre 5 h + cap hebdo). Au-delà : achat de credits ou branchement API key — **jamais automatique**. Compatible « aucun repli payant implicite ».
- **Claude Code** : deux modes exclusifs — OAuth Pro/Max (included) ou API key (paid). **`ANTHROPIC_API_KEY` a priorité sur le login abonné** (vincentschmalbach.com). `/status` affiche le mode. `subscription_only` n'est pas documenté publiquement — signal local.
- **Claude Desktop** : usage couvert par l'abonnement ; Cowork inclus Pro/Max/Team/Enterprise, absent du Free. Aucun crédit payant séparé prouvé.
- **ASI:Cloud** : per-token pour l'API ; ASI-1 Mini « Free to use » ; quotas exacts non documentés. « Super Decider » : aucune trace publique (interne au projet).

## Questions opérateur (validation requise)

1. **codex** : quel plan ChatGPT est actif, et `codexd` est-il authentifié OAuth (pas API key) ?
2. **claude-desktop** : un abonnement Claude Pro/Max est-il connecté dans l'app Desktop ? Cowork activé ?
3. **asi1 (API)** : une clé ASI:Cloud payante est-elle configurée ? Limite du tier gratuit mini ?
4. **super-decider** : ASI:Cloud est-il appelé avec une clé gratuite (mini) ou une route facturée ?
5. **ironclaw** : confirmer qu'ironclaw n'exécute aucune API distante facturée (SSH/local uniquement) ?

## Signaux machine recommandés (fiabilisation future)

- codex : `~/.codex/auth.json` (tokens OAuth vs `OPENAI_API_KEY` env) + `quota_blocked` existant.
- claude-code : `ANTHROPIC_API_KEY` non vide → paid ; `CLAUDE_CODE_OAUTH_TOKEN` ou `~/.claude/.credentials.json` → included ; `/status` parsé.
- claude-desktop : état d'authentification de l'app Desktop.
- asi1 : `ASI1_API_KEY` + endpoint + modèle demandé (asi1 vs asi1-mini) journalisé.
- foundation-models : `SystemLanguageModel.availability` — free par construction.
- ironclaw : config SSH locale ; absence de clé API distante.

## Limites

- asi1/asi1-ultra : per-token documenté par 1 seule source → reste `unknown` jusqu'à validation.
- super-decider : aucun document public — déclaration opérateur indispensable.
- Limites de quotas ASI:Cloud non mesurées ; ne pas extrapoler.

## Validation opérateur (29/10/2026) — classes DÉFINITIVES

| Canal | billing_class | Décision opérateur |
|---|---|---|
| codex | **included** | OAuth plan confirmé (fenêtre 5h/cap hebdo) |
| claude-desktop | **included** | Pro/Max connecté, pour l'instant — évolution prévue : bascule de l'expert principal codex → claude-code (intégration Work plus simple que Desktop) |
| claude-code (OAuth) | **included** | comme recherché |
| claude-code (API key) | paid | comme recherché — attention : ANTHROPIC_API_KEY prioritaire sur l'abonnement |
| asi1 / asi1-mini | **free** | clé ASI:Cloud payante configurée mais SANS crédit ; les ASI1 n'en consomment pas — la clé est requise, pas le crédit (correction opérateur : asi1 full = FREE) |
| super-decider | **free** | même clé, pas de crédit nécessaire |
| asi1 full API per-token | **free** | décision opérateur explicite |
| ironclaw | **paid** (provisoire) | utilise actuellement Morph ; évolution à prévoir |
| foundation-models | free | on-device, docs Apple |

Déclaration à appliquer dans route_admissibility via `declare()` (lot dédié ou setup).
