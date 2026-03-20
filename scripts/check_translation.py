"""
check_translation.py — Generic HA integration translation checker
=================================================================
Automatically detects the integration domain and checks that all
translation_key values found in entity Python files are present in
every translation JSON file (and strings.json), and vice-versa.

Also verifies that state options defined in code match those present
in each translation file.

Usage:
    python scripts/check_translation.py

Expected project layout:
    <repo_root>/
        scripts/
            check_translation.py    ← this file
        custom_components/
            <domain>/
                manifest.json       ← used to detect the domain
                const.py
                strings.json
                translations/
                    en.json
                    fr.json
                    ...
                <entity_domain>.py  ← sensor.py, switch.py, select.py …
"""

import json
import os
import re
import sys

from colorama import Fore, Style

# ---------------------------------------------------------------------------
# Path resolution — fully automatic, no hardcoded domain names
# ---------------------------------------------------------------------------

script_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.dirname(script_dir)
custom_components_dir = os.path.join(repo_root, "custom_components")


# Detect the domain from the single folder inside custom_components/
# (or from manifest.json if several folders exist)
def _find_integration_path() -> tuple[str, str]:
    """Return (domain, absolute_path) for the integration under custom_components/."""
    candidates = [
        d
        for d in os.listdir(custom_components_dir)
        if os.path.isdir(os.path.join(custom_components_dir, d))
        and not d.startswith(".")
    ]

    if len(candidates) == 1:
        domain = candidates[0]
        return domain, os.path.join(custom_components_dir, domain)

    # Multiple folders — pick the one whose manifest.json domain matches the folder name
    for candidate in candidates:
        manifest_path = os.path.join(custom_components_dir, candidate, "manifest.json")
        if os.path.isfile(manifest_path):
            with open(manifest_path) as f:
                manifest = json.load(f)
            if manifest.get("domain") == candidate:
                return candidate, os.path.join(custom_components_dir, candidate)

    print(Fore.RED + "ERROR: Could not detect integration domain." + Style.RESET_ALL)
    print("Make sure custom_components/<domain>/manifest.json exists.")
    sys.exit(1)


domain, base_path = _find_integration_path()
print(f"Checking translations for domain: {Fore.CYAN}{domain}{Style.RESET_ALL}\n")

const_file: str = os.path.join(base_path, "const.py")
translations_path: str = os.path.join(base_path, "translations")
strings_file: str = os.path.join(base_path, "strings.json")

# ---------------------------------------------------------------------------
# Detect entity domains (Platform.SWITCH, Platform.SENSOR, …) from const.py
# ---------------------------------------------------------------------------

entity_domains: list[str] = []
keys_in_code: list[str] = []
entity_options: dict[str, list[str]] = {}  # {domain.key: [options]}

if os.path.isfile(const_file):
    with open(const_file) as f:
        content = f.read()
    entity_domains = sorted(
        set(
            x.replace("Platform.", "").lower()
            for x in re.findall(r"Platform\.[A-Z_]+", content)
        )
    )

# Also detect entity domains from Python files present in the integration folder
# (catches integrations that don't declare Platform in const.py)
known_ha_domains = {
    "sensor",
    "switch",
    "select",
    "button",
    "number",
    "binary_sensor",
    "light",
    "climate",
    "cover",
    "fan",
    "lock",
    "media_player",
    "text",
    "time",
    "date",
    "event",
    "image",
}
for fname in os.listdir(base_path):
    if fname.endswith(".py"):
        stem = fname[:-3]
        if stem in known_ha_domains and stem not in entity_domains:
            entity_domains.append(stem)
entity_domains = sorted(set(entity_domains))

# ---------------------------------------------------------------------------
# Load all translation files
# ---------------------------------------------------------------------------

langs: list[dict] = []

if os.path.isfile(strings_file):
    with open(strings_file) as f:
        langs.append(
            {
                "lang": "strings",
                "data": json.load(f),
                "translations_keys": [],
                "state_keys": {},
            }
        )

if os.path.isdir(translations_path):
    for file in sorted(os.listdir(translations_path)):
        if file.endswith(".json"):
            with open(os.path.join(translations_path, file)) as f:
                langs.append(
                    {
                        "lang": file[:-5],  # strip .json
                        "data": json.load(f),
                        "translations_keys": [],
                        "state_keys": {},
                    }
                )

if not langs:
    print(Fore.RED + "ERROR: No translation files found." + Style.RESET_ALL)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Extract translation_key values and state options from entity Python files
# ---------------------------------------------------------------------------

# Build a pattern that matches any EntityDescription class name dynamically
# (works regardless of the integration's specific class names)
ENTITY_BLOCK_SPLIT_PATTERN = re.compile(r"\n\s*\w+EntityDescription\s*\(")

for entity_domain in entity_domains:
    entity_file = os.path.join(base_path, f"{entity_domain}.py")
    if not os.path.isfile(entity_file):
        continue

    with open(entity_file) as f:
        content = f.read()

    # Collect all translation_key="..." values
    translation_keys = [
        f"{entity_domain}.{m}"
        for m in re.findall(r'translation_key\s*=\s*"([a-zA-Z0-9_\-]+)"', content)
    ]
    keys_in_code.extend(translation_keys)

    # Extract per-entity-block options
    blocks = ENTITY_BLOCK_SPLIT_PATTERN.split(content)
    for block in blocks:
        trans_key_match = re.search(r'translation_key\s*=\s*"([a-zA-Z0-9_\-]+)"', block)
        if not trans_key_match:
            continue

        trans_key = trans_key_match.group(1)
        options_match = re.search(r"options\s*=\s*\[(.*?)\]", block, re.DOTALL)
        if options_match:
            options = re.findall(r'"([a-zA-Z0-9_\-]+)"', options_match.group(1))
            if options:
                full_key = f"{entity_domain}.{trans_key}"
                entity_options[full_key] = options

    # Extract translation keys from all lang files for this entity domain
    for lang in langs:
        entity_data = lang["data"].get("entity", {}).get(entity_domain, {})
        for key, value in entity_data.items():
            lang_key = f"{entity_domain}.{key}"
            lang["translations_keys"].append(lang_key)

            # Collect state option keys
            if isinstance(value, dict) and "state" in value:
                lang["state_keys"][lang_key] = list(value["state"].keys())

keys_in_code = sorted(set(keys_in_code))

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

all_good: bool = True

for lang in langs:
    prefix = "translations/" if lang["lang"] != "strings" else ""
    label = f"{Fore.CYAN}{prefix}{lang['lang']}.json{Style.RESET_ALL}"

    # Missing keys (in code but not in translation)
    needed = sorted(set(keys_in_code) - set(lang["translations_keys"]))
    # Obsolete keys (in translation but no longer in code)
    no_more_needed = sorted(set(lang["translations_keys"]) - set(keys_in_code))

    if needed:
        all_good = False
        print(f"[{len(needed)}] Entity keys needed in {label}")
        for key in needed:
            print(f"  -> {Fore.RED}{key}{Style.RESET_ALL}")
        print()

    if no_more_needed:
        all_good = False
        print(f"[{len(no_more_needed)}] Entity keys no longer needed in {label}")
        for key in no_more_needed:
            print(f"  -> {Fore.YELLOW}{key}{Style.RESET_ALL}")
        print()

    # State options consistency
    for entity_key, code_options in entity_options.items():
        translated_options = lang["state_keys"].get(entity_key, [])

        missing_options = sorted(set(code_options) - set(translated_options))
        extra_options = sorted(set(translated_options) - set(code_options))

        if missing_options:
            all_good = False
            print(
                f"[{len(missing_options)}] State options needed for "
                f"{Fore.CYAN}{entity_key}{Style.RESET_ALL} in {label}"
            )
            for opt in missing_options:
                print(f"  -> {Fore.RED}{opt}{Style.RESET_ALL}")
            print()

        if extra_options:
            all_good = False
            print(
                f"[{len(extra_options)}] State options no longer needed for "
                f"{Fore.CYAN}{entity_key}{Style.RESET_ALL} in {label}"
            )
            for opt in extra_options:
                print(f"  -> {Fore.YELLOW}{opt}{Style.RESET_ALL}")
            print()

if all_good:
    print("All " + Fore.GREEN + "good" + Style.RESET_ALL + ", no modifications needed")
else:
    sys.exit(1)
