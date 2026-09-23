# Déployer DICOM_discovery sur un NAS hospitalier

> **Research Use Only** — outil de curation de données de recherche, pas un dispositif
> médical. Les sorties (rapport HTML, CSV, verdicts JSON) contiennent des **identifiants
> patients et des dates d'examen** : le dossier de sortie doit être à accès restreint.

DICOM_discovery tourne sur le NAS **sans surveillance** : chaque nuit, la commande
`dicom-discovery job` parcourt le partage DICOM **en lecture seule** (en-têtes uniquement),
puis écrit un rapport de cohorte autonome (ouvrable hors-ligne par double-clic) et un fichier
d'état pour la supervision. Aucun accès réseau n'est nécessaire à l'exécution.

## 1. Choisir un mode de déploiement

| Mode | Cible | Installation | Planification |
|---|---|---|---|
| **A — Conteneur sur le NAS** *(recommandé)* | Synology DSM 7.2+ (Container Manager), QNAP (Container Station), TrueNAS, tout hôte Docker/Podman | image Docker chargée hors-ligne | Planificateur de tâches DSM / cron |
| **B — Serveur Linux qui monte le NAS** | serveur de calcul avec le partage monté en NFS/SMB (cas actuel : `/mnt/NAS2418_RADT`) | *wheelhouse* pip hors-ligne | timer systemd / cron |

**Prérequis communs**

- Processeur **x86_64** (image `amd64`) ou **ARM 64 bits** (image `arm64`).
- **RAM** : environ **1 Go par million de fichiers** du partage, plus ~0,5–1 Go pour la
  sauvegarde du cache et le rendu. *Estimation* (mesurée sur des enregistrements simulés,
  pas encore sur la cohorte réelle) : ~2 Go pour la cohorte RADIO-AIDE (~1 M de fichiers),
  sous la limite par défaut de 3 Go (`MEM_LIMIT`).
- Un **compte de service** qui peut **lire** le partage DICOM et **écrire** le dossier de sortie.
- Un **dossier de sortie à accès restreint** (équipe de recherche uniquement).

## 2. Récupérer les paquets (le NAS n'a pas besoin d'Internet)

Depuis un poste connecté : GitHub → dépôt `vivirtuose/DICOM_discovery` → onglet **Actions** →
workflow **NAS bundle** → dernier run réussi sur `master` → section **Artifacts** :

| Artefact | Contenu | Pour |
|---|---|---|
| `dicom-discovery-0.12.0-image-amd64` | `dicom-discovery-0.12.0-amd64.tar.gz` (+ `.sha256`) | mode A, NAS Intel/AMD |
| `dicom-discovery-0.12.0-image-arm64` | `dicom-discovery-0.12.0-arm64.tar.gz` (+ `.sha256`) | mode A, NAS ARM |
| `dicom-discovery-0.12.0-wheelhouse-py3.X-linux-x86_64` | wheels + `install-offline.sh` | mode B (choisir la version de Python du serveur) |

Vérifier l'intégrité après transfert : `sha256sum -c dicom-discovery-0.12.0-amd64.tar.gz.sha256`.

Chaque image est testée en CI dans les conditions du NAS (sans réseau, partage monté en
lecture seule, système de fichiers en lecture seule, utilisateur non-root) avant d'être
publiée comme artefact. Pour la construire soi-même :
`docker build -t dicom-discovery:0.12.0 DICOM_discovery && docker save dicom-discovery:0.12.0 | gzip > dd.tar.gz`.

## 3. Mode A — conteneur sur le NAS (exemple Synology)

1. **Charger l'image** — Container Manager → *Image* → *Importer* → *Ajouter depuis un
   fichier*. Si l'import graphique refuse le `.tar.gz`, le décompresser (`gunzip`) pour
   obtenir un `.tar`, ou en SSH : `sudo docker load -i dicom-discovery-0.12.0-amd64.tar.gz`.
2. **Préparer le dossier du projet**, p. ex. `/volume1/docker/dicom-discovery/`, et y copier
   [`deploy/nas/docker-compose.yml`](../deploy/nas/docker-compose.yml) et
   [`deploy/nas/.env.example`](../deploy/nas/.env.example) renommé **`.env`**.
3. **Renseigner `.env`** : `DICOM_ROOT` (partage à analyser), `OUTPUT_DIR` (sorties),
   `PUID`/`PGID` du compte de service (en SSH : `id <compte>` ; sur Synology le groupe
   `users` vaut 100).
4. **Vérification à blanc** (n'écrit rien) :
   ```bash
   cd /volume1/docker/dicom-discovery
   sudo docker compose run --rm dicom-discovery job --root /data --output-dir /output --dry-run
   ```
   Contrôler le *preflight* : nombre de patients, **source de la clé patient** (si le tag
   `PatientID` contient l'IPP hospitalier et non le pseudonyme d'étude, mettre
   `GROUP_BY=folder`), `excluded dirs` (corbeilles/instantanés ignorés), `unreadable dirs`
   (doit valoir 0 — sinon problème de droits).
5. **Premier passage manuel** : `sudo docker compose run --rm dicom-discovery`.
   Le premier scan lit chaque fichier (compter ~15–30 min par million de fichiers) ; les
   suivants ne relisent que les fichiers nouveaux ou modifiés grâce au cache d'index.
6. **Planifier** — Panneau de configuration → *Planificateur de tâches* → *Créer* →
   *Tâche planifiée* → *Script défini par l'utilisateur* ; utilisateur **root** ; horaire
   nocturne ; script :
   ```bash
   docker compose -f /volume1/docker/dicom-discovery/docker-compose.yml run --rm dicom-discovery
   ```
   (selon la version de DSM : `docker-compose` au lieu de `docker compose`). Cocher
   *« Envoyer les détails d'exécution par e-mail uniquement si le script se termine
   anormalement »* : tout code de sortie non nul déclenche une alerte.

**QNAP** : Container Station → *Applications* → *Créer* → coller le `docker-compose.yml`
(et les valeurs du `.env`) ; planifier la même commande via `/etc/config/crontab`
puis `crontab /etc/config/crontab`.

Le conteneur est **isolé** : `network_mode: none` (aucune donnée ne peut sortir), partage
DICOM monté en `:ro`, système de fichiers racine en lecture seule, aucune capacité Linux,
utilisateur non-root, RAM/CPU plafonnés (`MEM_LIMIT`, `CPUS`) pour que le NAS continue de
servir les fichiers pendant le scan.

## 4. Mode B — serveur Linux qui monte le partage

1. **Monter le partage en lecture seule** (exemples) :
   ```bash
   # NFS
   mount -t nfs -o ro,nfsvers=4.1,hard,noatime nas2418:/volume1/RADT /mnt/NAS2418_RADT
   # SMB/CIFS
   mount -t cifs -o ro,vers=3.0,credentials=/etc/dicomqc.cred,uid=dicomqc //nas2418/RADT /mnt/NAS2418_RADT
   ```
2. **Installer hors-ligne** (même version mineure de Python que le bundle, ≥ 3.9) :
   ```bash
   mkdir /tmp/dd && tar -xzf dicom-discovery-0.12.0-wheelhouse-py3.11-linux-x86_64.tar.gz -C /tmp/dd
   sudo PYTHON=python3.11 sh /tmp/dd/install-offline.sh /opt/dicom-discovery
   ```
3. **Vérifier à blanc** :
   ```bash
   /opt/dicom-discovery/venv/bin/dicom-discovery job --root /mnt/NAS2418_RADT/datasets/clinical_trials/RADIO-AIDE_full/DICOM \
       --output-dir /srv/dicom-discovery --group-by folder --dry-run
   ```
4. **Planifier** avec les unités fournies
   ([`deploy/nas/systemd/`](../deploy/nas/systemd/)) — elles attendent le montage
   (`RequiresMountsFor`), tournent en priorité basse et ne peuvent écrire que dans le
   dossier de sortie :
   ```bash
   sudo cp deploy/nas/systemd/dicom-discovery-job.* /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now dicom-discovery-job.timer
   ```
   Ou en cron : `30 2 * * * /opt/dicom-discovery/venv/bin/dicom-discovery job --root … --output-dir … >> /var/log/dicom-discovery.log 2>&1`.

## 5. Ce que produit chaque passage

```
OUTPUT_DIR/
├── latest/                     ← dernier passage exploitable (à mettre en favori)
│   ├── cohort_report.html      rapport autonome (Plotly embarqué, s'ouvre hors-ligne)
│   ├── verdicts.json           verdicts versionnés, validés par schéma
│   ├── rt_integrity.csv        intégrité RT par étude (Excel)
│   ├── rt_integrity_by_patient.csv
│   ├── index_manifest.json     ce que l'indexeur a vu (compteurs, dossiers exclus/illisibles)
│   └── run.log
├── runs/20260922T023000Z/      un dossier par passage (les 30 derniers, --keep)
├── last_run.json               état du dernier passage (compteurs seulement, aucun identifiant)
└── .cache/index_cache.pkl      cache d'index (accélère les passages suivants)
```

`latest/` n'est remplacé que par un passage **réussi** ou **partiel**, fichier par fichier
et de façon atomique : un lecteur n'y voit jamais un fichier à moitié écrit, et un échec ne
l'efface pas.

## 6. Supervision

| Code de sortie | `last_run.json` → `status` | Signification | `latest/` |
|---|---|---|---|
| 0 | `success` | passage complet | mis à jour |
| 1 | `no_dicom` | aucun DICOM trouvé (mauvais chemin, partage non monté, droits) | inchangé |
| 2 | `error` | erreur inattendue — trace dans `runs/<horodatage>/run.log` | inchangé |
| 3 | `partial` | rapport produit, mais des dossiers n'ont pas pu être listés | mis à jour (marqué PARTIAL) |
| 75 | *(inchangé)* | un passage précédent tient encore le verrou | inchangé |
| 137 | *(inchangé)* | conteneur tué par manque de mémoire → augmenter `MEM_LIMIT` | inchangé |

`last_run.json` contient `status`, `exit_code`, horodatages, durée, `n_files_seen`,
`n_dicom_indexed`, `n_unreadable`, `n_dirs_excluded`, `n_dirs_unreadable`, `n_patients`,
`n_studies` et la répartition des verdicts — de quoi alimenter une sonde (Zabbix, Nagios…).

## 7. Comportements propres aux partages NAS

- **Dossiers système ignorés** : corbeilles et instantanés (`#recycle`, `#snapshot`,
  `@eaDir` Synology ; `@Recycle`, `@Recently-Snapshot` QNAP ; `.snapshot` NetApp ;
  `$RECYCLE.BIN` ; résidus macOS). Sans cela, un patient supprimé resterait visible via la
  corbeille et un instantané doublerait la cohorte. Leur nombre apparaît dans
  `excluded dirs`. Pour ignorer d'autres dossiers : `--exclude-dir "_old*"` (répétable).
- **Dossiers illisibles signalés** : un dossier que le compte ne peut pas lister n'est
  plus ignoré en silence — il est compté (`unreadable dirs`), le rapport porte la mention
  *PARTIAL scan* et le code de sortie vaut 3.
- **Verrou** : `OUTPUT_DIR/.dicom-discovery.lock` empêche deux passages simultanés. Un
  verrou de plus de 24 h (passage interrompu) est repris automatiquement
  (`--stale-lock-hours`).
- **Cache d'index** : activé par défaut. Pour forcer une relecture complète, supprimer
  `OUTPUT_DIR/.cache/`. Pour une archive **en ajout seul** (fichiers jamais modifiés en
  place), `--assume-immutable` évite même le `stat` de chaque fichier.
- **Mémoire maîtrisée** : lectures parallèles en flux borné, points de sauvegarde du
  cache espacés géométriquement, UID partagés entre coupes d'une même série.

## 8. Mise à jour

Charger la nouvelle image, changer `DD_VERSION` dans `.env` ; le cache et les passages
précédents restent valides. En mode B, relancer `install-offline.sh` avec le nouveau bundle.

## 9. Dépannage

Avant tout diagnostic, lancer le bilan d'environnement — il répond à lui seul à la moitié
des questions (versions, encodages, droits de lecture/écriture) :

```bash
docker compose run --rm dicom-discovery doctor --root /data --output-dir /output
```

| Symptôme | Cause probable | Action |
|---|---|---|
| `no DICOM detected`, code 1 | `DICOM_ROOT` erroné, partage non monté | vérifier le chemin ; `--dry-run` |
| `unreadable dirs > 0`, code 3 | ACL : le compte `PUID` ne peut pas lister ces dossiers | donner le droit de lecture au compte de service |
| `Permission denied` sur `/output` | `PUID`/`PGID` sans droit d'écriture sur `OUTPUT_DIR` | corriger les droits du dossier de sortie |
| code 75 à chaque passage | passage précédent encore en cours, ou verrou orphelin récent | `docker ps` ; si rien ne tourne, supprimer `.dicom-discovery.lock` |
| code 137 | mémoire insuffisante | augmenter `MEM_LIMIT` (≈ 1 Go par million de fichiers) |
| patients = numéros IPP | le tag `PatientID` contient l'IPP | `GROUP_BY=folder` |
| scan très lent | partage SMB chargé, trop/peu de lectures parallèles | ajuster `WORKERS` (4–8) ; planifier la nuit |
