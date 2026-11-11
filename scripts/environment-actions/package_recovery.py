from pathlib import Path
import io
import zipfile

def package_bytes():
    root = Path(__file__).resolve().parents[2]
    entry = zipfile.ZipInfo('recovery.py', (2026, 11, 8, 0, 0, 0))
    entry.create_system = 3
    entry.external_attr = 0o100644 << 16
    entry.compress_type = zipfile.ZIP_DEFLATED
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr(entry, (root / 'scripts/eks-endpoint-recovery/recovery.py').read_bytes().replace(b'\r\n', b'\n'))
    return output.getvalue()


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[2]
    (root / 'phase4/bootstrap/identities/endpoint-recovery.zip').write_bytes(package_bytes())
