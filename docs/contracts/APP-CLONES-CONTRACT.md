# APP-CLONES — clones d'app à identité propre et version gelée

Contrat pour les copies gérées des apps de chat sous nouvelle identité :
`ReChatGPT.app` (ChatGPT Desktop) et `ReClaude.app` (Claude Desktop patché,
pipeline #124). Ces clones donnent à l'opérateur une app distincte dans le
Dock tout en laissant **l'originale `/Applications` pristine**.

## Définition

Un clone est une copie APFS (`cp -c -R`, clonefile — coût disque ~nul) de
l'originale, placée dans `~/Applications`, avec :

1. **Identité propre** : `CFBundleIdentifier` et `CFBundleName`/
   `CFBundleDisplayName` modifiés dans `Contents/Info.plist`
   — ReChatGPT : `com.reprise.rechatgpt` ; ReClaude :
   `com.anthropic.claudefordesktop.reprise` (posé par le pipeline #124).
2. **Re-signature ad-hoc profonde** (`codesign --force --deep --sign -`) :
   la modification du plist invalide la signature Developer ID d'origine ;
   l'ad-hoc local est valide et suffisant (aucun lancement hors machine).
3. **Version gelée** : auto-update du clone désactivé via les réglages de
   SON identité (séparée de l'originale) — `SUEnableAutomaticChecks` et
   `SUAutomaticallyUpdate` à `false`. Sans cela, Sparkle/Squirrel
   remplacerait le clone par le bundle officiel et écraserait l'identité
   et (pour ReClaude) le patch Reprise.
4. **Gel enregistré** : le manifeste `~/Library/Application
   Support/Reprise/app-clones.json` porte la version upstream au moment du
   gel, la version du clone, la date et le bundle id.

## Invariants

- **L'originale `/Applications` n'est jamais mutée** : tout écrit cible
  `~/Applications/<Clone>.app`. Aucune exception.
- **L'app cible ne doit pas tourner pendant une mutation** : le PID est
  authentifié par chemin d'exécutable (jamais un nom de processus seul,
  leçon #198/#94) ; refus sinon.
- **Le patch Claude reste le pipeline #124** : `app_clones` gère identité,
  gel et signature ; le patch asar/slot d'un nouveau clone ReClaude passe
  par `desktop_patch.py`, puis `app_clones apply` finalise identité + gel.
- **Le holder dédie sa binaire au clone** : `chatgpt_desktop_pipe.py`
  résout `~/Applications/ReChatGPT.app/.../ChatGPT` pour le profil
  `dedicated` quand la copie existe (sinon `/Applications/ChatGPT.app`).
- Divergence de versions admise : si l'originale avance, le clone reste
  gelé ; une refonte DOM peut nécessiter un recalage sélecteurs (leçon
  #226) visible dans les reçus (`selectors_stale`).

## Opérations gérées (inspect / plan / apply / verify)

Même logique que les composants gérés :

- **inspect** : présence, version clone vs upstream, identité, signature,
  auto-update, PID en cours.
- **plan** : actions à exécuter (`clone`, `identity`, `sign`,
  `update-off`, `refresh`, `freeze-record`) par clone.
- **apply** : exécution bornée avec gardes (disque, PID, cible) ; écrit le
  manifeste après chaque clone.
- **verify** : post-conditions (identité, signature valide, SU off,
  versions cohérentes avec le manifeste).

Le branchement comme composant natif de `managed_update.py`
(`COMPONENTS`) est prévu avec la release v0.1.20 ; en attendant,
`reprise/app_clones.py` porte les quatre opérations avec son manifeste
local et la même sémantique.

## Refresh après update upstream

Quand l'originale a été mise à jour et que le clone doit suivre :

1. quit + suppression du clone courant (c'est une sauvegarde possédée par
   l'opération, jamais une release référencée) ;
2. re-clone + identité + re-sign + SU off (`app_clones apply`) ;
3. ReClaude uniquement : re-run du pipeline #124 avant la finalisation ;
4. recalage éventuel des sélecteurs de capture (leçon #226 : le DOM peut
   avoir changé — vérifier sur une delegation t1 avant toute délégation
   réelle).

## Limites

- Le clone n'est **pas distribué** : il vit sur le Mac qui l'a créé (D3/D4
  hors périmètre).
- L'app opérateur peut tourner en parallèle ; le nom dans la barre de
  menus du clone est le sien (`ReChatGPT`/`ReClaude`), l'icône est la même
  (icns partagé).
- `operator_isolation_verified` reste false tant que la coexistence
  n'est pas prouvée formellement (#161).
