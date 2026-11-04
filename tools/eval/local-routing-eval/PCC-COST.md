# Coût théorique PCC pour Reprise — 16 octobre 2026

Recherche documentaire officielle uniquement : aucune inscription, consultation de compte privé, modification, souscription ou génération cloud. Le Mac M3 16 Go et Reprise privé sont le contexte fourni, pas une preuve d’éligibilité du compte. Le résultat `fm` 1008 déjà observé n’établit aucun accès PCC.

## Qui paierait quoi ?

Un **utilisateur final d’une app déjà éligible** n’a pas à acheter son propre abonnement Developer pour faire une requête : Apple décrit un accès depuis l’app sur appareil compatible, sans gestion de clé API par l’app. Il dispose toutefois d’un quota quotidien ; davantage d’accès peut nécessiter iCloud+. Nombre de requêtes, partage éventuel entre apps et niveau iCloud+ utile ne sont pas établis ici. Aucun abonnement supplémentaire n’est supposé. [Apple, intégration PCC](https://developer.apple.com/documentation/foundationmodels/adding-server-side-intelligence-with-private-cloud-compute?changes=latest_major).

**Dans notre cas, propriétaire de Reprise privé et développeur sont la même personne ou organisation.** Il faut donc aussi considérer l’accès développeur, sa cotisation éventuelle et le travail d’intégration. Un accès PCC via Siri ou une fonction système ne prouve pas qu’un exécutable Reprise peut appeler l’API développeur. Foundation Models expose cette dernière par `PrivateCloudComputeLanguageModel`, avec disponibilité propre à vérifier. [Apple, Foundation Models](https://developer.apple.com/documentation/FoundationModels/).

## Conditions du cloud sans facture API

Apple réserve l’offre sans coût API aux développeurs inscrits au Small Business Program, sous le seuil de deux millions de premiers téléchargements App Store, avec entitlement PCC attribué au compte. La page autorise les apps distribuées par l’App Store et leurs tests TestFlight/ad hoc. Les installations de test ne comptent pas comme premiers téléchargements. Une sortie d’éligibilité ouvre un délai annoncé de six mois pour migrer. Aucun tarif public « payez les tokens PCC à la place » n’est établi. [Apple, accès PCC](https://developer.apple.com/private-cloud-compute/).

Small Business n’est pas une déduction fondée sur la petite taille de HERAISYS : inscription effective requise. Les nouveaux développeurs peuvent être éligibles ; le programme examine aussi les revenus App Store et comptes associés, avec seuil de 1 million USD, et demande notamment le rôle Account Holder et l’accord Paid Apps. Notre statut réel reste inconnu ; aucun compte n’a été vérifié. [Apple, Small Business Program](https://developer.apple.com/app-store/small-business-program/).

L’entitlement géré est `com.apple.developer.private-cloud-compute`. **La signature locale `codesign` ad hoc de Reprise v0.1.0 n’équivaut pas à une distribution de test autorisée par Apple avec entitlement et provisioning valides.** Le parcours exact d’un test macOS privé doit être confirmé avant intégration réelle. Les pages consultées n’établissent pas l’autorisation d’une exploitation durable par téléchargement privé Developer ID, usage interne sans distribution App Store ou proxy MCP généraliste. Une approbation d’entitlement ne doit pas être présumée. [Apple, entitlement PCC](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.private-cloud-compute), [conditions d’accès](https://developer.apple.com/private-cloud-compute/).

## Montants vérifiables et inconnus

La fiche officielle **App Store France** affiche l’achat intégré « Apple Developer Program » à **98,99 €**. C’est une référence publique du canal app, pas un devis personnalisé ni une preuve de prix du canal web. [Apple Developer, boutique France](https://apps.apple.com/fr/app/apple-developer/id640199958).

Apple confirme une cotisation annuelle, un abonnement renouvelable par l’app, un prix public de référence de 99 USD/an et des prix locaux variables présentés à l’inscription. Des taxes peuvent s’appliquer selon la région. La ventilation HT/TVA, le prix final propre au compte français ou à HERAISYS et une éventuelle exonération restent inconnus : aucune TVA ajoutée ou récupérabilité supposée. [Apple, inscription et paiement](https://developer.apple.com/help/account/membership/program-enrollment/).

Calcul budgétaire, avec la seule base publique française de 98,99 € : **98,99 / 12 = 8,249… €, soit environ 8,25 €/mois lissés**. Il s’agit d’un paiement annuel, pas d’un abonnement mensuel à 8,25 €. Cela représente environ 82,5 % d’une enveloppe de 10 €/mois, ou 41,2 % de 20 €/mois ; reste respectivement environ 1,75 € ou 11,75 €/mois lissés pour le reste, hors autres frais. Ce calcul n’établit ni l’éligibilité ni le prix final du compte.

| Situation | Dépense supplémentaire théorique | Décision pour le pilote de 2 € |
|---|---|---|
| Utilisateur final d’une app éligible existante | Pas de compte Developer personnel imposé ; supplément API annoncé nul dans l’offre, quota et iCloud+ à vérifier | Potentiellement compatible sans achat ; accès réel à vérifier |
| Notre compte déjà payé, Small Business et entitlement confirmés, test Reprise autorisé | Cotisation déjà engagée : coût marginal de cotisation nul sur sa période ; cloud sans coût API dans ces conditions | Potentiellement compatible, aucune génération autorisée par ce document |
| Notre compte payé, mais Small Business/entitlement/distribution non confirmés | Coût cloud et accès non établis ; payer Developer seul ne débloque pas PCC | Garder PCC indisponible jusqu’à confirmation |
| Nouveau compte nécessaire | Référence app France 98,99 € annuels à confirmer ; admission Small Business/PCC non garantie | Incompatible avec achat sous plafond total de 2 € ; ne rien souscrire |
| Développeur non éligible | Aucun barème public de remplacement établi | Route PCC indisponible, pas un coût nul ni une API payante supposée |

Pour le compte déjà payé, distinguer **coût marginal immédiat** et **coût complet** : le renouvellement reste annuel, et affecter une part de la cotisation à Reprise dépend de ses autres usages. Sans cette répartition, le coût amorti propre à Reprise reste inconnu. L’achat d’iCloud+ ne remplace pas l’éligibilité développeur.

## Coût d’intégration et conclusion pratique

Le matériel est déjà acquis ; ne pas ajouter d’achat RAM/modèle pour PCC. Mais la compatibilité matérielle ne prouve pas la disponibilité effective en France, pour la langue, le compte et le modèle du SDK installé : vérifier l’état runtime sans lancer de génération cloud. Apple indique des conditions régionales, appareil et logiciel. [Apple, disponibilité Apple Intelligence](https://support.apple.com/en-us/121115).

Restent non chiffrés : signature/provisioning, traitement des quotas, UI et consentement de transmission, tests qualité/outils, maintien du SDK, approbation de distribution, réseau et temps humain. Ne pas présenter une ligne de Swift comme le coût total. Sans estimation de travail, le coût complet par tâche n’est pas calculable ; la gratuité API conditionnelle ne démontre aucune économie face au local ou à Flash.

Avec **2 € de pilote**, PCC est une piste seulement si un compte et une voie de test déjà autorisés existent. Avec **10–20 €/mois**, une nouvelle cotisation pourrait mathématiquement occuper environ 8,25 €/mois lissés sur la référence française, mais réclamerait près de 99 € immédiatement et une décision d’achat distincte, non autorisée ici. La prochaine vérification utile porte sur l’éligibilité et la distribution, avant de financer ou développer cette route. Aucun réglage global, achat ou envoi de données n’a été effectué.
