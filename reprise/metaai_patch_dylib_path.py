#!/usr/bin/env python3
"""Réécrit en place le chemin d'une dépendance LC_LOAD_DYLIB d'un Mach-O
(tranche arm64 d'un FAT ou binaire thin), sans changer la taille du fichier :
le nouveau chemin est écrit sur l'ancien, complété de NULs (la longueur de
chaîne est lue jusqu'au premier NUL, le rembourrage est ignoré par dyld).
Le binaire doit être re-signé après le patch."""
import struct
import sys
from pathlib import Path

FAT_MAGIC = 0xCAFEBABE
CPU_ARM64 = 0x0100000C
LC_LOAD_DYLIB = 0x0C
LC_LOAD_WEAK_DYLIB = 0x18
LC_REEXPORT_DYLIB = 0x1F


def find_arm64_slice(data: bytes) -> int:
    if struct.unpack_from(">I", data, 0)[0] != FAT_MAGIC:
        return 0
    nfat = struct.unpack_from(">I", data, 4)[0]
    for i in range(nfat):
        cputype, _, off, _, _ = struct.unpack_from(">IIIII", data, 8 + i * 20)
        if cputype == CPU_ARM64:
            return off
    raise SystemExit("pas de tranche arm64")


def patch_path(binary: Path, old_prefix: str, new_path: str) -> dict:
    data = bytearray(binary.read_bytes())
    base = find_arm64_slice(data)
    magic, _, _, filetype, ncmds, sizeofcmds, _, _ = struct.unpack_from("<IiIIIIII", data, base)
    if magic != 0xfeedfacf or filetype != 2:
        raise SystemExit(f"tranche invalide (magic={magic:#x} type={filetype})")
    off = base + 32
    hits = []
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, off)
        if cmd in (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB):
            name_off = struct.unpack_from("<I", data, off + 8)[0]
            start = off + name_off
            end = data.find(b"\x00", start, off + cmdsize)
            name = data[start:end].decode("utf-8", "replace")
            if name.startswith(old_prefix):
                new_bytes = new_path.encode("utf-8") + b"\x00"
                budget = end - start
                if len(new_bytes) > budget:
                    raise SystemExit(f"chemin trop long: {len(new_bytes)} > {budget}")
                data[start:start + budget] = new_bytes + b"\x00" * (budget - len(new_bytes))
                hits.append(name)
        off += cmdsize
    if not hits:
        raise SystemExit(f"aucune dépendance trouvée pour le préfixe {old_prefix!r}")
    tmp = binary.with_suffix(binary.suffix + ".patched")
    tmp.write_bytes(bytes(data))
    tmp.replace(binary)
    return {"remplaces": hits, "nouveau_chemin": new_path, "tranche_arm64": base}


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: patch_dylib_path.py <binaire> <préfixe-ancien-chemin> <nouveau-chemin>")
    binary = Path(sys.argv[1]).resolve()
    result = patch_path(binary, sys.argv[2], sys.argv[3])
    print(f"patch OK: {result}")


if __name__ == "__main__":
    main()
