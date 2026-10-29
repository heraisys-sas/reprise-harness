# Optimisation du prefix cache — Règle permanente

**Date :** 2026-10-28  
**Objectif :** Maximiser les hits de cache de préfixe pour réduire coût et latence sur tous les appels API (ASI:Cloud, Codex, DeepSeek).

---

## Principe

Le cache de préfixe économise ~90% des tokens traités quand le début du prompt est identique à une demande précédente. **Les instructions stables doivent TOUJOURS précéder les données variables.**

---

## Pattern obligatoire pour TOUTE délégation

```
┌─────────────────────────────────────┐
│  INSTRUCTIONS STABLES               │  ← Toujours identique → HIT cache
│  - Rôle du modèle                   │
│  - Contexte projet                  │
│  - Format de sortie attendu         │
│  - Contraintes permanentes          │
├─────────────────────────────────────┤
│  DONNÉES VARIABLES                  │  ← Change à chaque appel → calculé
│  - Prompt utilisateur               │
│  - État du système                  │
│  - Questions spécifiques            │
│  - Fichiers référencés              │
└─────────────────────────────────────┘
```

---

## Exemples concrets

### ❌ Mauvais (cache inefficace)

```python
subagent(
    provider="asi-cloud",
    model="asi1-mini",
    prompt=f"""
Traduis en anglais : {prompt_utilisateur}

Tu es un traducteur technique. Préserve tous les identifiants.
Coût : ~$0,00036 par prompt.
""")
```

→ Le texte variable arrive EN PREMIER → Zéro hit de cache.

### ✅ Bon (cache optimal)

```python
subagent(
    provider="asi-cloud",
    model="asi1-mini",
    prompt="""
Tu es un traducteur technique spécialisé en systèmes distribués.
Préserve TOUS les identifiants techniques sans modification :
fonctions, chemins, URLs, signaux Unix, noms de fichiers, permissions octales.
Ne traduisez JAMAIS : socket_path(), killpg, NLEmbedding, work_id, turn_id, session_key.
Output format : numbered translations only, no preamble.

Traduis en anglais :
{prompt_utilisateur}
""")
```

→ Les instructions stables sont en premier → HIT cache après 1ère utilisation.

---

## Templates de prompts optimisés pour le cache

### Template A : Traduction FR→EN (asi1-mini)

```text
[STABLE]
Tu es un traducteur technique FR→EN pour reprise-harness.
Règles absolues :
1. Préserve 100% des identifiants : fonctions, chemins, URLs, signaux, noms de fichiers, permissions octales
2. Ne modifie JAMAIS : socket_path(), killpg, NLEmbedding.sentenceEmbedding, work_id, turn_id, session_key, event_id, tool_call_id
3. Output = numéro + traduction uniquement, aucun préambule
4. Coût cible : ~$0,00036/prompt

[VARIABLE]
Prompt à traduire :
{prompt_fr}
```

### Template B : Routage Super Decider

```text
[STABLE]
Contexte : reprise-harness, supervision multi-harness (Codex, Claude Desktop, DeepSeek Harness, AutoClaw, IronClaw).
Questions typées Super Decider :
- best_model (choice): codex-sol | claude-astra | deepseek-flash | local
- confidence (score): 0.0 → 1.0
- handoff_ready (noul): doit-on passer la main à un autre harness ?
Fichier de référence : agent-resume/SUPER-DECIDER-INTEGRATION.md

[VARIABLE]
État à évaluer :
{state_json}   ← contient work_id, session_key, engine_history
```

**Note :** toute donnée par instance (work_id, session_key, timestamps) reste dans la
partie VARIABLE. Un identifiant laissé dans la partie stable casse le cache à chaque appel.

### Template C : Revue d'architecture

```text
[STABLE]
Vous êtes un architecte logiciel senior spécialisé en systèmes distribués et orchestration d'agents IA.
Répondez en français, soyez direct et technique.
Structurez votre réponse : forces, faiblesses, risques, recommandations prioritaires.

[VARIABLE]
Architecture à évaluer :
{description_architecture}
```

---

## Vérification du cache

### Sur ASI:Cloud / OpenAI compatible

Après chaque appel API, vérifier les headers de réponse :

```bash
curl -s https://llm.a.singularitycompute.com/v1/chat/completions \
  -H "Authorization: Bearer $ASI_CLOUD_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "asi1-mini",
    "messages": [{"role": "user", "content": "..."}],
    "max_tokens": 100
  }' | python3 -m json.tool
```

Les headers `x-amzn-request-priority` ou `x-request-id` peuvent indiquer si le cache a été utilisé. Sur OpenAI standard, le champ `prompt_tokens_details.cached_tokens` indique le nombre de tokens servis depuis le cache.

### Sur DeepSeek / Morph

DeepSeek expose `cached_tokens` dans la réponse JSON :

```json
{
  "usage": {
    "prompt_tokens": 500,
    "cached_tokens": 450,
    "completion_tokens": 100
  }
}
```

Si `cached_tokens > 0`, le cache a été utilisé → économie confirmée.

---

## Règles de conformité

1. **Toujours** placer les instructions stables AVANT les données variables
2. **Toujours** utiliser des templates plutôt que des prompts construits dynamiquement
3. **Toujours** vérifier `cached_tokens` dans les réponses API au moins une fois par jour
4. **Ne jamais** insérer de timestamp, UUID ou valeur aléatoire dans la partie stable du prompt
5. **Documenter** chaque template dans ce fichier avec son SHA de hash pour détecter les dérives

---

## Hash des templates (pour détection de dérive)

| Template | SHA-256 | Dernière vérification |
|---|---|---|
| Template A (Traduction) | À calculer après validation | 2026-10-28 |
| Template B (Routage) | À calculer après validation | 2026-10-28 |
| Template C (Revue) | À calculer après validation | 2026-10-28 |

Pour calculer : `echo -n "<contenu exact du template>" | sha256sum`

---

## Impact — à mesurer, pas à estimer

**Aucun chiffre d'économie n'est avancé ici tant qu'il n'est pas mesuré.** Le cache de
préfixe est un comportement serveur : rien ne garantit qu'ASI:Cloud l'implémente.

**Première action requise :** appel de test à ASI:Cloud avec le même préfixe deux fois,
puis lecture du champ usage (`cached_tokens` / `prompt_tokens_details`). Si le champ reste
absent ou à 0, l'optimisation est sans effet sur cette route et ce document ne s'applique
qu'aux fournisseurs qui exposent le cache (DeepSeek vérifié, autres à confirmer).

Le gain le plus fiable, mesurable sans appel de test, reste la **continuité de session** :
poursuivre le même sous-agent (`send_message`) plutôt que relancer un agent neuf — le
préfixe de toute la conversation distante est déjà connu du serveur.
