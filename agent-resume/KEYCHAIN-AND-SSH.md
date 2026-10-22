# Trousseau macOS, cache privé et clés SSH

Diagnostic du 22/10/2026. Aucun changement de clé, passphrase, ACL ou élément du Trousseau effectué par ce diagnostic.

## Ce que fait Vault dans Reprise

`Vault.swift` est un helper de chiffrement, pas un gestionnaire de mots de passe tiers. Il stocke une clé symétrique AES-256 dans le Trousseau macOS, service `local.agent-resume.vault.v1`, puis protège l’index de recherche et le cache d’analyse. La clé n’est pas synchronisée et est disponible quand le Trousseau est déverrouillé (`WhenUnlockedThisDeviceOnly`). Les registres de reprise et les journaux sources ne sont pas chiffrés par ce helper.

Un accès autorisé une seule fois peut redemander une validation lors de l’appel suivant ; « Toujours autoriser » concerne l’application qui demande cet élément, pas toutes les applications. [Apple — accès des applications au Trousseau](https://support.apple.com/en-gb/guide/mac-help/kychn002/26/mac/26).

Le helper de développement inspecté possède une signature ad hoc, sans TeamIdentifier. Sa reconstruction et sa copie dans des releases peuvent faire varier l’identité de code reconnue ; c’est une explication plausible, pas une cause reproduite de chaque demande. Une distribution doit stabiliser identité de signature et installation et tester les accès après mise à jour. Ne pas élargir l’accès à toutes les applications, exporter la clé en clair ou remplacer automatiquement les données chiffrées lorsque l’accès est refusé. [Apple — exigences d’identité de code](https://developer.apple.com/library/archive/documentation/Security/Conceptual/CodeSigningGuide/RequirementLang/RequirementLang.html).

Pistes produit à valider : maintenance et services de fond sans dialogue d’authentification ; déverrouillage explicite seulement dans un parcours utilisateur ; message actionnable si l’index/cache est indisponible ; possibilité de ne pas utiliser les fonctions privées facultatives. Ne pas stocker silencieusement l’index/cache en clair comme repli. La reprise déterministe après quota ne doit pas dépendre de l’accès à ce cache. Ces pistes ne sont pas annoncées implémentées par cette note.

## SSH : mécanisme distinct

IronClaw tourne dans un conteneur distant : le sens demandé est **Reprise (client local) → conteneur IronClaw (serveur SSH)**. La clé privée reste côté client ; seule la clé publique est autorisée côté distant. Ne pas installer une clé privée dans le conteneur pour ce besoin et ne pas activer l’agent forwarding. Le client SSH réellement invoqué par Reprise, l’hôte, le port et le compte distant restent à identifier avant configuration. Cette identité SSH est indépendante de la clé symétrique de Reprise.

Option sans coffre tiers : conserver la clé SSH chiffrée sur disque et la déverrouiller dans `ssh-agent` pour la session, éventuellement avec durée limitée. Les connexions ultérieures peuvent utiliser l’agent sans redemander la passphrase ; un redémarrage ou expiration nécessite un nouveau déverrouillage si aucun stockage persistant du secret n’est employé. Les contraintes de destination dépendent des versions et doivent être vérifiées. [OpenSSH — ssh-add](https://man.openbsd.org/ssh-add).

L’autonomie complète après redémarrage demande un mécanisme d’accès non interactif : secret disponible via stockage système ou service de signature adapté, ou clé dédiée sans passphrase avec privilèges et périmètre strictement réduits. Ce dernier choix augmente l’exposition en cas de lecture du fichier ; ne jamais retirer la passphrase d’une clé générale existante par commodité. Évaluer une clé dédiée, compte distant restreint, absence d’agent forwarding et restrictions côté serveur selon le besoin réel. Le choix et la configuration demandent une validation explicite ; aucune clé n’est créée ou modifiée par Reprise pour ce chantier.
