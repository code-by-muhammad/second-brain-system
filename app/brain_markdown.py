"""Read YAML metadata without mistaking quoted evidence for a closing fence."""
import yaml


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
        value = yaml.safe_load(''.join(lines[1:end]))
    except (yaml.YAMLError, ValueError):
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
