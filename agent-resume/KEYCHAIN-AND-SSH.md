# Trousseau macOS, cache privé et clés SSH

Diagnostic du 22/10/2026. Aucun changement de clé, passphrase, ACL ou élément du Trousseau effectué par ce diagnostic.

## Ce que fait Vault dans Reprise

`Vault.swift` est un helper de chiffrement, pas un gestionnaire de mots de passe tiers. Il stocke une clé symétrique AES-256 dans le Trousseau macOS, service `local.agent-resume.vault.v1`, puis protège l’index de recherche et le cache d’analyse. La clé n’est pas synchronisée et est disponible quand le Trousseau est déverrouillé (`WhenUnlockedThisDeviceOnly`). Les registres de reprise et les journaux sources ne sont pas chiffrés par ce helper.

Un accès autorisé une seule fois peut redemander une validation lors de l’appel suivant ; « Toujours autoriser » concerne l’application qui demande cet élément, pas toutes les applications. [Apple — accès des applications au Trousseau](https://support.apple.com/en-gb/guide/mac-help/kychn002/26/mac/26).

Le helper de développement inspecté possède une signature ad hoc, sans TeamIdentifier. Sa reconstruction et sa copie dans des releases peuvent faire varier l’identité de code reconnue ; c’est une explication plausible, pas une cause reproduite de chaque demande. Une distribution doit stabiliser identité de signature et installation et tester les accès après mise à jour. Ne pas élargir l’accès à toutes les applications, exporter la clé en clair ou remplacer automatiquement les données chiffrées lorsque l’accès est refusé. [Apple — exigences d’identité de code](https://developer.apple.com/library/archive/documentation/Security/Conceptual/CodeSigningGuide/RequirementLang/RequirementLang.html).

Pistes produit à valider : maintenance et services de fond sans dialogue d’authentification ; déverrouillage explicite seulement dans un parcours utilisateur ; message actionnable si l’index/cache est indisponible ; possibilité de ne pas utiliser les fonctions privées facultatives. Ne pas stocker silencieusement l’index/cache en clair comme repli. La reprise déterministe après quota ne doit pas dépendre de l’accès à ce cache. Ces pistes ne sont pas annoncées implémentées par cette note.

## SSH : mécanisme distinct

IronClaw tourne dans un conteneur distant : le sens demandé est **Reprise (client local) → conteneur IronClaw (serveur SSH)**. La clé privée reste côté client ; seule la clé publique est autorisée côté distant. Ne pas installer une clé privée dans le conteneur pour ce besoin et ne pas activer l’agent forwarding. Le script fourni utilise OpenSSH avec une clé dédiée déjà lisible sans passphrase et des permissions `0600`. Le dépôt ne contient pas encore d’adaptateur IronClaw ; le tunnel WebUI externe ne constitue pas une intégration de tâches. Cette identité SSH est indépendante de la clé symétrique de Reprise.

Option sans coffre tiers : conserver la clé SSH chiffrée sur disque et la déverrouiller dans `ssh-agent` pour la session, éventuellement avec durée limitée. Les connexions ultérieures peuvent utiliser l’agent sans redemander la passphrase ; un redémarrage ou expiration nécessite un nouveau déverrouillage si aucun stockage persistant du secret n’est employé. Les contraintes de destination dépendent des versions et doivent être vérifiées. [OpenSSH — ssh-add](https://man.openbsd.org/ssh-add).

L’autonomie complète après redémarrage demande un mécanisme d’accès non interactif : secret disponible via stockage système ou service de signature adapté, ou clé dédiée sans passphrase avec privilèges et périmètre strictement réduits. Ce dernier choix augmente l’exposition en cas de lecture du fichier ; ne jamais retirer la passphrase d’une clé générale existante par commodité. Évaluer une clé dédiée, compte distant restreint, absence d’agent forwarding et restrictions côté serveur selon le besoin réel. Le choix et la configuration demandent une validation explicite ; aucune clé n’est créée ou modifiée par Reprise pour ce chantier.

## Diagnostic IronClaw et solution retenue (22/10/2026)

Contrôles réels sans inférence ni modification distante : authentification SSH non interactive réussie avec identité explicite et sans agent ; binaire distant IronClaw 1.4.1 ; GET HTTP distant sur `127.0.0.1:3000/` réussi (200), HTTPS échoue à la négociation TLS. L’URL locale HTTPS fournie n’est donc pas adaptée au service HTTP constaté. Aucun jeton de l’URL n’a été utilisé ou affiché.

Un transfert local temporaire a bien ouvert son port local, mais le canal SSH a été refusé : `administratively prohibited`. Le tunnel a été arrêté à la fin du test. L’existence du listener local et `ExitOnForwardFailure` ne prouvent pas que la destination distante soit accessible. La lecture des seules directives disponibles ne permet pas d’attribuer le refus : aucune directive explicite de forwarding observée ; restrictions de la clé non accessibles ; politique de passerelle fournisseur encore inconnue.

Avis indépendant convergent : ne pas installer un service relançant ce tunnel avant de résoudre ce refus. Pour la WebUI, privilégier un transfert local autorisé uniquement vers `127.0.0.1:3000` pour l’identité dédiée, sans agent forwarding ni exposition publique ; la modification de politique distante doit être préparée puis explicitement autorisée. Si la passerelle fournisseur interdit les transferts, demander sa méthode supportée plutôt que contourner sa politique.

Après validation du transfert : LaunchAgent utilisateur installable/réparable depuis Reprise, chemins absolus, clé référencée sans copie, hôte déjà approuvé, `StrictHostKeyChecking=yes`, `IdentitiesOnly=yes`, `IdentityAgent=none`, `ForwardAgent=no`, délais de connexion et keepalives, relance temporisée, journaux privés sans jeton, diagnostic HTTP distinct de l’état SSH, retrait sélectif. Démarrage après ouverture de session macOS ; exécution avant login non revendiquée. Aucun LaunchAgent IronClaw n’a été trouvé dans les emplacements standard inspectés et aucun n’a été installé par ce diagnostic.

Pour l’intégration harness, SSH exec est déjà démontré et peut servir aux diagnostics déterministes. Soumission, clarifications, résultats durables et reprise nécessitent un adaptateur et un protocole distant vérifiés ; ne pas assimiler un GET ou une WebUI à une reprise fonctionnelle. Ne pas improviser un proxy shell ni transmettre un jeton dans les arguments des processus.

Références : [OpenSSH — configuration client](https://man.openbsd.org/ssh_config), [Apple — agents launchd](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html).

## Correctif des demandes répétées

Les opérations de chiffrement, déchiffrement et vérification désactivent désormais l’interface du Trousseau dans le processus helper. Un refus échoue sans dialogue et sans copie en clair ; le cache existant est préservé. L’analyse vérifie l’accès à la clé avant d’appeler le modèle local, pour ne pas calculer un résultat qu’elle ne pourrait pas conserver. La reprise après quota reste indépendante du cache.

L’action **Confidentialité → Autoriser l’accès au cache** est la seule voie interactive ; elle appelle `resume vault-authorize`, sans initialiser le registre ni lancer d’inférence. Elle peut créer la clé lors d’une absence authentique, jamais remplacer une clé inaccessible ou invalide. L’autorisation ne modifie pas les ACL automatiquement ; l’utilisateur garde les choix du dialogue macOS. Un accès ponctuellement autorisé peut ne pas suffire aux appels ultérieurs non interactifs.

`native-install` accepte `REPRISE_SIGNING_IDENTITY` pour signer Vault avec un identifiant constant `local.agent-resume.vault`, puis vérifier sa signature. Réutiliser le même certificat et identifiant entre releases ; ne pas annoncer une identité durable pour une compilation ad hoc. Aucun certificat de signature valide n’a été trouvé sur le poste lors du diagnostic : cette voie de distribution reste à tester avec un certificat, sans création de certificat ni modification du Trousseau par ce chantier.

Validation du correctif : 8 tests dédiés Vault/analyse, 5 recherche ; suite complète **412 tests réussie, dont 4 ignorés** ; compilation sans lancement réussie. Vérification réelle `check` sur le helper compilé : refus immédiat `keychain_unavailable`, aucune autorisation interactive appelée, aucune clé créée. Les API historiques Security utilisées pour le Trousseau classique produisent des avertissements de dépréciation ; aucun accès illimité n’a été accordé. Les fixtures du scheduler échouent sous les restrictions d’exécution temporaires puis passent avec les permissions prévues (17 tests).
