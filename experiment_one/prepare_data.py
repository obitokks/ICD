"""Verify the tracked public archives and extract only experiment inputs."""
import hashlib
from pathlib import Path
import shutil
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1] / 'ICD_Bitstream_Data'
ARCHIVES = {
    'ROAD': ('road.zip', '0e4fe6ed7f99b5cdabf6b772c91a2025c614a071ba8579c532904d0ef7aea5f6'),
    'DroneKit': ('dronekit.zip', 'c570e3c2104654f833e6f325f827d6ae62b76bd42ac0918a1dc6f0f91adb5769'),
}


def prepare():
    for source, (filename, expected) in ARCHIVES.items():
        archive = ROOT/'01_raw'/source/filename
        with archive.open('rb') as handle:
            if hashlib.file_digest(handle,'sha256').hexdigest()!=expected:
                raise ValueError(f'SHA-256 mismatch: {archive}. Run git lfs pull.')
        dest = (ROOT/'03_intermediate'/source).resolve()
        with ZipFile(archive) as zipped:
            for member in zipped.infolist():
                if member.is_dir() or (source=='ROAD' and not (
                    member.filename.startswith('road/') and member.filename.endswith('.log'))):
                    continue
                target = (dest/member.filename).resolve()
                if not target.is_relative_to(dest):
                    raise ValueError(f'Unsafe archive path: {member.filename}')
                if target.is_file():
                    with target.open('rb') as existing, zipped.open(member) as original:
                        if hashlib.file_digest(existing,'sha256').digest()!=hashlib.file_digest(original,'sha256').digest():
                            raise ValueError(f'Existing data differs from archive: {target}')
                    continue
                target.parent.mkdir(parents=True,exist_ok=True)
                partial = target.with_suffix(target.suffix+'.part')
                with zipped.open(member) as original, partial.open('wb') as handle:
                    shutil.copyfileobj(original,handle)
                partial.replace(target)
        print(f'{source}: verified and ready')


if __name__ == '__main__':
    prepare()
