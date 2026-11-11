"""Python distribution build backend."""

import base64
import binascii
import csv
import contextlib
import shutil
import hashlib
import io
import os
import struct
import tarfile
import tempfile
import zipfile
from pathlib import Path

from setuptools import build_meta as backend

def _product_files():
    root = Path(__file__).resolve().parent
    files = {'pyproject.toml', 'build_backend.py', 'MANIFEST.in', 'README.md', 'METHODOLOGY.md', 'NOTES.md', '.env.example', 'LICENSE', 'docs/design.md', 'docs/contract.md', 'docs/guide.md'}
    for package in ('dalel', 'locqa'):
        for path in (root / 'src' / package).rglob('*'):
            if (path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts
                    and 'demo' not in path.parts and path.name != 'demo.js'
                    and (path.suffix == '.py' or 'assets' in path.parts and path.suffix not in ('.pyc', '.pyo'))):
                files.add(path.relative_to(root).as_posix())
    files.update(path.relative_to(root).as_posix() for path in (root / 'docs/screenshots').glob('*.jpg') if path.is_file())
    files.update(path.relative_to(root).as_posix() for path in (root / 'docs/images/readme').glob('*') if path.is_file() and path.suffix in ('.jpg', '.webp', '.md'))
    return frozenset(files)


SOURCE_FILES = _product_files()
RUNTIME_FILES = frozenset(name.removeprefix('src/') for name in SOURCE_FILES if name.startswith('src/'))

get_requires_for_build_wheel = backend.get_requires_for_build_wheel
get_requires_for_build_sdist = backend.get_requires_for_build_sdist


def _metadata(content):
    header, separator, body = content.partition(b'\n\n')
    return b'\n'.join(line for line in header.split(b'\n')
                       if not line.startswith(b'Dynamic:')) + separator + body


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    filename = backend.prepare_metadata_for_build_wheel(metadata_directory, config_settings)
    directory = Path(metadata_directory) / filename
    metadata = directory / 'METADATA'
    metadata.write_bytes(_metadata(metadata.read_bytes()))
    (directory / 'top_level.txt').unlink(missing_ok=True)
    return filename


def _stored_gzip(stream, content):
    """Write fixed gzip and stored-deflate blocks without compressor state."""
    stream.write(b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff')
    for start in range(0, len(content), 65535):
        block = content[start:start + 65535]
        final = start + len(block) == len(content)
        stream.write(struct.pack('<BHH', int(final), len(block), len(block) ^ 65535))
        stream.write(block)
    if not content:
        stream.write(b'\x01\x00\x00\xff\xff')
    stream.write(struct.pack('<II', binascii.crc32(content) & 0xffffffff, len(content) & 0xffffffff))


def _temporary(destination):
    descriptor, name = tempfile.mkstemp(dir=destination.parent, prefix='.package-')
    os.close(descriptor)
    return Path(name)


def _source_archive(path):
    temporary = _temporary(path)
    try:
        with tarfile.open(path, 'r:gz') as source, temporary.open('wb') as stream:
            roots = {member.name.split('/')[0] for member in source.getmembers()}
            if len(roots) != 1:
                raise ValueError('Source distribution must have one root directory')
            root = roots.pop()
            expected = {f'{root}/{name}' for name in SOURCE_FILES | {'PKG-INFO'}}
            members = {member.name: member for member in source.getmembers() if member.name in expected}
            if (len(source.getnames()) != len(set(source.getnames())) or set(members) != expected
                    or any(not member.isfile() for member in members.values())):
                raise ValueError('Source distribution does not match the product file set')
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode='w', format=tarfile.USTAR_FORMAT) as target:
                for member in sorted(members.values(), key=lambda item: item.name):
                    clean = tarfile.TarInfo(member.name)
                    clean.mode = 0o644
                    content = source.extractfile(member).read()
                    if member.name == f'{root}/PKG-INFO':
                        content = _metadata(content)
                    clean.size = len(content)
                    target.addfile(clean, io.BytesIO(content))
            _stored_gzip(stream, archive.getvalue())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _wheel_archive(path):
    temporary = _temporary(path)
    try:
        with zipfile.ZipFile(path) as source:
            if len(source.namelist()) != len(set(source.namelist())):
                raise ValueError('Wheel contains duplicate paths')
            files = {item.filename: source.read(item) for item in source.infolist() if not item.is_dir()}
        metadata = [name for name in files if name.endswith('.dist-info/WHEEL')]
        if len(metadata) != 1:
            raise ValueError('Wheel must contain exactly one WHEEL file')
        wheel = metadata[0]
        record = wheel.removesuffix('WHEEL') + 'RECORD'
        prefix = wheel.removesuffix('WHEEL')
        files.pop(prefix + 'top_level.txt', None)
        expected = RUNTIME_FILES | {prefix + name for name in (
            'WHEEL', 'METADATA', 'RECORD', 'entry_points.txt', 'licenses/LICENSE',
        )}
        if set(files) != expected:
            raise ValueError('Wheel does not match the product file set')
        files[wheel] = ''.join(line for line in files[wheel].decode('utf-8').splitlines(keepends=True)
                               if not line.startswith('Generator:')).encode('utf-8')
        files[prefix + 'METADATA'] = _metadata(files[prefix + 'METADATA'])
        files.pop(record)
        rows = io.StringIO(newline='')
        writer = csv.writer(rows, lineterminator='\n')
        for name, content in sorted(files.items()):
            digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b'=').decode('ascii')
            writer.writerow((name, 'sha256=' + digest, len(content)))
        writer.writerow((record, '', ''))
        files[record] = rows.getvalue().encode('utf-8')
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED) as target:
            for name in sorted(files, key=lambda value: ('.dist-info/' in value, value == record, value)):
                item = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                item.create_system = 3
                item.external_attr = 0o100644 << 16
                item.compress_type = zipfile.ZIP_STORED
                target.writestr(item, files[name])
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _publish(staging, filename, destination):
    directory = Path(destination)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / filename
    temporary = _temporary(output)
    try:
        temporary.write_bytes((Path(staging) / filename).read_bytes())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def _product_tree():
    root = Path(__file__).resolve().parent
    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='dalel-source-') as directory:
        candidate = Path(directory)
        for name in SOURCE_FILES:
            target = candidate / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / name, target)
        try:
            os.chdir(candidate)
            yield
        finally:
            os.chdir(previous)


def build_sdist(sdist_directory, config_settings=None):
    with tempfile.TemporaryDirectory(prefix='.build-') as staging:
        with _product_tree():
            filename = backend.build_sdist(staging, config_settings)
        _source_archive(Path(staging) / filename)
        _publish(staging, filename, sdist_directory)
        return filename


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    metadata_directory = str(Path(metadata_directory).resolve()) if metadata_directory else None
    with tempfile.TemporaryDirectory(prefix='.build-') as staging:
        with _product_tree():
            filename = backend.build_wheel(staging, config_settings, metadata_directory)
        _wheel_archive(Path(staging) / filename)
        _publish(staging, filename, wheel_directory)
        return filename
