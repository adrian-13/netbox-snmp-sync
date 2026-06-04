# netbox-snmp-sync

> **NetBox plugin** — číta rozhrania, IP adresy a VLAN-y zo sieťových zariadení cez **SNMP**
> a synchronizuje ich priamo do NetBoxu. Celý workflow žije natívne v NetBox UI bez
> akéhokoľvek externého skriptu alebo cronu.

[![NetBox](https://img.shields.io/badge/NetBox-4.6%2B-blue)](https://github.com/netbox-community/netbox)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## Obsah

- [Čo plugin robí](#čo-plugin-robí)
- [Funkcie](#funkcie)
- [Požiadavky](#požiadavky)
- [Inštalácia](#inštalácia)
- [Konfigurácia](#konfigurácia)
- [Použitie](#použitie)
- [REST API](#rest-api)
- [Bezpečnosť](#bezpečnosť)
- [Vývoj a testy](#vývoj-a-testy)

---

## Čo plugin robí

Sieťové zariadenie (router, switch) hovorí cez SNMP: aké má rozhrania, IP adresy, VLANy.
Plugin tieto dáta prečíta a zapíše (alebo porovná) priamo do NetBoxu cez ORM — bez exportu,
bez CSV, bez druhého nástroja. Celé to ovládaš z panela zariadenia alebo z menu SNMP Sync.

```
Zariadenie (SNMP) ──► SNMP zber ──► Engine (diff / apply) ──► NetBox ORM
                                                         └──► Changelog, História, Revert
```

---

## Funkcie

### Per-zariadenie SNMP nastavenia
- SNMPv1 / v2c (community) aj **SNMPv3** (username + auth/priv protokol + kľúče)
- Port, timeout, retries, target override (iný cieľ ako primárna IP)
- Nastavenia žijú na karte zariadenia (pravý panel) aj v zozname SNMP Sync → Device SNMP Configs

### Zbierané dáta
- **Rozhrania** — názov, typ (odvodený z rýchlosti), MTU, rýchlosť, duplex, stav (admin/oper), popis, MAC adresa, rodičovský interface pre sub-interfaces
- **IPv4 adresy** — s prefixovou dĺžkou, priradenie na interface
- **VLAN membership** — tagged/untagged priradenie na interface (voliteľné)

### Zápis / porovnanie
| Akcia | Popis |
|-------|-------|
| **Test SNMP** | Rýchly ping + SNMP test dosahiteľnosti (nezmení NetBox). Zobrazí výsledkovú stránku s OK/Failed, uloží čas + správu do stĺpca *Last test* |
| **Bulk test** | Test SNMP pre viacero vybraných zariadení naraz (paralelne) |
| **Preview & write** | SNMP poll → zobrazí diff s checkboxmi → zapíše len vybraté |
| **Compare** | SNMP poll → diff do logu background jobu (read-only) |
| **Sync all** | SNMP poll → add-only zápis všetkých nových interface + IP |
| **Scheduled sync** | Automatický sync podľa `sync_interval_hours` (systémový job) |

### História a audit
- **SyncRun** — každý beh (manuálny aj plánovaný) sa uloží do DB: štatistiky, stav, timestamp
- **NetBox changelog** — všetky zápisy (aj z background jobov) sa zaznačia do NetBox audit logu (kto/kedy/čo, predtým→potom)
- **Revert** — každý beh eviduje presne, ktoré objekty vytvoril; kliknutím **Revert run** sa tie objekty zmažú (mazanie je tiež v changelogu)

### Nastavenia v UI
- Globálne nastavenia pluginu (sync interval, update_existing, VLAN write/create, história) sú editovateľné priamo v NetBoxe cez SNMP Sync → Settings — bez reštartu

### Bezpečnosť
- SNMP tajomstvá (community, auth/priv kľúče) sú v REST API write-only (GET ich nevracia)
- Permissioning cez štandardné NetBox oprávnenia

---

## Požiadavky

| Závislosť | Verzia |
|-----------|--------|
| NetBox | 4.6+ |
| Python | 3.12+ |
| pysnmp | ≥ 7.1, < 8 |
| Redis + RQ worker | štandardná NetBox prerekvizita |

---

## Inštalácia

```bash
# Z PyPI (po publikovaní)
pip install netbox-snmp-sync

# Alebo priamo zo zdrojov
pip install -e /path/to/netbox-snmp-sync
```

V `configuration/plugins.py` (resp. `configuration.py`):

```python
PLUGINS = ["netbox_snmp_sync"]
```

Migrácie a reštart:

```bash
python manage.py migrate
python manage.py collectstatic
# Restart NetBox + RQ worker
```

### Dev s netbox-docker

Repozitár má `Dockerfile`, ktorý nabuildí NetBox image s pluginom nainštalovaným v
editable móde + bind-mount zdrojového adresára pre živé úpravy bez rebuildu.

---

## Konfigurácia

```python
PLUGINS_CONFIG = {
    "netbox_snmp_sync": {
        # SNMP transport defaulty (keď zariadenie nemá vlastné nastavenie)
        "snmp_version": "2c",
        "snmp_community": "public",
        "snmp_port": 161,
        "snmp_timeout": 2.0,
        "snmp_retries": 1,
        # Správanie
        "default_ethernet_type": "1000base-t",
        "set_mac_address": True,
        "update_existing": False,   # True = prepisuj aj zmenené polia existujúcich objectov
        "skip_loopback_ips": True,
        # VLAN sync
        "write_vlans": False,       # True = priraď VLANy na interface
        "create_vlans": False,      # True = vytvor chýbajúce VLANy v site zariadenia
        # Plánovač
        "sync_interval_hours": 24,  # 0 = plánovač vypnutý
        # História (SyncRun)
        "history_keep_days": 90,
        "history_keep_count": 1000,
    },
}
```

> **Tip:** Všetky tieto nastavenia môžeš po prvom spustení zmeniť priamo v NetBoxe cez
> **SNMP Sync → Settings** bez reštartu servera.

---

## Použitie

### 1. Pridaj SNMP konfiguráciu k zariadeniu

Otvor **Devices → zariadenie → panel „SNMP Sync"** a klikni **Add**. Vyplň:
- SNMP verzia (v2c / v3)
- Community alebo SNMPv3 prihlasovacie údaje
- Cieľ zberu = primárna IP zariadenia alebo **Target override**

### 2. Test spojenia

Klikni **Test SNMP** (pri ceruzke v zozname alebo na paneli zariadenia). Zobrazí sa výsledková
stránka — OK s výpisom sysName/vendor/počty, alebo Failed s chybou. Výsledok sa uloží do
stĺpca **Last test** a ostane viditeľný v zozname aj na paneli.

Pre viacero zariadení naraz: zaškrtni ich v zozname → **Test selected**.

### 3. Porovnaj alebo syncuj

- **Preview & write** — interaktívny výber, čo sa má zapísať
- **Compare** — len diff do logu jobu (nič nezapíše)
- **Sync all** — add-only zápis všetkého nového

### 4. Pozri históriu

**SNMP Sync → Sync Runs** — každý beh s časom, typom, štatistikami. Ak chceš odvolať
zmeny behu, klikni naň a daj **Revert run**.

### 5. Automatický sync

Nastav `sync_interval_hours > 0` v Settings. Systémový job každú hodinu skontroluje, ktoré
zariadenia neboli synchronizované za posledných N hodín, a zaradí ich do fronty RQ workera.

---

## REST API

```
GET/POST  /api/plugins/snmp-sync/device-snmp-configs/
GET/PUT   /api/plugins/snmp-sync/device-snmp-configs/{id}/
GET       /api/plugins/snmp-sync/sync-runs/
GET       /api/plugins/snmp-sync/sync-runs/{id}/
```

Swagger UI: `http://localhost:8000/api/schema/swagger-ui/` → sekcia `plugins`

---

## Bezpečnosť

- **SNMP tajomstvá** (community, auth_key, priv_key) sú v REST API nastavené ako `write_only` —
  `GET /api/plugins/snmp-sync/device-snmp-configs/` ich nevráti v odpovedi
- Prístup k API aj UI kontrolujú štandardné **NetBox oprávnenia** (`view_devicesnmpconfig`, `add_devicesnmpconfig`, ...)
- Tajomstvá sú v DB v čitateľnej podobe — obmedzuj prístup k DB a rotuj tokens

---

## Vývoj a testy

```bash
# Testy (vrátane security testov)
export NETBOX_CONFIGURATION=netbox.configuration_testing
python manage.py test netbox_snmp_sync

# Lokálny SNMP simulátor (snmpsim-lextudio)
# Nastav target_override na adresu simulátora
```

Projekt využíva NetBox plugin API (verejné rozhranie): `NetBoxModel`, `NetBoxModelForm`,
`NetBoxTable`, `JobRunner`, `system_job`, `event_tracking`, `register_model_view`.

---

## Changelog

### v0.2.0
- **Test SNMP** — výsledková stránka (OK/Failed + sysName, vendor, počty) namiesto toastu; výsledok sa ukladá do stĺpca *Last test*
- **Bulk SNMP test** — vyber viacero zariadení → Test selected → súhrnná výsledková stránka (paralelný test, pool 8)
- Globálne nastavenia editovateľné v UI (SNMP Sync → Settings)
- Per-device scheduler — každé zariadenie sa syncuje samostatným RQ jobom (škálovateľnosť, izolácia chýb)
- VLAN membership sync (write_vlans / create_vlans)
- Changelog pre background joby (event_tracking + NetBoxFakeRequest)
- Revert behu — evidencia vytvorených objektov, akcia Revert run
- Bulk setup — hromadné založenie SNMP konfigurácií pre vybrané zariadenia
- SNMP tajomstvá write-only v REST API
- Security testy (17 testov)

### v0.1.0
- Počiatočný commit: základný SNMP zber, per-device konfigurácia, Compare/Sync joby, história SyncRun, REST API
