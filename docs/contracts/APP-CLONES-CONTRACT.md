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
- **Le mode ChatGPT pur est vérifié avant tout dispatch (#248)** : avant
  l'envoi irréversible, le holder relit le sélecteur de mode en haut à
  gauche et, si « Codex » est actif, bascule vers « ChatGPT » puis **relit**
  ; le reçu ne porte `mode: "ChatGPT"` et `mode_verified: true` qu'après
  une relecture positive. Sans preuve positive (sélecteur absent, ambigu,
  état inconnu, clic sans effet, erreur CDP), l'envoi est refusé — jamais
  de `_dispatch` — et le reçu emporte la raison précise et les candidats
  DOM observés (borne 8 Kio). Ne comptent comme candidats que les éléments
  dont le texte **est exactement** « ChatGPT » ou « Codex »
  (insensible à la casse) : une simple mention comme la puce latérale
  « Trouver une skill UX/UI pour Codex » (reçu live) n'est ni un candidat
  ni une cible de clic — sonde, clic déclencheur et détection du mode
  appliquent ce libellé exact, `classify()` le re-filtre en défense
  complémentaire.
- **Bascule de mode : séquence pointeur + poll borné du menu (#248,
  correctif 2)** : le menu Radix du sélecteur ignore un `click` synthétique
  (reçu live `chatgpt-mode-248-live-a2`) — le clic déclencheur et le clic
  sur l'item simulent une séquence pointeur complète
  (`pointerdown`/`mousedown`/`pointerup`/`mouseup`/`click`, bouton 0, centre
  du rect, repli `el.click()` sans `PointerEvent`). Après le clic
  déclencheur, une sonde bornée (0,45 s max, pas de 0,1 s) attend
  l'ouverture du menu (`[role=menuitem]`/`[role=option]`, y compris sous le
  wrapper Radix) ; menu non ouvert = refus dédié `menu_not_opened`
  (distincte de `click_no_change`, réservée au clic sans effet et à la
  relecture inchangée), le reçu emportant un échantillon borné du
  menu/popper.
- **Respawn du holder après bascule de mode** : si le holder tombe entre
  deux dispatchs, le relancer proprement — SIGTERM du processus `serve`,
  fermeture du Chrome dédié associé (profil `dedicated`), puis relance
  `python3 chatgpt_desktop_pipe.py serve --profile dedicated`. Jamais de
  Chrome partagé avec un usage manuel (conflit de verrou singleton).
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
