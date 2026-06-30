"""Choice sets for the SNMP Sync plugin.

Values intentionally match what ``snmp_collector`` accepts (see ``_build_auth`` and the
``_AUTH_PROTO`` / ``_PRIV_PROTO`` maps), so a DeviceSNMPConfig row can be handed to the
collector without translation.
"""
from utilities.choices import ChoiceSet


class SNMPVersionChoices(ChoiceSet):
    V1 = "1"
    V2C = "2c"
    V3 = "3"

    CHOICES = [
        (V1, "SNMPv1", "red"),
        (V2C, "SNMPv2c", "blue"),
        (V3, "SNMPv3", "green"),
    ]


class AuthProtocolChoices(ChoiceSet):
    NONE = "none"
    MD5 = "md5"
    SHA = "sha"
    SHA224 = "sha224"
    SHA256 = "sha256"
    SHA384 = "sha384"
    SHA512 = "sha512"

    CHOICES = [
        (NONE, "None"),
        (MD5, "MD5"),
        (SHA, "SHA"),
        (SHA224, "SHA-224"),
        (SHA256, "SHA-256"),
        (SHA384, "SHA-384"),
        (SHA512, "SHA-512"),
    ]


class PrivProtocolChoices(ChoiceSet):
    NONE = "none"
    DES = "des"
    AES = "aes"
    AES128 = "aes128"
    AES192 = "aes192"
    AES256 = "aes256"

    CHOICES = [
        (NONE, "None"),
        (DES, "DES"),
        (AES, "AES (128)"),
        (AES128, "AES-128"),
        (AES192, "AES-192"),
        (AES256, "AES-256"),
    ]


class SyncModeChoices(ChoiceSet):
    COMPARE = "compare"
    DRY_RUN = "dry-run"
    APPLY = "apply"

    CHOICES = [
        (COMPARE, "Compare", "cyan"),
        (DRY_RUN, "Dry-run", "orange"),
        (APPLY, "Apply", "green"),
    ]


class SyncTriggerChoices(ChoiceSet):
    MANUAL = "manual"
    SCHEDULED = "scheduled"

    CHOICES = [
        (MANUAL, "Manual", "blue"),
        (SCHEDULED, "Scheduled", "purple"),
    ]


class SyncStatusChoices(ChoiceSet):
    OK = "ok"
    FAILED = "failed"

    CHOICES = [
        (OK, "OK", "green"),
        (FAILED, "Failed", "red"),
    ]


class VlanSubinterfaceInferenceChoices(ChoiceSet):
    AUTO = "auto"
    ENABLED = "enabled"
    DISABLED = "disabled"

    CHOICES = [
        (AUTO, "Auto", "blue"),
        (ENABLED, "Enabled", "green"),
        (DISABLED, "Disabled", "red"),
    ]
