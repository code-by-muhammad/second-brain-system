"""Read YAML metadata without mistaking quoted evidence for a closing fence."""
import yaml
import json,os,stat
from datetime import date,datetime

def bounded_file_bytes(path,limit,limit_error='File exceeds its byte limit.'):
    info=path.stat()
    if not stat.S_ISREG(info.st_mode):raise ValueError('Non-regular file is refused.')
    if info.st_size>limit:raise ValueError(limit_error)
    if os.name=='posix':
        descriptor=os.open(path,os.O_RDONLY|os.O_NONBLOCK|getattr(os,'O_NOFOLLOW',0))
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):raise ValueError('Non-regular file is refused.')
            with os.fdopen(descriptor,'rb') as stream:
                descriptor=None;raw=stream.read(limit+1)
        finally:
            if descriptor is not None:os.close(descriptor)
    else:
        with path.open('rb') as stream:raw=stream.read(limit+1)
    if len(raw)>limit:raise ValueError(limit_error)
    return raw

def bounded_json_file(path,limit):
    if any(item.is_symlink() or item.is_junction() for item in (path,*path.parents)):
        raise ValueError('Linked cache is refused.')
    return json.loads(bounded_file_bytes(path,limit))

def bounded_date_label(value):
    """Metadata can be an aliased container: never ask it for a representation."""
    kind=type(value)
    if kind is str:return value[:10]
    if kind in (date,datetime):return value.isoformat()[:10]
    if kind is int:return str(value)[:10] if value.bit_length()<=256 else 'unknown'
    if kind in (bool,float) or value is None:return str(value)[:10]
    return 'unknown'

def bounded_capture_datetime(value):
    if type(value) is datetime:return value
    if type(value) is date:return datetime.combine(value,datetime.min.time())
    if type(value) is str and len(value)<=200:return datetime.fromisoformat(value)
    raise ValueError('Invalid capture date.')


# Count aggregate mapping visits and copied pair slots, including overridden
# merge entries. Raw text size and the final dict size do not bound this work.
_YAML_NODE_LIMIT = 100_000
_YAML_DEPTH_LIMIT = 128
_YAML_WORK_LIMIT = 100_000
_YAML_INT_WORK_LIMIT = 8_000_000


class _BoundedSafeLoader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self._mapping_work = 0
        self._integer_work = 0
        self._node_work = 0
        self._compose_depth = 0

    def compose_node(self,parent,index):
        if self._node_work>=_YAML_NODE_LIMIT or self._compose_depth>_YAML_DEPTH_LIMIT:
            raise yaml.YAMLError('YAML composition exceeds its node/depth budget.')
        self._node_work+=1;self._compose_depth+=1
        try:return super().compose_node(parent,index)
        finally:self._compose_depth-=1

    def _reserve(self, amount):
        if amount > _YAML_WORK_LIMIT - self._mapping_work:
            raise yaml.YAMLError('YAML mapping construction exceeds its work limit.')
        self._mapping_work += amount

    def flatten_mapping(self, node):
        # Preserve pinned PyYAML 6.0.3 merge ordering and node mutations. Reserve
        # before its scans, list shifts and allocations, rather than inspecting
        # the much smaller constructed dict after duplicate keys collapse.
        # Adapted library algorithm: see THIRD-PARTY-NOTICES.md.
        merge = []
        index = 0
        while index < len(node.value):
            self._reserve(1)
            key_node, value_node = node.value[index]
            if key_node.tag == 'tag:yaml.org,2002:merge':
                self._reserve(len(node.value) - index - 1)
                del node.value[index]
                if isinstance(value_node, yaml.MappingNode):
                    self.flatten_mapping(value_node)
                    self._reserve(len(value_node.value))
                    merge.extend(value_node.value)
                elif isinstance(value_node, yaml.SequenceNode):
                    submerge = []
                    self._reserve(len(value_node.value))
                    for subnode in value_node.value:
                        if not isinstance(subnode, yaml.MappingNode):
                            raise yaml.constructor.ConstructorError(
                                'while constructing a mapping', node.start_mark,
                                'expected a mapping for merging, but found %s'
                                % subnode.id, subnode.start_mark)
                        self.flatten_mapping(subnode)
                        self._reserve(1)
                        submerge.append(subnode.value)
                    self._reserve(len(submerge))
                    submerge.reverse()
                    for value in submerge:
                        self._reserve(len(value))
                        merge.extend(value)
                else:
                    raise yaml.constructor.ConstructorError(
                        'while constructing a mapping', node.start_mark,
                        'expected a mapping or list of mappings for merging, but found %s'
                        % value_node.id, value_node.start_mark)
            elif key_node.tag == 'tag:yaml.org,2002:value':
                key_node.tag = 'tag:yaml.org,2002:str'
                index += 1
            else:
                index += 1
        if merge:
            self._reserve(len(merge) + len(node.value))
            node.value = merge + node.value

    def construct_mapping(self, node, deep=False):
        if isinstance(node, yaml.MappingNode):
            self.flatten_mapping(node)
            # Reserve the original pair loop and the final dict/set population
            # before either runs; duplicate entries can still cost one visit.
            self._reserve(2 * len(node.value))
        return yaml.constructor.BaseConstructor.construct_mapping(self, node, deep=deep)

    def construct_yaml_int(self, node):
        # SafeLoader's sexagesimal loop grows big integers before later source
        # screening. Reserve a conservative conversion/output estimate before
        # forwarding it; repeated aliases reuse the loader's constructed object.
        raw = self.construct_scalar(node)
        size = len(raw)
        amount = (size * size if ':' in raw else size) + 2 * size + 4
        if amount > _YAML_INT_WORK_LIMIT - self._integer_work:
            raise yaml.YAMLError('YAML integer construction exceeds its work limit.')
        self._integer_work += amount
        return yaml.constructor.SafeConstructor.construct_yaml_int(self, node)


    def construct_yaml_float(self, node):
        # Pinned SafeLoader can overflow while converting a short sexagesimal
        # float. Treat only that known constructor fault as invalid YAML; keep
        # unrelated and fatal exceptions visible to callers.
        try:
            return yaml.constructor.SafeConstructor.construct_yaml_float(self, node)
        except OverflowError:
            raise yaml.YAMLError('Invalid YAML float.') from None


# Copy only this subclass's dispatch table; ordinary PyYAML consumers retain
# their original constructors and alias caching remains in BaseConstructor.
_BoundedSafeLoader.add_constructor('tag:yaml.org,2002:int', _BoundedSafeLoader.construct_yaml_int)
_BoundedSafeLoader.add_constructor('tag:yaml.org,2002:float', _BoundedSafeLoader.construct_yaml_float)


def frontmatter(text: str, *, strict: bool = False) -> dict:
    lines = text.removeprefix('\ufeff').replace('\r\n', '\n').replace('\r', '\n').splitlines(keepends=True)
    if not lines or lines[0].rstrip('\n \t') != '---':
        return {}
    end = next((i for i, line in enumerate(lines[1:], 1)
                if line.rstrip('\n \t') == '---'), None)
    if end is None:
        if strict:
            raise ValueError('Unterminated YAML frontmatter.')
        return {}
    try:
        value = yaml.load(''.join(lines[1:end]), Loader=_BoundedSafeLoader)
    # Pinned SafeLoader constructors may raise these on malformed explicit
    # scalar tags. Keep containment local; do not swallow unrelated/fatal errors.
    except (yaml.YAMLError, ValueError, TypeError, RecursionError, KeyError, AttributeError, IndexError):
        if strict:
            raise ValueError('Invalid YAML frontmatter.') from None
        return {}
    if value is None:
        return {}
    if not isinstance(value, dict):
        if strict:
            raise ValueError('YAML frontmatter must be a mapping.')
        return {}
    return value
