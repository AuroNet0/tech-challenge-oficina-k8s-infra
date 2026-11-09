from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[2]
entry = zipfile.ZipInfo('recovery.py', (2026, 11, 8, 0, 0, 0))
entry.create_system = 3
entry.external_attr = 0o100644 << 16
entry.compress_type = zipfile.ZIP_DEFLATED
with zipfile.ZipFile(root / 'phase4/bootstrap/identities/endpoint-recovery.zip', 'w') as archive:
    archive.writestr(entry, (root / 'scripts/eks-endpoint-recovery/recovery.py').read_bytes().replace(b'\r\n', b'\n'))
