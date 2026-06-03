"""Cisco-style port-name normalization.

LLDP neighbours often advertise a different abbreviation of the same port than NetBox
stores — e.g. one side has ``GigabitEthernet0/1`` and the other ``Gi0/1``. SNMP also
sometimes returns the long form while operators manually create the short form in NetBox
(or vice versa). This module gives us a single canonical form so the two can be compared.

The strategy is to expand any recognised Cisco-style abbreviated prefix to its long form,
leaving the numeric suffix (``0/1``, ``1/1/3``, ``1.20``) untouched. Unknown prefixes
(MikroTik ``ether1``, Juniper ``ge-0/0/0``, etc.) pass through unchanged because their
short and long forms already agree.
"""
from __future__ import annotations

# (long, short) pairs. Order matters within each *length tier* — longer keys must be
# tried first so "TwentyFiveGigE" wins over "TwoGigabitEthernet" and "Twe" wins over "Tw".
# Both forms appear on the wire from real Cisco IOS / NX-OS / IOS-XR gear.
_ABBREVS: list[tuple[str, str]] = [
    ("HundredGigE",           "Hu"),
    ("TwentyFiveGigE",        "Twe"),
    ("TwoGigabitEthernet",    "Tw"),
    ("FortyGigE",             "Fo"),
    ("TenGigE",               "Te"),
    ("GigabitEthernet",       "Gi"),
    ("FastEthernet",          "Fa"),
    ("Ethernet",              "Eth"),   # NX-OS abbreviation
    ("Ethernet",              "Et"),    # IOS abbreviation
    ("AppGigabitEthernet",    "Ap"),
    ("Management",            "Mgmt"),
    ("Port-channel",          "Po"),
    ("Loopback",              "Lo"),
    ("Tunnel",                "Tu"),
    ("Serial",                "Se"),
    ("Vlan",                  "Vl"),
]
_LONG_FORMS = sorted({long for long, _ in _ABBREVS}, key=len, reverse=True)
_SHORT_TO_LONG: dict[str, str] = {short.lower(): long for long, short in _ABBREVS}
_SHORT_FORMS = sorted(_SHORT_TO_LONG.keys(), key=len, reverse=True)


def normalize_port_name(name: str | None) -> str:
    """Expand a recognised Cisco-style abbreviated prefix to its long form.

    Examples::

        Gi0/1                  -> GigabitEthernet0/1
        GigabitEthernet0/1     -> GigabitEthernet0/1   (canonical case enforced)
        Te1/1                  -> TenGigE1/1
        Eth1/1, Et1/1          -> Ethernet1/1
        ether1                 -> ether1               (no known prefix)
        ""                     -> ""

    Short prefixes are accepted only when followed by a non-alpha character (or end of
    string), so ``Etx`` will not be mistaken for ``Et``.
    """
    if not name:
        return name or ""
    s = name.strip()
    if not s:
        return s
    low = s.lower()
    # Long form already there → enforce canonical case on the prefix; keep suffix as-is.
    for lp in _LONG_FORMS:
        if low.startswith(lp.lower()):
            return lp + s[len(lp):]
    # Short form → expand to long.
    for sp in _SHORT_FORMS:
        if low.startswith(sp):
            n = len(sp)
            if n == len(s) or not s[n].isalpha():
                return _SHORT_TO_LONG[sp] + s[n:]
    return s


def port_names_match(a: str | None, b: str | None) -> bool:
    """True iff two port names refer to the same port after Cisco-style normalization."""
    if not a or not b:
        return a == b
    return normalize_port_name(a) == normalize_port_name(b)
