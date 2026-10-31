#!/usr/bin/env python3
"""Rebuild PlaceTogether.rbxmx from plugin_source.luau.

The plugin ships as a single Roblox model file (.rbxmx) that embeds the Luau
source in a CDATA block. Keeping both files by hand meant they silently drifted
apart: fixing plugin_source.luau did not change what users actually installed.

Usage:
    python build.py            # rebuild PlaceTogether.rbxmx
    python build.py --check    # exit 1 if the .rbxmx is out of date (CI)
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "plugin_source.luau")
TARGET = os.path.join(HERE, "PlaceTogether.rbxmx")

TEMPLATE = """<roblox xmlns:xmime="http://www.w3.org/2005/05/xmlmime" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://www.roblox.com/roblox.xsd" version="4">
\t<External>null</External>
\t<External>nil</External>
\t<Item class="Script" referent="RBXBB3C8BCE355E44D3ABE8F155BAB255D4">
\t\t<Properties>
\t\t\t<ProtectedString name="Source"><![CDATA[{source}]]></ProtectedString>
\t\t\t<bool name="Disabled">false</bool>
\t\t\t<Content name="LinkedSource"><null></null></Content>
\t\t\t<token name="RunContext">0</token>
\t\t\t<string name="ScriptGuid">{{3E59CB32-C0C0-48B9-ADAF-D9C79E34A31A}}</string>
\t\t\t<BinaryString name="AttributesSerialize"></BinaryString>
\t\t\t<SecurityCapabilities name="Capabilities">0</SecurityCapabilities>
\t\t\t<bool name="DefinesCapabilities">false</bool>
\t\t\t<string name="Name">PlaceTogether</string>
\t\t\t<BinaryString name="SerializedOverrides"></BinaryString>
\t\t\t<int64 name="SourceAssetId">-1</int64>
\t\t\t<SharedString name="Tags">yuZpQdnvvUBOTYh1jqZ2cA==</SharedString>
\t\t</Properties>
\t</Item>
\t<SharedStrings>
\t\t<SharedString md5="yuZpQdnvvUBOTYh1jqZ2cA=="></SharedString>
\t</SharedStrings>
</roblox>


"""


def read_source():
    with open(SOURCE, "r", encoding="utf-8", newline="") as f:
        # Normalize so the built artifact is byte-identical on every platform.
        return f.read().replace("\r\n", "\n").replace("\r", "\n")


def render(source):
    # A literal "]]>" would terminate the CDATA section early. Luau has no use
    # for it, but split it just in case so the build never emits broken XML.
    safe = source.replace("]]>", "]]]]><![CDATA[>")
    return TEMPLATE.format(source=safe)


def main():
    parser = argparse.ArgumentParser(description="Build PlaceTogether.rbxmx from plugin_source.luau")
    parser.add_argument("--check", action="store_true", help="verify the .rbxmx is up to date without writing")
    args = parser.parse_args()

    if not os.path.exists(SOURCE):
        print(f"[ERROR] Missing {SOURCE}", file=sys.stderr)
        return 1

    source = read_source()
    rendered = render(source)

    if args.check:
        if not os.path.exists(TARGET):
            print("[ERROR] PlaceTogether.rbxmx is missing; run: python build.py", file=sys.stderr)
            return 1
        with open(TARGET, "r", encoding="utf-8", newline="") as f:
            current = f.read().replace("\r\n", "\n")
        if current != rendered:
            print("[ERROR] PlaceTogether.rbxmx is out of date; run: python build.py", file=sys.stderr)
            return 1
        print("[OK] PlaceTogether.rbxmx matches plugin_source.luau")
        return 0

    with open(TARGET, "w", encoding="utf-8", newline="") as f:
        f.write(rendered)
    print(f"[OK] Wrote {os.path.basename(TARGET)} ({len(rendered)} bytes) from {os.path.basename(SOURCE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
