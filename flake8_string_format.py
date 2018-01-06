#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Extension for flake8 to test string format usage."""
from __future__ import print_function, unicode_literals

import ast
import itertools
import re
import sys
import token
import optparse

from string import Formatter

import flake8

__version__ = '0.3.0.dev0'


if sys.version_info[0] < 3:
    def generate_tokens(readline):
        from collections import namedtuple
        from tokenize import generate_tokens
        Token = namedtuple('Token', 'type string start end line')
        return (Token(*single_token)
                for single_token in generate_tokens(readline))
else:
    from tokenize import generate_tokens


class TextVisitor(ast.NodeVisitor):

    """
    Node visitor for bytes and str instances.

    It tries to detect docstrings as string of the first expression of each
    module, class or function.
    """

    def __init__(self):
        super(TextVisitor, self).__init__()
        self.nodes = []
        self.calls = {}

    def _add_node(self, node):
        if not hasattr(node, 'is_docstring'):
            node.is_docstring = False
        self.nodes += [node]

    def is_base_string(self, node):
        typ = (ast.Str,)
        if sys.version_info[0] > 2:
            typ += (ast.Bytes,)
        return isinstance(node, typ)

    def visit_Str(self, node):
        self._add_node(node)

    def visit_Bytes(self, node):
        self._add_node(node)

    def _visit_definition(self, node):
        # Manually traverse class or function definition
        # * Handle decorators normally
        # * Use special check for body content
        # * Don't handle the rest (e.g. bases)
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_body(node)

    def _visit_body(self, node):
        """
        Traverse the body of the node manually.

        If the first node is an expression which contains a string or bytes it
        marks that as a docstring.
        """
        if (node.body and isinstance(node.body[0], ast.Expr) and
                self.is_base_string(node.body[0].value)):
            node.body[0].value.is_docstring = True
            self.visit(node.body[0].value)

        for sub_node in node.body:
            self.visit(sub_node)

    def visit_Expr(self, node):
        # Skip Expr unless they are calls as they won't be formatted anyway
        # docstrings are handled separately
        if isinstance(node.value, ast.Call):
            self.visit_Call(node.value)

    def visit_Module(self, node):
        self._visit_body(node)

    def visit_ClassDef(self, node):
        # Skipped nodes: ('name', 'bases', 'keywords', 'starargs', 'kwargs')
        self._visit_definition(node)

    def visit_FunctionDef(self, node):
        # Skipped nodes: ('name', 'args', 'returns')
        self._visit_definition(node)

    def visit_Call(self, node):
        if (isinstance(node.func, ast.Attribute) and
                node.func.attr == 'format'):
            if self.is_base_string(node.func.value):
                self.calls[node.func.value] = (node, False)
            elif (isinstance(node.func.value, ast.Name) and
                    node.func.value.id == 'str' and node.args and
                    self.is_base_string(node.args[0])):
                self.calls[node.args[0]] = (node, True)
        super(TextVisitor, self).generic_visit(node)


class StringFormatChecker(object):

    _FORMATTER = Formatter()
    FIELD_REGEX = re.compile(r'^((?:\s|.)*?)(\..*|\[.*\])?$')

    version = __version__
    name = 'flake8-string-format'
    check_raw = False

    ERRORS = {
        101: 'format string does contain unindexed parameters',
        102: 'docstring does contain unindexed parameters',
        103: 'other string does contain unindexed parameters',
        201: 'format call uses to large index ({idx})',
        202: 'format call uses missing keyword ({kw})',
        203: 'format call uses keyword arguments but no named entries',
        204: 'format call uses variable arguments but no numbered entries',
        205: 'format call uses implicit and explicit indexes together',
        301: 'format call provides unused index ({idx})',
        302: 'format call provides unused keyword ({kw})',
    }

    def __init__(self, tree, lines):
        self.tree = tree
        if lines is None:
            self.tokens = None
        else:
            self.tokens = dict((single_token.start, single_token.string)
                               for single_token in self.generate_tokens(lines)
                               if single_token.type == token.STRING)

    @staticmethod
    def generate_tokens(lines):
        line_iter = iter(lines)
        return list(generate_tokens(lambda: next(line_iter)))

    @classmethod
    def add_options(cls, parser):
        class OptionWrapper():

            original = parser

            def add_option(self, *args, **kwargs):
                try:
                    self.original.add_option(*args, **kwargs)
                except (optparse.OptionError, TypeError):
                    use_config = kwargs.pop('parse_from_config', False)
                    option = self.original.add_option(*args, **kwargs)
                    if use_config:
                        # Flake8 2.x uses config_options instead of the
                        # 'parse_from_config' parameter
                        self.original.config_options.append(
                            option.get_opt_string().lstrip('-'))

        # Support Flake8 3.x feature in Flake 2.x
        parser = OptionWrapper()
        parser.add_option(
            '--check-raw-strings', action='store_true', parse_from_config=True,
            help='Also verify raw strings (r".")')

    @classmethod
    def parse_options(cls, options):
        cls.check_raw = options.check_raw_strings

    def _generate_error(self, node, code, **params):
        if sys.version_info[:3] == (3, 4, 2) and isinstance(node, ast.Call):
            # Due to https://bugs.python.org/issue21295 we cannot use the
            # Call object
            node = node.func.value
        msg = 'P{0} {1}'.format(code, self.ERRORS[code])
        msg = msg.format(**params)
        return node.lineno, node.col_offset, msg, type(self)

    def get_fields(self, string):
        fields = set()
        cnt = itertools.count()
        implicit = False
        explicit = False
        try:
            for literal, field, spec, conv in self._FORMATTER.parse(string):
                if field is not None and (conv is None or conv in 'rsa'):
                    if not field:
                        field = str(next(cnt))
                        implicit = True
                    else:
                        explicit = True
                    fields.add(field)
                    fields.update(parsed_spec[1]
                                  for parsed_spec in self._FORMATTER.parse(spec)
                                  if parsed_spec[1] is not None)
        except ValueError as e:
            return set(), False, False
        else:
            return fields, implicit, explicit

    def run(self):
        visitor = TextVisitor()
        visitor.visit(self.tree)
        assert not (set(visitor.calls) - set(visitor.nodes))
        for node in visitor.nodes:
            text = node.s
            if sys.version_info[0] > 2 and isinstance(text, bytes):
                try:
                    # TODO: Maybe decode using file encoding?
                    text = text.decode('ascii')
                except UnicodeDecodeError as e:
                    continue
            fields, implicit, explicit = self.get_fields(text)
            if implicit:
                if node in visitor.calls:
                    assert not node.is_docstring
                    yield self._generate_error(node, 101)
                elif node.is_docstring:
                    yield self._generate_error(node, 102)
                else:
                    if self.tokens is not None:
                        text_token = self.tokens[(node.lineno, node.col_offset)]
                        # Use last character as the quote character
                        quote_start = text_token.index(text_token[-1])
                        prefixes = set(text_token[:quote_start])
                    else:
                        prefixes = set()
                    # Do handle "other" raw strings only when enabled
                    if "r" not in prefixes or self.check_raw:
                        yield self._generate_error(node, 103)

            if node in visitor.calls:
                call, str_args = visitor.calls[node]

                numbers = set()
                names = set()
                # Determine which fields require a keyword and which an arg
                for name in fields:
                    field_match = self.FIELD_REGEX.match(name)
                    try:
                        number = int(field_match.group(1))
                    except ValueError:
                        number = -1
                    # negative numbers are considered keywords
                    if number >= 0:
                        numbers.add(number)
                    else:
                        names.add(field_match.group(1))

                keywords = set(keyword.arg for keyword in call.keywords)
                num_args = len(call.args)
                if str_args:
                    num_args -= 1
                if sys.version_info < (3, 5):
                    has_kwargs = bool(call.kwargs)
                    has_starargs = bool(call.starargs)
                else:
                    has_kwargs = None in keywords
                    has_starargs = sum(1 for arg in call.args
                                       if isinstance(arg, ast.Starred))
                    # TODO: Determine when Starred is not at the end, so make sure we know about it!
                    assert has_starargs <= 1
                    assert not has_starargs or isinstance(call.args[-1], ast.Starred)

                    if has_kwargs:
                        keywords.discard(None)
                    if has_starargs:
                        num_args -= 1

                # if starargs or kwargs is not None, it can't count the
                # parameters but at least check if the args are used
                if has_kwargs:
                    if not names:
                        # No names but kwargs
                        yield self._generate_error(call, 203)
                if has_starargs:
                    if not numbers:
                        # No numbers but args
                        yield self._generate_error(call, 204)

                if not has_kwargs and not has_starargs:
                    # can actually verify numbers and names
                    for number in sorted(numbers):
                        if number >= num_args:
                            yield self._generate_error(call, 201, idx=number)

                    for name in sorted(names):
                        if name not in keywords:
                            yield self._generate_error(call, 202, kw=name)

                for arg in range(num_args):
                    if arg not in numbers:
                        yield self._generate_error(call, 301, idx=arg)

                for keyword in keywords:
                    if keyword not in names:
                        yield self._generate_error(call, 302, kw=keyword)


                if implicit and explicit:
                    yield self._generate_error(call, 205)


class StringFormatCheckerOwnTokens(StringFormatChecker):

    """Implement reading tokens manually for Flake8 2.x."""

    def __init__(self, tree, filename):
        from flake8.engine import pep8

        if filename is None or filename in ('-', 'stdin'):
            lines = pep8.stdin_get_value().splitlines(True)
        else:
            lines = pep8.readlines(filename)

        super(StringFormatCheckerOwnTokens, self).__init__(tree, lines)


if getattr(flake8, '__version_info__', (2, 0))[:3] < (3,):
    StringFormatChecker = StringFormatCheckerOwnTokens
