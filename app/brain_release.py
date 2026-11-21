"""Bounded release app inventories and sealed deployment file images."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import tempfile

REPOSITORY = 'code-by-muhammad/second-brain-system'
APP_LIMIT = 64 * 1024 * 1024
IMAGE_LIMIT = 8 * 1024 * 1024
APP_ENTRIES = 2048


def digest(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def json_object(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate release metadata key.')
            value[key] = item
        return value

    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError('Release metadata must be an object.')
    return value


def _plain_path(path):
    if any(part.is_symlink() or part.is_junction() for part in (path, *path.parents)):
        raise ValueError('Linked release or deployment path is refused.')
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('Noncanonical release or deployment path is refused.')


@contextmanager
def _parent_handle(path, *, create=False):
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Noncanonical release or deployment path is refused.')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for name in path.parts[1:-1]:
            try:
                child = os.open(name, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(name, mode=0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
                child = os.open(name, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _path_identity(info):
    if os.name == 'nt':
        # Windows pathname stat uses creation time for ctime and derives execute bits from the filename.
        return (info.st_dev, info.st_ino, info.st_mode & ~0o111, info.st_size, info.st_mtime_ns, info.st_birthtime_ns)
    return _identity(info)


def _read_image(path, limit, *, parent=None):
    name = path if parent is None else path.name
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    try:
        initial = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return (None, None), None
    if not stat.S_ISREG(initial.st_mode) or initial.st_size > limit:
        raise ValueError('Release or deployment file is special or exceeds its bound.')
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
    except FileNotFoundError as error:
        raise ValueError('Release or deployment file disappeared while opening.') from error
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if _path_identity(initial) != _path_identity(opened):
            raise ValueError('Release or deployment file changed while opening.')
        raw = stream.read(limit + 1)
        final = os.fstat(stream.fileno())
        try:
            observed = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError as error:
            raise ValueError('Release or deployment file disappeared while reading.') from error
        if (len(raw) > limit or len(raw) != final.st_size or _identity(opened) != _identity(final)
                or _path_identity(final) != _path_identity(observed)):
            raise ValueError('Release or deployment file changed while reading.')
    return (raw, stat.S_IMODE(final.st_mode)), final


def file_image(path, limit=IMAGE_LIMIT):
    path = Path(path)
    _plain_path(path)
    if os.name == 'posix':
        try:
            with _parent_handle(path) as parent:
                return _read_image(path, limit, parent=parent)[0]
        except FileNotFoundError:
            return None, None
    return _read_image(path, limit)[0]


def _app_name(name):
    if (not isinstance(name, str) or len(name) > 512 or '\\' in name or ':' in name
            or any(part in ('', '.', '..', '__pycache__') for part in name.split('/'))
            or PurePosixPath(name).is_absolute()):
        raise ValueError('Invalid release app path.')
    return os.path.normcase(name)


def app_map(payload):
    if not isinstance(payload, dict) or not 0 < len(payload) <= APP_ENTRIES:
        raise ValueError('A complete nonempty app hash map is required.')
    names = set()
    for name, sha in payload.items():
        normalized = _app_name(name)
        if normalized in names or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
            raise ValueError('Invalid or duplicate release app hash.')
        names.add(normalized)
    return dict(payload)


def manifest_app(manifest, version):
    if manifest.get('version') != version or not isinstance(manifest.get('sha256'), dict):
        raise ValueError('Release manifest does not match the installed version.')
    return app_map({name.removeprefix('app/'): sha for name, sha in manifest['sha256'].items()
                    if isinstance(name, str) and name.startswith('app/')})


def _cache_file(name):
    return re.fullmatch(r'[\w.-]+\.[a-z]+-\d+(?:\.opt-[012])?\.pyc', name) is not None


def inventory(app, *, copy_to=None, allow_cache=True):
    """Hash every regular payload file and optionally copy those same checked bytes."""
    app = Path(app)
    _plain_path(app)
    if not stat.S_ISDIR(app.lstat().st_mode):
        raise ValueError('Installed app directory is missing or invalid.')
    if copy_to is not None:
        copy_to = Path(copy_to)
        _plain_path(copy_to)
        copy_to.mkdir(mode=0o700)
    count = total = 0
    paths = [(app, False)]
    hashes = {}
    directories = set()
    while paths:
        directory, cache = paths.pop()
        _plain_path(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > APP_ENTRIES:
                    raise ValueError('Installed app inventory exceeds its bound.')
                path = Path(entry.path)
                _plain_path(path)
                mode = entry.stat(follow_symlinks=False).st_mode
                name = path.relative_to(app).as_posix()
                if stat.S_ISDIR(mode) and not cache:
                    if entry.name == '__pycache__' and allow_cache:
                        paths.append((path, True))
                    else:
                        _app_name(name)
                        directories.add(name)
                        paths.append((path, False))
                elif stat.S_ISREG(mode):
                    if cache:
                        if not _cache_file(entry.name):
                            raise ValueError('Unexpected file in installed app cache.')
                        total += entry.stat(follow_symlinks=False).st_size
                        if total > APP_LIMIT:
                            raise ValueError('Installed app bytes exceed their bound.')
                        continue
                    _app_name(name)
                    raw, source_mode = file_image(path, APP_LIMIT - total)
                    if raw is None:
                        raise ValueError('Installed app changed during inventory.')
                    total += len(raw)
                    hashes[name] = digest(raw)
                    if copy_to is not None:
                        target = copy_to / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with target.open('xb') as stream:
                            stream.write(raw)
                            stream.flush()
                            os.fsync(stream.fileno())
                        target.chmod(source_mode)
                        sync_directory(target.parent)
                else:
                    raise ValueError('Special or nested cache payload in installed app.')
    populated = {parent.as_posix() for name in hashes for parent in PurePosixPath(name).parents if parent.parts}
    if directories != populated:
        raise ValueError('App contains unlisted empty payload directories.')
    return app_map(hashes)


def verify_installed(root, checkout, version):
    raw, _ = file_image(Path(checkout) / 'release-manifest.json')
    expected = manifest_app(json_object(raw), version)
    actual = inventory(Path(root) / 'app')
    if actual != expected:
        raise ValueError('Installed app does not match the complete release manifest.')
    return actual


def catalog(raw):
    value = json_object(raw)
    if (set(value) != {'format', 'repository', 'builds'} or type(value['format']) is not int
            or value['format'] != 1 or value['repository'] != REPOSITORY
            or not isinstance(value['builds'], list) or not 0 < len(value['builds']) <= 64):
        raise ValueError('Unsupported predecessor catalog identity.')
    seen_commits = set()
    maps = set()
    for row in value['builds']:
        if (not isinstance(row, dict) or set(row) != {'version', 'app_sha256', 'provenance'}
                or not isinstance(row['version'], str) or not re.fullmatch(r'\d+\.\d+\.\d+', row['version'])
                or not isinstance(row['provenance'], list) or not 0 < len(row['provenance']) <= 64):
            raise ValueError('Invalid predecessor catalog build.')
        identity = (row['version'], tuple(sorted(app_map(row['app_sha256']).items())))
        if identity in maps:
            raise ValueError('Duplicate predecessor catalog app map.')
        maps.add(identity)
        for pair in row['provenance']:
            if (not isinstance(pair, dict) or set(pair) != {'commit', 'manifest_sha256'}
                    or not isinstance(pair['commit'], str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', pair['commit'])
                    or not isinstance(pair['manifest_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', pair['manifest_sha256'])
                    or pair['commit'] in seen_commits):
                raise ValueError('Invalid or duplicate predecessor catalog provenance.')
            seen_commits.add(pair['commit'])
    return value['builds']


def sync_directory(directory):
    if os.name == 'nt':
        return
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _same_parent(path, parent):
    with _parent_handle(path) as current:
        if not os.path.samestat(os.fstat(parent), os.fstat(current)):
            raise ValueError('Deployment directory changed during publication: ' + str(path))


def _write_posix_image(path, image, expected):
    data, mode = image
    if image == expected == (None, None):
        if file_image(path) != expected:
            raise ValueError('Deployment artifact appeared outside this transaction: ' + str(path))
        return
    with _parent_handle(path, create=data is not None) as parent:
        before, info = _read_image(path, IMAGE_LIMIT, parent=parent)
        if before != expected:
            raise ValueError('Deployment artifact changed outside this transaction: ' + str(path))
        if image == expected:
            return
        temporary = '.' + path.name + '-' + secrets.token_hex(8)
        staged = False
        try:
            if data is not None:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=parent)
                staged = True
                with os.fdopen(descriptor, 'wb') as stream:
                    os.fchmod(stream.fileno(), mode)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            _same_parent(path, parent)
            observed, current = _read_image(path, IMAGE_LIMIT, parent=parent)
            if observed != expected or (info is not None and (current is None or _identity(info) != _identity(current))):
                raise ValueError('Deployment artifact changed before publication: ' + str(path))
            if data is None:
                os.unlink(path.name, dir_fd=parent)
            elif info is None:
                os.link(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            else:
                os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
            _same_parent(path, parent)
            if _read_image(path, IMAGE_LIMIT, parent=parent)[0] != image:
                raise ValueError('Deployment artifact publication was not verified: ' + str(path))
        finally:
            if staged:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass


def write_image(path, image, expected, *, protect=None):
    """Publish an exact file or its absence against the sealed bytes and mode."""
    path = Path(path)
    _plain_path(path)
    if os.name == 'posix':
        _write_posix_image(path, image, expected)
        return
    if file_image(path) != expected:
        raise ValueError('Deployment artifact changed outside this transaction: ' + str(path))
    if image == expected:
        return
    data, mode = image
    if data is None:
        path.unlink()
    else:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
        stage = Path(name)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                if protect is not None:
                    protect(stage)
                stage.chmod(mode)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if file_image(path) != expected:
                raise ValueError('Deployment artifact changed before publication: ' + str(path))
            if expected[0] is not None:
                os.replace(stage, path)
            else:
                os.rename(stage, path)
        finally:
            stage.unlink(missing_ok=True)
    sync_directory(path.parent)
    if file_image(path) != image:
        raise ValueError('Deployment artifact publication was not verified: ' + str(path))


def artifact_rows(target, snapshot):
    """Load the immutable before/after files referenced by a format-3 snapshot."""
    deployment = snapshot.get('deployment')
    if deployment is None:
        return []
    root = Path(snapshot['root'])
    fixed = {'device': root/'device.json', 'launcher': root/'bin/second-brain-mcp',
             'auth-client': root/'state/client-access.json', 'auth-server': root/'state/access.json',
             'config': root/'state/config.json', 'ignore': root/'state/.bmignore'}
    if (set(deployment) != {'hold_id', 'service_policy', 'target_app_sha256', 'artifacts'}
            or deployment['service_policy'] not in ('registered', 'first', 'absent')
            or not re.fullmatch('[0-9a-f]{32}', deployment['hold_id'])
            or not isinstance(deployment['artifacts'], list) or len(deployment['artifacts']) > 12):
        raise ValueError('Sealed deployment identity is invalid.')
    result = []
    seen = set()
    for index, row in enumerate(deployment['artifacts']):
        if set(row) != {'kind', 'phase', 'path', 'before', 'after'}:
            raise ValueError('Sealed deployment artifact is invalid.')
        path = Path(row['path'])
        _plain_path(path)
        if path in seen:
            raise ValueError('Duplicate sealed deployment destination.')
        seen.add(path)
        if row['phase'] == 'deploy':
            if fixed.get(row['kind']) != path:
                raise ValueError('Sealed deployment destination is invalid.')
        elif row['phase'] != 'native' or row['kind'] not in ('unit', 'activation', 'hook'):
            raise ValueError('Sealed native deployment artifact is invalid.')
        images = []
        for phase in ('before', 'after'):
            record = row[phase]
            if set(record) != {'sha256', 'mode'} or (record['mode'] is None) != (record['sha256'] is None):
                raise ValueError('Sealed file image identity is invalid.')
            raw, _ = file_image(Path(target)/('artifact-'+str(index)+'.'+phase))
            if (digest(raw) != record['sha256']
                    or raw is not None and (type(record['mode']) is not int or not 0 <= record['mode'] <= 0o7777)):
                raise ValueError('Sealed deployment image changed.')
            images.append((raw, record['mode']))
        result.append((row, *images))
    return result
