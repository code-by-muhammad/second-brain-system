"""Read YAML metadata without mistaking quoted evidence for a closing fence."""
import yaml


# Count aggregate mapping visits and copied pair slots, including overridden
# merge entries. Raw text size and the final dict size do not bound this work.
_YAML_WORK_LIMIT = 100_000
_YAML_INT_WORK_LIMIT = 8_000_000


class _BoundedSafeLoader(yaml.SafeLoader):
    def __init__(self, stream):
        super().__init__(stream)
        self._mapping_work = 0
        self._integer_work = 0

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
