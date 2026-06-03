# netbox-snmp-sync (NetBox plugin)

Číta interface, IP adresy a VLAN-y zo sieťových zariadení cez **SNMP** a zosynchronizuje
ich priamo do **NetBoxu** — všetko z vnútra NetBoxu, bez druhého programu.

Plugin-nástupca samostatného nástroja `netbox-snmp-sync`: SNMP zber a mapovacia logika
sa znovupoužíva, ale dáta sa zapisujú priamo cez Django ORM a celý workflow
(zber → porovnanie → zápis, plánovanie aj história behov) žije natívne v UI NetBoxu
a jeho background-job frameworku.

## Funkcie

- **Per-zariadenie SNMP nastavenia** (verzia 1/2c/3, port, community, SNMPv3 creds, timeout, retries) priamo na detaile zariadenia.
- Tlačidlá **Compare** / **Sync** na zariadení → background job vo workeri.
- **Add-only** zápis cez ORM (voliteľne aj update zmenených polí) — nikdy nemaže. Vytvára interface (typ, MTU, rýchlosť, duplex, stav, popis, MAC ako objekt, parent pre sub-interface) a IPv4 adresy.
- **Plánovaný** periodický sync (systémový job) — nahrádza cron / Windows Task Scheduler.
- **História behov** (`SyncRun`) v DB NetBoxu so štatistikami a stavom.
- **Verzovanie:** všetky zápisy (interaktívne aj automatické/plánované) sa zaznamenajú do NetBox **changelogu** (audit kto/kedy/čo, predtým→potom).
- **Revert behu:** každý beh eviduje, čo vytvoril; tlačidlom **„Revert run"** sa presne tie objekty zmažú (mazanie je tiež v changelogu).
- **REST API** pre konfigurácie aj históriu.

## Požiadavky

- NetBox **4.6+**
- `pysnmp>=7.1,<8` (inštaluje sa ako závislosť)
- Bežiaci RQ worker (`netbox-rq` / `manage.py rqworker`) — joby bežia v ňom.

## Inštalácia

```bash
pip install netbox-snmp-sync   # alebo: pip install -e . z tohto repozitára
```

V `configuration/plugins.py` (resp. `configuration.py`):

```python
PLUGINS = ["netbox_snmp_sync"]
```

Spustite migrácie a reštartujte NetBox + worker:

```bash
python manage.py migrate
```

> **Dev cez netbox-docker:** repozitár obsahuje `Dockerfile`, ktorý nabuildí NetBox image
> s pluginom (editable, s bind-mountom zdroja pre živé úpravy). Pozri
> `docker-compose.override.yml` v sprievodnom `netbox-docker` projekte.

## Konfigurácia

Predvolené SNMP hodnoty (keď zariadenie nemá vlastné nastavenie) a správanie sync-u sa
nastavujú v `PLUGINS_CONFIG`:

```python
PLUGINS_CONFIG = {
    "netbox_snmp_sync": {
        # SNMP defaulty
        "snmp_version": "2c",
        "snmp_community": "public",
        "snmp_port": 161,
        "snmp_timeout": 2.0,
        "snmp_retries": 1,
        # správanie
        "default_ethernet_type": "1000base-t",
        "set_mac_address": True,
        "update_existing": False,   # aj prepisovať zmenené polia existujúcich interface
        "skip_loopback_ips": True,
        # plánovač: hodiny medzi automatickými sync-mi; 0 = vypnuté
        "sync_interval_hours": 24,
    },
}
```

## Použitie

1. **Devices → zariadenie → panel „SNMP Sync"** → **Add** a vyplň SNMP nastavenia
   (alebo SNMP Sync → Device SNMP Configs → Add). Cieľ zberu = primárna IP zariadenia,
   alebo „target override".
2. **Compare** prečíta zariadenie cez SNMP a vypíše diff (nový / zmena / existuje) do logu jobu.
3. **Sync** prečíta a **add-only** zapíše chýbajúce interface/IP.
4. **SNMP Sync → Sync Runs** — história všetkých behov (manuálnych aj plánovaných).
5. Pri `sync_interval_hours > 0` plánovač každú hodinu zosynchronizuje zariadenia, ktoré
   neboli synchronizované za posledných N hodín.

## REST API

- `/api/plugins/snmp-sync/device-snmp-configs/`
- `/api/plugins/snmp-sync/sync-runs/`

> **Bezpečnosť:** SNMP tajomstvá (community, auth/priv kľúče) sú v DB v čitateľnej podobe
> (rovnako ako pôvodný `config.yaml`). Obmedz prístup cez NetBox permissions; API ich
> vracia, takže obmedz aj prístup k API tokenom.

## Obmedzenia (v0.1)

- Pokrýva interface (+ MAC, parent), IPv4 a VLAN membership. IPv6 a kabeláž z LLDP zatiaľ nie.
- Tri spôsoby zápisu: **Preview & write** (interaktívny výber), **Sync all** (všetko, add-only),
  a **plánovaný** automatický sync. `Compare` je len read-only diff do logu.

## Vývoj a testy

```bash
python manage.py test netbox_snmp_sync
```

Na lokálny test bez reálneho zariadenia použi SNMP simulátor (`snmpsim-lextudio`) s
priloženými `*.snmprec` dátami a nastav `target_override` na jeho adresu.
