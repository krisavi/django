"Misc. utility functions/classes for admin documentation generator."

import re
from importlib import import_module

from email.errors import HeaderParseError
from email.parser import HeaderParser

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ViewDoesNotExist
from django.urls import reverse
from django.utils import six
from django.utils.encoding import force_bytes
from django.utils.safestring import mark_safe

try:
    import docutils.core
    import docutils.nodes
    import docutils.parsers.rst.roles
except ImportError:
    docutils_is_available = False
else:
    docutils_is_available = True


def trim_docstring(docstring):
    """
    Uniformly trim leading/trailing whitespace from docstrings.

    Based on https://www.python.org/dev/peps/pep-0257/#handling-docstring-indentation
    """
    if not docstring or not docstring.strip():
        return ''
    # Convert tabs to spaces and split into lines
    lines = docstring.expandtabs().splitlines()
    indent = min(len(line) - len(line.lstrip()) for line in lines if line.lstrip())
    trimmed = [lines[0].lstrip()] + [line[indent:].rstrip() for line in lines[1:]]
    return "\n".join(trimmed).strip()


def parse_docstring(docstring):
    """
    Parse out the parts of a docstring.  Return (title, body, metadata).
    """
    docstring = trim_docstring(docstring)
    parts = re.split(r'\n{2,}', docstring)
    title = parts[0]
    if len(parts) == 1:
        body = ''
        metadata = {}
    else:
        parser = HeaderParser()
        try:
            metadata = parser.parsestr(parts[-1])
        except HeaderParseError:
            metadata = {}
            body = "\n\n".join(parts[1:])
        else:
            metadata = dict(metadata.items())
            if metadata:
                body = "\n\n".join(parts[1:-1])
            else:
                body = "\n\n".join(parts[1:])
    return title, body, metadata


def parse_rst(text, default_reference_context, thing_being_parsed=None):
    """
    Convert the string from reST to an XHTML fragment.
    """
    overrides = {
        'doctitle_xform': True,
        'initial_header_level': 3,
        "default_reference_context": default_reference_context,
        "link_base": reverse('django-admindocs-docroot').rstrip('/'),
        'raw_enabled': False,
        'file_insertion_enabled': False,
    }
    if thing_being_parsed:
        thing_being_parsed = force_bytes("<%s>" % thing_being_parsed)
    # Wrap ``text`` in some reST that sets the default role to ``cmsreference``,
    # then restores it.
    source = """
.. default-role:: cmsreference

%s

.. default-role::
"""
    parts = docutils.core.publish_parts(
        source % text,
        source_path=thing_being_parsed, destination_path=None,
        writer_name='html', settings_overrides=overrides,
    )
    return mark_safe(parts['fragment'])

#
# reST roles
#
ROLES = {
    'model': '%s/models/%s/',
    'view': '%s/views/%s/',
    'template': '%s/templates/%s/',
    'filter': '%s/filters/#%s',
    'tag': '%s/tags/#%s',
}


def create_reference_role(rolename, urlbase):
    def _get_full_name(func):
        mod_name = func.__module__
        if six.PY3:
            return '%s.%s' % (mod_name, func.__qualname__)
        else:
            # PY2 does not support __qualname__
            func_name = getattr(func, '__name__', func.__class__.__name__)
            return '%s.%s' % (mod_name, func_name)

    def _role(name, rawtext, text, lineno, inliner, options=None, content=None):
        if options is None:
            options = {}
        if content is None:
            content = []
        text = text.replace(':', '.')   # This is for namespace : to . conversion for link URL and link text.
        link_text = text.lower()        # Link URL string from lower-case text, no changes on template
        if '/views/' in urlbase:        # Views override and check, changes link URL and link text
            urlconf = import_module(settings.ROOT_URLCONF)
            view_functions = extract_views_from_urlpatterns(urlconf.urlpatterns)
            for (func, regex, namespace, func_name) in view_functions:
                if link_text in ('.'.join((namespace or []) + (func_name and [func_name] or []))):
                    link_text = _get_full_name(func)
                    text = text.replace(func_name, func.func_name)
        elif '/models/' in urlbase:     # Models override, changes link URL to correct
                                        # format according to model from lowercase text.
            m_list = [m._meta for m in apps.get_models()]
            for m in m_list:
                if link_text in ('.'.join(([m.app_label] or []) + ([m.model_name] or []))):
                    link_text = m.label
        elif '/#' in urlbase:           # Filters and tag override for link text
            text = text.split('-', 1)[1]
        node = docutils.nodes.reference(
            rawtext,
            text,
            refuri=(urlbase % (
                inliner.document.settings.link_base,
                link_text,              # Link URL string
            )),
            **options
        )
        return [node], []
    docutils.parsers.rst.roles.register_canonical_role(rolename, _role)


def default_reference_role(name, rawtext, text, lineno, inliner, options=None, content=None):
    if options is None:
        options = {}
    if content is None:
        content = []
    context = inliner.document.settings.default_reference_context
    node = docutils.nodes.reference(
        rawtext,
        text,
        refuri=(ROLES[context] % (
            inliner.document.settings.link_base,
            text.lower(),
        )),
        **options
    )
    return [node], []

if docutils_is_available:
    docutils.parsers.rst.roles.register_canonical_role('cmsreference', default_reference_role)

    for name, urlbase in ROLES.items():
        create_reference_role(name, urlbase)


def extract_views_from_urlpatterns(urlpatterns, base='', namespace=None):
    """
    Return a list of views from a list of urlpatterns.

    Each object in the returned list is a two-tuple: (view_func, regex)
    """
    views = []
    for p in urlpatterns:
        if hasattr(p, 'url_patterns'):
            try:
                patterns = p.url_patterns
            except ImportError:
                continue
            views.extend(extract_views_from_urlpatterns(
                patterns,
                base + p.regex.pattern,
                (namespace or []) + (p.namespace and [p.namespace] or [])
            ))
        elif hasattr(p, 'callback'):
            try:
                views.append((p.callback, base + p.regex.pattern,
                              namespace, p.name))
            except ViewDoesNotExist:
                continue
        else:
            raise TypeError(_("%s does not appear to be a urlpattern object") % p)
    return views
