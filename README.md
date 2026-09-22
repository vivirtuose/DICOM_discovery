# DICOM Discovery — EpiBrainRad

[![CI](https://github.com/vivirtuose/DICOM_discovery/actions/workflows/ci.yml/badge.svg)](https://github.com/vivirtuose/DICOM_discovery/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.9%E2%80%933.14-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Use: research-only](https://img.shields.io/badge/use-research--only-orange)

Dépôt regroupant les deux générations d'outils de découverte et de QC des fichiers DICOM RT de la cohorte EpiBrainRad.

> **Preuve de fonctionnement (proof-of-work).** La CI teste `DICOM_discovery` sur Python
> 3.9→3.14, puis génère une **cohorte DICOM-RT synthétique** (aucune donnée patient) et exécute
> le vrai pipeline `report --json` de bout en bout ; le rapport HTML autonome et le
> `verdicts.json` produits sont téléversés comme **artefacts téléchargeables** de chaque run
> CI. Voir l'onglet *Actions* → job *proof-of-work*.

## Structure du dépôt

```
DICOM_discovery/                 # racine du dépôt
├── file_discovery/              # Version historique — scanner utilisé en production
├── DICOM_discovery/             # Package Python structuré (src/, tests/, Dockerfile, deploy/nas/, docs/)
├── .github/workflows/           # CI (matrice 3.9→3.14 + Windows/macOS, proof-of-work), bundle NAS, cohorte réelle TCIA
├── outputs/                     # (local, non versionné) sorties sur la cohorte réelle
└── _archive/                    # (local, non versionné) code périmé conservé pour référence
```

---

## `file_discovery/` — Version stable (production)

Scanner DICOM modulaire, opérationnel sur le NAS RADIO-AIDE. Utilisé pour produire les inventaires patients et les rapports de complétude RT.

**Modules :**

| Fichier | Rôle |
|---|---|
| `file_discovery_runner.py` | Point d'entrée principal — lance le scan et produit les sorties |
| `reports.py` | Génération des rapports HTML et CSV |
| `rt_integrity.py` | Vérification de la complétude des fichiers RT (CT, RTDOSE, RTPLAN, RTSTRUCT) |
| `epibrainrad_legacy/` | Modules hérités de la version monolithique (discovery, qc, maps, clinical) |

**Commande cohorte globale :**

```bash
python file_discovery_runner.py \
  --nas-root /mnt/NAS2418_RADT/datasets/clinical_trials/RADIO-AIDE_full/DICOM \
  --workers 3 \
  --out outputs/run_epibrainrad/
```

**Commande test patient :**

```bash
python file_discovery_runner.py \
  --nas-root /mnt/NAS2418_RADT/datasets/clinical_trials/RADIO-AIDE_full/DICOM \
  --patients "IC 003" \
  --workers 1 \
  --out outputs/run_test_3patients/
```

---

## `DICOM_discovery/` — Version en développement

Package Python structuré (`src/` layout), versionné, compatible **Python 3.9→3.14**, avec tests automatisés (191 tests, pytest) et CLI installable en **une commande**.

**Fonctionnalités ajoutées par rapport à `file_discovery/` :**
- Index DICOM par tags (PatientID, Modality, SeriesInstanceUID)
- Verdicts par patient avec provenance horodatée (JSON + schema_version)
- Vérification légère des ROI TG-263
- Rapport de cohorte HTML interactif (timeline, KPI cliniques)
- CLI `dicom-discovery` avec sous-commandes `index`, `rt-check`, `report`, `completeness`, `job`, `doctor`
- **Exécution planifiée sur NAS hospitalier** (`job`) : lecture seule, dossier par passage,
  `latest/`, état `last_run.json`, verrou, rétention, cache d'index ; corbeilles/instantanés
  NAS ignorés, dossiers illisibles signalés

**Installation (une commande, directement depuis GitHub) :**

```bash
pip install "git+https://github.com/vivirtuose/DICOM_discovery.git#subdirectory=DICOM_discovery"
```

Le rapport interactif (Plotly) est une dépendance de base : `dicom-discovery report` fonctionne
sans étape supplémentaire. Pour le développement : `cd DICOM_discovery && pip install -e ".[dev]"`.

**Usage rapide :**

```bash
dicom-discovery report --root /chemin/vers/cohorte --out outputs/run_epibrainrad/cohort_report.html
```

**Tests :**

```bash
cd DICOM_discovery
pytest
```

Voir `DICOM_discovery/README.md` pour la documentation complète.

### Déploiement sur un NAS hospitalier

Deux voies, **sans accès Internet requis sur le NAS**, construites et testées en CI (workflow
*NAS bundle*, artefacts téléchargeables) :

- **Conteneur sur le NAS** (Synology Container Manager, QNAP, TrueNAS) — image durcie
  `amd64`/`arm64` : sans réseau, partage DICOM en lecture seule, utilisateur non-root
  (`DICOM_discovery/deploy/nas/docker-compose.yml`) ;
- **Serveur Linux montant le partage** — *wheelhouse* pip hors-ligne + timer systemd.

Guide pas à pas : [`DICOM_discovery/docs/NAS_DEPLOYMENT.md`](DICOM_discovery/docs/NAS_DEPLOYMENT.md).

---

## `outputs/` — Sorties de production

Résultats générés par `file_discovery/` sur la cohorte réelle. Ne pas modifier manuellement.

| Dossier | Outil utilisé | Contenu |
|---|---|---|
| `run_epibrainrad/` | `file_discovery` | Inventaires CSV + carte HTML cohorte EpiBrainRad |
| `run_all_patients/` | `file_discovery` | RT integrity CSV + rapports HTML tous patients |
| `run_test_3patients/` | `file_discovery` | Inventaires de validation sur 3 patients |

---

## `_archive/` — Code périmé

| Dossier | Contenu |
|---|---|
| `file_discovery_BACKUP_20260612/` | Version monolithique originale (avant refactoring) |
| `file_discovery_package_audit/` | Documents d'audit et plan de refactoring (juin 2026) |

Ces dossiers sont conservés pour référence historique. Ne pas réutiliser.

---

## Environnement

- `DICOM_discovery/` : **Python 3.9→3.14**, testé en CI sur Linux, macOS et Windows. Installation via
  un simple `venv` + `pip` (aucun environnement Conda requis).
- `file_discovery/` : version historique, Python 3.8 (production NAS).
- Accès NAS requis uniquement pour les runs sur données réelles : `/mnt/NAS2418_RADT/`. Les
  tests et la démo n'utilisent que des données **synthétiques** (aucune donnée patient).
