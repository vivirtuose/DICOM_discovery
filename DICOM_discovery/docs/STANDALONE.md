# Exécutable autonome — sans Python, sans installation

> **Research Use Only** — outil de recherche, pas un dispositif médical. Le rapport et les CSV
> produits contiennent des identifiants patients : les enregistrer dans un dossier à accès
> restreint.

Un seul fichier, à poser sur le Bureau et à double-cliquer. Il embarque Python, pydicom,
pandas et Plotly : **rien à installer, aucun accès Internet nécessaire**.

## 1. Télécharger

GitHub → dépôt `vivirtuose/DICOM_discovery` → *Releases* → dernière version :

| Fichier | Pour |
|---|---|
| `dicom-discovery-<version>-windows-amd64.zip` | Windows 64 bits |
| `dicom-discovery-<version>-macos-arm64.tar.gz` | Mac Apple Silicon (M1 et suivants) |
| `dicom-discovery-<version>-linux-x86_64.tar.gz` | Linux 64 bits |

Vérifier l'empreinte après transfert : le `.sha256` accompagne chaque archive
(`sha256sum -c …` ou, sous Windows, `Get-FileHash`).

Décompresser : on obtient `dicom-discovery.exe` (ou `dicom-discovery`), la licence et ce guide.

## 2. Premier lancement — les avertissements de sécurité

Les binaires **ne sont pas signés** (une signature de code est payante et nominative). Au
premier lancement, le système prévient donc que l'éditeur est inconnu. C'est attendu.

- **Windows / SmartScreen** : « Windows a protégé votre ordinateur » → *Informations
  complémentaires* → *Exécuter quand même*.
- **macOS / Gatekeeper** : clic droit sur le fichier → *Ouvrir* → *Ouvrir*. Si le message
  persiste : `xattr -d com.apple.quarantine dicom-discovery` dans le Terminal.
- **Linux** : rendre exécutable — `chmod +x dicom-discovery`.
- **Antivirus** : un exécutable auto-extractible est parfois mis en quarantaine à tort. Le
  binaire n'est pas compressé par UPX (cause fréquente de faux positifs) ; en cas de blocage,
  faire ajouter une exception par le service informatique, ou utiliser l'installation
  hors-ligne classique (voir [NAS_DEPLOYMENT.md](NAS_DEPLOYMENT.md)).

## 3. Utilisation en double-clic

Double-cliquer ouvre une fenêtre noire et pose **deux questions**, chacune avec un sélecteur
de dossier :

1. **le dossier DICOM à contrôler** — il est uniquement *lu*, jamais modifié ;
2. **le dossier où écrire les résultats**.

Le contrôle démarre, la progression s'affiche, puis le rapport s'ouvre tout seul dans le
navigateur. La fenêtre attend une touche avant de se fermer : le message reste lisible.

Les résultats sont ceux du passage planifié habituel (`latest/`, `runs/`, `last_run.json`) —
voir la section 5 de [NAS_DEPLOYMENT.md](NAS_DEPLOYMENT.md).

## 4. Utilisation en ligne de commande

Le même fichier accepte toutes les commandes habituelles :

```bash
dicom-discovery doctor --root D:\DICOM --output-dir D:\qc   # bilan de l'environnement
dicom-discovery job --root D:\DICOM --output-dir D:\qc --dry-run
dicom-discovery report --root D:\DICOM --out rapport.html
```

Lancé **avec** des arguments, il ne pose aucune question : il est donc utilisable dans une
tâche planifiée Windows exactement comme la version installée.

## 5. Limites à connaître

- **Taille et démarrage** : ~50 Mo, et chaque lancement décompresse le contenu en mémoire —
  compter quelques secondes avant l'affichage. C'est normal pour un fichier unique.
- **Une architecture par fichier** : le binaire Windows ne tourne pas sur un Mac, et le
  binaire Apple Silicon pas sur un Mac Intel.
- **Mises à jour manuelles** : il n'y a pas d'auto-mise-à-jour ; retélécharger à chaque version.
- Pour un usage **planifié sur serveur ou NAS**, préférer l'image Docker ou l'installation
  hors-ligne : plus légères, plus faciles à mettre à jour ([NAS_DEPLOYMENT.md](NAS_DEPLOYMENT.md)).

## 6. Construire le binaire soi-même

```bash
cd DICOM_discovery
pip install . pyinstaller
pyinstaller --clean --noconfirm --distpath dist --workpath build/standalone \
    deploy/standalone/dicom-discovery.spec
```

Le binaire apparaît dans `dist/`. La CI (*Standalone binaries*) fait exactement cela sur les
trois systèmes, puis exécute le binaire produit et vérifie que le rapport reste autonome.
